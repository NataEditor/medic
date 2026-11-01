"""Тесты: врач отмечает свои выполнения, отчёт производительности, аудит просмотров.

Проверяем:
- врач может отметить выполнение процедуры, которую назначил сам;
- врач НЕ может отметить чужое назначение (403);
- медсестра отмечает своё, performed_by = она;
- отчёт производительности считает три счётчика;
- врач видит только свои показатели;
- просмотр ЭМК и скана паспорта пишется в аудит.
"""

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.exceptions import PermissionDeniedError
from app.db.base import Base
from app.models.entities import (
    Assignment,
    AuditLog,
    Clinic,
    Encounter,
    MedicalRecord,
    Patient,
    ProcedureExecution,
    Service,
    User,
)
from app.models.enums import (
    AssignmentType,
    EncounterStatus,
)
from app.schemas.assignments import ProcedureExecutionCreate
from app.services.assignment_service import AssignmentService
from app.services.report_service import ReportService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestDoctorCanRecordOwnExecution(unittest.IsolatedAsyncioTestCase):
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
            nurse = User(
                clinic_id=1,
                email="nurse@medic.local",
                phone="+70000000003",
                hashed_password="x",
                roles=["nurse"],
                first_name="Мед",
                last_name="Сестра",
            )
            session.add_all([clinic, doctor, other_doctor, nurse])
            await session.commit()

            from app.schemas.patients import PatientCreate
            from app.services.patient_service import PatientService

            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Пац",
                    last_name="Пациентов",
                    passport_series="5555",
                    passport_number="555555",
                    birth_date=date(1990, 1, 1),
                    gender="M",
                )
            )
            await session.commit()

            record = (
                await session.execute(
                    select(MedicalRecord).where(MedicalRecord.patient_id == patient.id)
                )
            ).scalar_one()

            service = Service(
                code="PROC-USI",
                name="УЗИ",
                price=Decimal("2000.00"),
                duration_minutes=30,
                is_procedure=True,
            )
            session.add(service)
            await session.commit()

            # Назначение врача №1
            assignment = Assignment(
                record_id=record.id,
                clinic_id=clinic.id,
                doctor_id=doctor.id,
                service_id=service.id,
                type=AssignmentType.PROCEDURE,
                title="УЗИ",
                procedure_type="ultrasound",
            )
            session.add(assignment)
            await session.commit()

            self.doctor_id = doctor.id
            self.other_doctor_id = other_doctor.id
            self.nurse_id = nurse.id
            self.assignment_id = assignment.id
            self.patient_id = patient.id
            self.clinic_id = clinic.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _pay_assignment(self, session: AsyncSession) -> None:
        """Создаёт оплаченный счёт по назначению (процедура требует оплаты)."""
        from app.models.entities import Invoice
        from app.models.enums import PaymentStatus

        session.add(
            Invoice(
                assignment_id=self.assignment_id,
                patient_id=self.patient_id,
                clinic_id=self.clinic_id,
                amount=Decimal("2000.00"),
                status=PaymentStatus.PAID,
            )
        )
        await session.commit()

    async def test_doctor_records_own_execution(self) -> None:
        """Врач отмечает процедуру, которую назначил сам."""
        async with self.session_factory() as session:
            await self._pay_assignment(session)
            doctor = await session.get(User, self.doctor_id)
            service = AssignmentService(session)

            execution = await service.record_execution(
                doctor,
                ProcedureExecutionCreate(
                    assignment_id=self.assignment_id,
                    executed_at=datetime.now(UTC),
                ),
            )
            await session.commit()

            self.assertEqual(execution.performed_by_id, doctor.id)
            self.assertIsNotNone(execution.performed_at)

    async def test_doctor_cannot_record_other_doctor_assignment(self) -> None:
        """Врач не может отметить чужое назначение → 403."""
        async with self.session_factory() as session:
            await self._pay_assignment(session)
            other = await session.get(User, self.other_doctor_id)
            service = AssignmentService(session)

            with self.assertRaises(PermissionDeniedError):
                await service.record_execution(
                    other,
                    ProcedureExecutionCreate(
                        assignment_id=self.assignment_id,
                        executed_at=datetime.now(UTC),
                    ),
                )

    async def test_nurse_records_own_execution(self) -> None:
        """Медсестра отмечает своё выполнение, performed_by = она."""
        async with self.session_factory() as session:
            await self._pay_assignment(session)
            nurse = await session.get(User, self.nurse_id)
            service = AssignmentService(session)

            execution = await service.record_execution(
                nurse,
                ProcedureExecutionCreate(
                    assignment_id=self.assignment_id,
                    executed_at=datetime.now(UTC),
                ),
            )
            await session.commit()

            self.assertEqual(execution.performed_by_id, nurse.id)

    async def test_receptionist_cannot_record_execution(self) -> None:
        """Ресепшен не отмечает выполнение процедур."""
        async with self.session_factory() as session:
            await self._pay_assignment(session)
            receptionist = User(
                clinic_id=1,
                email="rec@medic.local",
                phone="+70000000009",
                hashed_password="x",
                roles=["receptionist"],
                first_name="Рес",
                last_name="Епшен",
            )
            session.add(receptionist)
            await session.commit()

            service = AssignmentService(session)
            with self.assertRaises(PermissionDeniedError):
                await service.record_execution(
                    receptionist,
                    ProcedureExecutionCreate(
                        assignment_id=self.assignment_id,
                        executed_at=datetime.now(UTC),
                    ),
                )


class TestDoctorProductivityReport(unittest.IsolatedAsyncioTestCase):
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
            accountant = User(
                clinic_id=1,
                email="acc@medic.local",
                phone="+70000000002",
                hashed_password="x",
                roles=["accountant"],
                first_name="Бух",
                last_name="Галтер",
            )
            patient = Patient(
                first_name="Пац",
                last_name="Пациентов",
                passport_series="6666",
                passport_number="666666",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, doctor, accountant, patient])
            await session.commit()

            record = MedicalRecord(patient_id=patient.id, record_number="MED-2026-000001")
            session.add(record)
            await session.commit()

            # Закрытый Encounter
            session.add(
                Encounter(
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    clinic_id=clinic.id,
                    record_id=record.id,
                    status=EncounterStatus.CLOSED,
                    opened_at=datetime.now(UTC) - timedelta(days=1),
                    closed_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            # Назначение процедуры
            assignment = Assignment(
                record_id=record.id,
                clinic_id=clinic.id,
                doctor_id=doctor.id,
                type=AssignmentType.PROCEDURE,
                title="УЗИ",
            )
            session.add(assignment)
            await session.commit()

            # Выполнение врачом лично
            session.add(
                ProcedureExecution(
                    assignment_id=assignment.id,
                    nurse_id=doctor.id,
                    performed_by_id=doctor.id,
                    executed_at=datetime.now(UTC) - timedelta(hours=5),
                    performed_at=datetime.now(UTC) - timedelta(hours=5),
                )
            )
            await session.commit()

            self.doctor_id = doctor.id
            self.accountant_id = accountant.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_report_counts_three_metrics(self) -> None:
        async with self.session_factory() as session:
            accountant = await session.get(User, self.accountant_id)
            service = ReportService(session)
            today = date.today()
            report = await service.doctor_productivity(
                accountant,
                self.doctor_id,
                today - timedelta(days=7),
                today,
            )

            self.assertEqual(len(report.doctors), 1)
            item = report.doctors[0]
            self.assertEqual(item.closed_encounters, 1)
            self.assertEqual(item.prescribed_procedures, 1)
            self.assertEqual(item.performed_procedures, 1)

    async def test_doctor_sees_only_own_report(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = ReportService(session)
            today = date.today()

            # Свои показатели — ок
            report = await service.my_productivity(doctor, today - timedelta(days=7), today)
            self.assertEqual(len(report.doctors), 1)

    async def test_doctor_cannot_see_other_doctor_report(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            service = ReportService(session)
            today = date.today()

            with self.assertRaises(PermissionDeniedError):
                await service.doctor_productivity(doctor, 99999, today - timedelta(days=7), today)


class TestAuditOfViews(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_view_audit_log_written(self) -> None:
        """Просмотр ПДн фиксируется в audit_logs."""
        from app.api.deps import record_audit_log

        async with self.session_factory() as session:
            user = User(
                clinic_id=1,
                email="doc@medic.local",
                phone="+70000000001",
                hashed_password="x",
                roles=["doctor"],
                first_name="Врач",
                last_name="Первый",
            )
            session.add(user)
            await session.commit()

            await record_audit_log(
                db=session,
                user=user,
                action="view_medical_record",
                entity_name="medical_record",
                entity_id=1,
            )
            await session.commit()

            logs = list((await session.execute(select(AuditLog))).scalars().all())
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].action, "view_medical_record")
            self.assertEqual(logs[0].user_id, user.id)


if __name__ == "__main__":
    unittest.main()
