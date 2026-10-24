from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.db.session import get_db
from app.models.entities import Invoice, Service, User
from app.models.enums import RefundRequestStatus, RoleEnum
from app.repositories.base import BaseRepository
from app.repositories.billing_repo import BillingRepository
from app.schemas.billing import (
    ClinicFinancialReport,
    InvoiceResponse,
    PaymentCreate,
    PaymentRefundRequest,
    PaymentResponse,
    RefundRequestCreate,
    RefundRequestResponse,
    RefundRequestReview,
    ReversalCreate,
    ServiceCreate,
    ServiceResponse,
    ServiceUpdate,
)
from app.services.billing_service import BillingService
from app.services.refund_service import RefundService

router = APIRouter(prefix="/billing", tags=["Биллинг, касса и бухгалтерия"])

# ----- Прайс-лист услуг -----
@router.get(
    "/services",
    response_model=list[ServiceResponse],
    summary="Каталог услуг клиники (доступен и пациентам)",
)
async def list_services(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    only_active: bool = True,
) -> list[Service]:
    """Прайс-лист видят все авторизованные, включая пациента.

    Пациенту показываем только активные услуги: архивные позиции
    (снятые с продажи) ему не нужны и вводят в заблуждение.
    """
    repo = BaseRepository(Service, db)

    # Пациент всегда видит только активный прайс
    if current_user.has_role(RoleEnum.PATIENT):
        only_active = True

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

    finance_roles = (
        RoleEnum.ACCOUNTANT,
        RoleEnum.RECEPTIONIST,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.HEAD_DOCTOR,
    )
    if current_user.has_any_role(*finance_roles):
        return inv
    if current_user.has_role(RoleEnum.PATIENT):
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


# ═══════════════════════════════════════════════════════════════
# Двойной контроль возвратов: заявка → подтверждение бухгалтерией
# ═══════════════════════════════════════════════════════════════


@router.post(
    "/refund-requests",
    response_model=RefundRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заявку на возврат (касса/ресепшен)",
)
async def create_refund_request(
    data: RefundRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_roles(RoleEnum.RECEPTIONIST, RoleEnum.ACCOUNTANT)),
    ],
) -> RefundRequestResponse:
    """Деньги не двигаются: заявка ждёт решения бухгалтерии."""
    service = RefundService(db)
    return await service.create_refund_request(current_user, data)


@router.get(
    "/refund-requests",
    response_model=list[RefundRequestResponse],
    summary="Список заявок на возврат (бухгалтерия)",
)
async def list_refund_requests(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_roles(RoleEnum.ACCOUNTANT))],
    status_filter: RefundRequestStatus | None = Query(None, alias="status"),
) -> list[RefundRequestResponse]:
    service = RefundService(db)
    return await service.list_refund_requests(status_filter)


@router.post(
    "/refund-requests/{request_id}/review",
    response_model=RefundRequestResponse,
    summary="Подтвердить или отклонить заявку (только бухгалтерия)",
)
async def review_refund_request(
    request_id: int,
    data: RefundRequestReview,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.ACCOUNTANT))],
) -> RefundRequestResponse:
    """При подтверждении создаётся сторно-проводка; оригинал не меняется."""
    service = RefundService(db)
    return await service.review_refund_request(current_user, request_id, data)


@router.post(
    "/refund-requests/{request_id}/cancel",
    response_model=RefundRequestResponse,
    summary="Отозвать заявку до рассмотрения (автор заявки)",
)
async def cancel_refund_request(
    request_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RefundRequestResponse:
    service = RefundService(db)
    return await service.cancel_refund_request(current_user, request_id)


@router.post(
    "/payments/{payment_id}/reverse",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Сторно-проводка (только бухгалтерия, оригинал сохраняется)",
)
async def reverse_payment(
    payment_id: int,
    data: ReversalCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(require_roles(RoleEnum.ACCOUNTANT))],
) -> PaymentResponse:
    """Компенсирующая проводка: оригинал не удаляется и не меняется."""
    service = RefundService(db)
    return await service.create_reversal(current_user, payment_id, data)
