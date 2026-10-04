from datetime import datetime

from pydantic import Field

from app.models.enums import (
    AssignmentStatus,
    AssignmentType,
    ProcedureExecutionStatus,
)
from app.schemas.common import ORMModel
from app.schemas.users import UserResponse


class ProcedureExecutionCreate(ORMModel):
    assignment_id: int
    executed_at: datetime
    status: ProcedureExecutionStatus = ProcedureExecutionStatus.COMPLETED
    materials_used: str | None = None
    notes: str | None = None


class ProcedureExecutionResponse(ORMModel):
    id: int
    assignment_id: int
    nurse_id: int
    executed_at: datetime
    status: ProcedureExecutionStatus
    materials_used: str | None = None
    notes: str | None = None
    nurse: UserResponse | None = None
    created_at: datetime


class AnalysisResultCreate(ORMModel):
    assignment_id: int
    title: str = Field(..., max_length=255)
    summary: str | None = None
    performed_at: datetime


class AnalysisResultResponse(ORMModel):
    id: int
    assignment_id: int
    patient_id: int
    lab_worker_id: int | None = None
    title: str
    summary: str | None = None
    file_path: str | None = None
    performed_at: datetime
    created_at: datetime


class AssignmentCreate(ORMModel):
    record_id: int
    service_id: int | None = None
    type: AssignmentType
    title: str = Field(..., max_length=255)
    dosage: str | None = None
    frequency: str | None = None
    duration_days: int | None = Field(None, ge=1, le=365)
    instructions: str | None = None


class AssignmentUpdate(ORMModel):
    title: str | None = None
    dosage: str | None = None
    frequency: str | None = None
    duration_days: int | None = None
    instructions: str | None = None
    status: AssignmentStatus | None = None


class AssignmentResponse(ORMModel):
    id: int
    record_id: int
    doctor_id: int
    service_id: int | None = None
    type: AssignmentType
    title: str
    dosage: str | None = None
    frequency: str | None = None
    duration_days: int | None = None
    instructions: str | None = None
    status: AssignmentStatus
    executions: list[ProcedureExecutionResponse] = []
    analyses: list[AnalysisResultResponse] = []
    created_at: datetime
    updated_at: datetime
