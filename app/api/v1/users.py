from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import STAFF_VIEW_ROLES, get_current_user, require_roles
from app.core.constants import DEFAULT_PAGE, DEFAULT_PAGE_SIZE
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
    current_user: Annotated[User, Depends(get_current_user)],
    clinic_id: int | None = None,
) -> list[User]:
    """Список врачей.

    Супер-админ и главврач видят врачей всей сети (🌐), остальные —
    только своего филиала (🏥). Пациент видит врачей своей клиники,
    чтобы записаться на приём.
    """
    repo = UserRepository(db)
    if current_user.has_any_role(RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR):
        target_clinic = clinic_id
    else:
        target_clinic = current_user.clinic_id
    return await repo.list_doctors(clinic_id=target_clinic)


@router.get(
    "",
    response_model=list[UserResponse],
    summary="Список сотрудников (SA — сеть, админ клиники и главврач — своя)",
)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*STAFF_VIEW_ROLES))],
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> list[User]:
    """Список сотрудников.

    Супер-админ видит всю сеть, админ клиники и главврач — только свой
    филиал. Врач, медсестра, ресепшен и бухгалтерия список не видят.
    """
    repo = UserRepository(db)
    # Супер-админ видит всю сеть, остальные — только свой филиал
    clinic_id = None if current_user.has_role(RoleEnum.SUPER_ADMIN) else current_user.clinic_id

    items, _ = await repo.list_staff(
        clinic_id=clinic_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return items
