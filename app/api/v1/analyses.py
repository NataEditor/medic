from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_user, require_roles
from app.core.config import settings
from app.core.constants import ALLOWED_ANALYSIS_EXTENSIONS
from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import AnalysisResult, Assignment, User
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
        require_roles(RoleEnum.NURSE, RoleEnum.DOCTOR, RoleEnum.RECEPTIONIST)
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

    # Ресепшен только прикладывает файл (скан/бланк), но не заполняет
    # медицинское содержимое: заключение и показатели — зона медперсонала.
    if current_user.has_role(RoleEnum.RECEPTIONIST) and not current_user.has_any_role(
        RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR
    ):
        if summary:
            raise PermissionDeniedError(
                "Ресепшен может приложить только файл, без заключения"
            )
        if not file or not file.filename:
            raise BusinessRuleError("Ресепшен должен приложить файл результата")

    file_path: str | None = None
    if file and file.filename:
        # Валидация расширения файла
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_ANALYSIS_EXTENSIONS:
            allowed_str = ", ".join(ALLOWED_ANALYSIS_EXTENSIONS)
            raise BusinessRuleError(f"Разрешены только файлы формата: {allowed_str}")
        # Сохранение файла на диск
        upload_dir = Path(settings.media_dir) / "analyses" / str(patient_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(datetime.now(UTC).timestamp())
        safe_name = f"{timestamp}_{file.filename.replace(' ', '_')}"
        target_path = upload_dir / safe_name

        content = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise BusinessRuleError(f"Файл превышает {settings.max_upload_size_mb} МБ")

        target_path.write_bytes(content)
        file_path = f"/media/analyses/{patient_id}/{safe_name}"

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
