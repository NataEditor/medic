from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.logging import logger
from app.core.security import hash_password
from app.models.entities import Clinic, User
from app.models.enums import RoleEnum
from app.repositories.user_repo import UserRepository


class BootstrapService:
    """Первичная инициализация системы.

    Решает «проблему курицы и яйца»: чтобы создать администратора через API,
    нужен уже существующий администратор. Поэтому первого супер-админа
    заводим напрямую в базе — через CLI или автоматически при старте.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)

    async def has_any_admin(self) -> bool:
        """Есть ли в системе хотя бы один администратор (супер-админ или главврач)."""
        stmt = select(func.count(User.id)).where(
            User.role.in_((RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR))
        )
        return int((await self.session.execute(stmt)).scalar_one()) > 0

    async def create_super_admin(
        self,
        *,
        email: str,
        password: str,
        phone: str,
        first_name: str,
        last_name: str,
        middle_name: str | None = None,
        clinic_id: int | None = None,
    ) -> User:
        """Создать супер-админа напрямую в базе.

        Проверяет уникальность email и телефона. Пароль сохраняется как хеш Argon2id.
        """
        if await self.user_repo.get_by_email(email):
            raise ConflictError(f"Пользователь с email {email} уже существует")
        if await self.user_repo.get_by_phone(phone):
            raise ConflictError(f"Пользователь с телефоном {phone} уже существует")

        admin = User(
            clinic_id=clinic_id,
            email=email.lower().strip(),
            phone=phone.strip(),
            hashed_password=hash_password(password),
            role=RoleEnum.SUPER_ADMIN,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            middle_name=middle_name.strip() if middle_name else None,
            is_active=True,
        )
        created = await self.user_repo.create(admin)
        logger.info("Создан супер-админ: {}", created.email)
        return created

    async def ensure_super_admin_on_startup(self) -> User | None:
        """Создаёт супер-админа из настроек при старте, если администраторов ещё нет.

        Возвращает созданного админа или None, если админ уже существует
        либо автосоздание отключено.
        """
        if not settings.bootstrap_admin_on_startup:
            return None

        if await self.has_any_admin():
            return None

        logger.warning("В системе нет администраторов — создаём супер-админа из настроек bootstrap")
        return await self.create_super_admin(
            email=settings.bootstrap_admin_email,
            password=settings.bootstrap_admin_password,
            phone=settings.bootstrap_admin_phone,
            first_name=settings.bootstrap_admin_first_name,
            last_name=settings.bootstrap_admin_last_name,
        )

    async def ensure_default_clinic(self) -> Clinic:
        """Создаёт клинику по умолчанию, если её ещё нет (для быстрого старта)."""
        stmt = select(Clinic).limit(1)
        existing = (await self.session.execute(stmt)).scalars().first()
        if existing:
            return existing

        clinic = Clinic(
            name="Головная клиника",
            code="MAIN",
            address="г. Москва, ул. Примерная, д. 1",
            phone="+70000000000",
        )
        self.session.add(clinic)
        await self.session.flush()
        await self.session.refresh(clinic)
        logger.info("Создана клиника по умолчанию: {}", clinic.name)
        return clinic
