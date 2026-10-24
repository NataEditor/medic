from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ensure_appointment_access,
    get_current_user,
    require_roles,
)
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

# Статусы, которые может выставлять медсестра (отметка прибытия и начала приёма).
NURSE_ALLOWED_STATUSES: frozenset[AppointmentStatus] = frozenset(
    {AppointmentStatus.WAITING, AppointmentStatus.IN_PROGRESS}
)


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
    if current_user.has_role(RoleEnum.PATIENT) and (
        not current_user.patient_profile or current_user.patient_profile.id != data.patient_id
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
    if current_user.has_role(RoleEnum.PATIENT):
        # Пациент видит строго только свои приёмы
        if not current_user.patient_profile:
            return PaginatedResponse(
                items=[], total=0, page=page, page_size=page_size, total_pages=0
            )
        patient_id = current_user.patient_profile.id
    elif current_user.has_role(RoleEnum.DOCTOR):
        # Врач по умолчанию видит свои приёмы
        doctor_id = current_user.id
    elif current_user.has_any_role(RoleEnum.RECEPTIONIST, RoleEnum.NURSE, RoleEnum.ACCOUNTANT):
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

    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
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

    # Единое правило доступа: врач — свои, пациент — свой, ресепшен/админ — любой,
    # медсестра — приёмы клиники (ведёт поток пациентов).
    await ensure_appointment_access(current_user, appt.doctor_id, appt.patient_id)
    return appt


@router.patch(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    summary="Перенос приёма (ресепшен — любой, врач — только свой)",
)
async def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR)),
    ],
) -> Appointment:
    repo = AppointmentRepository(db)
    appt = await repo.get_by_id(appointment_id)
    if not appt:
        raise NotFoundError("Приём не найден")

    # Врач переносит только свои приёмы; ресепшен — любые.
    if current_user.has_role(RoleEnum.DOCTOR) and appt.doctor_id != current_user.id:
        raise PermissionDeniedError("Врач может переносить только свои приёмы")

    service = AppointmentService(db)
    return await service.update_appointment(appointment_id, data)


@router.patch(
    "/{appointment_id}/status",
    response_model=AppointmentResponse,
    summary="Изменить статус приёма (ресепшен/врач — по правам, медсестра — прибытие)",
)
async def update_status(
    appointment_id: int,
    data: AppointmentStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
            )
        ),
    ],
) -> Appointment:
    repo = AppointmentRepository(db)
    appt = await repo.get_by_id(appointment_id)
    if not appt:
        raise NotFoundError("Приём не найден")

    # Медсестра отмечает только факт прибытия/начала приёма, не закрывая его.
    if current_user.has_role(RoleEnum.NURSE):
        if data.status not in NURSE_ALLOWED_STATUSES:
            raise PermissionDeniedError(
                "Медсестра может отметить только «ожидает» или «идёт приём»"
            )
    else:
        # Врач меняет статус только своих приёмов; ресепшен — любых.
        await ensure_appointment_access(current_user, appt.doctor_id, appt.patient_id)

    service = AppointmentService(db)
    return await service.update_status(appointment_id, data)
