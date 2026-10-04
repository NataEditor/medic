from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Базовая модель с поддержкой ORM (from_attributes=True)."""

    model_config = ConfigDict(from_attributes=True)


class StatusResponse(BaseModel):
    success: bool = True
    message: str = "Операция выполнена успешно"


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, description="Номер страницы")
    page_size: int = Field(default=20, ge=1, le=100, description="Элементов на странице")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
