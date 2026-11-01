from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AUDIT_VIEW_ROLES, require_roles
from app.core.constants import DEFAULT_PAGE, DEFAULT_PAGE_SIZE
from app.db.session import get_db
from app.models.entities import User
from app.schemas.audit import AuditLogFilter, AuditLogResponse
from app.schemas.common import PaginatedResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/audit-logs", tags=["Журнал аудита"])


@router.get(
    "",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="Журнал аудита: действия и просмотры ПДн (SA — сеть, админ — своя)",
)
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*AUDIT_VIEW_ROLES))],
    action: str | None = Query(None, description="Тип действия, например VIEW_PDN"),
    entity_name: str | None = Query(None, description="Имя сущности"),
    entity_id: int | None = Query(None, description="ID объекта"),
    user_id: int | None = Query(None, description="ID пользователя"),
    only_pdn: bool = Query(
        False,
        description="Только просмотры персональных данных (152-ФЗ)",
    ),
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[AuditLogResponse]:
    """Журнал аудита.

    Супер-админ видит всю сеть, админ клиники — только записи сотрудников
    своего филиала. Журнал неизменяем: правка и удаление запрещены всем.
    """
    service = AuditService(db)
    filters = AuditLogFilter(
        action=action,
        entity_name=entity_name,
        entity_id=entity_id,
        user_id=user_id,
        only_pdn=only_pdn,
    )
    items, total = await service.list_logs(
        current_user,
        filters,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
