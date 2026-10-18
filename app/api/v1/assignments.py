from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import Assignment, ProcedureExecution, User
from app.models.enums import AssignmentStatus, AssignmentType, RoleEnum
from app.repositories.assignment_repo import AssignmentRepository
from app.schemas.assignments import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentUpdate,
    ProcedureExecutionCreate,
    ProcedureExecutionResponse,
)
from app.schemas.common import StatusResponse
from app.services.assignment_service import AssignmentService

router = APIRouter(prefix="/assignments", tags=["Назначения и процедуры"])


@router.post(
    "",
    response_model=AssignmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать назначение пациенту (только врач)",
)
async def create_assignment(
    data: AssignmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
) -> Assignment:
    service = AssignmentService(db)
    return await service.create_assignment(current_user, data)


@router.get(
    "/by-record/{record_id}",
    response_model=list[AssignmentResponse],
    summary="Все назначения по медкарте (медперсонал сети, ресепшен)",
)
async def list_assignments(
    record_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
                RoleEnum.RECEPTIONIST,
            )
        ),
    ],
    type_filter: AssignmentType | None = None,
    status_filter: AssignmentStatus | None = None,
) -> list[Assignment]:
    repo = AssignmentRepository(db)
    return await repo.list_for_record(
        record_id=record_id,
        type_=type_filter,
        status=status_filter,
    )


@router.get(
    "/{assignment_id}",
    response_model=AssignmentResponse,
    summary="Детали назначения с историей процедур",
)
async def get_assignment(
    assignment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
                RoleEnum.RECEPTIONIST,
            )
        ),
    ],
) -> Assignment:
    repo = AssignmentRepository(db)
    assignment = await repo.get_detailed(assignment_id)
    if not assignment:
        raise NotFoundError("Назначение не найдено")
    return assignment


@router.patch(
    "/{assignment_id}",
    response_model=AssignmentResponse,
    summary="Редактировать назначение (только врач)",
)
async def update_assignment(
    assignment_id: int,
    data: AssignmentUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
) -> Assignment:
    service = AssignmentService(db)
    return await service.update_assignment(current_user, assignment_id, data)


@router.delete(
    "/{assignment_id}",
    response_model=StatusResponse,
    summary="Удалить назначение (только врач)",
)
async def delete_assignment(
    assignment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
) -> StatusResponse:
    service = AssignmentService(db)
    await service.delete_assignment(current_user, assignment_id)
    return StatusResponse(message="Назначение успешно удалено")


@router.post(
    "/executions",
    response_model=ProcedureExecutionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Отметка о выполнении процедуры (только медсестра)",
)
async def record_execution(
    data: ProcedureExecutionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.NURSE))],
) -> ProcedureExecution:
    service = AssignmentService(db)
    return await service.record_execution(current_user, data)
