from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.models.enums import RoleEnum
from app.schemas.common import ORMModel
from app.schemas.types import Email


class DoctorProfileBase(ORMModel):
    specialization: str = Field(..., max_length=200)
    cabinet_number: str | None = Field(None, max_length=50)
    appointment_fee_percent: Decimal = Field(default=Decimal("30.00"), ge=0, le=100)
    procedure_fee_percent: Decimal = Field(default=Decimal("15.00"), ge=0, le=100)


class DoctorProfileCreate(DoctorProfileBase):
    pass


class DoctorProfileResponse(DoctorProfileBase):
    id: int
    user_id: int


class UserBase(ORMModel):
    clinic_id: int | None = None
    email: Email
    phone: str = Field(..., max_length=50)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    role: RoleEnum
    is_active: bool = True


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    doctor_profile: DoctorProfileCreate | None = None


class AdminCreate(ORMModel):
    """Создание администратора платформы (только супер-админ).

    Роль фиксирована (SUPER_ADMIN) и не задаётся клиентом —
    это исключает эскалацию прав через API.
    """

    email: Email
    phone: str = Field(..., max_length=50)
    password: str = Field(..., min_length=8)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    clinic_id: int | None = None


class UserUpdate(ORMModel):
    clinic_id: int | None = None
    email: Email | None = None
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    role: RoleEnum | None = None
    is_active: bool | None = None
    doctor_profile: DoctorProfileBase | None = None


class UserResponse(UserBase):
    id: int
    full_name: str
    doctor_profile: DoctorProfileResponse | None = None
    created_at: datetime
    updated_at: datetime
