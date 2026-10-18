from datetime import date, datetime, time

from pydantic import Field, model_validator

from app.schemas.common import ORMModel


class DoctorScheduleBase(ORMModel):
    """Базовые поля шаблона рабочей смены врача."""

    doctor_id: int
    clinic_id: int
    weekday: int = Field(..., ge=0, le=6, description="День недели: 0=Пн ... 6=Вс")
    start_time: time = Field(..., description="Начало смены")
    end_time: time = Field(..., description="Конец смены")
    break_start: time | None = Field(None, description="Начало обеда")
    break_end: time | None = Field(None, description="Конец обеда")
    cabinet_number: str | None = Field(None, max_length=50)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_times(self) -> "DoctorScheduleBase":
        if self.end_time <= self.start_time:
            raise ValueError("Конец смены должен быть позже начала")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("Обед задаётся парой: и начало, и конец")
        if self.break_start and self.break_end:
            if self.break_end <= self.break_start:
                raise ValueError("Конец обеда должен быть позже начала")
            if self.break_start < self.start_time or self.break_end > self.end_time:
                raise ValueError("Обед должен находиться внутри смены")
        return self


class DoctorScheduleCreate(DoctorScheduleBase):
    pass


class DoctorScheduleUpdate(ORMModel):
    clinic_id: int | None = None
    start_time: time | None = None
    end_time: time | None = None
    break_start: time | None = None
    break_end: time | None = None
    cabinet_number: str | None = None
    is_active: bool | None = None


class DoctorScheduleResponse(DoctorScheduleBase):
    id: int
    created_at: datetime
    updated_at: datetime


class TimeOffCreate(ORMModel):
    doctor_id: int
    start_date: date
    end_date: date
    reason: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def validate_period(self) -> "TimeOffCreate":
        if self.end_date < self.start_date:
            raise ValueError("Дата окончания не может быть раньше даты начала")
        return self


class TimeOffResponse(ORMModel):
    id: int
    doctor_id: int
    start_date: date
    end_date: date
    reason: str | None = None
    created_at: datetime
    updated_at: datetime


class FreeSlot(ORMModel):
    """Свободное окно для записи пациента."""

    start: datetime
    end: datetime
    cabinet_number: str | None = None


class DoctorDayScheduleResponse(ORMModel):
    """Расписание врача на конкретную дату: занятые приёмы и свободные окна."""

    doctor_id: int
    date: date
    is_working_day: bool
    busy_intervals: list[FreeSlot] = []
    free_slots: list[FreeSlot] = []
