from datetime import date, datetime

from pydantic import EmailStr, Field

from app.core.constants import (
    GENDER_REGEX_PATTERN,
    LONG_STRING_LENGTH,
    MEDIUM_STRING_LENGTH,
    SHORT_STRING_LENGTH,
)
from app.schemas.common import ORMModel


class PatientBase(ORMModel):
    first_name: str = Field(..., max_length=MEDIUM_STRING_LENGTH)
    last_name: str = Field(..., max_length=MEDIUM_STRING_LENGTH)
    middle_name: str | None = Field(None, max_length=MEDIUM_STRING_LENGTH)
    birth_date: date
    gender: str = Field(
        ...,
        pattern=GENDER_REGEX_PATTERN,
        description="M (мужской) или F (женский)",
    )
    phone: str = Field(..., max_length=SHORT_STRING_LENGTH)
    email: EmailStr | None = None
    snils: str | None = Field(None, max_length=20)
    oms_number: str | None = Field(None, max_length=30)
    address: str | None = Field(None, max_length=LONG_STRING_LENGTH)


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
