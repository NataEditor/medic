from datetime import UTC, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import TEMP_PASSWORD_TTL_HOURS
from app.core.exceptions import AuthenticationError, BusinessRuleError, ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.utils import utcnow
from app.models.entities import DoctorProfile, User
from app.models.enums import RoleEnum
from app.repositories.user_repo import UserRepository
from app.schemas.auth import LoginRequest, PhoneLoginRequest, TokenResponse
from app.schemas.users import UserCreate


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)

    async def authenticate(self, data: LoginRequest) -> TokenResponse:
        """Вход сотрудника клиники по email и паролю."""
        user = await self.user_repo.get_by_email(data.email)
        if not user or not verify_password(data.password, user.hashed_password):
            raise AuthenticationError("Неверный email или пароль")
        if not user.is_active:
            raise AuthenticationError("Учётная запись деактивирована")

        return self._issue_tokens(user)

    async def authenticate_patient(self, data: PhoneLoginRequest) -> TokenResponse:
        """Вход пациента в личный кабинет по телефону и паролю.

        Пациенты не вводят email (может отсутствовать), поэтому вход по телефону,
        указанному при регистрации на ресепшене.
        """
        user = await self.user_repo.get_by_phone(data.phone)
        if not user or not user.has_role(RoleEnum.PATIENT):
            raise AuthenticationError("Пациент с таким телефоном не найден")
        if not user.is_active:
            raise AuthenticationError("Учётная запись деактивирована")
        if not verify_password(data.password, user.hashed_password):
            raise AuthenticationError("Неверный пароль")

        # Проверяем срок действия временного пароля
        if user.must_change_password and user.temp_password_expires_at:
            expires = user.temp_password_expires_at
            # SQLite может вернуть naive-datetime: приводим к UTC для сравнения
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < utcnow():
                raise AuthenticationError(
                    "Срок действия временного пароля истёк. Обратитесь на ресепшен."
                )
        return self._issue_tokens(user)

    def _issue_tokens(self, user: User) -> TokenResponse:
        return TokenResponse(
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
            expires_in_minutes=settings.access_token_expire_minutes,
            must_change_password=user.must_change_password,
        )

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        """Смена пароля. Для пациента снимает флаг обязательной смены временного пароля."""
        if not verify_password(current_password, user.hashed_password):
            raise AuthenticationError("Неверный текущий пароль")
        if current_password == new_password:
            raise BusinessRuleError("Новый пароль должен отличаться от текущего")

        user.hashed_password = hash_password(new_password)
        user.must_change_password = False
        user.temp_password_expires_at = None
        await self.session.flush()

    async def grant_patient_access(self, user: User) -> tuple[str, User]:
        """Помечает учётную запись пациента как требующую смены временного пароля.

        Возвращает сгенерированный временный пароль (он уже сохранён как хеш)
        и учётную запись. Открытый пароль уходит в SMS и не хранится в БД.
        """
        from app.core.utils import generate_temp_password

        temp_password = generate_temp_password()
        user.hashed_password = hash_password(temp_password)
        user.must_change_password = True
        user.temp_password_expires_at = utcnow() + timedelta(hours=TEMP_PASSWORD_TTL_HOURS)
        user.is_active = True
        await self.session.flush()
        await self.session.refresh(user)
        return temp_password, user

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = payload.get("sub")
        user = await self.user_repo.get_by_id(int(user_id)) if user_id else None

        if not user or not user.is_active:
            raise AuthenticationError("Пользователь не найден или заблокирован")

        return self._issue_tokens(user)

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
            roles=[data.role.value],
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
