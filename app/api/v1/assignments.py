from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    EMR_WRITE_ROLES,
    ensure_patient_card_access,
    require_roles,
)
from app.core.access import resolve_patient_id_from_assignment
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import Assignment, MedicalRecord, ProcedureExecution, User
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
    current_user: Annotated[User, Depends(require_roles(*EMR_WRITE_ROLES))],
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
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
                RoleEnum.HEAD_DOCTOR,
            )
        ),
    ],
    type_filter: AssignmentType | None = None,
    status_filter: AssignmentStatus | None = None,
) -> list[Assignment]:
    """Назначения по медкарте.

    Ресепшен назначения не видит: это медицинские данные.
    Врач — только «по касанию» (есть приём с пациентом).
    """
    record = await db.get(MedicalRecord, record_id)
    if not record:
        raise NotFoundError("Медкарта не найдена")

    await ensure_patient_card_access(db, current_user, record.patient_id)
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
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
                RoleEnum.HEAD_DOCTOR,
            )
        ),
    ],
) -> Assignment:
    """Детали назначения. Врач — только «по касанию» (есть приём с пациентом)."""
    patient_id = await resolve_patient_id_from_assignment(db, assignment_id)
    await ensure_patient_card_access(db, current_user, patient_id)
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
    current_user: Annotated[User, Depends(require_roles(*EMR_WRITE_ROLES))],
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
    current_user: Annotated[User, Depends(require_roles(*EMR_WRITE_ROLES))],
) -> StatusResponse:
    service = AssignmentService(db)
    await service.delete_assignment(current_user, assignment_id)
    return StatusResponse(message="Назначение успешно удалено")


@router.post(
    "/executions",
    response_model=ProcedureExecutionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Отметка о выполнении процедуры (медсестра — своё, врач — своё)",
)
async def record_execution(
    data: ProcedureExecutionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.NURSE,
                RoleEnum.DOCTOR,
                RoleEnum.HEAD_DOCTOR,
            )
        ),
    ],
) -> ProcedureExecution:
    """Врач отмечает процедуры, выполненные им лично (УЗИ, инъекции, блокады).

    Это нужно бухгалтерии для третьей цифры отчёта — «сколько процедур
    врач выполнил сам». Отметить чужое выполнение нельзя.
    """
    service = AssignmentService(db)
    return await service.record_execution(current_user, data)
