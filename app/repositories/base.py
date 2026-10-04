from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Базовый CRUD-репозиторий для сущностей SQLAlchemy."""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, item_id: int) -> ModelType | None:
        return await self.session.get(self.model, item_id)

    async def list_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        filters: Sequence[Any] | None = None,
    ) -> tuple[list[ModelType], int]:
        stmt = select(self.model)
        count_stmt = select(func.count()).select_from(self.model)

        if filters:
            for f in filters:
                stmt = stmt.where(f)
                count_stmt = count_stmt.where(f)

        stmt = stmt.offset(offset).limit(limit)
        items_res = await self.session.execute(stmt)
        count_res = await self.session.execute(count_stmt)

        return list(items_res.scalars().all()), int(count_res.scalar_one())

    async def create(self, instance: ModelType) -> ModelType:
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def delete(self, instance: ModelType) -> None:
        await self.session.delete(instance)
        await self.session.flush()

    async def delete_by_id(self, item_id: int) -> bool:
        stmt = delete(self.model).where(self.model.id == item_id)  # type: ignore[attr-defined]
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.rowcount > 0
