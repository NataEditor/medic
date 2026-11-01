from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.logging import logger
from app.core.utils import utcnow
from app.models.entities import AnonymizationRequest, Patient, User
from app.models.enums import AnonymizationRequestStatus, PatientStatus, RoleEnum
from app.schemas.anonymization import (
    AnonymizationRequestCreate,
    AnonymizationRequestReview,
)
from app.services.patient_service import PatientService


class AnonymizationService:
    """Заявка пациента на обезличивание аккаунта (152-ФЗ, право на забвение).

    Инварианты:
    - Пациент не обезличивает себя сам: он подаёт заявку.
    - Решение принимает только супер-админ (🌐).
    - Обезличивание ≠ удаление: медзаписи сохраняются по срокам Минздрава.
    - Свою заявку нельзя подтвердить самому (двойной контроль).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.patient_service = PatientService(session)

    async def create_request(
        self,
        actor: User,
        data: AnonymizationRequestCreate,
    ) -> AnonymizationRequest:
        """Пациент подаёт заявку на обезличивание своей карты."""
        if not actor.has_role(RoleEnum.PATIENT):
            raise PermissionDeniedError("Заявку на обезличивание подаёт только пациент")

        # Явный запрос вместо actor.patient_profile: ленивая загрузка
        # в async-сессии падает с MissingGreenlet.
        patient = await self._get_patient_for_user(actor.id)
        if patient is None:
            raise BusinessRuleError("Учётная запись не связана с картой пациента")
        if patient.status == PatientStatus.ANONYMIZED:
            raise BusinessRuleError("Карта уже обезличена")

        existing = await self._get_pending_for_patient(patient.id)
        if existing is not None:
            raise BusinessRuleError(f"Заявка уже на рассмотрении (id={existing.id})")

        request = AnonymizationRequest(
            patient_id=patient.id,
            requested_by_id=actor.id,
            reason=data.reason,
            status=AnonymizationRequestStatus.PENDING,
        )
        self.session.add(request)
        await self.session.flush()
        await self.session.refresh(request)
        logger.info(
            "Создана заявка на обезличивание id={}: пациент={} автор={}",
            request.id,
            patient.id,
            actor.id,
        )
        return request

    async def list_requests(
        self,
        actor: User,
        status: AnonymizationRequestStatus | None = None,
    ) -> list[AnonymizationRequest]:
        """Список заявок: супер-админ видит все, пациент — только свои."""
        stmt = select(AnonymizationRequest).order_by(AnonymizationRequest.created_at.desc())

        if actor.has_role(RoleEnum.SUPER_ADMIN):
            pass
        elif actor.has_role(RoleEnum.PATIENT):
            patient = await self._get_patient_for_user(actor.id)
            if patient is None:
                return []
            stmt = stmt.where(AnonymizationRequest.patient_id == patient.id)
        else:
            raise PermissionDeniedError("Заявки доступны супер-админу и пациенту")

        if status is not None:
            stmt = stmt.where(AnonymizationRequest.status == status)

        return list((await self.session.execute(stmt)).scalars().all())

    async def review_request(
        self,
        actor: User,
        request_id: int,
        data: AnonymizationRequestReview,
    ) -> AnonymizationRequest:
        """Супер-админ подтверждает или отклоняет заявку."""
        if not actor.has_role(RoleEnum.SUPER_ADMIN):
            raise PermissionDeniedError("Решение по заявке принимает только супер-админ")

        request = await self.session.get(AnonymizationRequest, request_id)
        if request is None:
            raise NotFoundError("Заявка не найдена")
        if request.status != AnonymizationRequestStatus.PENDING:
            raise BusinessRuleError(f"Заявка уже рассмотрена (статус: {request.status.value})")

        # Двойной контроль: свою заявку подтвердить нельзя
        if request.requested_by_id == actor.id:
            raise BusinessRuleError("Нельзя рассмотреть собственную заявку")

        if data.approve:
            await self.patient_service.anonymize_patient(
                request.patient_id,
                reason=request.reason,
            )
            request.status = AnonymizationRequestStatus.APPROVED
        else:
            request.status = AnonymizationRequestStatus.REJECTED

        request.reviewed_by_id = actor.id
        request.review_comment = data.comment
        request.reviewed_at = utcnow()

        await self.session.flush()
        await self.session.refresh(request)
        logger.info(
            "Заявка на обезличивание id={} рассмотрена: статус={} супер-админ={}",
            request.id,
            request.status.value,
            actor.id,
        )
        return request

    async def cancel_request(
        self,
        actor: User,
        request_id: int,
    ) -> AnonymizationRequest:
        """Пациент отзывает свою заявку до рассмотрения."""
        request = await self.session.get(AnonymizationRequest, request_id)
        if request is None:
            raise NotFoundError("Заявка не найдена")

        if request.requested_by_id != actor.id:
            raise PermissionDeniedError("Можно отозвать только свою заявку")
        if request.status != AnonymizationRequestStatus.PENDING:
            raise BusinessRuleError(f"Заявка уже рассмотрена (статус: {request.status.value})")

        request.status = AnonymizationRequestStatus.CANCELLED
        await self.session.flush()
        await self.session.refresh(request)
        logger.info("Заявка на обезличивание id={} отозвана пациентом={}", request.id, actor.id)
        return request

    async def _get_patient_for_user(self, user_id: int) -> Patient | None:
        """Карта пациента по учётной записи (без ленивой загрузки связи)."""
        stmt = select(Patient).where(Patient.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_pending_for_patient(
        self,
        patient_id: int,
    ) -> AnonymizationRequest | None:
        stmt = select(AnonymizationRequest).where(
            AnonymizationRequest.patient_id == patient_id,
            AnonymizationRequest.status == AnonymizationRequestStatus.PENDING,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
