from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    STAFF_MANAGE_ROLES,
    get_current_user,
    record_audit_log,
    require_roles,
    require_super_admin,
)
from app.core.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    PermissionDeniedError,
)
from app.core.logging import log_security_event
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import RoleEnum
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    PhoneLoginRequest,
    RefreshTokenRequest,
    TokenResponse,
)
from app.schemas.common import StatusResponse
from app.schemas.users import AdminCreate, UserCreate, UserResponse
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.login_guard import login_guard

router = APIRouter(prefix="/auth", tags=["Аутентификация"])


@router.post("/login", response_model=TokenResponse, summary="Вход сотрудника (email + пароль)")
async def login(
    data: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Вход сотрудника с защитой от подбора пароля.

    После LOGIN_MAX_ATTEMPTS неудач пара «логин + IP» блокируется на
    LOGIN_LOCKOUT_MINUTES минут. Каждая неудача пишется в аудит-лог.
    """
    ip = _client_ip(request)
    _ensure_not_locked(data.email, ip)

    service = AuthService(db)
    try:
        tokens = await service.authenticate(data)
    except AuthenticationError as exc:
        await _register_failed_login(db, data.email, ip, "staff")
        raise exc

    login_guard.reset(data.email, ip)
    log_security_event("login_success", "Успешный вход сотрудника", login=data.email, ip=ip)
    return tokens


@router.post(
    "/login/patient",
    response_model=TokenResponse,
    summary="Вход пациента в личный кабинет (телефон + пароль)",
)
async def login_patient(
    data: PhoneLoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Вход пациента с той же защитой от подбора пароля."""
    ip = _client_ip(request)
    _ensure_not_locked(data.phone, ip)

    service = AuthService(db)
    try:
        tokens = await service.authenticate_patient(data)
    except AuthenticationError as exc:
        await _register_failed_login(db, data.phone, ip, "patient")
        raise exc

    login_guard.reset(data.phone, ip)
    log_security_event("login_success", "Успешный вход пациента", login=data.phone, ip=ip)
    return tokens


@router.post(
    "/change-password",
    response_model=TokenResponse,
    summary="Смена пароля (пациент обязан сменить временный пароль)",
)
async def change_password(
    data: ChangePasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TokenResponse:
    service = AuthService(db)
    await service.change_password(current_user, data.current_password, data.new_password)
    # После смены пароля выдаём свежие токены (флаг must_change_password снят)
    await db.refresh(current_user)
    return service._issue_tokens(current_user)


@router.post("/refresh", response_model=TokenResponse, summary="Обновление access токена")
async def refresh_token(
    data: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    return await service.refresh_tokens(data.refresh_token)


@router.get("/me", response_model=UserResponse, summary="Данные текущего пользователя")
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    return current_user


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация сотрудника клиники (SA — сеть, админ клиники — своя)",
)
async def register_user(
    data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*STAFF_MANAGE_ROLES))],
) -> User:
    """Регистрация сотрудника.

    Главврач людьми не управляет — это зона админа клиники.
    Админ клиники создаёт сотрудников только в своём филиале.
    """
    if not current_user.has_role(RoleEnum.SUPER_ADMIN):
        if data.clinic_id is not None and data.clinic_id != current_user.clinic_id:
            raise PermissionDeniedError("Нельзя создать сотрудника в другой клинике")
        data = data.model_copy(update={"clinic_id": current_user.clinic_id})

    service = AuthService(db)
    return await service.register_user(data)


@router.post(
    "/admins",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать администратора платформы (только супер-админ)",
)
async def create_admin(
    data: AdminCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> User:
    """Создать нового супер-админа. Роль фиксирована — клиент не может её подменить."""
    service = AdminService(db)
    return await service.create_admin(
        actor=current_user,
        email=data.email,
        phone=data.phone,
        password=data.password,
        first_name=data.first_name,
        last_name=data.last_name,
        middle_name=data.middle_name,
        clinic_id=data.clinic_id,
    )


@router.delete(
    "/users/{user_id}",
    response_model=StatusResponse,
    summary="Удаление пользователя запрещено (только блокировка)",
)
async def delete_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> StatusResponse:
    """Удаление пользователей запрещено инвариантом ТЗ.

    Ничего не удаляется физически: учётная запись блокируется
    (POST /auth/users/{id}/deactivate), история сохраняется для аудита.
    Эндпоинт оставлен, чтобы клиент получал внятную ошибку вместо 404.
    """
    raise BusinessRuleError(
        "Удаление пользователей запрещено. Используйте блокировку: "
        "POST /auth/users/{user_id}/deactivate"
    )


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Заблокировать сотрудника (SA — любого, админ клиники — кроме админов)",
)
async def deactivate_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(*STAFF_MANAGE_ROLES))],
) -> User:
    """Блокировка учётной записи.

    Себя заблокировать нельзя — защита от «запертой двери».
    Админ клиники не может блокировать других администраторов и сотрудников
    чужого филиала.
    """
    service = AdminService(db)
    return await service.deactivate_user(current_user, user_id)


# ═══════════════════════════════════════════════════════════════
# Защита входа: rate limiting, блокировка и аудит неудачных попыток
# ═══════════════════════════════════════════════════════════════


def _client_ip(request: Request) -> str | None:
    """IP клиента с учётом reverse proxy (Nginx передаёт X-Forwarded-For)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Первый адрес в цепочке — исходный клиент
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _ensure_not_locked(login: str, ip: str | None) -> None:
    """Бросает 429, если пара «логин + IP» временно заблокирована."""
    if not login_guard.is_locked(login, ip):
        return
    seconds = login_guard.remaining_lockout_seconds(login, ip)
    minutes = max(1, seconds // 60)
    log_security_event(
        "login_locked",
        "Вход заблокирован из-за частых неудачных попыток",
        login=login,
        ip=ip,
        retry_after_seconds=seconds,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(f"Слишком много неудачных попыток входа. Повторите через {minutes} мин."),
        headers={"Retry-After": str(seconds)},
    )


async def _register_failed_login(
    db: AsyncSession,
    login: str,
    ip: str | None,
    kind: str,
) -> None:
    """Учитывает неудачную попытку и пишет её в аудит-лог."""
    login_guard.register_failure(login, ip)
    log_security_event(
        "login_failed",
        "Неудачная попытка входа",
        login=login,
        ip=ip,
        kind=kind,
    )

    await record_audit_log(
        db=db,
        user=None,
        action="login_failed",
        entity_name="auth",
        entity_id=None,
        changes=f'{{"login": "{login}", "kind": "{kind}"}}',
        ip_address=ip,
    )
    await db.commit()
