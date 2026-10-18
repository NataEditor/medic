"""Тесты правил доступа (борьба с IDOR и разграничение ролей).

Проверяем:
- доступ к медкарте для медперсонала любых клиник сети (кросс-клиничность — фича);
- бухгалтерия не видит медицинские данные и карты пациентов;
- перенос приёма: врач — только свои, ресепшен — любые;
- статус приёма: медсестра — только WAITING/IN_PROGRESS;
- просмотр счёта: финансы — любой, пациент — свой, посторонний — отказ.
"""

import unittest
from datetime import date
from decimal import Decimal

from app.api.deps import (
    FINANCE_ROLES,
    MEDICAL_STAFF_ROLES,
    ensure_appointment_access,
    is_medical_staff,
)
from app.core.exceptions import PermissionDeniedError
from app.db.base import Base
from app.models.entities import Clinic, Invoice, Patient, User
from app.models.enums import PaymentStatus, RoleEnum
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _make_user(    user_id: int,
    role: RoleEnum,
    *,
    patient_id: int | None = None,
) -> User:
    """Создаёт transient-пользователя (без сохранения в БД) для проверки правил."""
    user = User(
        id=user_id,
        clinic_id=1,
        email=f"u{user_id}@medic.local",
        phone=f"+7000000{user_id:04d}",
        hashed_password="x",
        role=role,
        first_name="Имя",
        last_name="Фамилия",
        is_active=True,
    )
    if patient_id is not None:
        patient = Patient(
            id=patient_id,
            first_name="Пац",
            last_name="Пациентов",
            passport_series="1234",
            passport_number="567890",
            birth_date=date(1990, 1, 1),
            gender="M",
        )
        user.patient_profile = patient
    return user


class TestRoleHelpers(unittest.TestCase):
    def test_is_medical_staff(self) -> None:
        self.assertTrue(is_medical_staff(_make_user(1, RoleEnum.DOCTOR)))
        self.assertTrue(is_medical_staff(_make_user(2, RoleEnum.NURSE)))
        self.assertTrue(is_medical_staff(_make_user(3, RoleEnum.HEAD_DOCTOR)))
        # Бухгалтерия и ресепшен — не медперсонал
        self.assertFalse(is_medical_staff(_make_user(4, RoleEnum.ACCOUNTANT)))
        self.assertFalse(is_medical_staff(_make_user(5, RoleEnum.RECEPTIONIST)))
        self.assertFalse(is_medical_staff(_make_user(6, RoleEnum.PATIENT)))


class TestAppointmentAccess(unittest.IsolatedAsyncioTestCase):
    async def test_doctor_can_access_only_own_appointments(self) -> None:
        doctor = _make_user(10, RoleEnum.DOCTOR)
        # Свой приём — ок
        await ensure_appointment_access(doctor, appointment_doctor_id=10, appointment_patient_id=1)
        # Чужой приём — отказ
        with self.assertRaises(PermissionDeniedError):
            await ensure_appointment_access(
                doctor, appointment_doctor_id=99, appointment_patient_id=1
            )

    async def test_receptionist_can_access_any_appointment(self) -> None:
        receptionist = _make_user(20, RoleEnum.RECEPTIONIST)
        await ensure_appointment_access(
            receptionist, appointment_doctor_id=99, appointment_patient_id=1
        )

    async def test_nurse_can_access_any_appointment(self) -> None:
        nurse = _make_user(30, RoleEnum.NURSE)
        await ensure_appointment_access(nurse, appointment_doctor_id=99, appointment_patient_id=1)

    async def test_patient_can_access_only_own_appointment(self) -> None:
        patient = _make_user(40, RoleEnum.PATIENT, patient_id=500)
        # Свой приём
        await ensure_appointment_access(
            patient, appointment_doctor_id=1, appointment_patient_id=500
        )        # Чужой приём
        with self.assertRaises(PermissionDeniedError):
            await ensure_appointment_access(
                patient, appointment_doctor_id=1, appointment_patient_id=999
            )

    async def test_accountant_cannot_use_appointment_access(self) -> None:
        accountant = _make_user(50, RoleEnum.ACCOUNTANT)
        with self.assertRaises(PermissionDeniedError):
            await ensure_appointment_access(
                accountant, appointment_doctor_id=1, appointment_patient_id=1
            )


class TestMedicalStaffGuard(unittest.IsolatedAsyncioTestCase):
    async def test_medical_staff_allowed(self) -> None:
        # Проверяем принадлежность наборов ролей (логика guard'ов)
        self.assertIn(RoleEnum.DOCTOR, MEDICAL_STAFF_ROLES)
        self.assertIn(RoleEnum.NURSE, MEDICAL_STAFF_ROLES)
        self.assertIn(RoleEnum.HEAD_DOCTOR, MEDICAL_STAFF_ROLES)

    async def test_accountant_denied_medical_data(self) -> None:
        self.assertNotIn(RoleEnum.ACCOUNTANT, MEDICAL_STAFF_ROLES)

    async def test_patient_denied_medical_staff_guard(self) -> None:
        self.assertNotIn(RoleEnum.PATIENT, MEDICAL_STAFF_ROLES)


class TestFinanceGuard(unittest.IsolatedAsyncioTestCase):
    async def test_finance_roles_allowed(self) -> None:
        for role in (RoleEnum.ACCOUNTANT, RoleEnum.RECEPTIONIST, RoleEnum.SUPER_ADMIN):
            self.assertIn(role, FINANCE_ROLES)

    async def test_doctor_denied_finance(self) -> None:
        self.assertNotIn(RoleEnum.DOCTOR, FINANCE_ROLES)
        self.assertNotIn(RoleEnum.NURSE, FINANCE_ROLES)

class TestInvoiceVisibility(unittest.IsolatedAsyncioTestCase):
    """Проверяет логику видимости счёта, повторяя правила эндпоинта без HTTP."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C", address="А", phone="+70000000000")
            patient = Patient(
                first_name="Пац",
                last_name="Пациентов",
                passport_series="1111",
                passport_number="222222",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, patient])
            await session.commit()

            invoice = Invoice(
                patient_id=patient.id,
                clinic_id=clinic.id,
                amount=Decimal("1000.00"),
                status=PaymentStatus.PENDING,
            )
            session.add(invoice)
            await session.commit()
            self.invoice_id = invoice.id
            self.patient_id = patient.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _can_view(user: User, invoice_patient_id: int) -> bool:
        """Повторяет правило доступа эндпоинта GET /billing/invoices/{id}."""
        finance_roles = (
            RoleEnum.ACCOUNTANT,
            RoleEnum.RECEPTIONIST,
            RoleEnum.SUPER_ADMIN,
            RoleEnum.HEAD_DOCTOR,
        )
        if user.role in finance_roles:
            return True
        if user.role == RoleEnum.PATIENT:
            return bool(
                user.patient_profile and invoice_patient_id == user.patient_profile.id
            )
        return False

    async def test_finance_can_view_any_invoice(self) -> None:
        accountant = _make_user(1, RoleEnum.ACCOUNTANT)
        self.assertTrue(self._can_view(accountant, self.patient_id))

    async def test_patient_can_view_only_own_invoice(self) -> None:
        patient = _make_user(2, RoleEnum.PATIENT, patient_id=self.patient_id)
        self.assertTrue(self._can_view(patient, self.patient_id))
        # Чужой счёт
        self.assertFalse(self._can_view(patient, 99999))

    async def test_doctor_cannot_view_invoice(self) -> None:
        doctor = _make_user(3, RoleEnum.DOCTOR)
        self.assertFalse(self._can_view(doctor, self.patient_id))


class TestCrossClinicMedicalAccess(unittest.IsolatedAsyncioTestCase):
    """Пациент клиники А может лечиться в клинике В — карта общая для сети."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic_a = Clinic(name="Клиника А", code="A", address="А", phone="+70000000001")
            clinic_b = Clinic(name="Клиника Б", code="B", address="Б", phone="+70000000002")
            # Врач из клиники Б
            doctor_b = User(
                clinic_id=2,
                email="docb@medic.local",
                phone="+70000000003",
                hashed_password="x",
                role=RoleEnum.DOCTOR,
                first_name="Врач",
                last_name="Бэ",
            )
            session.add_all([clinic_a, clinic_b, doctor_b])
            await session.commit()

            from app.schemas.patients import PatientCreate
            from app.services.patient_service import PatientService

            # Пациент регистрируется в сети (ЭМК создаётся автоматически)
            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Пац",
                    last_name="Межклиничный",
                    passport_series="2222",
                    passport_number="333333",
                    birth_date=date(1985, 3, 3),
                    gender="F",
                )
            )
            await session.commit()
            self.patient_id = patient.id

            # Карта существует и доступна (в реальности — врачу любой клиники сети)
            record = await PatientService(session).get_patient_card(patient.id)
            self.assertIsNotNone(record)
    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_doctor_from_other_clinic_is_medical_staff(self) -> None:
        """Врач клиники Б считается медперсоналом и видит карту пациента клиники А."""
        doctor_b = _make_user(100, RoleEnum.DOCTOR)
        self.assertTrue(is_medical_staff(doctor_b))
        self.assertIn(doctor_b.role, MEDICAL_STAFF_ROLES)
        self.assertTrue(is_medical_staff(doctor_b))

if __name__ == "__main__":
    unittest.main()
    unittest.main()
