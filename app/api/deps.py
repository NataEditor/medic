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

# Медперсонал, которому доступна медкарта пациента во ВСЕХ клиниках сети:
# пациент может лечиться в разных филиалах, и история должна быть общей.
MEDICAL_STAFF_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR, RoleEnum.SUPER_ADMIN}
)

# Роли, работающие с базой пациентов (ресепшен, медперсонал).
PATIENT_REGISTRY_ROLES: frozenset[RoleEnum] = MEDICAL_STAFF_ROLES | {RoleEnum.RECEPTIONIST}

# Роли, имеющие доступ к финансам (счета, оплаты, отчёты).
# Бухгалтерия — единая на всю сеть и НЕ видит медицинские данные пациентов.
FINANCE_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.ACCOUNTANT, RoleEnum.RECEPTIONIST, RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR}
)

bearer_scheme = HTTPBearer(auto_error=False)


def is_admin(user: User) -> bool:
    """True, если пользователь имеет административные права (супер-админ или главврач)."""
    return user.role in ADMIN_ROLES


def is_super_admin(user: User) -> bool:
    """True, если пользователь — владелец платформы (супер-админ)."""
    return user.role == RoleEnum.SUPER_ADMIN


def is_medical_staff(user: User) -> bool:
    """True для врачей и медсестёр (включая главврача) — они видят медкарты."""
    return user.role in MEDICAL_STAFF_ROLES


async def set_request_id(
    x_request_id: Annotated[str | None, Header()] = None,
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


def require_medical_staff() -> Callable[..., User]:
    """Доступ к медицинским данным (медкарты, назначения, анализы).

    Разрешён врачам и медсёстрам ВСЕХ клиник сети: пациент может лечиться
    в разных филиалах, и история болезни должна быть общей.
    Бухгалтерия и пациент сюда не входят.
    """

    async def medical_staff_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not is_medical_staff(current_user):
            raise PermissionDeniedError("Доступно только медицинскому персоналу")
        return current_user

    return medical_staff_checker


def require_finance() -> Callable[..., User]:
    """Доступ к финансовым данным (счета, оплаты, отчёты).

    Бухгалтерия — единая на всю сеть клиник. Она работает только с суммами
    и аналитикой и не получает доступ к медкартам и базе пациентов.
    """

    async def finance_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in FINANCE_ROLES:
            raise PermissionDeniedError("Доступно только финансовому отделу")
        return current_user

    return finance_checker


async def ensure_appointment_access(
    current_user: User,
    appointment_doctor_id: int,
    appointment_patient_id: int,
) -> None:
    """Проверяет право на изменение/просмотр приёма.

    Правила:
    - ресепшен и администраторы — любой приём;
    - врач — только свои приёмы (где он лечащий врач);
    - медсестра — приёмы своей клиники (нужны для отметки «пациент прибыл»);
    - пациент — только свой приём.
    """
    role = current_user.role

    if role in (RoleEnum.RECEPTIONIST, RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR):
        return

    if role == RoleEnum.DOCTOR:
        if appointment_doctor_id != current_user.id:
            raise PermissionDeniedError("Врач может изменять только свои приёмы")
        return

    if role == RoleEnum.NURSE:
        # Медсестра ведёт поток пациентов клиники, но не меняет чужие приёмы по существу
        return

    if role == RoleEnum.PATIENT:
        if (
            not current_user.patient_profile
            or appointment_patient_id != current_user.patient_profile.id
        ):
            raise PermissionDeniedError("Пациент может видеть только свои приёмы")
        return

    raise PermissionDeniedError("Недостаточно прав для работы с приёмом")
