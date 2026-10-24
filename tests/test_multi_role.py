"""Тесты Этапа 1: мультиролевость, админ клиники, доступ «по касанию».

Проверяем:
- у пользователя может быть несколько ролей, права суммируются;
- CLINIC_ADMIN не читает ЭМК, но управляет людьми;
- врач видит карту только пациентов, с которыми есть приём («по касанию»);
- создавать карту пациента может только регистратура и выше.
"""

import unittest
from datetime import UTC, date, datetime, timedelta

from app.api.deps import (
    PATIENT_CREATE_ROLES,
    can_read_emk,
    ensure_medical_access,
    is_admin,
    is_clinic_admin,
    is_medical_staff,
)
from app.core.exceptions import PermissionDeniedError
from app.db.base import Base
from app.models.entities import Appointment, Clinic, Patient, User
from app.models.enums import AppointmentStatus, RoleEnum
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _user(roles: list[RoleEnum], user_id: int = 1) -> User:
    return User(
        id=user_id,
        clinic_id=1,
        email=f"u{user_id}@medic.local",
        phone=f"+7000000{user_id:04d}",
        hashed_password="x",
        roles=[r.value for r in roles],
        first_name="Имя",
        last_name="Фамилия",
        is_active=True,
    )


class TestMultiRoleModel(unittest.TestCase):
    def test_user_can_have_multiple_roles(self) -> None:
        user = _user([RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR])
        self.assertTrue(user.has_role(RoleEnum.DOCTOR))
        self.assertTrue(user.has_role(RoleEnum.HEAD_DOCTOR))
        self.assertFalse(user.has_role(RoleEnum.ACCOUNTANT))

    def test_has_any_role(self) -> None:
        user = _user([RoleEnum.CLINIC_ADMIN, RoleEnum.RECEPTIONIST])
        self.assertTrue(user.has_any_role(RoleEnum.CLINIC_ADMIN, RoleEnum.ACCOUNTANT))
        self.assertFalse(user.has_any_role(RoleEnum.DOCTOR, RoleEnum.NURSE))

    def test_primary_role_is_first(self) -> None:
        user = _user([RoleEnum.NURSE, RoleEnum.DOCTOR])
        self.assertEqual(user.role, RoleEnum.NURSE)

    def test_role_setter_backward_compatible(self) -> None:
        user = User(
            email="a@b.c",
            phone="1",
            hashed_password="x",
            first_name="A",
            last_name="B",
            role=RoleEnum.DOCTOR,
        )
        self.assertEqual(user.roles, ["doctor"])

    def test_role_enums(self) -> None:
        user = _user([RoleEnum.DOCTOR, RoleEnum.NURSE])
        self.assertEqual(user.role_enums, [RoleEnum.DOCTOR, RoleEnum.NURSE])


class TestClinicAdminSeparation(unittest.TestCase):
    def test_clinic_admin_is_admin_but_not_medical_staff(self) -> None:
        admin = _user([RoleEnum.CLINIC_ADMIN])
        self.assertTrue(is_admin(admin))
        self.assertTrue(is_clinic_admin(admin))
        # Ключевое: админ клиники НЕ читает ЭМК
        self.assertFalse(is_medical_staff(admin))
        self.assertFalse(can_read_emk(admin))

    def test_head_doctor_reads_emk(self) -> None:
        head = _user([RoleEnum.HEAD_DOCTOR])
        self.assertTrue(is_medical_staff(head))
        self.assertTrue(can_read_emk(head))

    def test_accountant_has_no_medical_access(self) -> None:
        acc = _user([RoleEnum.ACCOUNTANT])
        self.assertFalse(is_medical_staff(acc))
        self.assertFalse(can_read_emk(acc))

    def test_multi_role_doctor_plus_admin(self) -> None:
        """Врач + админ клиники: права суммируются, ЭМК доступна."""
        user = _user([RoleEnum.CLINIC_ADMIN, RoleEnum.DOCTOR])
        self.assertTrue(is_admin(user))
        self.assertTrue(is_medical_staff(user))
        self.assertTrue(can_read_emk(user))


class TestPatientCreateRoles(unittest.TestCase):
    def test_doctor_cannot_create_patient(self) -> None:
        self.assertNotIn(RoleEnum.DOCTOR, PATIENT_CREATE_ROLES)

    def test_reception_can_create_patient(self) -> None:
        self.assertIn(RoleEnum.RECEPTIONIST, PATIENT_CREATE_ROLES)

    def test_clinic_admin_can_create_patient(self) -> None:
        self.assertIn(RoleEnum.CLINIC_ADMIN, PATIENT_CREATE_ROLES)


class TestMedicalAccessByTouch(unittest.IsolatedAsyncioTestCase):
    """Правило «по касанию»: врач видит карту только пациентов своих приёмов."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C", address="А", phone="+70000000000")
            doctor = User(
                clinic_id=1,
                email="doc@medic.local",
                phone="+70000000001",
                hashed_password="x",
                roles=["doctor"],
                first_name="Врач",
                last_name="Первый",
            )
            other_doctor = User(
                clinic_id=1,
                email="doc2@medic.local",
                phone="+70000000002",
                hashed_password="x",
                roles=["doctor"],
                first_name="Врач",
                last_name="Второй",
            )
            patient = Patient(
                first_name="Пац",
                last_name="Пациентов",
                passport_series="1111",
                passport_number="222222",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, doctor, other_doctor, patient])
            await session.commit()

            # Приём: doctor ↔ patient
            session.add(
                Appointment(
                    clinic_id=clinic.id,
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    scheduled_at=datetime.now(UTC) + timedelta(days=1),
                    duration_minutes=30,
                    status=AppointmentStatus.SCHEDULED,
                )
            )
            await session.commit()

            self.doctor_id = doctor.id
            self.other_doctor_id = other_doctor.id
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_doctor_with_appointment_has_access(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            # Не должно бросить исключение
            await ensure_medical_access(session, doctor, self.patient_id)

    async def test_doctor_without_appointment_denied(self) -> None:
        async with self.session_factory() as session:
            other = await session.get(User, self.other_doctor_id)
            with self.assertRaises(PermissionDeniedError):
                await ensure_medical_access(session, other, self.patient_id)

    async def test_head_doctor_has_access_to_any_patient(self) -> None:
        async with self.session_factory() as session:
            head = _user([RoleEnum.HEAD_DOCTOR], user_id=99)
            await ensure_medical_access(session, head, self.patient_id)

    async def test_clinic_admin_denied_medical_access(self) -> None:
        async with self.session_factory() as session:
            admin = _user([RoleEnum.CLINIC_ADMIN], user_id=98)
            with self.assertRaises(PermissionDeniedError):
                await ensure_medical_access(session, admin, self.patient_id)

    async def test_accountant_denied_medical_access(self) -> None:
        async with self.session_factory() as session:
            acc = _user([RoleEnum.ACCOUNTANT], user_id=97)
            with self.assertRaises(PermissionDeniedError):
                await ensure_medical_access(session, acc, self.patient_id)


if __name__ == "__main__":
    unittest.main()
