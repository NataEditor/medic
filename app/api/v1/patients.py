from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.constants import DEFAULT_PAGE, DEFAULT_PAGE_SIZE
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import Patient, User
from app.models.enums import RoleEnum
from app.repositories.patient_repo import PatientRepository
from app.schemas.common import PaginatedResponse
from app.schemas.patients import PatientCreate, PatientResponse, PatientUpdate
from app.services.patient_service import PatientService

router = APIRouter(prefix="/patients", tags=["Пациенты"])


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация пациента и открытие ЭМК (ресепшен)",
)
async def create_patient(
    data: PatientCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR)),
    ],
) -> Patient:
    service = PatientService(db)
    return await service.create_patient_with_record(data)


@router.get(
    "",
    response_model=PaginatedResponse[PatientResponse],
    summary="Поиск и список пациентов (персонал)",
)
async def list_patients(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
                RoleEnum.ACCOUNTANT,
            )
        ),
    ],
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> PaginatedResponse[PatientResponse]:
    repo = PatientRepository(db)
    items, total = await repo.search(
        query=q,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return PaginatedResponse.create(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Карточка пациента по ID",
)
async def get_patient(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
            )
        ),
    ],
) -> Patient:
    repo = PatientRepository(db)
    patient = await repo.get_by_id(patient_id)
    if not patient:
        raise NotFoundError("Пациент не найден")
    return patient


@router.patch(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Редактирование данных пациента (ресепшен)",
)
async def update_patient(
    patient_id: int,
    data: PatientUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.RECEPTIONIST))],
) -> Patient:
    service = PatientService(db)
    return await service.update_patient(patient_id, data)
