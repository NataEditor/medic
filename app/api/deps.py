from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.logging import request_id_ctx
from app.core.security import decode_token
from app.db.session import get_db
from app.models.entities import AuditLog, User
from app.models.enums import RoleEnum

# Роли, обладающие административными правами над клиникой/сетью.
# SUPER_ADMIN — владелец платформы; HEAD_DOCTOR — руководитель клиники.
ADMIN_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR})

bearer_scheme = HTTPBearer(auto_error=False)


def is_admin(user: User) -> bool:
    """True, если пользователь имеет административные права (супер-админ или главврач)."""
    return user.role in ADMIN_ROLES


def is_super_admin(user: User) -> bool:
    """True, если пользователь — владелец платформы (супер-админ)."""
    return user.role == RoleEnum.SUPER_ADMIN


async def set_request_id(    x_request_id: Annotated[str | None, Header()] = None,
) -> str:
    rid = x_request_id or "-"
    request_id_ctx.set(rid)
    return rid


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Извлекает и валидирует текущего пользователя из JWT Bearer токена."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется Bearer токен аутентификации",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный subject токена",
        )

    stmt = (
        select(User)
        .options(
            selectinload(User.clinic),
            selectinload(User.doctor_profile),
            selectinload(User.patient_profile),
        )
        .where(User.id == int(user_id))
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись деактивирована",
        )

    return user


async def get_current_patient_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Доступ к медицинским данным пациента.

    Блокируется, пока пациент не сменил временный пароль из SMS:
    так исключается ситуация, когда посторонний узнал одноразовый код,
    но ещё не получил доступ к врачебной тайне.
    """
    if current_user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Сначала смените временный пароль (эндпоинт /auth/change-password)",
        )
    return current_user

def require_roles(*allowed_roles: RoleEnum) -> Callable[..., User]:
    """Декоратор зависимостей: проверяет наличие одной из разрешённых ролей.

    SUPER_ADMIN (владелец платформы) и HEAD_DOCTOR (главврач) автоматически
    получают доступ к любой операции, независимо от переданного списка ролей.
    Финальные ограничители (запрет самоудаления, защита кассы) проверяются
    отдельно в сервисах, а не здесь.
    """

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if is_admin(current_user):
            return current_user
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Доступ запрещён для роли {current_user.role.value}",
            )
        return current_user

    return role_checker


def require_super_admin() -> Callable[..., User]:
    """Ограничивает доступ строго владельцем платформы (SUPER_ADMIN).

    Нужен для операций управления администраторами и настройками платформы,
    которые не должны быть доступны главврачу клиники.
    """

    async def super_admin_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not is_super_admin(current_user):
            raise PermissionDeniedError("Операция доступна только владельцу платформы")
        return current_user

    return super_admin_checker


async def check_clinic_access(
    current_user: User,
    target_clinic_id: int | None,
) -> None:
    """Администраторы (супер-админ, главврач) видят любую клинику сети.
    Остальные сотрудники ограничены своей клиникой."""
    if is_admin(current_user):
        return
    if current_user.clinic_id is None or current_user.clinic_id != target_clinic_id:
        raise PermissionDeniedError("Нет доступа к данным другой клиники")

async def record_audit_log(
    db: AsyncSession,
    user: User | None,
    action: str,
    entity_name: str,
    entity_id: int | None,
    changes: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Запись действия в аудит-лог (врачебная тайна, безопасность данных)."""
    log_entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_name=entity_name,
        entity_id=entity_id,
        changes=changes,
        ip_address=ip_address,
    )
    db.add(log_entry)
