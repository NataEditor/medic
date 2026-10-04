from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.entities import AnalysisResult, Assignment, ProcedureExecution
from app.models.enums import AssignmentStatus, AssignmentType
from app.repositories.base import BaseRepository


class AssignmentRepository(BaseRepository[Assignment]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Assignment, session)

    async def get_detailed(self, assignment_id: int) -> Assignment | None:
        stmt = (
            select(Assignment)
            .options(
                selectinload(Assignment.doctor),
                selectinload(Assignment.service),
                selectinload(Assignment.executions).selectinload(ProcedureExecution.nurse),
                selectinload(Assignment.analyses),
            )
            .where(Assignment.id == assignment_id)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_for_record(
        self,
        record_id: int,
        type_: AssignmentType | None = None,
        status: AssignmentStatus | None = None,
    ) -> list[Assignment]:
        stmt = (
            select(Assignment)
            .options(
                selectinload(Assignment.doctor),
                selectinload(Assignment.service),
                selectinload(Assignment.executions).selectinload(ProcedureExecution.nurse),
                selectinload(Assignment.analyses),
            )
            .where(Assignment.record_id == record_id)
        )
        if type_:
            stmt = stmt.where(Assignment.type == type_)
        if status:
            stmt = stmt.where(Assignment.status == status)

        stmt = stmt.order_by(Assignment.created_at.desc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def add_execution(self, execution: ProcedureExecution) -> ProcedureExecution:
        self.session.add(execution)
        await self.session.flush()
        await self.session.refresh(execution)
        return execution

    async def add_analysis(self, analysis: AnalysisResult) -> AnalysisResult:
        self.session.add(analysis)
        await self.session.flush()
        await self.session.refresh(analysis)
        return analysis
