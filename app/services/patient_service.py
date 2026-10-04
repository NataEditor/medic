from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.utils import format_medical_record_number
from app.models.entities import MedicalRecord, Patient
from app.repositories.patient_repo import PatientRepository
from app.schemas.patients import PatientCreate, PatientUpdate


class PatientService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.patient_repo = PatientRepository(session)

    async def create_patient_with_record(self, data: PatientCreate) -> Patient:
        """Ресепшен создаёт пациента — одновременно автоматически создаётся ЭМК."""
        existing = await self.patient_repo.get_by_phone(data.phone)
        if existing:
            raise ConflictError("Пациент с таким номером телефона уже зарегистрирован")

        patient = Patient(
            first_name=data.first_name.strip(),
            last_name=data.last_name.strip(),
            middle_name=data.middle_name.strip() if data.middle_name else None,
            birth_date=data.birth_date,
            gender=data.gender,
            phone=data.phone.strip(),
            email=data.email.lower().strip() if data.email else None,
            snils=data.snils.strip() if data.snils else None,
            oms_number=data.oms_number.strip() if data.oms_number else None,
            address=data.address.strip() if data.address else None,
        )
        created_patient = await self.patient_repo.create(patient)

        # Автоматическая генерация номера ЭМК по единому формату
        record_number = format_medical_record_number(created_patient.id)

        medical_record = MedicalRecord(
            patient_id=created_patient.id,
            record_number=record_number,
        )
        self.session.add(medical_record)
        await self.session.flush()

        return created_patient

    async def update_patient(self, patient_id: int, data: PatientUpdate) -> Patient:
        patient = await self.patient_repo.get_by_id(patient_id)
        if not patient:
            raise NotFoundError("Пациент не найден")

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(patient, key, val)

        await self.session.flush()
        await self.session.refresh(patient)
        return patient

    async def get_patient_card(self, patient_id: int) -> MedicalRecord:
        record = await self.patient_repo.get_medical_record(patient_id)
        if not record:
            raise NotFoundError("Медицинская карта не найдена")
        return record
