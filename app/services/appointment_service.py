from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PATIENT_CANCEL_LIMIT_HOURS
from app.core.exceptions import BusinessRuleError, NotFoundError
from app.core.logging import logger
from app.models.entities import Appointment, Invoice, Service
from app.models.enums import AppointmentStatus, PaymentStatus
from app.repositories.appointment_repo import AppointmentRepository
from app.repositories.billing_repo import BillingRepository
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentStatusUpdate,
    AppointmentUpdate,
)
from app.services.schedule_service import ScheduleService


class AppointmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.appt_repo = AppointmentRepository(session)
        self.billing_repo = BillingRepository(session)
        self.schedule_service = ScheduleService(session)

    async def create_appointment(
        self,
        data: AppointmentCreate,
        *,
        enforce_schedule: bool = True,
    ) -> Appointment:
        """Создание записи на приём (ресепшен/пациент).

        Проверяет: время не в прошлом, не пересекается с другими приёмами
        и попадает в рабочую смену врача (кроме обеда и отпусков).
        """
        # Проверка даты — нельзя записать в прошлое
        if data.scheduled_at < datetime.now(UTC):
            raise BusinessRuleError("Нельзя записать на прошедшее время")

        # Блокируем слот врача на время транзакции — защита от двойного бронирования
        await self.appt_repo.lock_doctor(data.doctor_id)

        # Проверка занятости врача
        has_overlap = await self.appt_repo.check_doctor_overlap(
            doctor_id=data.doctor_id,
            start_time=data.scheduled_at,
            duration_minutes=data.duration_minutes,
        )
        if has_overlap:
            raise BusinessRuleError("У врача уже есть запись на это время")

        # Проверка, что время входит в рабочую смену врача (график задан администратором)
        if enforce_schedule:
            available = await self.schedule_service.is_doctor_available(
                doctor_id=data.doctor_id,
                scheduled_at=data.scheduled_at,
                duration_minutes=data.duration_minutes,
            )
            if not available:
                raise BusinessRuleError(
                    "Врач не принимает в это время: выходной, отпуск, обед или вне смены"
                )

        appointment = Appointment(
            clinic_id=data.clinic_id,
            patient_id=data.patient_id,
            doctor_id=data.doctor_id,
            service_id=data.service_id,
            scheduled_at=data.scheduled_at,
            duration_minutes=data.duration_minutes,
            status=AppointmentStatus.SCHEDULED,
            notes=data.notes,
        )
        created = await self.appt_repo.create(appointment)

        # Если указана платная услуга — автоматически создаём счёт
        if data.service_id:
            service = await self.session.get(Service, data.service_id)
            if service and service.price > 0:
                invoice = Invoice(
                    appointment_id=created.id,
                    patient_id=created.patient_id,
                    clinic_id=created.clinic_id,
                    amount=service.price,
                    status=PaymentStatus.PENDING,
                )
                self.session.add(invoice)
                await self.session.flush()

        detailed = await self.appt_repo.get_detailed(created.id)
        logger.info(
            "Создан приём id={}: пациент={} врач={} клиника={} время={}",
            created.id,
            created.patient_id,
            created.doctor_id,
            created.clinic_id,
            created.scheduled_at,
        )
        return detailed or created

    @staticmethod
    def ensure_patient_time_limit(appt: Appointment) -> None:
        """Пациент может перенести/отменить приём не позднее чем за 24 часа.

        Защита клиники от «отмены за 5 минут», когда слот уже не перепродать.
        Сотрудники (ресепшен, врач) этим лимитом не ограничены.
        """
        scheduled = appt.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        limit = datetime.now(UTC) + timedelta(hours=PATIENT_CANCEL_LIMIT_HOURS)
        if scheduled < limit:
            raise BusinessRuleError(
                f"Перенести или отменить приём можно не позднее чем за "
                f"{PATIENT_CANCEL_LIMIT_HOURS} ч. Обратитесь на ресепшен."
            )

    async def update_appointment(self, appt_id: int, data: AppointmentUpdate) -> Appointment:
        appt = await self.appt_repo.get_by_id(appt_id)
        if not appt:
            raise NotFoundError("Приём не найден")

        target_doctor = data.doctor_id or appt.doctor_id
        target_time = data.scheduled_at or appt.scheduled_at
        target_duration = data.duration_minutes or appt.duration_minutes

        if data.scheduled_at or data.doctor_id or data.duration_minutes:
            has_overlap = await self.appt_repo.check_doctor_overlap(
                doctor_id=target_doctor,
                start_time=target_time,
                duration_minutes=target_duration,
                exclude_id=appt.id,
            )
            if has_overlap:
                raise BusinessRuleError("Новое время пересекается с другой записью врача")

            # Перенос тоже должен попадать в рабочую смену врача
            available = await self.schedule_service.is_doctor_available(
                doctor_id=target_doctor,
                scheduled_at=target_time,
                duration_minutes=target_duration,
            )
            if not available:
                raise BusinessRuleError(
                    "Врач не принимает в это время: выходной, отпуск, обед или вне смены"
                )
        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(appt, key, val)

        await self.session.flush()
        logger.info(
            "Перенесён приём id={} (поля: {})",
            appt.id,
            ", ".join(update_dict.keys()) or "нет",
        )
        return (await self.appt_repo.get_detailed(appt.id)) or appt

    async def update_status(self, appt_id: int, data: AppointmentStatusUpdate) -> Appointment:
        appt = await self.appt_repo.get_by_id(appt_id)
        if not appt:
            raise NotFoundError("Приём не найден")

        # Нельзя возобновить отменённый/завершённый приём
        if appt.status in (AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED):
            raise BusinessRuleError("Нельзя изменить статус закрытого приёма")

        appt.status = data.status
        if data.cancel_reason:
            appt.cancel_reason = data.cancel_reason

        # Отмена приёма освобождает слот и аннулирует неоплаченный счёт
        if data.status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            await self._cancel_invoice_for_cancelled_appointment(appt)

        await self.session.flush()
        logger.info(
            "Изменён статус приёма id={}: {} (причина: {})",
            appt.id,
            data.status.value,
            data.cancel_reason or "-",
        )
        return (await self.appt_repo.get_detailed(appt.id)) or appt

    async def _cancel_invoice_for_cancelled_appointment(self, appt: Appointment) -> None:
        """Аннулирует счёт при отмене приёма, если по нему ещё нет оплат.

        Если пациент уже оплатил — счёт не трогаем: возврат оформляется
        отдельной контролируемой операцией бухгалтерии (refund).
        """
        invoice = await self.billing_repo.get_invoice_by_appointment(appt.id)
        if invoice is None:
            return

        paid = await self.billing_repo.get_total_paid(invoice.id)
        if paid > 0:
            # Оплаченный счёт авто-отменять нельзя — это компетенция бухгалтерии
            return

        invoice.status = PaymentStatus.CANCELLED
