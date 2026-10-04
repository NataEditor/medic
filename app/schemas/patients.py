from datetime import date, datetime

from pydantic import EmailStr, Field

from app.schemas.common import ORMModel


class PatientBase(ORMModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    birth_date: date
    gender: str = Field(..., pattern=r"^[MF]$", description="M (мужской) или F (женский)")
    phone: str = Field(..., max_length=50)
    email: EmailStr | None = None
    snils: str | None = Field(None, max_length=20)
    oms_number: str | None = Field(None, max_length=30)
    address: str | None = Field(None, max_length=500)


class PatientCreate(PatientBase):
    pass


class PatientUpdate(ORMModel):
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    birth_date: date | None = None
    gender: str | None = Field(None, pattern=r"^[MF]$")
    phone: str | None = None
    email: EmailStr | None = None
    snils: str | None = None
    oms_number: str | None = None
    address: str | None = None


class PatientResponse(PatientBase):
    id: int
    user_id: int | None = None
    full_name: str
    created_at: datetime
    updated_at: datetime
