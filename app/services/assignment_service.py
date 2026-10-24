from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.models.entities import (
    AnalysisResult,
    Assignment,
    Invoice,
    MedicalRecord,
    ProcedureExecution,
    Service,
    User,
)
from app.models.enums import (
    AssignmentStatus,
    AssignmentType,
    PaymentStatus,
    RoleEnum,
)
from app.repositories.assignment_repo import AssignmentRepository
from app.schemas.assignments import (
    AnalysisResultCreate,
    AssignmentCreate,
    AssignmentUpdate,
    ProcedureExecutionCreate,
)

# Типы назначений, за которые выставляется счёт (анализы, процедуры, диагностика).
BILLABLE_ASSIGNMENT_TYPES: frozenset[AssignmentType] = frozenset(
    {
        AssignmentType.ANALYSIS,
        AssignmentType.PROCEDURE,
        AssignmentType.DIAGNOSTIC,
    }
)


class AssignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.assignment_repo = AssignmentRepository(session)

    async def create_assignment(self, doctor: User, data: AssignmentCreate) -> Assignment:
        """Создаёт назначение и (при наличии цены) счёт на оплату.

        Счёт выставляется сразу при назначении — пациент оплачивает его
        на ресепшене. Это закрывает «серые» бесплатные процедуры/анализы.
        """
        if not doctor.has_any_role(RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR):
            raise PermissionDeniedError("Назначения может создавать только врач")

        # Клиника: из запроса, иначе — клиника врача (обязательна для счёта)
        clinic_id = data.clinic_id or doctor.clinic_id
        if clinic_id is None:
            raise BusinessRuleError("Не удалось определить клинику для назначения")

        assignment = Assignment(
            record_id=data.record_id,
            clinic_id=clinic_id,
            doctor_id=doctor.id,
            service_id=data.service_id,
            type=data.type,
            title=data.title.strip(),
            dosage=data.dosage.strip() if data.dosage else None,
            frequency=data.frequency.strip() if data.frequency else None,
            duration_days=data.duration_days,
            instructions=data.instructions.strip() if data.instructions else None,
            status=AssignmentStatus.PRESCRIBED,
        )
        created = await self.assignment_repo.create(assignment)

        # Счёт за платное назначение (анализ/процедура/диагностика с ценой в прайсе)
        await self._create_invoice_for_assignment(created, clinic_id)

        return created

    async def _create_invoice_for_assignment(
        self,
        assignment: Assignment,
        clinic_id: int,
    ) -> Invoice | None:
        """Создаёт счёт по назначению, если у услуги есть положительная цена."""
        if assignment.type not in BILLABLE_ASSIGNMENT_TYPES or assignment.service_id is None:
            return None

        service = await self.session.get(Service, assignment.service_id)
        if service is None or service.price <= 0:
            return None

        # Определяем пациента по медкарте (для счёта нужен patient_id)
        record = await self.session.get(MedicalRecord, assignment.record_id)
        if record is None:
            raise BusinessRuleError("Медкарта для назначения не найдена")

        invoice = Invoice(
            assignment_id=assignment.id,
            patient_id=record.patient_id,
            clinic_id=clinic_id,
            amount=service.price,
            status=PaymentStatus.PENDING,
        )
        self.session.add(invoice)
        await self.session.flush()
        return invoice
    async def update_assignment(
        self,
        doctor: User,
        assignment_id: int,
        data: AssignmentUpdate,
    ) -> Assignment:
        assignment = await self.assignment_repo.get_by_id(assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")

        # Изменять назначение может создавший его врач или главврач
        if not doctor.has_role(RoleEnum.HEAD_DOCTOR) and assignment.doctor_id != doctor.id:
            raise PermissionDeniedError("Вы можете редактировать только свои назначения")

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            setattr(assignment, key, val)

        await self.session.flush()
        return (await self.assignment_repo.get_detailed(assignment.id)) or assignment

    async def delete_assignment(self, doctor: User, assignment_id: int) -> None:
        assignment = await self.assignment_repo.get_by_id(assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")

        if not doctor.has_role(RoleEnum.HEAD_DOCTOR) and assignment.doctor_id != doctor.id:
            raise PermissionDeniedError("Вы можете удалять только свои назначения")

        # Если процедура уже выполнена хотя бы раз — удалять нельзя, только отменять
        if assignment.status == AssignmentStatus.COMPLETED:
            raise BusinessRuleError("Нельзя удалить выполненное назначение. Измените статус")
        await self.assignment_repo.delete(assignment)

    async def record_execution(
        self,
        performer: User,
        data: ProcedureExecutionCreate,
    ) -> ProcedureExecution:
        """Отметка о выполнении процедуры: медсестра — своё, врач — своё.

        Врач выполняет часть манипуляций лично (УЗИ, инъекции, блокады).
        Бухгалтерии нужна третья цифра отчёта — «сколько процедур врач
        выполнил сам», поэтому врач тоже ставит отметку.

        Ограничение: нельзя «нарисовать» чужое выполнение.
        - медсестра отмечает только свои выполнения;
        - врач — только свои (где он назначил и выполнил сам);
        - главврач/супер-админ — любые (контроль и исправление ошибок).

        Если клиника работает по предоплате (REQUIRE_PAYMENT_BEFORE_PROCEDURE),
        процедура выполняется только по оплаченному счёту.
        """
        is_nurse = performer.has_role(RoleEnum.NURSE)
        is_doctor = performer.has_role(RoleEnum.DOCTOR)
        is_overseer = performer.has_any_role(RoleEnum.HEAD_DOCTOR, RoleEnum.SUPER_ADMIN)

        if not (is_nurse or is_doctor or is_overseer):
            raise PermissionDeniedError(
                "Отметку о выполнении вносит медсестра или врач"
            )

        assignment = await self.assignment_repo.get_by_id(data.assignment_id)
        if not assignment:
            raise NotFoundError("Назначение не найдено")
        if assignment.type != AssignmentType.PROCEDURE:
            raise BusinessRuleError("Отметку выполнения можно делать только для процедур")

        # Защита от «рисования» чужого выполнения
        if not is_overseer:
            if is_doctor and not is_nurse:
                # Врач отмечает только процедуры, которые назначил сам
                if assignment.doctor_id != performer.id:
                    raise PermissionDeniedError(
                        "Врач может отмечать выполнение только своих назначений"
                    )
            elif is_nurse and not is_doctor:
                # Медсестра не может отметить выполнение врачебной манипуляции
                # как своё, если назначение ведёт врач и он сам её выполняет.
                # Разрешаем: медсестра отмечает процедуры, назначенные врачом
                # (это её штатная работа), но performed_by всегда = она сама.
                pass

        # Проверка оплаты: процедура только по оплаченному счёту
        invoice_id: int | None = None
        if settings.require_payment_before_procedure:
            invoice = await self._get_paid_invoice_for_assignment(assignment.id)
            if invoice is None:
                raise BusinessRuleError(
                    "Процедура не оплачена. Пациент должен оплатить счёт на ресепшене."
                )
            invoice_id = invoice.id

        execution = ProcedureExecution(
            assignment_id=data.assignment_id,
            # nurse_id остаётся обязательным полем (историческая схема):
            # для врача дублируем его id, семантика — «кто выполнил».
            nurse_id=performer.id,
            performed_by_id=performer.id,
            executed_at=data.executed_at,
            performed_at=data.executed_at,
            status=data.status,
            materials_used=data.materials_used,
            notes=data.notes,
            invoice_id=invoice_id,
        )
        saved = await self.assignment_repo.add_execution(execution)

        # Переводим назначение в статус "выполняется" или "выполнено"
        assignment.status = AssignmentStatus.IN_PROGRESS
        await self.session.flush()

        return saved

    async def _get_paid_invoice_for_assignment(self, assignment_id: int) -> Invoice | None:
        """Возвращает оплаченный счёт по назначению (или None)."""
        stmt = select(Invoice).where(
            Invoice.assignment_id == assignment_id,
            Invoice.status == PaymentStatus.PAID,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def record_analysis(
        self,
        patient_id: int,
        worker: User | None,
        data: AnalysisResultCreate,
        file_path: str | None = None,
    ) -> AnalysisResult:
        """Сохранение результатов анализа с прикреплением файла."""
        analysis = AnalysisResult(
            assignment_id=data.assignment_id,
            patient_id=patient_id,
            lab_worker_id=worker.id if worker else None,
            title=data.title.strip(),
            summary=data.summary,
            file_path=file_path,
            performed_at=data.performed_at,
        )
        return await self.assignment_repo.add_analysis(analysis)
