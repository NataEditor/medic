from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ACTION_VIEW_PDN
from app.core.exceptions import PermissionDeniedError
from app.models.entities import AuditLog, User
from app.models.enums import RoleEnum
from app.schemas.audit import AuditLogFilter, AuditLogResponse


class AuditService:
    """Чтение журнала аудита.

    Права по матрице ТЗ:
    - супер-админ — вся сеть (🌐);
    - админ клиники — только записи сотрудников своей клиники (🏥);
    - главврач, врач, медсестра, ресепшен, бухгалтерия, пациент — запрещено.

    Журнал только читается: правка и удаление записей не предусмотрены
    ни для одной роли, включая супер-админа.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _ensure_can_view(actor: User) -> None:
        if not actor.has_any_role(RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN):
            raise PermissionDeniedError("Журнал аудита доступен супер-админу и админу клиники")

    async def list_logs(
        self,
        actor: User,
        filters: AuditLogFilter,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AuditLogResponse], int]:
        """Список записей журнала с фильтрацией и изоляцией по клинике."""
        self._ensure_can_view(actor)

        stmt = select(AuditLog).options(selectinload(AuditLog.user))
        count_stmt = select(func.count()).select_from(AuditLog)

        # Изоляция по клинике: админ клиники видит только своих сотрудников.
        # Записи с user_id = NULL (системные) ему недоступны.
        if not actor.has_role(RoleEnum.SUPER_ADMIN):
            clinic_users = select(User.id).where(User.clinic_id == actor.clinic_id)
            stmt = stmt.where(AuditLog.user_id.in_(clinic_users))
            count_stmt = count_stmt.where(AuditLog.user_id.in_(clinic_users))

        if filters.only_pdn:
            stmt = stmt.where(AuditLog.action == ACTION_VIEW_PDN)
            count_stmt = count_stmt.where(AuditLog.action == ACTION_VIEW_PDN)
        if filters.action:
            stmt = stmt.where(AuditLog.action == filters.action)
            count_stmt = count_stmt.where(AuditLog.action == filters.action)
        if filters.entity_name:
            stmt = stmt.where(AuditLog.entity_name == filters.entity_name)
            count_stmt = count_stmt.where(AuditLog.entity_name == filters.entity_name)
        if filters.entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == filters.entity_id)
            count_stmt = count_stmt.where(AuditLog.entity_id == filters.entity_id)
        if filters.user_id is not None:
            stmt = stmt.where(AuditLog.user_id == filters.user_id)
            count_stmt = count_stmt.where(AuditLog.user_id == filters.user_id)

        stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)

        rows = list((await self.session.execute(stmt)).scalars().all())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        items = [
            AuditLogResponse(
                id=row.id,
                user_id=row.user_id,
                user_full_name=row.user.full_name if row.user else None,
                action=row.action,
                entity_name=row.entity_name,
                entity_id=row.entity_id,
                changes=row.changes,
                ip_address=row.ip_address,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return items, total
