from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.entities import DoctorProfile, User
from app.models.enums import RoleEnum
from app.repositories.user_repo import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.users import UserCreate


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)

    async def authenticate(self, data: LoginRequest) -> TokenResponse:
        user = await self.user_repo.get_by_email(data.email)
        if not user or not verify_password(data.password, user.hashed_password):
            raise AuthenticationError("Неверный email или пароль")
        if not user.is_active:
            raise AuthenticationError("Учётная запись деактивирована")

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_minutes=settings.access_token_expire_minutes,
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = payload.get("sub")
        user = await self.user_repo.get_by_id(int(user_id)) if user_id else None

        if not user or not user.is_active:
            raise AuthenticationError("Пользователь не найден или заблокирован")

        return TokenResponse(
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
            expires_in_minutes=settings.access_token_expire_minutes,
        )

    async def register_user(self, data: UserCreate) -> User:
        if await self.user_repo.get_by_email(data.email):
            raise ConflictError("Пользователь с таким email уже существует")
        if await self.user_repo.get_by_phone(data.phone):
            raise ConflictError("Пользователь с таким телефоном уже существует")

        user = User(
            clinic_id=data.clinic_id,
            email=data.email.lower().strip(),
            phone=data.phone.strip(),
            hashed_password=hash_password(data.password),
            role=data.role,
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            middle_name=data.middle_name.strip() if data.middle_name else None,
            is_active=data.is_active,
        )
        created_user = await self.user_repo.create(user)

        # Если создаём врача — прикрепляем профиль со ставками
        if data.role == RoleEnum.DOCTOR and data.doctor_profile:
            profile = DoctorProfile(
                user_id=created_user.id,
                specialization=data.doctor_profile.specialization,
                cabinet_number=data.doctor_profile.cabinet_number,
                appointment_fee_percent=data.doctor_profile.appointment_fee_percent,
                procedure_fee_percent=data.doctor_profile.procedure_fee_percent,
            )
            await self.user_repo.create_doctor_profile(profile)

        return created_user
