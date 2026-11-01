from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.core.logging import logger
from app.core.utils import utcnow
from app.models.entities import (
    Appointment,
    Encounter,
    MedicalRecord,
    Patient,
    User,
)
from app.models.enums import (
    AppointmentStatus,
    EncounterStatus,
    PatientStatus,
    RoleEnum,
)
from app.schemas.encounters import EncounterOpen


class EncounterService:
    """Медицинские обращения (Encounter) — отдельно от слотов расписания.

    Appointment — бронь времени. Encounter — факт приёма, внутри которого
    врач документирует осмотр. Один слот порождает ноль или один Encounter.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def open_encounter(self, doctor: User, data: EncounterOpen) -> Encounter:
        """Врач открывает обращение в начале приёма.

        Источник пациента: либо слот расписания (appointment_id),
        либо прямой приём без записи (walk-in, patient_id).
        """
        if not doctor.has_any_role(RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Открывать обращение может только врач")

        if data.appointment_id is None and data.patient_id is None:
            raise BusinessRuleError("Укажите приём (appointment_id) или пациента (patient_id)")

        appointment: Appointment | None = None
        if data.appointment_id is not None:
            appointment = await self.session.get(Appointment, data.appointment_id)
            if appointment is None:
                raise NotFoundError("Приём не найден")
            if appointment.doctor_id != doctor.id:
                raise PermissionDeniedError("Открыть обращение может только лечащий врач")
            if appointment.status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
                raise BusinessRuleError("Нельзя открыть обращение по отменённому приёму")

            # Один слот — максимум одно обращение
            existing = await self._get_by_appointment(appointment.id)
            if existing is not None:
                raise BusinessRuleError(f"По этому приёму уже открыто обращение (id={existing.id})")

            patient_id = appointment.patient_id
            clinic_id = appointment.clinic_id
        else:
            patient_id = data.patient_id
            if doctor.clinic_id is None:
                raise BusinessRuleError("У врача не указана клиника")
            clinic_id = doctor.clinic_id

        patient = await self.session.get(Patient, patient_id)
        if patient is None:
            raise NotFoundError("Пациент не найден")
        if patient.status != PatientStatus.ACTIVE:
            raise BusinessRuleError(
                f"Карта пациента в статусе '{patient.status.value}': приём невозможен"
            )

        record = await self._get_record(patient_id)
        if record is None:
            raise BusinessRuleError("У пациента нет медкарты — обратитесь в регистратуру")

        encounter = Encounter(
            appointment_id=appointment.id if appointment else None,
            patient_id=patient_id,
            doctor_id=doctor.id,
            clinic_id=clinic_id,
            record_id=record.id,
            status=EncounterStatus.OPEN,
            opened_at=utcnow(),
            chief_complaint=data.chief_complaint,
        )
        self.session.add(encounter)

        # Слот переходит в «идёт приём»
        if appointment is not None:
            appointment.status = AppointmentStatus.IN_PROGRESS

        await self.session.flush()
        await self.session.refresh(encounter)
        logger.info(
            "Открыто обращение id={}: врач={} пациент={} клиника={}",
            encounter.id,
            doctor.id,
            patient_id,
            clinic_id,
        )
        return encounter

    async def close_encounter(self, doctor: User, encounter_id: int) -> Encounter:
        """Врач закрывает обращение. После закрытия записи не редактируются."""
        encounter = await self.session.get(Encounter, encounter_id)
        if encounter is None:
            raise NotFoundError("Обращение не найдено")
        if encounter.doctor_id != doctor.id and not doctor.has_role(RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Закрыть обращение может только лечащий врач")
        if encounter.status != EncounterStatus.OPEN:
            raise BusinessRuleError("Обращение уже закрыто или отменено")

        encounter.status = EncounterStatus.CLOSED
        encounter.closed_at = utcnow()

        # Слот завершается вместе с обращением
        if encounter.appointment_id is not None:
            appointment = await self.session.get(Appointment, encounter.appointment_id)
            if appointment is not None and appointment.status == AppointmentStatus.IN_PROGRESS:
                appointment.status = AppointmentStatus.COMPLETED

        await self.session.flush()
        await self.session.refresh(encounter)
        logger.info(
            "Закрыто обращение id={}: врач={} пациент={}",
            encounter.id,
            doctor.id,
            encounter.patient_id,
        )
        return encounter

    async def get_encounter(self, encounter_id: int) -> Encounter:
        encounter = await self.session.get(Encounter, encounter_id)
        if encounter is None:
            raise NotFoundError("Обращение не найдено")
        return encounter

    async def list_patient_encounters(self, patient_id: int) -> list[Encounter]:
        stmt = (
            select(Encounter)
            .where(Encounter.patient_id == patient_id)
            .order_by(Encounter.opened_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def ensure_encounter_open(self, encounter_id: int) -> Encounter:
        """Проверяет, что обращение открыто (можно вносить записи)."""
        encounter = await self.get_encounter(encounter_id)
        if encounter.status != EncounterStatus.OPEN:
            raise BusinessRuleError(
                "Обращение закрыто: новые записи запрещены. Оформите корректирующую запись."
            )
        return encounter

    async def _get_by_appointment(self, appointment_id: int) -> Encounter | None:
        stmt = select(Encounter).where(Encounter.appointment_id == appointment_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_record(self, patient_id: int) -> MedicalRecord | None:
        stmt = select(MedicalRecord).where(MedicalRecord.patient_id == patient_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
