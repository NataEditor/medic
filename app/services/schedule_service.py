from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.core.utils import intervals_overlap
from app.models.entities import DoctorSchedule, TimeOff
from app.repositories.schedule_repo import ScheduleRepository
from app.schemas.schedules import (
    DoctorDayScheduleResponse,
    DoctorScheduleCreate,
    DoctorScheduleUpdate,
    FreeSlot,
    TimeOffCreate,
)


class ScheduleService:
    """Управление расписанием врачей и расчёт свободных слотов.

    Слоты не хранятся в БД: они вычисляются «на лету» из шаблона смены
    с учётом обеда, отпусков и уже занятых приёмов.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ScheduleRepository(session)

    # ----- CRUD шаблонов смен -----
    async def create_schedule(self, data: DoctorScheduleCreate) -> DoctorSchedule:
        existing = await self.repo.get_by_doctor_and_weekday(data.doctor_id, data.weekday)
        if existing:
            raise ConflictError(
                f"У врача уже есть смена на этом дне недели (id={existing.id}). "
                f"Отредактируйте её вместо создания новой."
            )
        schedule = DoctorSchedule(**data.model_dump())
        return await self.repo.create(schedule)

    async def update_schedule(
        self,
        schedule_id: int,
        data: DoctorScheduleUpdate,
    ) -> DoctorSchedule:
        schedule = await self.repo.get_by_id(schedule_id)
        if not schedule:
            raise NotFoundError("Смена не найдена")

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(schedule, key, val)

        # Проверяем корректность после применения изменений
        self._validate_schedule_times(schedule)

        await self.session.flush()
        await self.session.refresh(schedule)
        return schedule

    async def delete_schedule(self, schedule_id: int) -> None:
        schedule = await self.repo.get_by_id(schedule_id)
        if not schedule:
            raise NotFoundError("Смена не найдена")
        await self.repo.delete(schedule)

    @staticmethod
    def _validate_schedule_times(schedule: DoctorSchedule) -> None:
        if schedule.end_time <= schedule.start_time:
            raise BusinessRuleError("Конец смены должен быть позже начала")
        if schedule.break_start and schedule.break_end:
            if schedule.break_end <= schedule.break_start:
                raise BusinessRuleError("Конец обеда должен быть позже начала")
            if schedule.break_start < schedule.start_time or schedule.break_end > schedule.end_time:
                raise BusinessRuleError("Обед должен находиться внутри смены")

    # ----- CRUD отпусков -----
    async def create_time_off(self, data: TimeOffCreate) -> TimeOff:
        time_off = TimeOff(**data.model_dump())
        self.session.add(time_off)
        await self.session.flush()
        await self.session.refresh(time_off)
        return time_off

    async def delete_time_off(self, time_off_id: int) -> None:
        time_off = await self.session.get(TimeOff, time_off_id)
        if not time_off:
            raise NotFoundError("Период отсутствия не найден")
        await self.session.delete(time_off)
        await self.session.flush()

    # ----- Расчёт расписания на дату -----
    async def get_doctor_day_schedule(
        self,
        doctor_id: int,
        on_date: date,
        slot_minutes: int,
    ) -> DoctorDayScheduleResponse:
        """Собирает расписание врача на дату: занятые интервалы и свободные слоты.

        Учитывает: выходной (нет смены), отпуск/больничный (TimeOff),
        обеденный перерыв и уже существующие приёмы.
        """
        if slot_minutes <= 0:
            raise BusinessRuleError("Длительность слота должна быть положительной")

        # 1. Отпуск/больничный — врач недоступен весь день
        time_off = await self.repo.has_time_off(doctor_id, on_date)
        if time_off:
            return DoctorDayScheduleResponse(
                doctor_id=doctor_id,
                date=on_date,
                is_working_day=False,
                busy_intervals=[],
                free_slots=[],
            )

        # 2. Шаблон смены на этот день недели
        weekday = on_date.weekday()
        schedule = await self.repo.get_by_doctor_and_weekday(doctor_id, weekday)
        if not schedule:
            return DoctorDayScheduleResponse(
                doctor_id=doctor_id,
                date=on_date,
                is_working_day=False,
                busy_intervals=[],
                free_slots=[],
            )

        # 3. Занятые интервалы (приёмы, занимающие время; отменённые уже отфильтрованы)
        appointments = await self.repo.get_busy_intervals(doctor_id, on_date)
        busy_intervals = [
            FreeSlot(
                start=appt.scheduled_at.replace(tzinfo=None),
                end=appt.scheduled_at.replace(tzinfo=None)
                + timedelta(minutes=appt.duration_minutes),
                cabinet_number=schedule.cabinet_number,
            )
            for appt in appointments
        ]
        busy_ranges = [
            (
                appt.scheduled_at.replace(tzinfo=None),
                appt.scheduled_at.replace(tzinfo=None) + timedelta(minutes=appt.duration_minutes),
            )
            for appt in appointments
        ]  # 4. Свободные слоты: идём по смене с шагом slot_minutes
        shift_start = datetime.combine(on_date, schedule.start_time)
        shift_end = datetime.combine(on_date, schedule.end_time)
        break_range: tuple[datetime, datetime] | None = None
        if schedule.break_start and schedule.break_end:
            break_range = (
                datetime.combine(on_date, schedule.break_start),
                datetime.combine(on_date, schedule.break_end),
            )

        free_slots: list[FreeSlot] = []
        cursor = shift_start
        step = timedelta(minutes=slot_minutes)
        while cursor + step <= shift_end:
            slot_start = cursor
            slot_end = cursor + step

            in_break = bool(
                break_range
                and intervals_overlap(slot_start, slot_end, break_range[0], break_range[1])
            )
            is_busy = any(
                intervals_overlap(slot_start, slot_end, busy[0], busy[1]) for busy in busy_ranges
            )

            if not in_break and not is_busy:
                free_slots.append(
                    FreeSlot(
                        start=slot_start,
                        end=slot_end,
                        cabinet_number=schedule.cabinet_number,
                    )
                )
            cursor = slot_end

        return DoctorDayScheduleResponse(
            doctor_id=doctor_id,
            date=on_date,
            is_working_day=True,
            busy_intervals=busy_intervals,
            free_slots=free_slots,
        )

    async def is_doctor_available(
        self,
        doctor_id: int,
        scheduled_at: datetime,
        duration_minutes: int,
    ) -> bool:
        """Проверяет, что время приёма попадает в рабочую смену и не пересекает обед.

        Используется при создании приёма как дополнительный барьер.
        """
        on_date = scheduled_at.date()
        if await self.repo.has_time_off(doctor_id, on_date):
            return False

        schedule = await self.repo.get_by_doctor_and_weekday(doctor_id, on_date.weekday())
        if not schedule:
            return False

        start = scheduled_at.replace(tzinfo=None)
        end = start + timedelta(minutes=duration_minutes)
        shift_start = datetime.combine(on_date, schedule.start_time)
        shift_end = datetime.combine(on_date, schedule.end_time)

        if start < shift_start or end > shift_end:
            return False

        if schedule.break_start and schedule.break_end:
            break_start = datetime.combine(on_date, schedule.break_start)
            break_end = datetime.combine(on_date, schedule.break_end)
            if intervals_overlap(start, end, break_start, break_end):
                return False

        return True
