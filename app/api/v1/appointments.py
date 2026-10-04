from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import Appointment, User
from app.models.enums import AppointmentStatus, RoleEnum
from app.repositories.appointment_repo import AppointmentRepository
from app.schemas.appointments import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatusUpdate,
    AppointmentUpdate,
)
from app.schemas.common import PaginatedResponse
from app.services.appointment_service import AppointmentService

router = APIRouter(prefix="/appointments", tags=["Приёмы и расписание"])


@router.post(
    "",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Запись на приём (ресепшен, врач или пациент)",
)
async def create_appointment(
    data: AppointmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Appointment:
    # Пациент может записать только себя
    if current_user.role == RoleEnum.PATIENT and (
        not current_user.patient_profile
        or current_user.patient_profile.id != data.patient_id
    ):
        raise PermissionDeniedError("Пациент может записать только себя")
    service = AppointmentService(db)
    return await service.create_appointment(data)


@router.get(
    "",
    response_model=PaginatedResponse[AppointmentResponse],
    summary="Список приёмов с фильтрацией (расписание)",
)
async def list_appointments(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    clinic_id: int | None = None,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    status_filter: AppointmentStatus | None = Query(None, alias="status"),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PaginatedResponse[AppointmentResponse]:
    repo = AppointmentRepository(db)

    # Ролевые ограничения фильтрации
    if current_user.role == RoleEnum.PATIENT:
        # Пациент видит строго только свои приёмы
        if not current_user.patient_profile:
            return PaginatedResponse(
                items=[], total=0, page=page, page_size=page_size, total_pages=0
            )
        patient_id = current_user.patient_profile.id
    elif current_user.role == RoleEnum.DOCTOR:
        # Врач по умолчанию видит свои приёмы
        doctor_id = current_user.id
    elif current_user.role in (RoleEnum.RECEPTIONIST, RoleEnum.NURSE, RoleEnum.ACCOUNTANT):
        clinic_id = clinic_id or current_user.clinic_id

    items, total = await repo.filter_appointments(
        clinic_id=clinic_id,
        doctor_id=doctor_id,
        patient_id=patient_id,
        status=status_filter,
        from_date=from_date,
        to_date=to_date,
        offset=(page - 1) * page_size,
        limit=page_size,
    )

    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if total else 0,
    )


@router.get(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Детали приёма",
)
async def get_appointment(
    appointment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Appointment:
    repo = AppointmentRepository(db)
    appt = await repo.get_detailed(appointment_id)
    if not appt:
        raise NotFoundError("Приём не найден")

    # Пациент не может подглядывать чужие приёмы
    if (
        current_user.role == RoleEnum.PATIENT
        and current_user.patient_profile
        and appt.patient_id != current_user.patient_profile.id
    ):
        raise PermissionDeniedError("Доступ запрещён")

    return appt


@router.patch(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Перенос/редактирование приёма (ресепшен, врач)",
)
async def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR)),
    ],
) -> Appointment:
    service = AppointmentService(db)
    return await service.update_appointment(appointment_id, data)


@router.patch(
    "/{appointment_id}/status",
    response_model=AppointmentResponse,
    summary="Изменить статус приёма (завершить, отменить, ожидание)",
)
async def update_status(
    appointment_id: int,
    data: AppointmentStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR)),
    ],
) -> Appointment:
    service = AppointmentService(db)
    return await service.update_status(appointment_id, data)
