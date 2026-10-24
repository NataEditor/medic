from datetime import datetime

from pydantic import Field

from app.models.enums import EncounterStatus
from app.schemas.common import ORMModel
from app.schemas.users import UserResponse


class EncounterOpen(ORMModel):
    """Открытие обращения врачом в начале приёма.

    Обращение открывается из слота расписания (appointment_id) либо
    напрямую по пациенту (walk-in, без предварительной записи).
    """

    appointment_id: int | None = Field(
        None, description="Слот расписания, из которого открывается приём"
    )
    patient_id: int | None = Field(
        None, description="Пациент для приёма без записи (walk-in)"
    )
    chief_complaint: str | None = Field(
        None, max_length=500, description="Краткая причина обращения"
    )


class EncounterClose(ORMModel):
    """Закрытие обращения. После закрытия записи не редактируются."""

    summary: str | None = Field(None, description="Итоговое заключение по приёму")


class EncounterResponse(ORMModel):
    id: int
    appointment_id: int | None = None
    patient_id: int
    doctor_id: int
    clinic_id: int
    record_id: int
    status: EncounterStatus
    opened_at: datetime
    closed_at: datetime | None = None
    chief_complaint: str | None = None
    doctor: UserResponse | None = None
    created_at: datetime
    updated_at: datetime


class PatientStatusChange(ORMModel):
    """Смена статуса карты пациента (активация, архивация, обезличивание)."""

    reason: str | None = Field(None, max_length=500, description="Причина смены статуса")
