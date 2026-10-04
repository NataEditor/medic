from collections.abc import Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.core.utils import calculate_total_pages

T = TypeVar("T")


class ORMModel(BaseModel):
    """Базовая модель с поддержкой ORM (from_attributes=True)."""

    model_config = ConfigDict(from_attributes=True)


class StatusResponse(BaseModel):
    success: bool = True
    message: str = "Операция выполнена успешно"


class PageParams(BaseModel):
    page: int = Field(default=DEFAULT_PAGE, ge=1, description="Номер страницы")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Элементов на странице",
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    items: Sequence[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def create(
        cls,
        items: Sequence[T],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedResponse[T]":
        """Фабричный метод: автоматически вычисляет total_pages без дублирования формулы."""
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=calculate_total_pages(total, page_size),
        )
