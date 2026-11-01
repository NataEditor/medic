from datetime import datetime

from pydantic import Field

from app.schemas.common import ORMModel


class AuditLogResponse(ORMModel):
    """Запись журнала аудита.

    Журнал неизменяем: записи только добавляются, правка и удаление
    запрещены на уровне модели (нет updated_at).
    """

    id: int
    user_id: int | None = Field(None, description="Кто выполнил действие")
    user_full_name: str | None = Field(None, description="ФИО пользователя (для читаемости отчёта)")
    action: str = Field(..., description="Тип действия, например VIEW_PDN")
    entity_name: str = Field(..., description="Затронутая сущность")
    entity_id: int | None = Field(None, description="ID затронутого объекта")
    changes: str | None = Field(None, description="JSON-строка с деталями изменения")
    ip_address: str | None = Field(None, description="IP-адрес инициатора")
    created_at: datetime = Field(..., description="Время события (UTC)")


class AuditLogFilter(ORMModel):
    """Параметры фильтрации журнала аудита."""

    action: str | None = Field(None, description="Точное совпадение типа действия")
    entity_name: str | None = Field(None, description="Имя сущности")
    entity_id: int | None = Field(None, description="ID объекта")
    user_id: int | None = Field(None, description="ID пользователя")
    only_pdn: bool = Field(
        False,
        description="Только просмотры персональных данных (VIEW_PDN)",
    )
