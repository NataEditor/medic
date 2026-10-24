from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.entities import PatientRelative, User
from app.models.enums import RoleEnum
from app.schemas.relatives import (
    PatientRelativeConfirm,
    PatientRelativeCreate,
    PatientRelativeResponse,
)
from app.services.relative_service import RelativeService

router = APIRouter(prefix="/relatives", tags=["Близкие пациента"])


@router.post(
    "",
    response_model=PatientRelativeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Заявить связь с близким (пациент из ЛК)",
)
async def request_relative(
    data: PatientRelativeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PatientRelative:
    """Связь создаётся в статусе PENDING: можно записывать, но не видеть ЭМК.

    Исключение: «родитель — несовершеннолетний ребёнок» подтверждается сразу.
    """
    service = RelativeService(db)
    return await service.request_relative(current_user, data)


@router.get(
    "/my",
    response_model=list[PatientRelativeResponse],
    summary="Мои близкие (пациент)",
)
async def list_my_relatives(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[PatientRelative]:
    service = RelativeService(db)
    return await service.list_my_relatives(current_user)


@router.get(
    "/pending",
    response_model=list[PatientRelativeResponse],
    summary="Заявки на подтверждение связи (ресепшен)",
)
async def list_pending_relatives(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.RECEPTIONIST))],
) -> list[PatientRelative]:
    service = RelativeService(db)
    return await service.list_pending()


@router.post(
    "/{link_id}/confirm",
    response_model=PatientRelativeResponse,
    summary="Подтвердить или отклонить связь (ресепшен)",
)
async def confirm_relative(
    link_id: int,
    data: PatientRelativeConfirm,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.RECEPTIONIST))],
) -> PatientRelative:
    """При подтверждении открывается просмотр медданных близкого."""
    service = RelativeService(db)
    return await service.confirm_relative(current_user, link_id, data)
