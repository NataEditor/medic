from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    SCHEDULE_MANAGE_ROLES,
    SLOT_VIEW_ROLES,
    ensure_doctor_clinic_access,
    require_roles,
)
from app.core.constants import DEFAULT_APPOINTMENT_DURATION_MINUTES
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import DoctorSchedule, TimeOff, User
from app.models.enums import RoleEnum
from app.repositories.schedule_repo import ScheduleRepository
from app.schemas.common import StatusResponse
from app.schemas.schedules import (
    DoctorDayScheduleResponse,
    DoctorScheduleCreate,
    DoctorScheduleResponse,
    DoctorScheduleUpdate,
    TimeOffCreate,
    TimeOffResponse,
)
from app.services.schedule_service import ScheduleService

router = APIRouter(prefix="/schedules", tags=["Расписание врачей"])


# ----- Шаблоны смен (график по дням недели) -----
@router.post(
    "",
    response_model=DoctorScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать смену врача (администратор)",
)
async def create_schedule(
    data: DoctorScheduleCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*SCHEDULE_MANAGE_ROLES))],
) -> DoctorSchedule:
    await ensure_doctor_clinic_access(db, current_user, data.doctor_id)
    service = ScheduleService(db)
    return await service.create_schedule(data)


@router.get(
    "/doctor/{doctor_id}",
    response_model=list[DoctorScheduleResponse],
    summary="Все смены врача по дням недели (персонал)",
)
async def list_doctor_schedules(
    doctor_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.CLINIC_ADMIN,
                RoleEnum.HEAD_DOCTOR,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
            )
        ),
    ],
) -> list[DoctorSchedule]:
    """Шаблоны смен врача. Пациент видит только свободные слоты (другой эндпоинт)."""
    await ensure_doctor_clinic_access(db, current_user, doctor_id)
    repo = ScheduleRepository(db)
    return await repo.list_doctor_schedules(doctor_id)


@router.patch(
    "/{schedule_id}",
    response_model=DoctorScheduleResponse,
    summary="Изменить смену врача",
)
async def update_schedule(
    schedule_id: int,
    data: DoctorScheduleUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*SCHEDULE_MANAGE_ROLES))],
) -> DoctorSchedule:
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(schedule_id)
    if not schedule:
        raise NotFoundError("Смена не найдена")
    await ensure_doctor_clinic_access(db, current_user, schedule.doctor_id)
    service = ScheduleService(db)
    return await service.update_schedule(schedule_id, data)


@router.delete(
    "/{schedule_id}",
    response_model=StatusResponse,
    summary="Удалить смену врача",
)
async def delete_schedule(
    schedule_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*SCHEDULE_MANAGE_ROLES))],
) -> StatusResponse:
    repo = ScheduleRepository(db)
    schedule = await repo.get_by_id(schedule_id)
    if not schedule:
        raise NotFoundError("Смена не найдена")
    await ensure_doctor_clinic_access(db, current_user, schedule.doctor_id)
    service = ScheduleService(db)
    await service.delete_schedule(schedule_id)
    return StatusResponse(message="Смена удалена")


# ----- Отпуска и больничные -----
@router.post(
    "/time-off",
    response_model=TimeOffResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить отсутствие врача: отпуск/больничный (администратор)",
)
async def create_time_off(
    data: TimeOffCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*SCHEDULE_MANAGE_ROLES))],
) -> TimeOff:
    await ensure_doctor_clinic_access(db, current_user, data.doctor_id)
    service = ScheduleService(db)
    return await service.create_time_off(data)


@router.get(
    "/time-off/{doctor_id}",
    response_model=list[TimeOffResponse],
    summary="Список периодов отсутствия врача (персонал)",
)
async def list_time_off(
    doctor_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.CLINIC_ADMIN,
                RoleEnum.HEAD_DOCTOR,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
            )
        ),
    ],
) -> list[TimeOff]:
    await ensure_doctor_clinic_access(db, current_user, doctor_id)
    repo = ScheduleRepository(db)
    return await repo.list_time_off(doctor_id)


@router.delete(
    "/time-off/{time_off_id}",
    response_model=StatusResponse,
    summary="Удалить период отсутствия",
)
async def delete_time_off(
    time_off_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*SCHEDULE_MANAGE_ROLES))],
) -> StatusResponse:
    time_off = await db.get(TimeOff, time_off_id)
    if not time_off:
        raise NotFoundError("Период отсутствия не найден")
    await ensure_doctor_clinic_access(db, current_user, time_off.doctor_id)
    service = ScheduleService(db)
    await service.delete_time_off(time_off_id)
    return StatusResponse(message="Период отсутствия удалён")


# ----- Свободные слоты на дату -----
@router.get(
    "/doctor/{doctor_id}/day",
    response_model=DoctorDayScheduleResponse,
    summary="Расписание врача на дату: занятые интервалы и свободные слоты",
)
async def get_doctor_day(
    doctor_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(*SLOT_VIEW_ROLES))],
    on_date: date = Query(..., alias="date", description="Дата в формате ГГГГ-ММ-ДД"),
    slot_minutes: int = Query(
        DEFAULT_APPOINTMENT_DURATION_MINUTES,
        ge=10,
        le=240,
        description="Длительность слота в минутах",
    ),
) -> DoctorDayScheduleResponse:
    service = ScheduleService(db)
    day = await service.get_doctor_day_schedule(doctor_id, on_date, slot_minutes)
    if day is None:
        raise NotFoundError("Расписание не найдено")
    return day
