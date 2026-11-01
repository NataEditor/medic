import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.core.exceptions import BusinessRuleError, PermissionDeniedError
from app.core.security import hash_password
from app.models.entities import Clinic, Invoice, Patient, Service, User
from app.models.enums import PaymentMethod, PaymentStatus, RoleEnum
from app.schemas.billing import PaymentCreate, PaymentRefundRequest
from app.services.admin_service import AdminService
from app.services.billing_service import BillingService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class TestSuperAdminAndCashProtection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with self.engine.begin() as conn:
            from app.db.base import Base

            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.session_factory() as session:
            clinic = Clinic(name="Клиника", code="C1", address="Адрес", phone="+70000000000")
            super_admin = User(
                email="root@medic.local",
                phone="+70000000001",
                hashed_password=hash_password("secret123"),
                role=RoleEnum.SUPER_ADMIN,
                first_name="Root",
                last_name="Admin",
            )
            head_doctor = User(
                email="head@medic.local",
                phone="+70000000002",
                hashed_password=hash_password("secret123"),
                role=RoleEnum.HEAD_DOCTOR,
                first_name="Главный",
                last_name="Врач",
            )
            cashier = User(
                email="cash@medic.local",
                phone="+70000000003",
                hashed_password=hash_password("secret123"),
                role=RoleEnum.RECEPTIONIST,
                first_name="Кассир",
                last_name="Ресепшен",
            )
            patient = Patient(
                first_name="Пациент",
                last_name="Тестов",
                passport_series="1234",
                passport_number="567890",
                birth_date=datetime(1990, 1, 1, tzinfo=UTC).date(),
                gender="M",
            )
            service = Service(code="S1", name="Приём", price=Decimal("1000.00"))
            session.add_all([clinic, super_admin, head_doctor, cashier, patient, service])
            await session.commit()

            invoice = Invoice(
                patient_id=patient.id,
                clinic_id=clinic.id,
                amount=Decimal("1000.00"),
                status=PaymentStatus.PENDING,
            )
            session.add(invoice)
            await session.commit()

            self.super_admin_id = super_admin.id
            self.head_doctor_id = head_doctor.id
            self.cashier_id = cashier.id
            self.invoice_id = invoice.id

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    # ------- Запрет самоудаления и удаления другого админа -------
    async def test_cannot_delete_self(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AdminService(session)
            with self.assertRaises(BusinessRuleError):
                await service.delete_user(actor, actor.id)

    async def test_cannot_delete_other_admin(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AdminService(session)
            with self.assertRaises(BusinessRuleError):
                await service.delete_user(actor, self.head_doctor_id)

    async def test_cannot_deactivate_self(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AdminService(session)
            with self.assertRaises(BusinessRuleError):
                await service.deactivate_user(actor, actor.id)

    async def test_can_deactivate_regular_employee(self) -> None:
        async with self.session_factory() as session:
            actor = await session.get(User, self.super_admin_id)
            service = AdminService(session)
            target = await service.deactivate_user(actor, self.cashier_id)
            self.assertFalse(target.is_active)

    # ------- Защита кассы -------
    async def test_overpayment_rejected(self) -> None:
        """Нельзя принять оплату больше суммы счёта (подлог сумм)."""
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = BillingService(session)
            dto = PaymentCreate(
                invoice_id=self.invoice_id,
                amount=Decimal("1500.00"),
                method=PaymentMethod.CASH,
            )
            with self.assertRaises(BusinessRuleError):
                await service.process_payment(cashier, dto)

    async def test_refund_creates_compensating_entry_and_keeps_original(self) -> None:
        """Возврат не удаляет исходный платёж, а создаёт компенсацию и аудит."""
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = BillingService(session)
            payment = await service.process_payment(
                cashier,
                PaymentCreate(
                    invoice_id=self.invoice_id,
                    amount=Decimal("1000.00"),
                    method=PaymentMethod.CARD,
                ),
            )
            await session.commit()

            actor = await session.get(User, self.super_admin_id)
            refund = await service.refund_payment(
                actor, payment.id, PaymentRefundRequest(reason="Пациент отказался от услуги")
            )
            await session.commit()

            # Исходный платёж не тронут
            original = await session.get(type(payment), payment.id)
            self.assertEqual(original.amount, Decimal("1000.00"))

            # Создана компенсирующая проводка с отрицательной суммой
            self.assertEqual(refund.amount, Decimal("-1000.00"))

            invoice = await session.get(Invoice, self.invoice_id)
            self.assertEqual(invoice.status, PaymentStatus.REFUNDED)

    async def test_refund_forbidden_for_receptionist(self) -> None:
        """Кассир/ресепшен не может оформлять возврат — только руководитель/бухгалтерия."""
        async with self.session_factory() as session:
            cashier = await session.get(User, self.cashier_id)
            service = BillingService(session)
            payment = await service.process_payment(
                cashier,
                PaymentCreate(
                    invoice_id=self.invoice_id,
                    amount=Decimal("1000.00"),
                    method=PaymentMethod.CASH,
                ),
            )
            await session.commit()

            with self.assertRaises(PermissionDeniedError):
                await service.refund_payment(
                    cashier, payment.id, PaymentRefundRequest(reason="Попытка злоупотребления")
                )


if __name__ == "__main__":
    unittest.main()
