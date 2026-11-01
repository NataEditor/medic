from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# ═══════════════════════════════════════════════════════════════════
# МАТРИЦА ПРАВ И ПРОВЕРКИ ДОСТУПА
# ═══════════════════════════════════════════════════════════════════
# Вся логика разграничения доступа вынесена в app.core.access.
# Здесь — ре-экспорт для обратной совместимости: роутеры и сервисы
# продолжают импортировать константы и guard-функции из app.api.deps.
from app.core.access import (
    ADMIN_ROLES,
    AUDIT_VIEW_ROLES,
    CASHIER_ROLES,
    EMR_READ_ROLES,
    EMR_WRITE_ROLES,
    FINANCE_ROLES,
    MEDICAL_STAFF_ROLES,
    NETWORK_FINANCE_ROLES,
    NETWORK_MEDICAL_ROLES,
    PASSPORT_SCAN_ROLES,
    PASSPORT_SCAN_VIEW_ROLES,
    PATIENT_CREATE_ROLES,
    PATIENT_REGISTRY_ROLES,
    PATIENT_UPDATE_ROLES,
    PORTAL_ACCESS_ROLES,
    REFUND_APPROVE_ROLES,
    SCHEDULE_MANAGE_ROLES,
    SLOT_VIEW_ROLES,
    STAFF_MANAGE_ROLES,
    STAFF_VIEW_ROLES,
    _has_any,
    can_read_emk,
    can_view_emr,
    can_view_patient_card_and_procedures,
    check_clinic_access,
    ensure_analysis_access,
    ensure_appointment_access,
    ensure_clinic_scope,
    ensure_doctor_clinic_access,
    ensure_doctor_touch,
    ensure_medical_access,
    ensure_nurse_clinic_link,
    ensure_patient_admin_access,
    ensure_patient_card_access,
    ensure_patient_self,
    is_admin,
    is_clinic_admin,
    is_medical_staff,
    is_super_admin,
)
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.logging import bind_user_context, log_security_event, logger, request_id_ctx
from app.core.security import decode_token
from app.db.session import get_db
from app.models.entities import AuditLog, User
from app.models.enums import RoleEnum

bearer_scheme = HTTPBearer(auto_error=False)


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
        log_security_event("auth_missing_token", "Запрос без Bearer токена")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется Bearer токен аутентификации",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except AuthenticationError as exc:
        log_security_event("auth_invalid_token", exc.message)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        log_security_event("auth_invalid_token", "Некорректный subject токена")
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
        log_security_event(
            "auth_user_not_found",
            "Пользователь из токена не найден",
            user_id=user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден",
        )
    if not user.is_active:
        log_security_event(
            "auth_inactive_user",
            "Попытка входа деактивированной учётной записи",
            user_id=user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись деактивирована",
        )

    # Привязываем пользователя к контексту логирования: все дальнейшие
    # записи в рамках запроса будут содержать, кто выполнил действие.
    bind_user_context(user.id)
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
    """Проверяет, что у пользователя есть хотя бы одна из разрешённых ролей.

    Мультиролевая модель: права суммируются. Если у человека роли
    [doctor, head_doctor], он пройдёт проверку на любую из них.

    SUPER_ADMIN (владелец платформы) получает доступ к любой операции.
    CLINIC_ADMIN и HEAD_DOCTOR — только к тем, что явно разрешены:
    у них разные зоны ответственности (люди/деньги vs медицина).
    """

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if is_super_admin(current_user):
            return current_user
        if current_user.has_any_role(*allowed_roles):
            return current_user
        required = ", ".join(r.value for r in allowed_roles)
        log_security_event(
            "access_denied",
            "Недостаточно прав для операции",
            user_id=current_user.id,
            roles=current_user.roles,
            required=required,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Доступ запрещён. Требуется одна из ролей: {required}",
        )

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


async def record_audit_log(
    db: AsyncSession,
    user: User | None,
    action: str,
    entity_name: str,
    entity_id: int | None,
    changes: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Запись действия в аудит-лог (врачебная тайна, безопасность данных).

    Для просмотров ПДн (152-ФЗ) используйте action=ACTION_VIEW_PDN —
    такие записи попадают в фильтр only_pdn журнала аудита.
    """
    log_entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_name=entity_name,
        entity_id=entity_id,
        changes=changes,
        ip_address=ip_address,
    )
    db.add(log_entry)
    logger.debug(
        "Аудит: действие={} объект={}#{} пользователь={}",
        action,
        entity_name,
        entity_id,
        user.id if user else "-",
    )


async def record_pdn_view(
    db: AsyncSession,
    user: User | None,
    entity_name: str,
    entity_id: int | None,
    *,
    resource: str,
    ip_address: str | None = None,
) -> None:
    """Фиксация просмотра персональных данных (152-ФЗ).

    Единая точка для всех чтений ПДн: ЭМК, скан паспорта, карточка
    пациента. Пишет action=VIEW_PDN, а конкретный ресурс — в changes,
    чтобы журнал можно было фильтровать и по типу, и по объекту.
    """
    from app.core.constants import ACTION_VIEW_PDN

    await record_audit_log(
        db=db,
        user=user,
        action=ACTION_VIEW_PDN,
        entity_name=entity_name,
        entity_id=entity_id,
        changes=f'{{"resource": "{resource}"}}',
        ip_address=ip_address,
    )
    # Просмотр ПДн — событие безопасности (152-ФЗ): пишем в отдельный журнал.
    log_security_event(
        "view_pdn",
        "Просмотр персональных данных",
        user_id=user.id if user else "-",
        resource=resource,
        entity=f"{entity_name}#{entity_id}",
    )


def require_medical_staff() -> Callable[..., User]:
    """Доступ к медицинским данным (медкарты, назначения, анализы).

    Разрешён медперсоналу: врач, медсестра, главврач, супер-админ.
    Админ клиники и бухгалтерия сюда НЕ входят — они не читают ЭМК.
    Конкретный пациент дополнительно проверяется через ensure_medical_access
    (правило «по касанию» для врача).
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
        if not _has_any(current_user, FINANCE_ROLES):
            raise PermissionDeniedError("Доступно только финансовому отделу")
        return current_user

    return finance_checker


def require_patient_creator() -> Callable[..., User]:
    """Создание карты пациента — только регистратура и выше.

    Врач карту не создаёт: профиль с паспортом заводит тот, кто проверил
    документ (152-ФЗ). Врач только читает карту и ведёт приём.
    """

    async def creator_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if not _has_any(current_user, PATIENT_CREATE_ROLES):
            raise PermissionDeniedError("Создавать карту пациента может только регистратура")
        return current_user

    return creator_checker


# Ре-экспорт матрицы прав и guard-функций из app.core.access.
# Роутеры и сервисы импортируют их из app.api.deps (обратная совместимость).
__all__ = [
    "ADMIN_ROLES",
    "AUDIT_VIEW_ROLES",
    "CASHIER_ROLES",
    "EMR_READ_ROLES",
    "EMR_WRITE_ROLES",
    "FINANCE_ROLES",
    "MEDICAL_STAFF_ROLES",
    "NETWORK_FINANCE_ROLES",
    "NETWORK_MEDICAL_ROLES",
    "PASSPORT_SCAN_ROLES",
    "PASSPORT_SCAN_VIEW_ROLES",
    "PATIENT_CREATE_ROLES",
    "PATIENT_REGISTRY_ROLES",
    "PATIENT_UPDATE_ROLES",
    "PORTAL_ACCESS_ROLES",
    "REFUND_APPROVE_ROLES",
    "SCHEDULE_MANAGE_ROLES",
    "SLOT_VIEW_ROLES",
    "STAFF_MANAGE_ROLES",
    "STAFF_VIEW_ROLES",
    "bearer_scheme",
    "can_read_emk",
    "can_view_emr",
    "can_view_patient_card_and_procedures",
    "check_clinic_access",
    "ensure_analysis_access",
    "ensure_appointment_access",
    "ensure_clinic_scope",
    "ensure_doctor_clinic_access",
    "ensure_doctor_touch",
    "ensure_medical_access",
    "ensure_nurse_clinic_link",
    "ensure_patient_admin_access",
    "ensure_patient_card_access",
    "ensure_patient_self",
    "get_current_patient_user",
    "get_current_user",
    "is_admin",
    "is_clinic_admin",
    "is_medical_staff",
    "is_super_admin",
    "record_audit_log",
    "record_pdn_view",
    "require_finance",
    "require_medical_staff",
    "require_patient_creator",
    "require_roles",
    "require_super_admin",
    "set_request_id",
]
