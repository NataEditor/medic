from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT,
    ONE_HUNDRED_PERCENT,
)
from app.models.entities import (
    Appointment,
    Invoice,
    Payment,
    ProcedureExecution,
    Service,
    User,
)
from app.models.enums import AppointmentStatus, PaymentStatus, RoleEnum
from app.repositories.base import BaseRepository
from app.schemas.billing import DoctorRevenueItem


class BillingRepository(BaseRepository[Invoice]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Invoice, session)

    async def get_invoice_detailed(self, invoice_id: int) -> Invoice | None:
        stmt = (
            select(Invoice)
            .options(
                selectinload(Invoice.payments).selectinload(Payment.cashier),
                selectinload(Invoice.appointment),
            )
            .where(Invoice.id == invoice_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_total_paid(self, invoice_id: int) -> Decimal:
        """Сумма всех фактических оплат по счёту (учитывая компенсации возврата)."""
        stmt = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.invoice_id == invoice_id
        )
        return Decimal(str((await self.session.execute(stmt)).scalar_one()))

    async def add_payment(self, payment: Payment) -> Payment:
        self.session.add(payment)
        await self.session.flush()

        invoice = await self.session.get(Invoice, payment.invoice_id)
        if invoice is None:
            await self.session.refresh(payment)
            return payment

        # Возврат (отрицательный платёж) не может «оплатить» счёт заново.
        total_paid: Decimal = await self.get_total_paid(payment.invoice_id)

        if total_paid >= invoice.amount and invoice.amount > 0:
            invoice.status = PaymentStatus.PAID
        elif invoice.status == PaymentStatus.PAID and total_paid < invoice.amount:
            # Оплата была отменена через возврат — счёт вновь ожидает оплаты.
            invoice.status = PaymentStatus.PENDING

        await self.session.flush()
        await self.session.refresh(payment)
        return payment

    async def get_financial_summary(
        self,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> dict[str, Decimal | int]:
        """Сводка по доходам для бухгалтерии."""
        dt_start = datetime.combine(start_date, time.min)
        dt_end = datetime.combine(end_date, time.max)

        # Всего завершённых приёмов
        appt_stmt = select(func.count(Appointment.id)).where(
            Appointment.status == AppointmentStatus.COMPLETED,
            Appointment.scheduled_at >= dt_start,
            Appointment.scheduled_at <= dt_end,
        )
        if clinic_id:
            appt_stmt = appt_stmt.where(Appointment.clinic_id == clinic_id)
        total_appointments = int((await self.session.execute(appt_stmt)).scalar_one())

        # Выполненных процедур
        proc_stmt = select(func.count(ProcedureExecution.id)).where(
            ProcedureExecution.executed_at >= dt_start,
            ProcedureExecution.executed_at <= dt_end,
        )
        total_procedures = int((await self.session.execute(proc_stmt)).scalar_one())

        # Выставлено счетов
        inv_stmt = select(func.coalesce(func.sum(Invoice.amount), 0)).where(
            Invoice.created_at >= dt_start,
            Invoice.created_at <= dt_end,
        )
        if clinic_id:
            inv_stmt = inv_stmt.where(Invoice.clinic_id == clinic_id)
        total_revenue = Decimal(str((await self.session.execute(inv_stmt)).scalar_one()))

        # Оплачено фактически (с учётом компенсаций возврата: отрицательные платежи вычитаются)
        pay_stmt = (
            select(func.coalesce(func.sum(Payment.amount), 0))
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .where(
                Payment.created_at >= dt_start,
                Payment.created_at <= dt_end,
            )
        )
        if clinic_id:
            pay_stmt = pay_stmt.where(Invoice.clinic_id == clinic_id)
        paid_revenue = Decimal(str((await self.session.execute(pay_stmt)).scalar_one()))
        return {
            "total_appointments": total_appointments,
            "total_procedures": total_procedures,
            "total_revenue": total_revenue,
            "paid_revenue": paid_revenue,
        }

    async def get_doctors_revenue(
        self,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> list[DoctorRevenueItem]:
        """Расчёт загрузки и выплат врачам (бухгалтерия)."""
        dt_start = datetime.combine(start_date, time.min)
        dt_end = datetime.combine(end_date, time.max)

        # Выбираем всех врачей с их профилями
        stmt = (
            select(User)
            .options(selectinload(User.doctor_profile))
            .where(User.role == RoleEnum.DOCTOR, User.is_active.is_(True))
        )
        if clinic_id:
            stmt = stmt.where(User.clinic_id == clinic_id)

        doctors = (await self.session.execute(stmt)).scalars().all()
        items: list[DoctorRevenueItem] = []

        for doc in doctors:
            profile = doc.doctor_profile
            fee_percent = (
                profile.appointment_fee_percent
                if profile
                else DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT
            )
            spec = profile.specialization if profile else "Общая практика"
            # Считаем приёмы врача
            appt_query = (
                select(
                    func.count(Appointment.id),
                    func.coalesce(func.sum(Service.price), 0),
                )
                .join(Service, Appointment.service_id == Service.id, isouter=True)
                .where(
                    Appointment.doctor_id == doc.id,
                    Appointment.status == AppointmentStatus.COMPLETED,
                    Appointment.scheduled_at >= dt_start,
                    Appointment.scheduled_at <= dt_end,
                )
            )
            appt_res = (await self.session.execute(appt_query)).one()
            appts_count = int(appt_res[0])
            appts_rev = Decimal(str(appt_res[1]))

            # Зарплата по проценту от приёмов
            salary = (appts_rev * fee_percent / ONE_HUNDRED_PERCENT).quantize(Decimal("0.01"))
            items.append(
                DoctorRevenueItem(
                    doctor_id=doc.id,
                    doctor_name=doc.full_name,
                    specialization=spec,
                    appointments_count=appts_count,
                    procedures_count=0,
                    total_appointments_revenue=appts_rev,
                    total_procedures_revenue=Decimal("0.00"),
                    calculated_salary=salary,
                )
            )

        return items
