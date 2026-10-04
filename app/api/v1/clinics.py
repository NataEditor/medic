from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.constants import DEFAULT_PAGE, DEFAULT_PAGE_SIZE
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import Clinic, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository
from app.schemas.clinics import ClinicCreate, ClinicResponse, ClinicUpdate
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/clinics", tags=["Клиники и филиалы"])


@router.get("", response_model=PaginatedResponse[ClinicResponse], summary="Список клиник")
async def list_clinics(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[ClinicResponse]:
    repo = BaseRepository(Clinic, db)
    items, total = await repo.list_paginated(
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=ClinicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать новую клинику/филиал (только главврач)",
)
async def create_clinic(
    data: ClinicCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
) -> Clinic:
    repo = BaseRepository(Clinic, db)
    clinic = Clinic(**data.model_dump())
    return await repo.create(clinic)


@router.get("/{clinic_id}", response_model=ClinicResponse, summary="Получить клинику по ID")
async def get_clinic(
    clinic_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Clinic:
    repo = BaseRepository(Clinic, db)
    clinic = await repo.get_by_id(clinic_id)
    if not clinic:
        raise NotFoundError("Клиника не найдена")
    return clinic


@router.patch(
    "/{clinic_id}",
    response_model=ClinicResponse,
    summary="Обновить данные клиники (только главврач)",
)
async def update_clinic(
    clinic_id: int,
    data: ClinicUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
) -> Clinic:
    repo = BaseRepository(Clinic, db)
    clinic = await repo.get_by_id(clinic_id)
    if not clinic:
        raise NotFoundError("Клиника не найдена")

    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(clinic, key, val)

    await db.flush()
    return clinic
