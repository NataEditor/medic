from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ensure_analysis_access,
    ensure_clinic_scope,
    get_current_patient_user,
    get_current_user,
    record_pdn_view,
    require_roles,
)
from app.core.config import settings
from app.core.constants import ALLOWED_ANALYSIS_EXTENSIONS
from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import AnalysisResult, Assignment, Patient, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository
from app.schemas.assignments import AnalysisResultCreate, AnalysisResultResponse
from app.services.assignment_service import AssignmentService

router = APIRouter(prefix="/analyses", tags=["Анализы и диагностика"])


@router.post(
    "/upload",
    response_model=AnalysisResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузка результата анализа с файлом (медперсонал)",
)
async def upload_analysis(
    assignment_id: Annotated[int, Form()],
    patient_id: Annotated[int, Form()],
    title: Annotated[str, Form()],
    summary: Annotated[str | None, Form()] = None,
    performed_at: Annotated[datetime | None, Form()] = None,
    file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_roles(
            RoleEnum.NURSE,
            RoleEnum.DOCTOR,
            RoleEnum.HEAD_DOCTOR,
            RoleEnum.RECEPTIONIST,
        )
    ),
) -> AnalysisResult:
    # Валидация существования назначения
    assignment = await db.get(Assignment, assignment_id)
    if not assignment:
        raise NotFoundError("Назначение не найдено")

    # Проверяем, что назначение и пациент согласованы (иначе анализ «прилипал» бы
    # к чужой карте при произвольных переданных ID).
    if assignment.record_id is None:
        raise BusinessRuleError("У назначения отсутствует медкарта")
    from app.models.entities import MedicalRecord

    record = await db.get(MedicalRecord, assignment.record_id)
    if not record or record.patient_id != patient_id:
        raise BusinessRuleError("Пациент не соответствует указанному назначению")

    # Изоляция по клинике: загружать анализ можно только по назначению,
    # выданному в клинике сотрудника (иначе ресепшен/медсестра чужого
    # филиала приложили бы файл к пациенту другой клиники).
    ensure_clinic_scope(current_user, assignment.clinic_id)

    # Ресепшен только прикладывает файл (скан/бланк), но не заполняет
    # медицинское содержимое: заключение и показатели — зона медперсонала.
    if current_user.has_role(RoleEnum.RECEPTIONIST) and not current_user.has_any_role(
        RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR
    ):
        if summary:
            raise PermissionDeniedError("Ресепшен может приложить только файл, без заключения")
        if not file or not file.filename:
            raise BusinessRuleError("Ресепшен должен приложить файл результата")

    file_path: str | None = None
    if file and file.filename:
        # Валидация расширения файла
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_ANALYSIS_EXTENSIONS:
            allowed_str = ", ".join(ALLOWED_ANALYSIS_EXTENSIONS)
            raise BusinessRuleError(f"Разрешены только файлы формата: {allowed_str}")
        # Файл кладём в ЗАКРЫТОЕ хранилище: Nginx его не раздаёт,
        # скачивание идёт только через API с проверкой прав и аудитом.
        upload_dir = Path(settings.private_media_dir) / "analyses" / str(patient_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(datetime.now(UTC).timestamp())
        safe_name = f"{timestamp}_{Path(file.filename).name.replace(' ', '_')}"
        target_path = upload_dir / safe_name

        content = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise BusinessRuleError(f"Файл превышает {settings.max_upload_size_mb} МБ")

        target_path.write_bytes(content)
        # Храним относительный путь внутри закрытого хранилища
        file_path = f"analyses/{patient_id}/{safe_name}"
    service = AssignmentService(db)
    dto = AnalysisResultCreate(
        assignment_id=assignment_id,
        title=title,
        summary=summary,
        performed_at=performed_at or datetime.now(UTC),
    )
    return await service.record_analysis(
        patient_id=patient_id,
        worker=current_user,
        data=dto,
        file_path=file_path,
    )


@router.get(
    "/my",
    response_model=list[AnalysisResultResponse],
    summary="Мои анализы (для пациента)",
)
async def get_my_analyses(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_patient_user)],
) -> list[AnalysisResult]:
    if not current_user.has_role(RoleEnum.PATIENT) or not current_user.patient_profile:
        raise PermissionDeniedError("Доступно только пациентам")
    repo = BaseRepository(AnalysisResult, db)
    items, _ = await repo.list_paginated(
        offset=0,
        limit=100,
        filters=[AnalysisResult.patient_id == current_user.patient_profile.id],
    )
    return items


@router.get(
    "/{analysis_id}/file",
    summary="Скачать файл результата анализа (по правам, с аудитом ПДн)",
    response_class=FileResponse,
)
async def download_analysis_file(
    analysis_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """Файл отдаётся только через API из закрытого хранилища.

    Nginx каталог private_media не раздаёт, поэтому прямой URL невозможен.
    Пациент получает только свой файл, медперсонал — по правам на карту.
    """
    analysis = await db.get(AnalysisResult, analysis_id)
    if analysis is None:
        raise NotFoundError("Результат анализа не найден")
    if not analysis.file_path:
        raise NotFoundError("У результата анализа нет файла")

    if current_user.has_role(RoleEnum.PATIENT):
        patient = await _get_patient_for_user(db, current_user.id)
        if patient is None or patient.id != analysis.patient_id:
            raise PermissionDeniedError("Пациент может скачать только свой файл")
    else:
        await ensure_analysis_access(db, current_user, analysis.patient_id)

    # Аудит доступа к ПДн (152-ФЗ)
    await record_pdn_view(
        db=db,
        user=current_user,
        entity_name="analysis_result",
        entity_id=analysis.id,
        resource="analysis_file",
    )

    path = Path(settings.private_media_dir) / analysis.file_path
    if not path.is_file():
        raise NotFoundError("Файл не найден в хранилище")

    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/octet-stream",
    )


@router.get(
    "/by-patient/{patient_id}",
    response_model=list[AnalysisResultResponse],
    summary="Анализы пациента (медперсонал: врач, медсестра, главврач)",
)
async def get_patient_analyses(
    patient_id: int,
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
) -> list[AnalysisResult]:
    """Результаты анализов пациента.

    Ресепшен сюда не допускается: он прикладывает файл, но не читает
    медицинское содержимое. Врач — только «по касанию».
    """
    await ensure_analysis_access(db, current_user, patient_id)

    repo = BaseRepository(AnalysisResult, db)
    items, _ = await repo.list_paginated(
        offset=0,
        limit=100,
        filters=[AnalysisResult.patient_id == patient_id],
    )
    return items


async def _get_patient_for_user(db: AsyncSession, user_id: int) -> Patient | None:
    """Карта пациента по учётной записи (без ленивой загрузки связи)."""
    stmt = select(Patient).where(Patient.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()
