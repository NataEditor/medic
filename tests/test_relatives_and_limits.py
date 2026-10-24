"""Тесты Этапа 4: родственники, лимит 24 ч, статус оплаты для врача.

Проверяем:
- пациент заявляет связь (PENDING): может записывать, но не видеть ЭМК;
- ресепшен подтверждает (CONFIRMED) → открывается просмотр медданных;
- «родитель — несовершеннолетний» подтверждается автоматически;
- пациент не может перенести приём менее чем за 24 часа;
- врач видит статус оплаты своего приёма.
"""

import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.exceptions import BusinessRuleError, ConflictError, PermissionDeniedError
from app.db.base import Base
from app.models.entities import Appointment, Clinic, Invoice, Patient, User
from app.models.enums import (
    AppointmentStatus,
    PatientRelativeStatus,
    PaymentStatus,
)
from app.schemas.patients import PatientCreate
from app.schemas.relatives import PatientRelativeConfirm, PatientRelativeCreate
from app.services.appointment_service import AppointmentService
from app.services.patient_service import PatientService
from app.services.relative_service import RelativeService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestPatientRelatives(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C", address="А", phone="+70000000000")
            session.add(clinic)
            await session.commit()

            patient_service = PatientService(session)

            # Взрослый пациент (владелец ЛК)
            adult = await patient_service.create_patient_with_record(
                PatientCreate(
                    first_name="Взрослый",
                    last_name="Пациент",
                    passport_series="1111",
                    passport_number="111111",
                    birth_date=date(1985, 1, 1),
                    gender="M",
                )
            )
            # Второй взрослый (например, супруг/родитель)
            other_adult = await patient_service.create_patient_with_record(
                PatientCreate(
                    first_name="Второй",
                    last_name="Взрослый",
                    passport_series="2222",
                    passport_number="222222",
                    birth_date=date(1980, 1, 1),
                    gender="F",
                )
            )
            # Несовершеннолетний ребёнок
            child = await patient_service.create_patient_with_record(
                PatientCreate(
                    first_name="Ребёнок",
                    last_name="Малыш",
                    passport_series="3333",
                    passport_number="333333",
                    birth_date=date.today().replace(year=date.today().year - 8),
                    gender="M",
                )
            )
            await session.commit()

            # Учётная запись ЛК для взрослого
            user = User(
                clinic_id=clinic.id,
                email="adult@medic.local",
                phone="+70000000001",
                hashed_password="x",
                roles=["patient"],
                first_name="Взрослый",
                last_name="Пациент",
            )
            session.add(user)
            await session.commit()
            adult.user_id = user.id
            await session.commit()

            self.adult_id = adult.id
            self.other_adult_id = other_adult.id
            self.child_id = child.id
            self.user_id = user.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _load_user(self, session: AsyncSession) -> User:
        """Загружает пользователя вместе с patient_profile (иначе lazy-load в async)."""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        stmt = (
            select(User)
            .options(selectinload(User.patient_profile))
            .where(User.id == self.user_id)
        )
        return (await session.execute(stmt)).scalar_one()

    async def test_pending_relative_can_book_but_not_view_medical(self) -> None:
        """PENDING: запись разрешена, просмотр ЭМК — нет."""
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)

            link = await service.request_relative(
                user,
                PatientRelativeCreate(relative_id=self.other_adult_id, relation="spouse"),
            )
            await session.commit()

            self.assertEqual(link.status, PatientRelativeStatus.PENDING)
            self.assertTrue(await service.can_book_for(user, self.other_adult_id))
            self.assertFalse(await service.can_view_medical_data(user, self.other_adult_id))

    async def test_confirmed_relative_can_view_medical(self) -> None:
        """CONFIRMED: открывается просмотр медданных близкого."""
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)

            link = await service.request_relative(
                user,
                PatientRelativeCreate(relative_id=self.other_adult_id, relation="spouse"),
            )
            await session.commit()

            # Ресепшен подтверждает
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

            confirmed = await service.confirm_relative(
                receptionist,
                link.id,
                PatientRelativeConfirm(approve=True, notes="Паспорт предъявлен"),
            )
            await session.commit()

            self.assertEqual(confirmed.status, PatientRelativeStatus.CONFIRMED)
            self.assertTrue(await service.can_view_medical_data(user, self.other_adult_id))

    async def test_minor_child_auto_confirmed(self) -> None:
        """Родитель — несовершеннолетний ребёнок: подтверждается сразу."""
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)

            link = await service.request_relative(
                user,
                PatientRelativeCreate(relative_id=self.child_id, relation="child"),
            )
            await session.commit()

            self.assertEqual(link.status, PatientRelativeStatus.CONFIRMED)
            self.assertIsNotNone(link.confirmed_at)
            self.assertTrue(await service.can_view_medical_data(user, self.child_id))

    async def test_cannot_link_to_self(self) -> None:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)
            with self.assertRaises(BusinessRuleError):
                await service.request_relative(
                    user,
                    PatientRelativeCreate(relative_id=self.adult_id, relation="other"),
                )

    async def test_duplicate_link_rejected(self) -> None:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)
            await service.request_relative(
                user,
                PatientRelativeCreate(relative_id=self.other_adult_id, relation="spouse"),
            )
            await session.commit()

            with self.assertRaises(ConflictError):
                await service.request_relative(
                    user,
                    PatientRelativeCreate(relative_id=self.other_adult_id, relation="parent"),
                )

    async def test_only_reception_can_confirm(self) -> None:
        async with self.session_factory() as session:
            user = await self._load_user(session)
            service = RelativeService(session)
            link = await service.request_relative(
                user,
                PatientRelativeCreate(relative_id=self.other_adult_id, relation="spouse"),
            )
            await session.commit()

            # Пациент не может подтвердить сам
            with self.assertRaises(PermissionDeniedError):
                await service.confirm_relative(
                    user, link.id, PatientRelativeConfirm(approve=True)
                )


class TestPatientCancelLimit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _appt(self, hours_ahead: int) -> Appointment:
        return Appointment(
            clinic_id=1,
            patient_id=1,
            doctor_id=1,
            scheduled_at=datetime.now(UTC) + timedelta(hours=hours_ahead),
            duration_minutes=30,
            status=AppointmentStatus.SCHEDULED,
        )

    async def test_cannot_cancel_within_24_hours(self) -> None:
        appt = self._appt(hours_ahead=5)
        with self.assertRaises(BusinessRuleError):
            AppointmentService.ensure_patient_time_limit(appt)

    async def test_can_cancel_more_than_24_hours_ahead(self) -> None:
        appt = self._appt(hours_ahead=48)
        # Не должно бросить исключение
        AppointmentService.ensure_patient_time_limit(appt)


class TestDoctorSeesPaymentStatus(unittest.IsolatedAsyncioTestCase):
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
                passport_series="4444",
                passport_number="444444",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, patient])
            await session.commit()

            appt = Appointment(
                clinic_id=clinic.id,
                patient_id=patient.id,
                doctor_id=1,
                scheduled_at=datetime.now(UTC) + timedelta(days=2),
                duration_minutes=30,
                status=AppointmentStatus.SCHEDULED,
            )
            session.add(appt)
            await session.commit()

            invoice = Invoice(
                appointment_id=appt.id,
                patient_id=patient.id,
                clinic_id=clinic.id,
                amount=Decimal("2500.00"),
                status=PaymentStatus.PENDING,
            )
            session.add(invoice)
            await session.commit()

            self.appt_id = appt.id
            self.invoice_id = invoice.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_invoice_linked_to_appointment(self) -> None:
        """Счёт приёма доступен для отображения статуса оплаты врачу."""
        from app.repositories.billing_repo import BillingRepository

        async with self.session_factory() as session:
            repo = BillingRepository(session)
            invoice = await repo.get_invoice_by_appointment(self.appt_id)
            self.assertIsNotNone(invoice)
            self.assertEqual(invoice.status, PaymentStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
