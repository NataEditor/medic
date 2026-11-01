from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_super_admin
from app.db.session import get_db
from app.models.entities import AnonymizationRequest, User
from app.models.enums import AnonymizationRequestStatus
from app.schemas.anonymization import (
    AnonymizationRequestCreate,
    AnonymizationRequestResponse,
    AnonymizationRequestReview,
)
from app.services.anonymization_service import AnonymizationService

router = APIRouter(
    prefix="/anonymization-requests",
    tags=["Обезличивание аккаунта (152-ФЗ)"],
)


@router.post(
    "",
    response_model=AnonymizationRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Подать заявку на обезличивание своего аккаунта (пациент)",
)
async def create_request(
    data: AnonymizationRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AnonymizationRequest:
    """Пациент подаёт заявку — ПДн пока на месте, решение за супер-админом."""
    service = AnonymizationService(db)
    return await service.create_request(current_user, data)


@router.get(
    "",
    response_model=list[AnonymizationRequestResponse],
    summary="Список заявок (SA — все, пациент — свои)",
)
async def list_requests(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: AnonymizationRequestStatus | None = Query(None, alias="status"),
) -> list[AnonymizationRequest]:
    service = AnonymizationService(db)
    return await service.list_requests(current_user, status_filter)


@router.post(
    "/{request_id}/review",
    response_model=AnonymizationRequestResponse,
    summary="Подтвердить или отклонить заявку (только супер-админ)",
)
async def review_request(
    request_id: int,
    data: AnonymizationRequestReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> AnonymizationRequest:
    """При подтверждении карта обезличивается: ПДн скрыты, медзаписи сохранены."""
    service = AnonymizationService(db)
    return await service.review_request(current_user, request_id, data)


@router.post(
    "/{request_id}/cancel",
    response_model=AnonymizationRequestResponse,
    summary="Отозвать заявку до рассмотрения (автор заявки)",
)
async def cancel_request(
    request_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AnonymizationRequest:
    service = AnonymizationService(db)
    return await service.cancel_request(current_user, request_id)
