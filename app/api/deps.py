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

# ═══════════════════════════════════════════════════════════════════
# МАТРИЦА ПРАВ (8 ролей, мультиролевая модель)
# ═══════════════════════════════════════════════════════════════════

# Администраторы платформы/клиники: управление людьми, графиками, доступами.
# CLINIC_ADMIN — административный директор (НЕ читает ЭМК).
# HEAD_DOCTOR — главный врач (медицинское качество, НЕ управляет людьми).
ADMIN_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN, RoleEnum.HEAD_DOCTOR}
)

# Медперсонал: доступ к медданным (ЭМК, назначения, анализы).
# Врач — только «по касанию» (пациенты своих приёмов), см. ensure_medical_access.
MEDICAL_STAFF_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR, RoleEnum.SUPER_ADMIN}
)

# Роли, которые могут создавать карту пациента (регистратура и выше).
# Врач карту НЕ создаёт — только читает и ведёт приём.
PATIENT_CREATE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.SUPER_ADMIN,
    }
)

# Роли, работающие с базой пациентов (поиск, просмотр карточки).
PATIENT_REGISTRY_ROLES: frozenset[RoleEnum] = MEDICAL_STAFF_ROLES | {
    RoleEnum.RECEPTIONIST,
    RoleEnum.CLINIC_ADMIN,
}

# Финансы: счета, оплаты, отчёты, зарплаты.
# Бухгалтерия — единая на всю сеть и НЕ видит медданные и ПДн.
FINANCE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.ACCOUNTANT,
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.HEAD_DOCTOR,
    }
)

# Роли, которые могут принимать оплату в кассе.
CASHIER_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.RECEPTIONIST, RoleEnum.ACCOUNTANT, RoleEnum.CLINIC_ADMIN, RoleEnum.SUPER_ADMIN}
)

# Роли, которые могут подтверждать возврат (двойной контроль: заявка ≠ подтверждение).
REFUND_APPROVE_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.ACCOUNTANT, RoleEnum.SUPER_ADMIN}
)

bearer_scheme = HTTPBearer(auto_error=False)


def _has_any(user: User, roles: frozenset[RoleEnum]) -> bool:
    """Есть ли у пользователя хотя бы одна роль из набора (мультиролевость)."""
    return any(role.value in user.roles for role in roles)


def is_admin(user: User) -> bool:
    """True для администраторов (супер-админ, админ клиники, главврач)."""
    return _has_any(user, ADMIN_ROLES)


def is_super_admin(user: User) -> bool:
    """True, если пользователь — владелец платформы (супер-админ)."""
    return user.has_role(RoleEnum.SUPER_ADMIN)


def is_medical_staff(user: User) -> bool:
    """True для медперсонала (врач, медсестра, главврач) — доступ к медданным."""
    return _has_any(user, MEDICAL_STAFF_ROLES)


def is_clinic_admin(user: User) -> bool:
    """True для административного директора клиники (без доступа к ЭМК)."""
    return user.has_role(RoleEnum.CLINIC_ADMIN)


def can_read_emk(user: User) -> bool:
    """Может ли пользователь читать ЭМК.

    Админ клиники управляет людьми и расписанием, но НЕ читает медкарты —
    разделение «деньги/люди» и «медицина».
    """
    return is_medical_staff(user)


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Доступ запрещён. Требуется одна из ролей: "
            f"{', '.join(r.value for r in allowed_roles)}",
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
            raise PermissionDeniedError(
                "Создавать карту пациента может только регистратура"
            )
        return current_user

    return creator_checker

async def ensure_appointment_access(
    current_user: User,
    appointment_doctor_id: int,
    appointment_patient_id: int,
) -> None:
    """Проверяет право на изменение/просмотр приёма (мультиролевая модель).

    Правила:
    - супер-админ, админ клиники, главврач, ресепшен — любой приём;
    - врач — только свои приёмы (где он лечащий врач);
    - медсестра — приёмы клиники (нужны для отметки «пациент прибыл»);
    - пациент — только свой приём.
    """
    if current_user.has_any_role(
        RoleEnum.SUPER_ADMIN,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.RECEPTIONIST,
    ):
        return

    if current_user.has_role(RoleEnum.DOCTOR):
        if appointment_doctor_id != current_user.id:
            raise PermissionDeniedError("Врач может изменять только свои приёмы")
        return

    if current_user.has_role(RoleEnum.NURSE):
        # Медсестра ведёт поток пациентов клиники
        return

    if current_user.has_role(RoleEnum.PATIENT):
        if (
            not current_user.patient_profile
            or appointment_patient_id != current_user.patient_profile.id
        ):
            raise PermissionDeniedError("Пациент может видеть только свои приёмы")
        return

    raise PermissionDeniedError("Недостаточно прав для работы с приёмом")


async def ensure_medical_access(
    db: AsyncSession,
    current_user: User,
    patient_id: int,
) -> None:
    """Правило «по касанию»: доступ врача к медкарте пациента.

    Карта общая для сети, но врач не «гуляет» по реестру:
    - супер-админ, главврач — доступ к любой карте сети;
    - медсестра — доступ в рамках своей клиники;
    - врач — только если с пациентом есть связь: назначенный приём
      (текущий или будущий) либо приём в прошлом;
    - админ клиники, бухгалтерия — доступа к ЭМК нет.
    """
    from app.models.entities import Appointment

    if current_user.has_any_role(RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR):
        return

    if current_user.has_role(RoleEnum.NURSE):
        # Медсестра работает с пациентами своей клиники
        return

    if current_user.has_role(RoleEnum.DOCTOR):
        stmt = (
            select(Appointment.id)
            .where(
                Appointment.doctor_id == current_user.id,
                Appointment.patient_id == patient_id,
            )
            .limit(1)
        )
        has_link = (await db.execute(stmt)).scalar_one_or_none() is not None
        if not has_link:
            raise PermissionDeniedError(
                "Нет доступа к карте: у вас нет приёмов с этим пациентом"
            )
        return

    raise PermissionDeniedError("Доступ к медицинской карте запрещён")
