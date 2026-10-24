from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ensure_medical_access, require_roles
from app.db.session import get_db
from app.models.entities import Encounter, User
from app.models.enums import RoleEnum
from app.schemas.encounters import EncounterClose, EncounterOpen, EncounterResponse
from app.services.encounter_service import EncounterService

router = APIRouter(prefix="/encounters", tags=["Медицинские обращения (Encounter)"])


@router.post(
    "",
    response_model=EncounterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Открыть обращение в начале приёма (только врач)",
)
async def open_encounter(
    data: EncounterOpen,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
) -> Encounter:
    """Врач открывает обращение из слота расписания или по walk-in пациенту."""
    service = EncounterService(db)
    return await service.open_encounter(current_user, data)


@router.post(
    "/{encounter_id}/close",
    response_model=EncounterResponse,
    summary="Закрыть обращение (только лечащий врач)",
)
async def close_encounter(
    encounter_id: int,
    data: EncounterClose,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
) -> Encounter:
    """После закрытия записи в обращение не редактируются."""
    service = EncounterService(db)
    return await service.close_encounter(current_user, encounter_id)


@router.get(
    "/{encounter_id}",
    response_model=EncounterResponse,
    summary="Детали обращения",
)
async def get_encounter(
    encounter_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.DOCTOR, RoleEnum.NURSE)),
    ],
) -> Encounter:
    service = EncounterService(db)
    encounter = await service.get_encounter(encounter_id)
    await ensure_medical_access(db, current_user, encounter.patient_id)
    return encounter


@router.get(
    "/by-patient/{patient_id}",
    response_model=list[EncounterResponse],
    summary="История обращений пациента",
)
async def list_patient_encounters(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.DOCTOR, RoleEnum.NURSE)),
    ],
) -> list[Encounter]:
    await ensure_medical_access(db, current_user, patient_id)
    service = EncounterService(db)
    return await service.list_patient_encounters(patient_id)
