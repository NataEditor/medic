from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles, require_super_admin
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

router = APIRouter(prefix="/auth", tags=["Аутентификация"])


@router.post("/login", response_model=TokenResponse, summary="Вход сотрудника (email + пароль)")
async def login(
    data: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    return await service.authenticate(data)


@router.post(
    "/login/patient",
    response_model=TokenResponse,
    summary="Вход пациента в личный кабинет (телефон + пароль)",
)
async def login_patient(
    data: PhoneLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    return await service.authenticate_patient(data)


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
    summary="Регистрация сотрудника клиники (администратор)",
)
async def register_user(
    data: UserCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
) -> User:
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
    summary="Удалить пользователя (запрещено удалять себя и других админов)",
)
async def delete_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> StatusResponse:
    """Удаление пользователя с защитой: нельзя удалить себя или администратора."""
    service = AdminService(db)
    await service.delete_user(current_user, user_id)
    return StatusResponse(message="Пользователь успешно удалён")


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Заблокировать сотрудника (нельзя заблокировать себя)",
)
async def deactivate_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_super_admin())],
) -> User:
    """Блокировка учётной записи. Себя заблокировать нельзя — защита от «запертой двери»."""
    service = AdminService(db)
    return await service.deactivate_user(current_user, user_id)
