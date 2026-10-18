from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Appointment, DoctorSchedule, TimeOff
from app.models.enums import AppointmentStatus
from app.repositories.base import BaseRepository

# Статусы, при которых приём занимает время врача.
# Отменённый приём и неявка слот НЕ занимают — время освобождается.
BUSY_APPOINTMENT_STATUSES: tuple[AppointmentStatus, ...] = (
    AppointmentStatus.SCHEDULED,
    AppointmentStatus.WAITING,
    AppointmentStatus.IN_PROGRESS,
    AppointmentStatus.COMPLETED,
)

class ScheduleRepository(BaseRepository[DoctorSchedule]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DoctorSchedule, session)

    async def get_by_doctor_and_weekday(
        self,
        doctor_id: int,
        weekday: int,
    ) -> DoctorSchedule | None:
        """Шаблон смены врача на конкретный день недели."""
        stmt = select(DoctorSchedule).where(
            DoctorSchedule.doctor_id == doctor_id,
            DoctorSchedule.weekday == weekday,
            DoctorSchedule.is_active.is_(True),
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_doctor_schedules(self, doctor_id: int) -> list[DoctorSchedule]:
        """Все смены врача, отсортированные по дню недели."""
        stmt = (
            select(DoctorSchedule)
            .where(DoctorSchedule.doctor_id == doctor_id)
            .order_by(DoctorSchedule.weekday)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def has_time_off(self, doctor_id: int, on_date: date) -> TimeOff | None:
        """Возвращает период отсутствия, если врач в отпуске/на больничном в эту дату."""
        stmt = select(TimeOff).where(
            TimeOff.doctor_id == doctor_id,
            TimeOff.start_date <= on_date,
            TimeOff.end_date >= on_date,
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def list_time_off(self, doctor_id: int) -> list[TimeOff]:
        stmt = (
            select(TimeOff)
            .where(TimeOff.doctor_id == doctor_id)
            .order_by(TimeOff.start_date.desc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_busy_intervals(
        self,
        doctor_id: int,
        on_date: date,
    ) -> list[Appointment]:
        """Приёмы врача в указанный день, занимающие время (без отменённых и неявок)."""
        day_start = datetime.combine(on_date, time.min)
        day_end = day_start + timedelta(days=1)

        stmt = (
            select(Appointment)
            .where(
                Appointment.doctor_id == doctor_id,
                Appointment.status.in_(BUSY_APPOINTMENT_STATUSES),
                Appointment.scheduled_at >= day_start,
                Appointment.scheduled_at < day_end,
            )
            .order_by(Appointment.scheduled_at)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
