"""Тесты Этапа 3: двойной контроль возвратов, сторно, оплата процедур.

Проверяем:
- заявку на возврат создаёт касса, подтверждает бухгалтерия (двойной контроль);
- инициатор не может подтвердить свою заявку;
- подтверждение создаёт сторно-проводку, оригинал не меняется;
- процедура выполняется только по оплаченному счёту (флаг клиники).
"""

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal

from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.db.base import Base
from app.models.entities import (
    Assignment,
    Clinic,
    Invoice,
    Patient,
    Payment,
    Service,
    User,
)
from app.models.enums import (
    AssignmentType,
    PaymentMethod,
    PaymentStatus,
    RefundRequestStatus,
)
from app.schemas.assignments import ProcedureExecutionCreate
from app.schemas.billing import (
    RefundRequestCreate,
    RefundRequestReview,
    ReversalCreate,
)
from app.services.assignment_service import AssignmentService
from app.services.refund_service import RefundService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestRefundDoubleControl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="К", code="C", address="А", phone="+70000000000")
            cashier = User(
                clinic_id=1,
                email="cash@medic.local",
                phone="+70000000001",
                hashed_password="x",
                roles=["receptionist"],
                first_name="Кассир",
                last_name="Ресепшен",
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
                passport_series="1111",
                passport_number="222222",
                birth_date=date(1990, 1, 1),
                gender="M",
            )
            session.add_all([clinic, cashier, accountant, patient])
            await session.commit()

            invoice = Invoice(
                patient_id=patient.id,
                clinic_id=clinic.id,
                amount=Decimal("1000.00"),
                status=PaymentStatus.PENDING,
            )
            session.add(invoice)
            await session.commit()

            payment = Payment(
                invoice_id=invoice.id,
                amount=Decimal("1000.00"),
                method=PaymentMethod.CASH,
                cashier_id=cashier.id,
            )
            session.add(payment)
            invoice.status = PaymentStatus.PAID
            await session.commit()

            self.cashier_id = cashier.id
            self.accountant_id = accountant.id
            self.invoice_id = invoice.id
            self.payment_id = payment.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_cashier_creates_request_accountant_approves(self) -> None:
        """Полный цикл: заявка кассы → подтверждение бухгалтерии → сторно."""
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            accountant = await session.get(User, self.accountant_id)
            service = RefundService(session)

            request = await service.create_refund_request(
                cashier,
                RefundRequestCreate(
                    payment_id=self.payment_id,
                    amount=Decimal("1000.00"),
                    reason="Пациент отказался от услуги",
                ),
            )
            await session.commit()
            self.assertEqual(request.status, RefundRequestStatus.PENDING)

            reviewed = await service.review_refund_request(
                accountant,
                request.id,
                RefundRequestReview(approve=True, comment="Проверено"),
            )
            await session.commit()

            self.assertEqual(reviewed.status, RefundRequestStatus.APPROVED)
            self.assertEqual(reviewed.reviewed_by_id, accountant.id)

            # Создана сторно-проводка
            reversal = (
                await session.execute(
                    select(Payment).where(Payment.reversal_of_id == self.payment_id)
                )
            ).scalar_one()
            self.assertEqual(reversal.amount, Decimal("-1000.00"))
            self.assertEqual(reversal.reversal_reason, "Пациент отказался от услуги")

            # Оригинал не изменён
            original = await session.get(Payment, self.payment_id)
            self.assertEqual(original.amount, Decimal("1000.00"))
            self.assertIsNone(original.reversal_of_id)

    async def test_initiator_cannot_approve_own_request(self) -> None:
        """Двойной контроль: нельзя подтвердить собственную заявку."""
        async with self.session_factory() as session:
            # Бухгалтер сам создал заявку
            accountant = await session.get(User, self.accountant_id)
            service = RefundService(session)
            request = await service.create_refund_request(
                accountant,
                RefundRequestCreate(
                    payment_id=self.payment_id,
                    amount=Decimal("500.00"),
                    reason="Ошибка кассира",
                ),
            )
            await session.commit()

            with self.assertRaises(PermissionDeniedError):
                await service.review_refund_request(
                    accountant,
                    request.id,
                    RefundRequestReview(approve=True),
                )

    async def test_reject_request_creates_no_reversal(self) -> None:
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            accountant = await session.get(User, self.accountant_id)
            service = RefundService(session)

            request = await service.create_refund_request(
                cashier,
                RefundRequestCreate(
                    payment_id=self.payment_id,
                    amount=Decimal("1000.00"),
                    reason="Сомнительный возврат",
                ),
            )
            await session.commit()

            reviewed = await service.review_refund_request(
                accountant,
                request.id,
                RefundRequestReview(approve=False, comment="Отклонено"),
            )
            await session.commit()

            self.assertEqual(reviewed.status, RefundRequestStatus.REJECTED)
            reversal = (
                await session.execute(
                    select(Payment).where(Payment.reversal_of_id == self.payment_id)
                )
            ).scalar_one_or_none()
            self.assertIsNone(reversal, "При отказе сторно не создаётся")

    async def test_cannot_refund_more_than_paid(self) -> None:
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = RefundService(session)
            with self.assertRaises(BusinessRuleError):
                await service.create_refund_request(
                    cashier,
                    RefundRequestCreate(
                        payment_id=self.payment_id,
                        amount=Decimal("5000.00"),
                        reason="Слишком много",
                    ),
                )

    async def test_duplicate_pending_request_rejected(self) -> None:
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = RefundService(session)
            await service.create_refund_request(
                cashier,
                RefundRequestCreate(
                    payment_id=self.payment_id,
                    amount=Decimal("100.00"),
                    reason="Первая заявка",
                ),
            )
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.create_refund_request(
                    cashier,
                    RefundRequestCreate(
                        payment_id=self.payment_id,
                        amount=Decimal("200.00"),
                        reason="Вторая заявка",
                    ),
                )

    async def test_direct_reversal_by_accountant(self) -> None:
        """Сторно напрямую бухгалтерией (исправление ошибки)."""
        async with self.session_factory() as session:
            accountant = await session.get(User, self.accountant_id)
            service = RefundService(session)
            reversal = await service.create_reversal(
                accountant,
                self.payment_id,
                ReversalCreate(reason="Ошибочно пробит чек"),
            )
            await session.commit()

            self.assertEqual(reversal.amount, Decimal("-1000.00"))
            self.assertEqual(reversal.reversal_of_id, self.payment_id)

            invoice = await session.get(Invoice, self.invoice_id)
            self.assertEqual(invoice.status, PaymentStatus.REFUNDED)

    async def test_cannot_reverse_twice(self) -> None:
        async with self.session_factory() as session:
            accountant = await session.get(User, self.accountant_id)
            service = RefundService(session)
            await service.create_reversal(
                accountant, self.payment_id, ReversalCreate(reason="Первое сторно")
            )
            await session.commit()

            with self.assertRaises(BusinessRuleError):
                await service.create_reversal(
                    accountant, self.payment_id, ReversalCreate(reason="Второе сторно")
                )

    async def test_cashier_cannot_do_direct_reversal(self) -> None:
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = RefundService(session)
            with self.assertRaises(PermissionDeniedError):
                await service.create_reversal(
                    cashier, self.payment_id, ReversalCreate(reason="Попытка")
                )


class TestProcedureRequiresPayment(unittest.IsolatedAsyncioTestCase):
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
            nurse = User(
                clinic_id=1,
                email="nurse@medic.local",
                phone="+70000000002",
                hashed_password="x",
                roles=["nurse"],
                first_name="Мед",
                last_name="Сестра",
            )
            session.add_all([clinic, doctor, nurse])
            await session.commit()

            from app.schemas.patients import PatientCreate
            from app.services.patient_service import PatientService

            patient = await PatientService(session).create_patient_with_record(
                PatientCreate(
                    first_name="Пац",
                    last_name="Пациентов",
                    passport_series="3333",
                    passport_number="444444",
                    birth_date=date(1990, 1, 1),
                    gender="M",
                )
            )
            await session.commit()

            from app.models.entities import MedicalRecord

            record = (
                await session.execute(
                    select(MedicalRecord).where(MedicalRecord.patient_id == patient.id)
                )
            ).scalar_one()

            service = Service(
                code="PROC-MASSAGE",
                name="Массаж",
                price=Decimal("1500.00"),
                duration_minutes=40,
                is_procedure=True,
            )
            session.add(service)
            await session.commit()

            assignment = Assignment(
                record_id=record.id,
                clinic_id=clinic.id,
                doctor_id=doctor.id,
                service_id=service.id,
                type=AssignmentType.PROCEDURE,
                title="Массаж",
                procedure_type="massage",
            )
            session.add(assignment)
            await session.commit()

            self.doctor_id = doctor.id
            self.nurse_id = nurse.id
            self.patient_id = patient.id
            self.assignment_id = assignment.id
            self.clinic_id = clinic.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_procedure_blocked_without_payment(self) -> None:
        """Процедура не выполняется, пока счёт не оплачен."""
        async with self.session_factory() as session:
            nurse = await session.get(User, self.nurse_id)
            service = AssignmentService(session)
            with self.assertRaises(BusinessRuleError):
                await service.record_execution(
                    nurse,
                    ProcedureExecutionCreate(
                        assignment_id=self.assignment_id,
                        executed_at=datetime.now(UTC),
                    ),
                )

    async def test_procedure_allowed_after_payment(self) -> None:
        async with self.session_factory() as session:
            # Создаём оплаченный счёт по назначению
            invoice = Invoice(
                assignment_id=self.assignment_id,
                patient_id=self.patient_id,
                clinic_id=self.clinic_id,
                amount=Decimal("1500.00"),
                status=PaymentStatus.PAID,
            )
            session.add(invoice)
            await session.commit()

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

            self.assertIsNotNone(execution.id)
            self.assertEqual(execution.invoice_id, invoice.id)
            self.assertEqual(execution.performed_by_id, nurse.id)
            self.assertIsNotNone(execution.performed_at)


if __name__ == "__main__":
    unittest.main()
