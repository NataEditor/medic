from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.security import hash_password
from app.models.entities import User
from app.models.enums import RoleEnum
from app.repositories.user_repo import UserRepository


class AdminService:
    """Управление администраторами платформы (роль SUPER_ADMIN).

    Бизнес-правила (защита от захвата и злоупотреблений):
    - Создавать администраторов может только действующий супер-админ.
    - Никто не может удалить самого себя (нельзя случайно/намеренно лишить себя доступа).
    - Никто не может удалить другого администратора, даже супер-админ
      (защита от «подчистки» коллег).
    Настройка чувствительных операций фиксируется в журнале аудита.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)

    @staticmethod
    def _ensure_is_super_admin(actor: User) -> None:
        if actor.role != RoleEnum.SUPER_ADMIN:
            raise PermissionDeniedError("Операция доступна только владельцу платформы")

    async def create_admin(
        self,
        actor: User,
        *,
        email: str,
        phone: str,
        password: str,
        first_name: str,
        last_name: str,
        middle_name: str | None = None,
        clinic_id: int | None = None,
    ) -> User:
        """Создать нового администратора платформы (только супер-админ)."""
        self._ensure_is_super_admin(actor)

        if await self.user_repo.get_by_email(email):
            raise BusinessRuleError("Пользователь с таким email уже существует")
        if await self.user_repo.get_by_phone(phone):
            raise BusinessRuleError("Пользователь с таким телефоном уже существует")

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

        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=actor,
            action="create_admin",
            entity_name="user",
            entity_id=created.id,
            changes=f'{{"email": "{created.email}", "role": "{created.role.value}"}}',
        )
        return created

    async def delete_user(self, actor: User, target_user_id: int) -> None:
        """Удаление пользователя с защитными проверками.

        Запрещено:
        - удалять самого себя;
        - удалять другого администратора (супер-админа или главврача).
        """
        if actor.id == target_user_id:
            raise BusinessRuleError("Нельзя удалить собственную учётную запись")

        target = await self.user_repo.get_by_id(target_user_id)
        if not target:
            raise NotFoundError("Пользователь не найден")

        if target.role in (RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR):
            raise BusinessRuleError(
                "Удаление администратора запрещено. Сначала понизьте его роль."
            )

        await self.user_repo.delete(target)

        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=actor,
            action="delete_user",
            entity_name="user",
            entity_id=target_user_id,
            changes=f'{{"email": "{target.email}"}}',
        )

    async def deactivate_user(self, actor: User, target_user_id: int) -> User:
        """Блокировка учётной записи сотрудника.

        Нельзя заблокировать себя — иначе администратор потеряет доступ
        к управлению системой и возникнет ситуация «запертой двери».
        """
        if actor.id == target_user_id:
            raise BusinessRuleError("Нельзя заблокировать собственную учётную запись")

        target = await self.user_repo.get_by_id(target_user_id)
        if not target:
            raise NotFoundError("Пользователь не найден")

        target.is_active = False
        await self.session.flush()
        await self.session.refresh(target)

        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=actor,
            action="deactivate_user",
            entity_name="user",
            entity_id=target_user_id,
            changes=f'{{"email": "{target.email}", "is_active": false}}',
        )
        return target
