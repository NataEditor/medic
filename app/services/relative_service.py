from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.utils import utcnow
from app.models.entities import Patient, PatientRelative, User
from app.models.enums import PatientRelativeStatus, PatientStatus, RoleEnum
from app.schemas.relatives import PatientRelativeConfirm, PatientRelativeCreate

# Связи, которые подтверждаются автоматически (родитель — несовершеннолетний).
AUTO_CONFIRM_RELATIONS: frozenset[str] = frozenset({"child"})
ADULT_AGE: int = 18


class RelativeService:
    """Связи «пациент ↔ близкий» с двухступенчатым подтверждением.

    Пациент заявляет связь (PENDING) → может записывать близкого на приём,
    но не видеть медданные. Ресепшен подтверждает (CONFIRMED) → открывается
    просмотр ЭМК и анализов близкого.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def request_relative(
        self,
        actor: User,
        data: PatientRelativeCreate,
    ) -> PatientRelative:
        """Пациент заявляет связь с близким из личного кабинета."""
        if not actor.has_role(RoleEnum.PATIENT) or actor.patient_profile is None:
            raise PermissionDeniedError("Заявлять связь может только пациент из ЛК")

        owner_id = actor.patient_profile.id
        if data.relative_id == owner_id:
            raise BusinessRuleError("Нельзя указать связь с самим собой")

        relative = await self.session.get(Patient, data.relative_id)
        if relative is None:
            raise NotFoundError("Карта близкого не найдена")
        if relative.status != PatientStatus.ACTIVE:
            raise BusinessRuleError(
                f"Карта близкого в статусе '{relative.status.value}': связь невозможна"
            )

        existing = await self._get_pair(owner_id, data.relative_id)
        if existing is not None:
            raise ConflictError(
                f"Связь с этим пациентом уже заявлена (статус: {existing.status.value})"
            )

        # Родитель — несовершеннолетний ребёнок: подтверждаем сразу
        status = PatientRelativeStatus.PENDING
        confirmed_at = None
        if data.relation in AUTO_CONFIRM_RELATIONS and self._is_minor(relative):
            status = PatientRelativeStatus.CONFIRMED
            confirmed_at = utcnow()

        link = PatientRelative(
            patient_id=owner_id,
            relative_id=data.relative_id,
            relation=data.relation,
            status=status,
            confirmed_at=confirmed_at,
            notes=data.notes,
        )
        self.session.add(link)
        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def confirm_relative(
        self,
        actor: User,
        link_id: int,
        data: PatientRelativeConfirm,
    ) -> PatientRelative:
        """Ресепшен подтверждает или отклоняет связь при совместном визите."""
        if not actor.has_any_role(
            RoleEnum.RECEPTIONIST,
            RoleEnum.CLINIC_ADMIN,
            RoleEnum.SUPER_ADMIN,
        ):
            raise PermissionDeniedError("Подтверждать связь может только регистратура")

        link = await self.session.get(PatientRelative, link_id)
        if link is None:
            raise NotFoundError("Связь не найдена")
        if link.status != PatientRelativeStatus.PENDING:
            raise BusinessRuleError(f"Связь уже рассмотрена (статус: {link.status.value})")

        link.status = (
            PatientRelativeStatus.CONFIRMED if data.approve else PatientRelativeStatus.REJECTED
        )
        link.confirmed_by_id = actor.id
        link.confirmed_at = utcnow()
        if data.notes:
            link.notes = data.notes

        await self.session.flush()
        await self.session.refresh(link)
        return link

    async def list_my_relatives(self, actor: User) -> list[PatientRelative]:
        """Связи текущего пациента (все статусы)."""
        if not actor.has_role(RoleEnum.PATIENT) or actor.patient_profile is None:
            raise PermissionDeniedError("Доступно только пациентам")
        return await self.list_by_patient(actor.patient_profile.id)

    async def list_by_patient(self, patient_id: int) -> list[PatientRelative]:
        stmt = (
            select(PatientRelative)
            .where(PatientRelative.patient_id == patient_id)
            .order_by(PatientRelative.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_pending(self) -> list[PatientRelative]:
        """Заявки, ожидающие подтверждения ресепшеном."""
        stmt = (
            select(PatientRelative)
            .where(PatientRelative.status == PatientRelativeStatus.PENDING)
            .order_by(PatientRelative.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def can_view_medical_data(self, actor: User, patient_id: int) -> bool:
        """Может ли пациент видеть медданные другого пациента как близкий.

        Только при CONFIRMED-связи. PENDING даёт право записи, но не чтения ЭМК.
        """
        if not actor.has_role(RoleEnum.PATIENT) or actor.patient_profile is None:
            return False
        if actor.patient_profile.id == patient_id:
            return True

        link = await self._get_pair(actor.patient_profile.id, patient_id)
        return link is not None and link.status == PatientRelativeStatus.CONFIRMED

    async def can_book_for(self, actor: User, patient_id: int) -> bool:
        """Может ли пациент записать этого пациента на приём.

        Своя карта — да. Близкий в PENDING или CONFIRMED — да (запись безобидна).
        """
        if not actor.has_role(RoleEnum.PATIENT) or actor.patient_profile is None:
            return False
        if actor.patient_profile.id == patient_id:
            return True

        link = await self._get_pair(actor.patient_profile.id, patient_id)
        return link is not None and link.status in (
            PatientRelativeStatus.PENDING,
            PatientRelativeStatus.CONFIRMED,
        )

    # ----- Внутренние -----
    async def _get_pair(self, patient_id: int, relative_id: int) -> PatientRelative | None:
        stmt = select(PatientRelative).where(
            PatientRelative.patient_id == patient_id,
            PatientRelative.relative_id == relative_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _is_minor(patient: Patient) -> bool:
        """Несовершеннолетний ли пациент (младше 18 лет)."""
        today = date.today()
        age = (
            today.year
            - patient.birth_date.year
            - ((today.month, today.day) < (patient.birth_date.month, patient.birth_date.day))
        )
        return age < ADULT_AGE
