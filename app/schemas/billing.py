from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import PaymentMethod, PaymentStatus, RefundRequestStatus
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
    reversal_of_id: int | None = None
    reversal_reason: str | None = None
    created_at: datetime


# ═══════════════════════════════════════════════════════════════
# Двойной контроль возвратов: заявка → подтверждение бухгалтерией
# ═══════════════════════════════════════════════════════════════


class RefundRequestCreate(ORMModel):
    """Заявка на возврат (создаёт ресепшен/касса).

    Деньги не двигаются: заявка ждёт решения бухгалтерии.
    """

    payment_id: int
    amount: Decimal = Field(..., gt=0, description="Сумма к возврату (может быть частичной)")
    reason: str = Field(..., min_length=5, max_length=500, description="Причина возврата")


class RefundRequestReview(ORMModel):
    """Решение бухгалтерии по заявке."""

    approve: bool = Field(..., description="True — подтвердить, False — отклонить")
    comment: str | None = Field(None, max_length=500, description="Комментарий бухгалтера")


class RefundRequestResponse(ORMModel):
    id: int
    payment_id: int
    invoice_id: int
    requested_by_id: int
    reviewed_by_id: int | None = None
    amount: Decimal
    reason: str
    status: RefundRequestStatus
    review_comment: str | None = None
    reviewed_at: datetime | None = None
    requested_by: UserResponse | None = None
    reviewed_by: UserResponse | None = None
    created_at: datetime
    updated_at: datetime


class ReversalCreate(ORMModel):
    """Сторно-проводка: компенсация платежа без изменения оригинала."""

    reason: str = Field(..., min_length=5, max_length=500, description="Причина сторно")


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
