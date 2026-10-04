from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models.entities import Invoice, Payment, Service, User
from app.models.enums import PaymentStatus, RoleEnum
from app.repositories.base import BaseRepository
from app.repositories.billing_repo import BillingRepository
from app.schemas.billing import (
    ClinicFinancialReport,
    PaymentCreate,
    ServiceCreate,
    ServiceUpdate,
)


class BillingService:
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
        """Ресепшен принимает оплату (нал, карта, СБП)."""
        invoice = await self.session.get(Invoice, data.invoice_id)
        if not invoice:
            raise NotFoundError("Счёт не найден")
        if invoice.status == PaymentStatus.PAID:
            raise BusinessRuleError("Счёт уже полностью оплачен")

        payment = Payment(
            invoice_id=data.invoice_id,
            amount=data.amount,
            method=data.method,
            cashier_id=cashier.id,
        )
        return await self.billing_repo.add_payment(payment)

    async def generate_financial_report(
        self,
        accountant: User,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> ClinicFinancialReport:
        """Генерация финансового отчёта для бухгалтерии и главврача."""
        if accountant.role not in (RoleEnum.ACCOUNTANT, RoleEnum.HEAD_DOCTOR):
            from app.core.exceptions import PermissionDeniedError

            raise PermissionDeniedError("Доступ к финотчётам имеет только бухгалтерия/главврач")
        # Если не главврач — бухгалтер видит только свою клинику
        target_clinic = clinic_id
        if accountant.role == RoleEnum.ACCOUNTANT:
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
