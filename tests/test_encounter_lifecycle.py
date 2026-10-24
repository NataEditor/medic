"""Тесты Этапа 2: Encounter и жизненный цикл карты пациента.

Проверяем:
- Appointment (слот) и Encounter (обращение) — разные сущности;
- один слот порождает максимум одно обращение;
- после закрытия обращения записи не редактируются;
- жизненный цикл карты: draft → active → archived / anonymized;
- обезличивание скрывает ПДн, но сохраняет медзаписи.
"""

import unittest
from datetime import UTC, date, datetime, timedelta

from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.db.base import Base
from app.models.entities import (
    Appointment,
    Clinic,
    User,
)
from app.models.enums import (
    AppointmentStatus,
    EncounterStatus,
    PatientStatus,
)
from app.schemas.encounters import EncounterOpen
from app.schemas.patients import PatientCreate
from app.services.encounter_service import EncounterService
from app.services.patient_service import PatientService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestPatientLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _dto(series: str = "1234", number: str = "567890") -> PatientCreate:
        return PatientCreate(
            first_name="Иван",
            last_name="Иванов",
            passport_series=series,
            passport_number=number,
            birth_date=date(1990, 1, 1),
            gender="M",
            phone="+79990000001",
        )

    async def test_draft_has_no_medical_record(self) -> None:
        """Черновик создаётся онлайн-записью — ЭМК ещё нет."""
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_draft_patient(self._dto())
            await session.commit()

            self.assertEqual(patient.status, PatientStatus.DRAFT)
            record = await service.patient_repo.get_medical_record(patient.id)
            self.assertIsNone(record, "У черновика не должно быть ЭМК")

    async def test_activate_creates_medical_record(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_draft_patient(self._dto())
            await session.commit()

            activated = await service.activate_patient(patient.id)
            await session.commit()

            self.assertEqual(activated.status, PatientStatus.ACTIVE)
            self.assertIsNotNone(activated.activated_at)
            record = await service.patient_repo.get_medical_record(patient.id)
            self.assertIsNotNone(record, "После активации ЭМК должна появиться")

    async def test_cannot_activate_twice(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_draft_patient(self._dto())
            await session.commit()
            await service.activate_patient(patient.id)
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.activate_patient(patient.id)

    async def test_archive_patient(self) -> None:
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_draft_patient(self._dto())
            await session.commit()
            await service.activate_patient(patient.id)
            await session.commit()

            archived = await service.archive_patient(patient.id, "Переезд")
            await session.commit()

            self.assertEqual(archived.status, PatientStatus.ARCHIVED)
            self.assertIsNotNone(archived.archived_at)

    async def test_anonymize_hides_personal_data_but_keeps_record(self) -> None:
        """Обезличивание ≠ удаление: ПДн скрыты, медзаписи сохранены."""
        async with self.session_factory() as session:
            service = PatientService(session)
            patient = await service.create_draft_patient(self._dto())
            await session.commit()
            await service.activate_patient(patient.id)
            await session.commit()

            original_record = await service.patient_repo.get_medical_record(patient.id)
            self.assertIsNotNone(original_record)

            anonymized = await service.anonymize_patient(patient.id, "Заявка пациента")
            await session.commit()

            self.assertEqual(anonymized.status, PatientStatus.ANONYMIZED)
            self.assertEqual(anonymized.last_name, "Обезличен")
            self.assertIsNone(anonymized.phone)
            self.assertIsNone(anonymized.snils)
            self.assertIsNone(anonymized.passport_scan_path)
            self.assertEqual(anonymized.passport_series, "0000")

            # Медкарта сохранилась
            record = await service.patient_repo.get_medical_record(patient.id)
            self.assertIsNotNone(record, "Медкарта должна сохраниться после обезличивания")
            self.assertEqual(record.id, original_record.id)


class TestEncounter(unittest.IsolatedAsyncioTestCase):
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
            session.add_all([clinic, doctor, other_doctor])
            await session.commit()

            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Пац",
                    last_name="Пациентов",
                    passport_series="1111",
                    passport_number="222222",
                    birth_date=date(1990, 1, 1),
                    gender="M",
                )
            )
            await session.commit()

            appointment = Appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                doctor_id=doctor.id,
                scheduled_at=datetime.now(UTC) + timedelta(hours=1),
                duration_minutes=30,
                status=AppointmentStatus.SCHEDULED,
            )
            session.add(appointment)
            await session.commit()

            self.doctor_id = doctor.id
            self.other_doctor_id = other_doctor.id
            self.patient_id = patient.id
            self.appointment_id = appointment.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_open_encounter_from_appointment(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            encounter = await service.open_encounter(
                doctor, EncounterOpen(appointment_id=self.appointment_id)
            )
            await session.commit()

            self.assertEqual(encounter.status, EncounterStatus.OPEN)
            self.assertEqual(encounter.patient_id, self.patient_id)
            self.assertIsNotNone(encounter.record_id)

            # Слот перешёл в «идёт приём»
            appt = await session.get(Appointment, self.appointment_id)
            self.assertEqual(appt.status, AppointmentStatus.IN_PROGRESS)

    async def test_one_appointment_one_encounter(self) -> None:
        """Один слот порождает максимум одно обращение."""
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            await service.open_encounter(
                doctor, EncounterOpen(appointment_id=self.appointment_id)
            )
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.open_encounter(
                    doctor, EncounterOpen(appointment_id=self.appointment_id)
                )

    async def test_other_doctor_cannot_open_encounter(self) -> None:
        async with self.session_factory() as session:
            other = await session.get(User, self.other_doctor_id)
            service = EncounterService(session)
            with self.assertRaises(PermissionDeniedError):
                await service.open_encounter(
                    other, EncounterOpen(appointment_id=self.appointment_id)
                )

    async def test_close_encounter_completes_appointment(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            encounter = await service.open_encounter(
                doctor, EncounterOpen(appointment_id=self.appointment_id)
            )
            await session.commit()

            closed = await service.close_encounter(doctor, encounter.id)
            await session.commit()

            self.assertEqual(closed.status, EncounterStatus.CLOSED)
            self.assertIsNotNone(closed.closed_at)

            appt = await session.get(Appointment, self.appointment_id)
            self.assertEqual(appt.status, AppointmentStatus.COMPLETED)

    async def test_cannot_write_to_closed_encounter(self) -> None:
        """После закрытия записи не редактируются."""
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            encounter = await service.open_encounter(
                doctor, EncounterOpen(appointment_id=self.appointment_id)
            )
            await session.commit()
            await service.close_encounter(doctor, encounter.id)
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.ensure_encounter_open(encounter.id)

    async def test_cannot_open_encounter_for_draft_patient(self) -> None:
        """Черновик карты: приём невозможен до активации ресепшеном."""
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            draft = await PatientService(session).create_draft_patient(
                PatientCreate(
                    first_name="Черновик",
                    last_name="Без",
                    passport_series="9999",
                    passport_number="888888",
                    birth_date=date(1995, 5, 5),
                    gender="F",
                )
            )
            await session.commit()

            service = EncounterService(session)
            with self.assertRaises(BusinessRuleError):
                await service.open_encounter(doctor, EncounterOpen(patient_id=draft.id))

    async def test_walk_in_encounter_without_appointment(self) -> None:
        """Приём без предварительной записи (walk-in)."""
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            encounter = await service.open_encounter(
                doctor, EncounterOpen(patient_id=self.patient_id)
            )
            await session.commit()

            self.assertIsNone(encounter.appointment_id)
            self.assertEqual(encounter.status, EncounterStatus.OPEN)

    async def test_encounter_requires_appointment_or_patient(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = EncounterService(session)
            with self.assertRaises(BusinessRuleError):
                await service.open_encounter(doctor, EncounterOpen())


if __name__ == "__main__":
    unittest.main()
