from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.logging import logger
from app.core.utils import utcnow
from app.models.entities import Invoice, Payment, RefundRequest, User
from app.models.enums import PaymentStatus, RefundRequestStatus, RoleEnum
from app.schemas.billing import (
    RefundRequestCreate,
    RefundRequestReview,
    ReversalCreate,
)


class RefundService:
    """Двойной контроль денег: заявка на возврат → подтверждение бухгалтерией.

    Инварианты:
    - Никто в одиночку не проводит полный цикл с деньгами.
    - Ресепшен/касса создаёт заявку, бухгалтерия подтверждает.
    - Подтверждение создаёт сторно-проводку; оригинал не меняется.
    - Прямая правка/удаление проводок запрещена всем, включая супер-админа.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----- Заявка на возврат (ресепшен/касса) -----
    async def create_refund_request(
        self,
        actor: User,
        data: RefundRequestCreate,
    ) -> RefundRequest:
        """Создаёт заявку на возврат. Деньги пока не двигаются."""
        if not actor.has_any_role(
            RoleEnum.RECEPTIONIST,
            RoleEnum.CLINIC_ADMIN,
            RoleEnum.ACCOUNTANT,
            RoleEnum.SUPER_ADMIN,
        ):
            raise PermissionDeniedError("Заявку на возврат создаёт касса или бухгалтерия")

        payment = await self.session.get(Payment, data.payment_id)
        if payment is None:
            raise NotFoundError("Платёж не найден")
        if payment.amount <= 0:
            raise BusinessRuleError("Нельзя вернуть средства по сторно-проводке")
        if payment.reversal_of_id is not None:
            raise BusinessRuleError("Это уже сторно-проводка")

        invoice = await self.session.get(Invoice, payment.invoice_id)
        if invoice is None:
            raise NotFoundError("Счёт не найден")

        # Нельзя вернуть больше, чем оплачено по этому платежу
        if data.amount > payment.amount:
            raise BusinessRuleError(f"Сумма возврата превышает платёж ({payment.amount})")

        # Не должно быть второй активной заявки по этому платежу
        existing = await self._get_pending_for_payment(payment.id)
        if existing is not None:
            raise BusinessRuleError(
                f"По этому платежу уже есть заявка на рассмотрении (id={existing.id})"
            )

        request = RefundRequest(
            payment_id=payment.id,
            invoice_id=invoice.id,
            requested_by_id=actor.id,
            amount=data.amount,
            reason=data.reason,
            status=RefundRequestStatus.PENDING,
        )
        self.session.add(request)
        await self.session.flush()
        await self.session.refresh(request)
        logger.info(
            "Создана заявка на возврат id={}: платёж={} сумма={} автор={}",
            request.id,
            payment.id,
            data.amount,
            actor.id,
        )
        return request

    # ----- Рассмотрение заявки (бухгалтерия) -----
    async def review_refund_request(
        self,
        reviewer: User,
        request_id: int,
        data: RefundRequestReview,
    ) -> RefundRequest:
        """Бухгалтерия подтверждает или отклоняет заявку.

        При подтверждении создаётся сторно-проводка (компенсация),
        оригинальный платёж остаётся неизменным.
        """
        if not reviewer.has_any_role(RoleEnum.ACCOUNTANT, RoleEnum.SUPER_ADMIN):
            raise PermissionDeniedError("Подтверждать возврат может только бухгалтерия")

        request = await self.session.get(RefundRequest, request_id)
        if request is None:
            raise NotFoundError("Заявка не найдена")
        if request.status != RefundRequestStatus.PENDING:
            raise BusinessRuleError(f"Заявка уже рассмотрена (статус: {request.status.value})")

        # Инициатор не может сам подтвердить свою заявку (двойной контроль)
        if request.requested_by_id == reviewer.id:
            raise PermissionDeniedError(
                "Нельзя подтвердить собственную заявку — нужен второй сотрудник"
            )

        request.reviewed_by_id = reviewer.id
        request.reviewed_at = utcnow()
        request.review_comment = data.comment

        if data.approve:
            request.status = RefundRequestStatus.APPROVED
            await self._create_reversal(request, reviewer)
        else:
            request.status = RefundRequestStatus.REJECTED

        await self.session.flush()
        await self.session.refresh(request)
        logger.info(
            "Заявка на возврат id={} рассмотрена: статус={} бухгалтер={}",
            request.id,
            request.status.value,
            reviewer.id,
        )
        return request

    async def cancel_refund_request(self, actor: User, request_id: int) -> RefundRequest:
        """Инициатор отзывает заявку до рассмотрения."""
        request = await self.session.get(RefundRequest, request_id)
        if request is None:
            raise NotFoundError("Заявка не найдена")
        if request.requested_by_id != actor.id and not actor.has_role(RoleEnum.SUPER_ADMIN):
            raise PermissionDeniedError("Отозвать заявку может только её автор")
        if request.status != RefundRequestStatus.PENDING:
            raise BusinessRuleError("Отозвать можно только заявку на рассмотрении")

        request.status = RefundRequestStatus.CANCELLED
        await self.session.flush()
        await self.session.refresh(request)
        return request

    # ----- Сторно-проводка -----
    async def create_reversal(
        self,
        actor: User,
        payment_id: int,
        data: ReversalCreate,
    ) -> Payment:
        """Прямое сторно бухгалтерией (без заявки) — для исправления ошибок.

        Оригинал сохраняется, создаётся компенсирующая проводка.
        """
        if not actor.has_any_role(RoleEnum.ACCOUNTANT, RoleEnum.SUPER_ADMIN):
            raise PermissionDeniedError("Сторно проводит только бухгалтерия")

        payment = await self.session.get(Payment, payment_id)
        if payment is None:
            raise NotFoundError("Платёж не найден")
        if payment.reversal_of_id is not None:
            raise BusinessRuleError("Нельзя сторнировать сторно-проводку")
        if payment.amount <= 0:
            raise BusinessRuleError("Платёж уже является компенсацией")

        existing = await self._get_reversal_for(payment.id)
        if existing is not None:
            raise BusinessRuleError(f"Сторно по этому платежу уже создано (id={existing.id})")

        return await self._make_reversal(payment, actor, data.reason)

    # ----- Внутренние -----
    async def _create_reversal(self, request: RefundRequest, reviewer: User) -> Payment:
        payment = await self.session.get(Payment, request.payment_id)
        if payment is None:
            raise NotFoundError("Платёж не найден")
        return await self._make_reversal(payment, reviewer, request.reason)

    async def _make_reversal(
        self,
        payment: Payment,
        actor: User,
        reason: str,
    ) -> Payment:
        """Создаёт компенсирующую проводку. Оригинал не меняется."""
        reversal = Payment(
            invoice_id=payment.invoice_id,
            amount=-payment.amount,
            method=payment.method,
            cashier_id=actor.id,
            reversal_of_id=payment.id,
            reversal_reason=reason,
        )
        self.session.add(reversal)

        # Пересчитываем статус счёта по сумме всех проводок
        invoice = await self.session.get(Invoice, payment.invoice_id)
        if invoice is not None:
            total = await self._total_paid(invoice.id)
            if total <= 0:
                invoice.status = PaymentStatus.REFUNDED
            elif total < invoice.amount:
                invoice.status = PaymentStatus.PENDING
            else:
                invoice.status = PaymentStatus.PAID

        await self.session.flush()
        await self.session.refresh(reversal)

        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=actor,
            action="reversal_payment",
            entity_name="payment",
            entity_id=payment.id,
            changes=f'{{"reason": "{reason}", "amount": "{payment.amount}"}}',
        )
        logger.info(
            "Создана сторно-проводка id={}: оригинал={} сумма={} автор={} причина='{}'",
            reversal.id,
            payment.id,
            -payment.amount,
            actor.id,
            reason,
        )
        return reversal

    async def _total_paid(self, invoice_id: int) -> Decimal:
        from sqlalchemy import func

        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice_id
        )
        return Decimal(str((await self.session.execute(stmt)).scalar_one()))

    async def _get_pending_for_payment(self, payment_id: int) -> RefundRequest | None:
        stmt = select(RefundRequest).where(
            RefundRequest.payment_id == payment_id,
            RefundRequest.status == RefundRequestStatus.PENDING,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_reversal_for(self, payment_id: int) -> Payment | None:
        stmt = select(Payment).where(Payment.reversal_of_id == payment_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_refund_requests(
        self,
        status: RefundRequestStatus | None = None,
    ) -> list[RefundRequest]:
        stmt = select(RefundRequest).order_by(RefundRequest.created_at.desc())
        if status is not None:
            stmt = stmt.where(RefundRequest.status == status)
        return list((await self.session.execute(stmt)).scalars().all())
