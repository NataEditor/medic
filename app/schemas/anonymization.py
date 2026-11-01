from datetime import datetime

from pydantic import Field

from app.models.enums import AnonymizationRequestStatus
from app.schemas.common import ORMModel


class AnonymizationRequestCreate(ORMModel):
    """Заявка пациента на обезличивание своего аккаунта (152-ФЗ)."""

    reason: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Причина обращения (право на забвение)",
    )


class AnonymizationRequestReview(ORMModel):
    """Решение супер-админа по заявке."""

    approve: bool = Field(..., description="True — обезличить, False — отклонить")
    comment: str | None = Field(
        None,
        max_length=2000,
        description="Комментарий с обоснованием решения",
    )


class AnonymizationRequestResponse(ORMModel):
    """Заявка на обезличивание в ответе API."""

    id: int
    patient_id: int
    requested_by_id: int
    reviewed_by_id: int | None = None
    reason: str
    status: AnonymizationRequestStatus
    review_comment: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
