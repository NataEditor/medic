from datetime import datetime

from pydantic import EmailStr, Field

from app.schemas.common import ORMModel


class ClinicBase(ORMModel):
    name: str = Field(..., max_length=255)
    code: str = Field(..., max_length=50)
    address: str = Field(..., max_length=500)
    phone: str = Field(..., max_length=50)
    email: EmailStr | None = None
    is_active: bool = True


class ClinicCreate(ClinicBase):
    pass


class ClinicUpdate(ORMModel):
    name: str | None = None
    address: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None


class ClinicResponse(ClinicBase):
    id: int
    created_at: datetime
    updated_at: datetime
