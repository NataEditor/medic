import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.entities import MedicalRecord, MedicalRecordEntry, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository
from app.schemas.medical_records import MedicalRecordEntryCreate


class MedicalRecordService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entry_repo = BaseRepository(MedicalRecordEntry, session)

    async def add_entry(
        self,
        doctor: User,
        record_id: int,
        data: MedicalRecordEntryCreate,
    ) -> MedicalRecordEntry:
        """Врач добавляет клиническую запись в карту (осмотр, диагноз МКБ-10)."""
        if doctor.role not in (RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Только врач может вносить клинические записи в карту")

        record = await self.session.get(MedicalRecord, record_id)
        if not record:
            raise NotFoundError("Электронная медицинская карта не найдена")

        entry = MedicalRecordEntry(
            record_id=record_id,
            doctor_id=doctor.id,
            appointment_id=data.appointment_id,
            complaints=data.complaints.strip(),
            anamnesis=data.anamnesis.strip() if data.anamnesis else None,
            objective_status=data.objective_status.strip() if data.objective_status else None,
            diagnosis_icd10=data.diagnosis_icd10.strip().upper(),
            diagnosis_text=data.diagnosis_text.strip(),
            treatment_plan=data.treatment_plan.strip() if data.treatment_plan else None,
        )
        created = await self.entry_repo.create(entry)

        # Логируем действие для соблюдения врачебной тайны
        from app.api.deps import record_audit_log

        await record_audit_log(
            db=self.session,
            user=doctor,
            action="create_entry",
            entity_name="medical_record_entry",
            entity_id=created.id,
            changes=json.dumps({"record_id": record_id, "diagnosis": entry.diagnosis_icd10}),
        )

        return created
