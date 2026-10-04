from datetime import date, datetime

from pydantic import EmailStr, Field, field_validator

from app.core.constants import (
    GENDER_REGEX_PATTERN,
    LONG_STRING_LENGTH,
    MEDIUM_STRING_LENGTH,
    PASSPORT_NUMBER_REGEX_PATTERN,
    PASSPORT_SCAN_URL_TEMPLATE,
    PASSPORT_SERIES_REGEX_PATTERN,
    SHORT_STRING_LENGTH,
)
from app.schemas.common import ORMModel


def _normalize_passport(value: object) -> object:
    """Единая нормализация паспорта: убираем пробелы и дефисы ('12 34 567890' -> '1234')."""
    if isinstance(value, str):
        return value.replace(" ", "").replace("-", "").strip()
    return value


class PatientBase(ORMModel):
    """Общие поля карточки пациента.

    Паспорт (серия + номер) — обязательный естественный ключ пациента.
    Пациент регистрируется по паспорту; телефон необязателен и уникальность
    пациента не определяет.
    """

    first_name: str = Field(..., max_length=MEDIUM_STRING_LENGTH)
    last_name: str = Field(..., max_length=MEDIUM_STRING_LENGTH)
    middle_name: str | None = Field(None, max_length=MEDIUM_STRING_LENGTH)

    passport_series: str = Field(
        ...,
        pattern=PASSPORT_SERIES_REGEX_PATTERN,
        description="Серия паспорта, ровно 4 цифры",
    )
    passport_number: str = Field(
        ...,
        pattern=PASSPORT_NUMBER_REGEX_PATTERN,
        description="Номер паспорта, ровно 6 цифр",
    )

    birth_date: date
    gender: str = Field(
        ...,
        pattern=GENDER_REGEX_PATTERN,
        description="M (мужской) или F (женский)",
    )
    phone: str | None = Field(
        None,
        max_length=SHORT_STRING_LENGTH,
        description="Телефон для напоминаний (не уникален)",
    )
    email: EmailStr | None = None
    snils: str | None = Field(None, max_length=20)
    oms_number: str | None = Field(None, max_length=30)
    address: str | None = Field(None, max_length=LONG_STRING_LENGTH)

    @field_validator("passport_series", "passport_number", mode="before")
    @classmethod
    def normalize_passport(cls, value: object) -> object:
        return _normalize_passport(value)


class PatientCreate(PatientBase):
    pass


class PatientUpdate(ORMModel):
    """Частичное обновление карточки. Паспорт меняется только осознанно."""

    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    passport_series: str | None = Field(None, pattern=PASSPORT_SERIES_REGEX_PATTERN)
    passport_number: str | None = Field(None, pattern=PASSPORT_NUMBER_REGEX_PATTERN)
    birth_date: date | None = None
    gender: str | None = Field(None, pattern=GENDER_REGEX_PATTERN)
    phone: str | None = None
    email: EmailStr | None = None
    snils: str | None = None
    oms_number: str | None = None
    address: str | None = None

    @field_validator("passport_series", "passport_number", mode="before")
    @classmethod
    def normalize_passport(cls, value: object) -> object:
        return _normalize_passport(value)


class PatientResponse(PatientBase):
    id: int
    user_id: int | None = None
    full_name: str
    passport: str = Field(..., description="Паспорт в виде '1234 567890'")
    passport_scan_path: str | None = Field(
        None,
        description="Внутренний путь к скану паспорта (клиенту не отдаётся)",
    )
    passport_scan_uploaded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PatientPassportScanResponse(ORMModel):
    """Ответ при загрузке скана паспорта."""

    patient_id: int
    passport_scan_uploaded_at: datetime
    passport_scan_url: str = Field(
        ...,
        description="Ссылка для получения скана через API (доступ по ролям)",
    )

    @classmethod
    def build(cls, patient_id: int, uploaded_at: datetime) -> "PatientPassportScanResponse":
        return cls(
            patient_id=patient_id,
            passport_scan_uploaded_at=uploaded_at,
            passport_scan_url=PASSPORT_SCAN_URL_TEMPLATE.format(patient_id=patient_id),
        )
