"""Тесты бизнес-правил: слоты, счета за назначения, отмена приёма.

Проверяем:
- отменённые приёмы/неявки освобождают слот в расписании;
- счёт создаётся при назначении платной процедуры/анализа;
- отмена приёма аннулирует неоплаченный счёт (оплаченный — не трогает).
"""

import unittest
from datetime import UTC, date, datetime, time
from decimal import Decimal

from app.db.base import Base
from app.models.entities import (
    Clinic,
    DoctorProfile,
    DoctorSchedule,
    Invoice,
    MedicalRecord,
    Service,
    User,
)
from app.models.enums import (
    AppointmentStatus,
    AssignmentType,
    PaymentStatus,
    RoleEnum,
)
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentStatusUpdate,
)
from app.schemas.assignments import AssignmentCreate
from app.services.appointment_service import AppointmentService
from app.services.assignment_service import AssignmentService
from app.services.schedule_service import ScheduleService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestBusinessRules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="Клиника", code="C1", address="Адрес", phone="+70000000000")
            doctor = User(
                clinic_id=1,
                email="doc@example.com",
                phone="+70000000010",
                hashed_password="hash",
                role=RoleEnum.DOCTOR,
                first_name="Врач",
                last_name="Докторов",
            )
            session.add_all([clinic, doctor])
            await session.commit()

            session.add(DoctorProfile(user_id=doctor.id, specialization="Терапевт"))

            # Расписание: понедельник 09:00-18:00 без обеда (для простоты слотов)
            session.add(
                DoctorSchedule(
                    doctor_id=doctor.id,
                    clinic_id=clinic.id,
                    weekday=0,
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                    cabinet_number="101",
                )
            )
            await session.commit()

            from app.schemas.patients import PatientCreate
            from app.services.patient_service import PatientService

            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Пац",
                    last_name="Пациентов",
                    passport_series="1234",
                    passport_number="567890",
                    birth_date=date(1990, 1, 1),
                    gender="M",
                    phone="+70000000020",
                )
            )
            service = Service(
                code="LAB-CBC",
                name="Общий анализ крови",
                price=Decimal("800.00"),
                duration_minutes=10,
                is_procedure=True,
            )
            session.add(service)
            await session.commit()

            self.clinic_id = clinic.id
            self.doctor_id = doctor.id
            self.patient_id = patient.id
            self.service_id = service.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    @staticmethod
    def _next_monday() -> date:
        today = date.today()
        ahead = (7 - today.weekday()) % 7 or 7
        return date.fromordinal(today.toordinal() + ahead)

    # ----- 1. Отменённые приёмы освобождают слот -----
    async def test_cancelled_appointment_frees_slot(self) -> None:
        target = self._next_monday()
        slot_time = datetime.combine(target, time(10, 0), tzinfo=UTC)

        async with self.session_factory() as session:
            appt_service = AppointmentService(session)
            appt = await appt_service.create_appointment(
                AppointmentCreate(
                    clinic_id=self.clinic_id,
                    patient_id=self.patient_id,
                    doctor_id=self.doctor_id,
                    scheduled_at=slot_time,
                    duration_minutes=30,
                )
            )
            await session.commit()

            schedule = ScheduleService(session)
            day_busy = await schedule.get_doctor_day_schedule(
                self.doctor_id, target, slot_minutes=30
            )
            busy_at_10 = any(
                s.start == datetime.combine(target, time(10, 0)) for s in day_busy.busy_intervals
            )
            self.assertTrue(busy_at_10, "Приём должен занимать слот")

            # Отменяем приём
            await appt_service.update_status(
                appt.id, AppointmentStatusUpdate(status=AppointmentStatus.CANCELLED)
            )
            await session.commit()

            day_free = await schedule.get_doctor_day_schedule(
                self.doctor_id, target, slot_minutes=30
            )
            free_at_10 = any(
                s.start == datetime.combine(target, time(10, 0)) for s in day_free.free_slots
            )
            self.assertTrue(free_at_10, "После отмены слот должен освободиться")

    # ----- 2. Счёт создаётся при назначении платной процедуры -----
    async def test_invoice_created_for_billable_assignment(self) -> None:
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            record = (
                await session.execute(
                    select(MedicalRecord).where(MedicalRecord.patient_id == self.patient_id)
                )
            ).scalar_one()

            assignment = await AssignmentService(session).create_assignment(
                doctor,
                AssignmentCreate(
                    record_id=record.id,
                    clinic_id=self.clinic_id,
                    service_id=self.service_id,
                    type=AssignmentType.ANALYSIS,
                    title="Общий анализ крови",
                ),
            )
            await session.commit()

            invoice = (
                await session.execute(select(Invoice).where(Invoice.assignment_id == assignment.id))
            ).scalar_one_or_none()
            self.assertIsNotNone(invoice, "Счёт за назначение должен создаться")
            self.assertEqual(invoice.amount, Decimal("800.00"))
            self.assertEqual(invoice.status, PaymentStatus.PENDING)

    async def test_no_invoice_for_medication_assignment(self) -> None:
        """Лекарства не тарифицируются — счёт не создаётся."""
        async with self.session_factory() as session:
            doctor = await session.get(User, self.doctor_id)
            record = (
                await session.execute(
                    select(MedicalRecord).where(MedicalRecord.patient_id == self.patient_id)
                )
            ).scalar_one()

            assignment = await AssignmentService(session).create_assignment(
                doctor,
                AssignmentCreate(
                    record_id=record.id,
                    clinic_id=self.clinic_id,
                    type=AssignmentType.MEDICATION,
                    title="Парацетамол",
                ),
            )
            await session.commit()

            invoice = (
                await session.execute(select(Invoice).where(Invoice.assignment_id == assignment.id))
            ).scalar_one_or_none()
            self.assertIsNone(invoice)

    # ----- 3. Отмена приёма аннулирует неоплаченный счёт -----
    async def test_cancel_appointment_cancels_unpaid_invoice(self) -> None:
        target = self._next_monday()
        slot_time = datetime.combine(target, time(11, 0), tzinfo=UTC)

        async with self.session_factory() as session:
            appt_service = AppointmentService(session)
            appt = await appt_service.create_appointment(
                AppointmentCreate(
                    clinic_id=self.clinic_id,
                    patient_id=self.patient_id,
                    doctor_id=self.doctor_id,
                    service_id=self.service_id,
                    scheduled_at=slot_time,
                    duration_minutes=30,
                )
            )
            await session.commit()

            invoice = (
                await session.execute(select(Invoice).where(Invoice.appointment_id == appt.id))
            ).scalar_one()
            self.assertEqual(invoice.status, PaymentStatus.PENDING)

            await appt_service.update_status(
                appt.id, AppointmentStatusUpdate(status=AppointmentStatus.CANCELLED)
            )
            await session.commit()

            refreshed = await session.get(Invoice, invoice.id)
            self.assertEqual(refreshed.status, PaymentStatus.CANCELLED)

    async def test_cancel_appointment_keeps_paid_invoice(self) -> None:
        """Оплаченный счёт авто-отменять нельзя — только через возврат бухгалтерии."""
        target = self._next_monday()
        slot_time = datetime.combine(target, time(12, 0), tzinfo=UTC)

        async with self.session_factory() as session:
            appt_service = AppointmentService(session)
            appt = await appt_service.create_appointment(
                AppointmentCreate(
                    clinic_id=self.clinic_id,
                    patient_id=self.patient_id,
                    doctor_id=self.doctor_id,
                    service_id=self.service_id,
                    scheduled_at=slot_time,
                    duration_minutes=30,
                )
            )
            await session.commit()

            invoice = (
                await session.execute(select(Invoice).where(Invoice.appointment_id == appt.id))
            ).scalar_one()

            # Имитируем оплату: помечаем счёт оплаченным и добавляем платёж
            from app.models.entities import Payment
            from app.models.enums import PaymentMethod

            session.add(
                Payment(
                    invoice_id=invoice.id,
                    amount=invoice.amount,
                    method=PaymentMethod.CASH,
                    cashier_id=self.doctor_id,
                )
            )
            invoice.status = PaymentStatus.PAID
            await session.commit()

            await appt_service.update_status(
                appt.id, AppointmentStatusUpdate(status=AppointmentStatus.CANCELLED)
            )
            await session.commit()

            refreshed = await session.get(Invoice, invoice.id)
            self.assertEqual(
                refreshed.status,
                PaymentStatus.PAID,
                "Оплаченный счёт не должен отменяться автоматически",
            )

    async def test_no_show_frees_slot(self) -> None:
        target = self._next_monday()
        slot_time = datetime.combine(target, time(14, 0), tzinfo=UTC)

        async with self.session_factory() as session:
            appt_service = AppointmentService(session)
            appt = await appt_service.create_appointment(
                AppointmentCreate(
                    clinic_id=self.clinic_id,
                    patient_id=self.patient_id,
                    doctor_id=self.doctor_id,
                    scheduled_at=slot_time,
                    duration_minutes=30,
                )
            )
            await session.commit()

            await appt_service.update_status(
                appt.id, AppointmentStatusUpdate(status=AppointmentStatus.NO_SHOW)
            )
            await session.commit()

            schedule = ScheduleService(session)
            day = await schedule.get_doctor_day_schedule(self.doctor_id, target, slot_minutes=30)
            free_at_14 = any(
                s.start == datetime.combine(target, time(14, 0)) for s in day.free_slots
            )
            self.assertTrue(free_at_14, "Неявка должна освобождать слот")


if __name__ == "__main__":
    unittest.main()
