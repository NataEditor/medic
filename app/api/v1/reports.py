from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import RoleEnum
from app.schemas.reports import DoctorProductivityReport
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["Отчёты"])


@router.get(
    "/doctor-productivity",
    response_model=DoctorProductivityReport,
    summary="Производительность врача за период (приёмы, назначения, выполнения)",
)
async def doctor_productivity(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    doctor_id: int = Query(..., description="ID врача"),
    from_date: date = Query(..., alias="from", description="Начало периода"),
    to_date: date = Query(..., alias="to", description="Конец периода"),
) -> DoctorProductivityReport:
    """Три счётчика: закрытые приёмы, назначенные процедуры, выполненные лично.

    Права: бухгалтерия/руководство — любой врач, врач — только себя.
    """
    service = ReportService(db)
    return await service.doctor_productivity(current_user, doctor_id, from_date, to_date)


@router.get(
    "/doctors-summary",
    response_model=DoctorProductivityReport,
    summary="Сводка по всем врачам за период (бухгалтерия, руководство)",
)
async def doctors_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(
            require_roles(
                RoleEnum.ACCOUNTANT,
                RoleEnum.HEAD_DOCTOR,
                RoleEnum.CLINIC_ADMIN,
            )
        ),
    ],
    from_date: date = Query(..., alias="from", description="Начало периода"),
    to_date: date = Query(..., alias="to", description="Конец периода"),
    clinic_id: int | None = Query(None, description="Фильтр по клинике"),
) -> DoctorProductivityReport:
    """Бухгалтерия видит всю сеть, главврач/админ — только свою клинику."""
    service = ReportService(db)
    return await service.doctors_summary(current_user, from_date, to_date, clinic_id)


@router.get(
    "/my-productivity",
    response_model=DoctorProductivityReport,
    summary="Мои показатели за период (врач)",
)
async def my_productivity(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.DOCTOR))],
    from_date: date = Query(..., alias="from", description="Начало периода"),
    to_date: date = Query(..., alias="to", description="Конец периода"),
) -> DoctorProductivityReport:
    """Врач видит только свои показатели."""
    service = ReportService(db)
    return await service.my_productivity(current_user, from_date, to_date)
