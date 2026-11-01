from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    EMR_READ_ROLES,
    EMR_WRITE_ROLES,
    ensure_medical_access,
    get_current_patient_user,
    record_pdn_view,
    require_roles,
)
from app.core.access import resolve_patient_id_from_record
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
    if not current_user.has_role(RoleEnum.PATIENT) or not current_user.patient_profile:
        raise PermissionDeniedError("Эндпоинт доступен только пациентам")

    repo = PatientRepository(db)
    record = await repo.get_medical_record(current_user.patient_profile.id)
    if not record:
        raise NotFoundError("Ваша медкарта пока не заведена")
    return record


@router.get(
    "/by-patient/{patient_id}",
    response_model=MedicalRecordResponse,
    summary="Медкарта пациента (врач, главврач, супер-админ)",
)
async def get_patient_record(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(*EMR_READ_ROLES)),
    ],
) -> MedicalRecord:
    """Врачебные записи ЭМК. Врач — только «по касанию» (есть приём).

    Медсестра, админ клиники и ресепшен сюда не допускаются: они работают
    с карточкой, назначениями и анализами, но не с историей болезни.
    """
    await ensure_medical_access(db, current_user, patient_id)
    repo = PatientRepository(db)
    record = await repo.get_medical_record(patient_id)
    if not record:
        raise NotFoundError("Медкарта не найдена")

    # Аудит доступа к ПДн (152-ФЗ): фиксируем факт просмотра ЭМК
    await record_pdn_view(
        db=db,
        user=current_user,
        entity_name="medical_record",
        entity_id=record.id,
        resource="medical_record",
    )
    return record


@router.post(
    "/{record_id}/entries",
    response_model=MedicalRecordEntryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Внести запись в медкарту по итогам приёма (врач, главврач)",
)
async def add_entry_to_record(
    record_id: int,
    data: MedicalRecordEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(*EMR_WRITE_ROLES)),
    ],
) -> MedicalRecordEntry:
    """Врач вносит запись только в карту пациента, с которым есть приём.

    Проверка «по касанию» (ensure_medical_access) не даёт записать осмотр
    в чужую ЭМК по одному лишь record_id.
    """
    patient_id = await resolve_patient_id_from_record(db, record_id)
    await ensure_medical_access(db, current_user, patient_id)
    service = MedicalRecordService(db)
    return await service.add_entry(current_user, record_id, data)
