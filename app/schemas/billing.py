from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import PaymentMethod, PaymentStatus
from app.schemas.common import ORMModel
from app.schemas.users import UserResponse


class ServiceBase(ORMModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=255)
    description: str | None = None
    price: Decimal = Field(..., ge=0)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    is_procedure: bool = False
    is_active: bool = True


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(ORMModel):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = Field(None, ge=0)
    duration_minutes: int | None = Field(None, ge=5, le=480)
    is_procedure: bool | None = None
    is_active: bool | None = None


class ServiceResponse(ServiceBase):
    id: int
    created_at: datetime
    updated_at: datetime


class PaymentCreate(ORMModel):
    invoice_id: int
    amount: Decimal = Field(..., gt=0)
    method: PaymentMethod


class PaymentRefundRequest(ORMModel):
    """Запрос на контролируемый возврат средств по проведённому платежу.

    Прямое изменение или удаление платежа запрещено всем ролям (защита кассы).
    Возврат оформляется отдельной операцией с обязательным указанием причины,
    которая фиксируется в журнале аудита.
    """

    reason: str = Field(..., min_length=5, max_length=500, description="Причина возврата")


class PaymentResponse(ORMModel):
    id: int
    invoice_id: int
    amount: Decimal
    method: PaymentMethod
    cashier_id: int
    cashier: UserResponse | None = None
    created_at: datetime


class InvoiceCreate(ORMModel):
    appointment_id: int | None = None
    patient_id: int
    clinic_id: int
    amount: Decimal = Field(..., ge=0)


class InvoiceResponse(ORMModel):
    id: int
    appointment_id: int | None = None
    patient_id: int
    clinic_id: int
    amount: Decimal
    status: PaymentStatus
    payments: list[PaymentResponse] = []
    created_at: datetime
    updated_at: datetime


# Схемы финансовых отчётов для бухгалтерии
class DoctorRevenueItem(ORMModel):
    doctor_id: int
    doctor_name: str
    specialization: str
    appointments_count: int
    procedures_count: int
    total_appointments_revenue: Decimal
    total_procedures_revenue: Decimal
    calculated_salary: Decimal  # Вычисленный процент врача


class ClinicFinancialReport(ORMModel):
    clinic_id: int | None = None
    start_date: date
    end_date: date
    total_appointments: int
    total_procedures: int
    total_revenue: Decimal
    paid_revenue: Decimal
    doctors: list[DoctorRevenueItem]
