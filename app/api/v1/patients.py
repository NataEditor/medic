from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    record_audit_log,
    require_patient_creator,
    require_roles,
)
from app.core.constants import (
    ALLOWED_PASSPORT_SCAN_EXTENSIONS,
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    PASSPORT_SCAN_MEDIA_TYPE,
    TEMP_PASSWORD_TTL_HOURS,
)
from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import Patient, User
from app.models.enums import RoleEnum
from app.repositories.patient_repo import PatientRepository
from app.schemas.auth import PatientAccessResponse
from app.schemas.common import PaginatedResponse
from app.schemas.encounters import PatientStatusChange
from app.schemas.patients import (
    PatientCreate,
    PatientPassportScanResponse,
    PatientResponse,
    PatientUpdate,
)
from app.services.patient_service import PatientService

router = APIRouter(prefix="/patients", tags=["Пациенты"])


@router.post(
    "/{patient_id}/grant-access",
    response_model=PatientAccessResponse,
    summary="Выдать пациенту доступ в личный кабинет (ресепшен)",
)
async def grant_patient_access(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.RECEPTIONIST))],
    login_phone: Annotated[
        str | None,
        Body(embed=True, description="Телефон для входа; иначе берётся из карты пациента"),
    ] = None,
) -> PatientAccessResponse:
    """Создаёт учётную запись пациента и отправляет временный пароль по SMS.

    Открытый пароль в ответе НЕ возвращается — он уходит только пациенту
    по SMS. После первого входа пациент обязан сменить пароль.
    """
    service = PatientService(db)
    patient = await service.patient_repo.get_by_id(patient_id)
    if not patient:
        raise NotFoundError("Пациент не найден")

    phone = login_phone or patient.phone
    if not phone:
        raise BusinessRuleError("У пациента не указан телефон — укажите его при выдаче доступа")

    _, user = await service.grant_portal_access(patient_id, phone)
    await db.commit()

    return PatientAccessResponse(
        patient_id=patient_id,
        user_id=user.id,
        login_phone=user.phone,
        temp_password_ttl_hours=TEMP_PASSWORD_TTL_HOURS,
    )


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация пациента по паспорту и открытие ЭМК (ресепшен)",
)
async def create_patient(
    data: PatientCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_patient_creator())],
) -> Patient:
    """Создание карты — только регистратура и выше.

    Врач карту не создаёт: профиль с паспортом заводит тот, кто проверил
    документ (152-ФЗ). Уникальность — пара серия+номер паспорта.
    """
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
    """Поиск работает по ФИО, телефону и паспорту (серии/номеру)."""
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


@router.post(
    "/{patient_id}/passport-scan",
    response_model=PatientPassportScanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузка скана паспорта (jpg) и привязка к карте пациента",
)
async def upload_passport_scan(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.DOCTOR)),
    ],
    file: UploadFile = File(..., description="Скан паспорта в формате JPG"),
) -> PatientPassportScanResponse:
    if not file.filename:
        raise BusinessRuleError("Файл не передан")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_PASSPORT_SCAN_EXTENSIONS:
        allowed = ", ".join(ALLOWED_PASSPORT_SCAN_EXTENSIONS)
        raise BusinessRuleError(f"Скан паспорта принимается только в формате: {allowed}")

    service = PatientService(db)
    patient = await service.attach_passport_scan(patient_id, await file.read())

    return PatientPassportScanResponse.build(
        patient_id=patient.id,
        uploaded_at=patient.passport_scan_uploaded_at,
    )


@router.get(
    "/{patient_id}/passport-scan",
    summary="Получить скан паспорта пациента (только персонал)",
    response_class=FileResponse,
)
async def get_passport_scan(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.RECEPTIONIST,
                RoleEnum.DOCTOR,
                RoleEnum.NURSE,
            )
        ),
    ],
) -> FileResponse:
    """Скан отдаётся только через API из закрытого хранилища — по ролям и с аудитом."""
    service = PatientService(db)
    scan_path = await service.get_passport_scan(patient_id)

    # Аудит доступа к ПДн (152-ФЗ): фиксируем, кто открыл скан паспорта
    await record_audit_log(
        db=db,
        user=current_user,
        action="view_passport_scan",
        entity_name="patient",
        entity_id=patient_id,
    )

    return FileResponse(
        path=scan_path,
        media_type=PASSPORT_SCAN_MEDIA_TYPE,
        filename=f"passport_{patient_id}.jpg",
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


@router.get(
    "/my/card",
    response_model=PatientResponse,
    summary="Моя карточка пациента (для пациента)",
)
async def get_my_patient_card(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Patient:
    """Пациент видит только свою карточку; чужой скан паспорта ему недоступен."""
    if not current_user.has_role(RoleEnum.PATIENT) or not current_user.patient_profile:
        raise PermissionDeniedError("Эндпоинт доступен только пациентам")

    repo = PatientRepository(db)
    patient = await repo.get_by_user_id(current_user.id)
    if not patient:
        raise NotFoundError("Ваша карточка не найдена")
    return patient


# ═══════════════════════════════════════════════════════════════
# Жизненный цикл карты: активация, архивация, обезличивание
# ═══════════════════════════════════════════════════════════════


@router.post(
    "/{patient_id}/activate",
    response_model=PatientResponse,
    summary="Активировать черновик карты (ресепшен сверил паспорт)",
)
async def activate_patient(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_patient_creator())],
) -> Patient:
    """Активация черновика: создаётся ЭМК, карта становится рабочей."""
    service = PatientService(db)
    return await service.activate_patient(patient_id)


@router.post(
    "/{patient_id}/archive",
    response_model=PatientResponse,
    summary="Архивировать карту (главврач или супер-админ)",
)
async def archive_patient(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
    data: PatientStatusChange | None = None,
) -> Patient:
    """Архивация: новые приёмы не создаются, записи сохраняются."""
    service = PatientService(db)
    return await service.archive_patient(patient_id, data.reason if data else None)


@router.post(
    "/{patient_id}/anonymize",
    response_model=PatientResponse,
    summary="Обезличить карту (152-ФЗ: ПДн скрыты, медзаписи сохранены)",
)
async def anonymize_patient(
    patient_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.HEAD_DOCTOR))],
    data: PatientStatusChange | None = None,
) -> Patient:
    """Обезличивание ≠ удаление: карту невозможно связать с человеком."""
    service = PatientService(db)
    return await service.anonymize_patient(patient_id, data.reason if data else None)
