from datetime import datetime

from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.users import UserResponse


class MedicalRecordEntryCreate(ORMModel):
    appointment_id: int | None = None
    complaints: str = Field(..., min_length=1, description="Жалобы пациента")
    anamnesis: str | None = Field(None, description="Анамнез заболевания и жизни")
    objective_status: str | None = Field(None, description="Данные осмотра")
    diagnosis_icd10: str = Field(..., max_length=20, description="Код МКБ-10 (например, J06.9)")
    diagnosis_text: str = Field(..., min_length=1, description="Клинический диагноз")
    treatment_plan: str | None = Field(None, description="План обследования и лечения")


class MedicalRecordEntryResponse(ORMModel):
    id: int
    record_id: int
    doctor_id: int
    appointment_id: int | None = None
    complaints: str
    anamnesis: str | None = None
    objective_status: str | None = None
    diagnosis_icd10: str
    diagnosis_text: str
    treatment_plan: str | None = None
    doctor: UserResponse | None = None
    created_at: datetime
    updated_at: datetime


class MedicalRecordBase(ORMModel):
    patient_id: int
    blood_group: str | None = Field(None, max_length=10)
    allergies: str | None = None
    chronic_diseases: str | None = None
    notes: str | None = None


class MedicalRecordCreate(MedicalRecordBase):
    pass


class MedicalRecordUpdate(ORMModel):
    blood_group: str | None = None
    allergies: str | None = None
    chronic_diseases: str | None = None
    notes: str | None = None


class MedicalRecordResponse(MedicalRecordBase):
    id: int
    record_number: str
    entries: list[MedicalRecordEntryResponse] = []
    created_at: datetime
    updated_at: datetime
