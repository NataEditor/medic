from datetime import datetime

from pydantic import Field

from app.core.constants import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    MAX_APPOINTMENT_DURATION_MINUTES,
    MIN_APPOINTMENT_DURATION_MINUTES,
)
from app.models.enums import AppointmentStatus, PaymentStatus
from app.schemas.clinics import ClinicResponse
from app.schemas.common import ORMModel
from app.schemas.patients import PatientResponse
from app.schemas.users import UserResponse


class AppointmentCreate(ORMModel):
    clinic_id: int
    patient_id: int
    doctor_id: int
    service_id: int | None = None
    scheduled_at: datetime
    duration_minutes: int = Field(
        default=DEFAULT_APPOINTMENT_DURATION_MINUTES,
        ge=MIN_APPOINTMENT_DURATION_MINUTES,
        le=MAX_APPOINTMENT_DURATION_MINUTES,
    )
    notes: str | None = None


class AppointmentUpdate(ORMModel):
    clinic_id: int | None = None
    doctor_id: int | None = None
    service_id: int | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(
        None,
        ge=MIN_APPOINTMENT_DURATION_MINUTES,
        le=MAX_APPOINTMENT_DURATION_MINUTES,
    )
    notes: str | None = None


class AppointmentStatusUpdate(ORMModel):
    status: AppointmentStatus
    cancel_reason: str | None = None


class AppointmentResponse(ORMModel):
    id: int
    clinic_id: int
    patient_id: int
    doctor_id: int
    service_id: int | None = None
    scheduled_at: datetime
    duration_minutes: int
    status: AppointmentStatus
    cancel_reason: str | None = None
    notes: str | None = None
    clinic: ClinicResponse | None = None
    patient: PatientResponse | None = None
    doctor: UserResponse | None = None
    # Статус оплаты приёма — врачу нужно знать, оплачен ли приём,
    # чтобы не проводить процедуры в долг.
    payment_status: PaymentStatus | None = None
    created_at: datetime
    updated_at: datetime
