from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ACTION_REFUND_PAYMENT, CASH_AUDIT_ENTITY
from app.core.exceptions import (
    BusinessRuleError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.logging import logger
from app.models.entities import Invoice, Payment, Service, User
from app.models.enums import PaymentStatus, RoleEnum
from app.repositories.base import BaseRepository
from app.repositories.billing_repo import BillingRepository
from app.schemas.billing import (
    ClinicFinancialReport,
    PaymentCreate,
    PaymentRefundRequest,
    ServiceCreate,
    ServiceUpdate,
)


class BillingService:
    """Финансовые операции клиники: прайс, касса и отчёты.

    Инварианты защиты кассы (действуют для ВСЕХ ролей, включая супер-админа):
    - Нельзя удалять или изменять уже проведённые платежи и оплаченные счета.
    - Нельзя принять оплату сверх суммы счёта (исключает подлог сумм).
    - Возврат оформляется отдельной операцией строго через refund_payment с причиной,
      которая фиксируется в журнале аудита.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.billing_repo = BillingRepository(session)
        self.service_repo = BaseRepository(Service, session)

    async def create_service(self, data: ServiceCreate) -> Service:
        service = Service(**data.model_dump())
        return await self.service_repo.create(service)

    async def update_service(self, service_id: int, data: ServiceUpdate) -> Service:
        service = await self.service_repo.get_by_id(service_id)
        if not service:
            raise NotFoundError("Услуга не найдена")

        for key, val in data.model_dump(exclude_unset=True).items():
            setattr(service, key, val)

        await self.session.flush()
        return service

    async def process_payment(self, cashier: User, data: PaymentCreate) -> Payment:
        """Приём оплаты по счёту (ресепшен/касса)."""
        invoice = await self.session.get(Invoice, data.invoice_id)
        if not invoice:
            raise NotFoundError("Счёт не найден")
        if invoice.status == PaymentStatus.PAID:
            raise BusinessRuleError("Счёт уже полностью оплачен")
        if invoice.status == PaymentStatus.REFUNDED:
            raise BusinessRuleError("По счёту оформлен возврат: новые оплаты запрещены")

        # Защита кассы: сумма оплат не может превысить сумму счёта.
        already_paid = await self.billing_repo.get_total_paid(invoice.id)
        if already_paid + data.amount > invoice.amount:
            remainder = invoice.amount - already_paid
            raise BusinessRuleError(f"Сумма превышает остаток по счёту. К оплате: {remainder}")

        payment = Payment(
            invoice_id=data.invoice_id,
            amount=data.amount,
            method=data.method,
            cashier_id=cashier.id,
        )
        created = await self.billing_repo.add_payment(payment)
        logger.info(
            "Принята оплата: счёт={} сумма={} способ={} кассир={}",
            data.invoice_id,
            data.amount,
            data.method.value,
            cashier.id,
        )
        return created

    async def refund_payment(
        self,
        actor: User,
        payment_id: int,
        data: PaymentRefundRequest,
    ) -> Payment:
        """Контролируемый возврат средств по платежу.

        Сама проводка не удаляется и не изменяется — вместо этого создаётся
        компенсирующая запись возврата, а счёт помечается статусом REFUNDED.
        Каждое действие фиксируется в журнале аудита.
        """
        if not actor.has_any_role(RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR, RoleEnum.ACCOUNTANT):
            raise PermissionDeniedError("Оформлять возврат может руководитель или бухгалтерия")

        payment = await self.session.get(Payment, payment_id)
        if not payment:
            raise NotFoundError("Платёж не найден")

        invoice = await self.session.get(Invoice, payment.invoice_id)
        if not invoice:
            raise NotFoundError("Счёт не найден")
        if invoice.status == PaymentStatus.REFUNDED:
            raise BusinessRuleError("Возврат по этому счёту уже оформлен")

        # Создаём компенсирующую проводку: исходный платёж остаётся нетронутым.
        refund = Payment(
            invoice_id=invoice.id,
            amount=-payment.amount,
            method=payment.method,
            cashier_id=actor.id,
        )
        self.session.add(refund)

        invoice.status = PaymentStatus.REFUNDED
        await self.session.flush()

        # Фиксируем факт возврата в журнале аудита (кто, что, почему)
        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=actor,
            action=ACTION_REFUND_PAYMENT,
            entity_name=CASH_AUDIT_ENTITY,
            entity_id=payment.id,
            changes=f'{{"reason": "{data.reason}", "amount": "{payment.amount}"}}',
        )

        await self.session.refresh(refund)
        return refund

    async def generate_financial_report(
        self,
        accountant: User,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> ClinicFinancialReport:
        """Генерация финансового отчёта для бухгалтерии, главврача и SA.

        Проверка ролей здесь дублирует require_roles в роутере, но нужна:
        сервис вызывается и из других мест. SUPER_ADMIN обязателен —
        иначе владелец платформы получает 403 на собственном отчёте.
        """
        if not accountant.has_any_role(
            RoleEnum.ACCOUNTANT,
            RoleEnum.HEAD_DOCTOR,
            RoleEnum.SUPER_ADMIN,
        ):
            from app.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError(
                "Доступ к финотчётам имеет только бухгалтерия, главврач и SA"
            )

        # Бухгалтерия ограничена своей клиникой; главврач и SA видят сеть
        target_clinic = clinic_id
        if accountant.has_role(RoleEnum.ACCOUNTANT) and not accountant.has_role(
            RoleEnum.SUPER_ADMIN
        ):
            target_clinic = accountant.clinic_id
        summary = await self.billing_repo.get_financial_summary(
            start_date=start_date,
            end_date=end_date,
            clinic_id=target_clinic,
        )
        doctors = await self.billing_repo.get_doctors_revenue(
            start_date=start_date,
            end_date=end_date,
            clinic_id=target_clinic,
        )

        return ClinicFinancialReport(
            clinic_id=target_clinic,
            start_date=start_date,
            end_date=end_date,
            total_appointments=int(summary["total_appointments"]),
            total_procedures=int(summary["total_procedures"]),
            total_revenue=Decimal(str(summary["total_revenue"])),
            paid_revenue=Decimal(str(summary["paid_revenue"])),
            doctors=doctors,
        )
