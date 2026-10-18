from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_user, require_roles
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import MedicalRecord, MedicalRecordEntry, User
from app.models.enums import RoleEnum
from app.repositories.patient_repo import PatientRepository
from app.schemas.medical_records import (
    MedicalRecordEntryCreate,
    MedicalRecordEntryResponse,
    MedicalRecordResponse,
)
from app.services.medical_record_service import MedicalRecordService

router = APIRouter(prefix="/medical-records", tags=["Электронная медкарта (ЭМК)"])


@router.get(
    "/my",
    response_model=MedicalRecordResponse,
    summary="Моя медкарта (для пациента, после смены временного пароля)",
)
async def get_my_record(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_patient_user)],
) -> MedicalRecord:
    if current_user.role != RoleEnum.PATIENT or not current_user.patient_profile:
        raise PermissionDeniedError("Эндпоинт доступен только пациентам")

    repo = PatientRepository(db)
    record = await repo.get_medical_record(current_user.patient_profile.id)
    if not record:
        raise NotFoundError("Ваша медкарта пока не заведена")
    return record


@router.get(
    "/by-patient/{patient_id}",
    response_model=MedicalRecordResponse,
    summary="Медкарта пациента (для врачей и медсестёр)",
)
async def get_patient_record(
    patient_id: int,
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
) -> MedicalRecord:
    repo = PatientRepository(db)
    record = await repo.get_medical_record(patient_id)
    if not record:
        raise NotFoundError("Медкарта не найдена")
    return record


@router.post(
    "/{record_id}/entries",
    response_model=MedicalRecordEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Внести запись в медкарту по итогам приёма (только врач)",
)
async def add_entry_to_record(
    record_id: int,
    data: MedicalRecordEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.DOCTOR)),
    ],
) -> MedicalRecordEntry:
    service = MedicalRecordService(db)
    return await service.add_entry(current_user, record_id, data)
