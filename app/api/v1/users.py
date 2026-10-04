from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import RoleEnum
from app.repositories.user_repo import UserRepository
from app.schemas.users import UserResponse

router = APIRouter(prefix="/users", tags=["Пользователи"])


@router.get(
    "/doctors",
    response_model=list[UserResponse],
    summary="Список врачей (с фильтром по клинике)",
)
async def list_doctors(
    db: Annotated[AsyncSession, Depends(get_db)],
    clinic_id: int | None = None,
) -> list[User]:
    repo = UserRepository(db)
    return await repo.list_doctors(clinic_id=clinic_id)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="Список всех сотрудников (только главврач)",
)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
    page: int = 1,
    page_size: int = 50,
) -> list[User]:
    repo = UserRepository(db)
    items, _ = await repo.list_paginated(
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return items
