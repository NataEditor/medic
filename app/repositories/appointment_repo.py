from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import Appointment, User
from app.models.enums import AppointmentStatus
from app.repositories.base import BaseRepository


class AppointmentRepository(BaseRepository[Appointment]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Appointment, session)

    async def get_detailed(self, appointment_id: int) -> Appointment | None:
        stmt = (
            select(Appointment)
            .options(
                selectinload(Appointment.clinic),
                selectinload(Appointment.patient),
                selectinload(Appointment.doctor),
                selectinload(Appointment.service),
            )
            .where(Appointment.id == appointment_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def lock_doctor(self, doctor_id: int) -> None:
        """Блокирует строку врача на время транзакции (SELECT ... FOR UPDATE).

        Защита от гонки при бронировании: два параллельных запроса на один слот
        будут выполняться последовательно, а не оба пройдут проверку занятости.
        SQLite не поддерживает FOR UPDATE — там блокировка пропускается
        (в тестовой среде гонок нет).
        """
        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name != "postgresql":
            return

        stmt = select(User.id).where(User.id == doctor_id).with_for_update()
        await self.session.execute(stmt)

    async def check_doctor_overlap(
        self,
        doctor_id: int,
        start_time: datetime,
        duration_minutes: int,
        exclude_id: int | None = None,
    ) -> bool:
        """Проверяет, нет ли пересечения с другим активным приёмом у врача."""
        end_time = start_time + timedelta(minutes=duration_minutes)

        # Активные статусы приёма
        active_statuses = (
            AppointmentStatus.SCHEDULED,
            AppointmentStatus.WAITING,
            AppointmentStatus.IN_PROGRESS,
        )

        stmt = select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_(active_statuses),
            Appointment.scheduled_at < end_time,
            Appointment.scheduled_at >= start_time - timedelta(hours=4),
        )
        if exclude_id:
            stmt = stmt.where(Appointment.id != exclude_id)

        res = await self.session.execute(stmt)
        for appt in res.scalars():
            existing_start = appt.scheduled_at
            if existing_start.tzinfo is None and start_time.tzinfo is not None:
                existing_start = existing_start.replace(tzinfo=start_time.tzinfo)
            elif existing_start.tzinfo is not None and start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=existing_start.tzinfo)
                end_time = end_time.replace(tzinfo=existing_start.tzinfo)

            existing_end = existing_start + timedelta(minutes=appt.duration_minutes)
            if existing_start < end_time and existing_end > start_time:
                return True
        return False

    async def filter_appointments(
        self,
        clinic_id: int | None = None,
        doctor_id: int | None = None,
        patient_id: int | None = None,
        status: AppointmentStatus | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Appointment], int]:
        filters: list[Any] = []
        if clinic_id:
            filters.append(Appointment.clinic_id == clinic_id)
        if doctor_id:
            filters.append(Appointment.doctor_id == doctor_id)
        if patient_id:
            filters.append(Appointment.patient_id == patient_id)
        if status:
            filters.append(Appointment.status == status)
        if from_date:
            filters.append(Appointment.scheduled_at >= from_date)
        if to_date:
            filters.append(Appointment.scheduled_at <= to_date)

        stmt = (
            select(Appointment)
            .options(
                selectinload(Appointment.clinic),
                selectinload(Appointment.patient),
                selectinload(Appointment.doctor),
                selectinload(Appointment.service),
            )
            .order_by(Appointment.scheduled_at.desc())
        )
        count_stmt = select(func.count()).select_from(Appointment)

        for f in filters:
            stmt = stmt.where(f)
            count_stmt = count_stmt.where(f)

        stmt = stmt.offset(offset).limit(limit)
        items = (await self.session.execute(stmt)).scalars().all()
        total = int((await self.session.execute(count_stmt)).scalar_one())
        return list(items), total
