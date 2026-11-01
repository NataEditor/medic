from datetime import datetime

from pydantic import Field

from app.models.enums import PatientRelativeStatus
from app.schemas.common import ORMModel
from app.schemas.users import UserResponse


class PatientRelativeCreate(ORMModel):
    """Заявка пациента на связь с близким (из личного кабинета).

    Пока связь не подтверждена ресепшеном, пациент может записывать
    близкого на приём, но не видит его медданные.
    """

    relative_id: int = Field(..., description="ID карты близкого (пациента)")
    relation: str = Field(
        ...,
        max_length=50,
        description="Кем приходится: child, parent, spouse, other",
    )
    notes: str | None = Field(None, max_length=500, description="Примечание")


class PatientRelativeConfirm(ORMModel):
    """Подтверждение или отклонение связи ресепшеном."""

    approve: bool = Field(..., description="True — подтвердить, False — отклонить")
    notes: str | None = Field(None, max_length=500, description="Какой документ предъявлен")


class PatientRelativeResponse(ORMModel):
    id: int
    patient_id: int
    relative_id: int
    relation: str
    status: PatientRelativeStatus
    confirmed_by_id: int | None = None
    confirmed_at: datetime | None = None
    notes: str | None = None
    confirmed_by: UserResponse | None = None
    created_at: datetime
    updated_at: datetime
