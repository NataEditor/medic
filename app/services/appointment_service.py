from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.models.entities import Appointment, Invoice, Service
from app.models.enums import AppointmentStatus, PaymentStatus
from app.repositories.appointment_repo import AppointmentRepository
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentStatusUpdate,
    AppointmentUpdate,
)


class AppointmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.appt_repo = AppointmentRepository(session)

    async def create_appointment(self, data: AppointmentCreate) -> Appointment:
        """Создание записи на приём (ресепшен/пациент). Проверяет занятость врача."""
        # Проверка даты — нельзя записать в прошлое
        if data.scheduled_at < datetime.now(UTC):
            raise BusinessRuleError("Нельзя записать на прошедшее время")

        # Проверка пересечения приёмов у врача
        has_overlap = await self.appt_repo.check_doctor_overlap(
            doctor_id=data.doctor_id,
            start_time=data.scheduled_at,
            duration_minutes=data.duration_minutes,
        )
        if has_overlap:
            raise BusinessRuleError("У врача уже есть запись на это время")

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
        return detailed or created

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

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(appt, key, val)

        await self.session.flush()
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

        await self.session.flush()
        return (await self.appt_repo.get_detailed(appt.id)) or appt
