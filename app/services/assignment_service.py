from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.models.entities import AnalysisResult, Assignment, ProcedureExecution, User
from app.models.enums import AssignmentStatus, AssignmentType, RoleEnum
from app.repositories.assignment_repo import AssignmentRepository
from app.schemas.assignments import (
    AnalysisResultCreate,
    AssignmentCreate,
    AssignmentUpdate,
    ProcedureExecutionCreate,
)


class AssignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.assignment_repo = AssignmentRepository(session)

    async def create_assignment(self, doctor: User, data: AssignmentCreate) -> Assignment:
        """Только врач может создавать назначения."""
        if doctor.role not in (RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Назначения может создавать только врач")

        assignment = Assignment(
            record_id=data.record_id,
            doctor_id=doctor.id,
            service_id=data.service_id,
            type=data.type,
            title=data.title.strip(),
            dosage=data.dosage.strip() if data.dosage else None,
            frequency=data.frequency.strip() if data.frequency else None,
            duration_days=data.duration_days,
            instructions=data.instructions.strip() if data.instructions else None,
            status=AssignmentStatus.PRESCRIBED,
        )
        return await self.assignment_repo.create(assignment)

    async def update_assignment(
        self,
        doctor: User,
        assignment_id: int,
        data: AssignmentUpdate,
    ) -> Assignment:
        assignment = await self.assignment_repo.get_by_id(assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")

        # Изменять назначение может создавший его врач или главврач
        if doctor.role != RoleEnum.HEAD_DOCTOR and assignment.doctor_id != doctor.id:
            raise PermissionDeniedError("Вы можете редактировать только свои назначения")

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(assignment, key, val)

        await self.session.flush()
        return (await self.assignment_repo.get_detailed(assignment.id)) or assignment

    async def delete_assignment(self, doctor: User, assignment_id: int) -> None:
        assignment = await self.assignment_repo.get_by_id(assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")

        if doctor.role != RoleEnum.HEAD_DOCTOR and assignment.doctor_id != doctor.id:
            raise PermissionDeniedError("Вы можете удалять только свои назначения")

        # Если процедура уже выполнена хотя бы раз — удалять нельзя, только отменять
        if assignment.status == AssignmentStatus.COMPLETED:
            raise BusinessRuleError("Нельзя удалить выполненное назначение. Измените статус")
        await self.assignment_repo.delete(assignment)

    async def record_execution(
        self,
        nurse: User,
        data: ProcedureExecutionCreate,
    ) -> ProcedureExecution:
        """Медсестра отмечает факт выполнения процедуры/манипуляции."""
        if nurse.role not in (RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Отметку о выполнении может вносить только медсестра")

        assignment = await self.assignment_repo.get_by_id(data.assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")
        if assignment.type != AssignmentType.PROCEDURE:
            raise BusinessRuleError("Отметку выполнения можно делать только для процедур")

        execution = ProcedureExecution(
            assignment_id=data.assignment_id,
            nurse_id=nurse.id,
            executed_at=data.executed_at,
            status=data.status,
            materials_used=data.materials_used,
            notes=data.notes,
        )
        saved = await self.assignment_repo.add_execution(execution)

        # Переводим назначение в статус "выполняется" или "выполнено"
        assignment.status = AssignmentStatus.IN_PROGRESS
        await self.session.flush()

        return saved

    async def record_analysis(
        self,
        patient_id: int,
        worker: User | None,
        data: AnalysisResultCreate,
        file_path: str | None = None,
    ) -> AnalysisResult:
        """Сохранение результатов анализа с прикреплением файла."""
        analysis = AnalysisResult(
            assignment_id=data.assignment_id,
            patient_id=patient_id,
            lab_worker_id=worker.id if worker else None,
            title=data.title.strip(),
            summary=data.summary,
            file_path=file_path,
            performed_at=data.performed_at,
        )
        return await self.assignment_repo.add_analysis(analysis)
