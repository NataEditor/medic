from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import Invoice, Service, User
from app.models.enums import RoleEnum
from app.repositories.base import BaseRepository
from app.repositories.billing_repo import BillingRepository
from app.schemas.billing import (
    ClinicFinancialReport,
    InvoiceResponse,
    PaymentCreate,
    PaymentRefundRequest,
    PaymentResponse,
    ServiceCreate,
    ServiceResponse,
    ServiceUpdate,
)
from app.services.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["Биллинг, касса и бухгалтерия"])


# ----- Прайс-лист услуг -----
@router.get("/services", response_model=list[ServiceResponse], summary="Каталог услуг клиники")
async def list_services(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    only_active: bool = True,
) -> list[Service]:
    repo = BaseRepository(Service, db)
    filters = [Service.is_active.is_(True)] if only_active else None
    items, _ = await repo.list_paginated(limit=500, filters=filters)
    return items


@router.post(
    "/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить услугу в прайс-лист (главврач/бухгалтерия)",
)
async def create_service(
    data: ServiceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.HEAD_DOCTOR, RoleEnum.ACCOUNTANT)),
    ],
) -> Service:
    service = BillingService(db)
    return await service.create_service(data)


@router.patch(
    "/services/{service_id}",
    response_model=ServiceResponse,
    summary="Изменить стоимость/параметры услуги (главврач/бухгалтерия)",
)
async def update_service(
    service_id: int,
    data: ServiceUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[
        User,
        Depends(require_roles(RoleEnum.HEAD_DOCTOR, RoleEnum.ACCOUNTANT)),
    ],
) -> Service:
    service = BillingService(db)
    return await service.update_service(service_id, data)


# ----- Счета и оплаты (ресепшен/касса) -----
@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse, summary="Получить счёт")
async def get_invoice(
    invoice_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Invoice:
    """Просмотр счёта с разграничением доступа.

    Финансовые роли (бухгалтерия, ресепшен, администраторы) видят любой счёт.
    Пациент — только свой. Врачи/медсёстры доступа к чужим счетам не имеют.
    """
    repo = BillingRepository(db)
    inv = await repo.get_invoice_detailed(invoice_id)
    if not inv:
        raise NotFoundError("Счёт не найден")

    role = current_user.role
    finance_roles = (
        RoleEnum.ACCOUNTANT,
        RoleEnum.RECEPTIONIST,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.HEAD_DOCTOR,
    )
    if role in finance_roles:
        return inv
    if role == RoleEnum.PATIENT:
        if current_user.patient_profile and inv.patient_id == current_user.patient_profile.id:
            return inv
        raise PermissionDeniedError("Доступ к чужому счёту запрещён")

    raise PermissionDeniedError("Недостаточно прав для просмотра счёта")


@router.post(
    "/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Приём оплаты по счёту (ресепшен)",
)
async def pay_invoice(
    data: PaymentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.ACCOUNTANT)),
    ],
) -> PaymentResponse:
    service = BillingService(db)
    payment = await service.process_payment(current_user, data)
    return payment


@router.post(
    "/payments/{payment_id}/refund",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Оформить возврат средств по платежу (руководитель/бухгалтерия)",
)
async def refund_payment(
    payment_id: int,
    data: PaymentRefundRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.ACCOUNTANT)),
    ],
) -> PaymentResponse:
    """Возврат создаёт компенсирующую проводку; исходный платёж не меняется.

    Факт возврата фиксируется в журнале аудита с указанием причины.
    """
    service = BillingService(db)
    refund = await service.refund_payment(current_user, payment_id, data)
    return refund


# ----- Финансовые отчёты (бухгалтерия и главврач) -----
@router.get(
    "/reports/financial",
    response_model=ClinicFinancialReport,
    summary="Финансовый отчёт по клинике и расчёт врачей (бухгалтерия)",
)
async def get_financial_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.ACCOUNTANT, RoleEnum.HEAD_DOCTOR)),
    ],
    start_date: date = Query(..., description="Начало периода (ГГГГ-ММ-ДД)"),
    end_date: date = Query(..., description="Конец периода (ГГГГ-ММ-ДД)"),
    clinic_id: int | None = Query(None, description="ID клиники (только для главврача)"),
) -> ClinicFinancialReport:
    service = BillingService(db)
    return await service.generate_financial_report(
        accountant=current_user,
        start_date=start_date,
        end_date=end_date,
        clinic_id=clinic_id,
    )
