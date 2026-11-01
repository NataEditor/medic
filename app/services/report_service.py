from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import ONE_HUNDRED_PERCENT
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.entities import (
    Appointment,
    Assignment,
    Encounter,
    ProcedureExecution,
    Service,
    User,
)
from app.models.enums import (
    AppointmentStatus,
    AssignmentType,
    EncounterStatus,
    RoleEnum,
)
from app.schemas.reports import (
    DoctorProductivityItem,
    DoctorProductivityReport,
    SalaryItem,
    SalaryReport,
)


class ReportService:
    """Отчёты производительности врачей для бухгалтерии.

    Три счётчика за период по каждому врачу:
    1. Закрытые Encounter (doctor_id) — сколько приёмов провёл.
    2. Назначенные процедуры (Assignment.doctor_id) — сколько назначил.
    3. Выполненные лично (ProcedureExecution.performed_by_id) — сколько сделал сам.

    Третья цифра — ключевая: врач выполняет УЗИ, инъекции, блокады лично,
    и это должно попадать в расчёт зарплаты.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def doctor_productivity(
        self,
        actor: User,
        doctor_id: int,
        start_date: date,
        end_date: date,
    ) -> DoctorProductivityReport:
        """Показатели одного врача за период."""
        self._check_access(actor, doctor_id)

        # Загружаем профиль врача сразу (иначе lazy-load в async-контексте)
        stmt = select(User).options(selectinload(User.doctor_profile)).where(User.id == doctor_id)
        doctor = (await self.session.execute(stmt)).scalar_one_or_none()
        if doctor is None:
            raise NotFoundError("Врач не найден")
        item = await self._build_item(doctor, start_date, end_date)
        return DoctorProductivityReport(
            start_date=start_date,
            end_date=end_date,
            clinic_id=doctor.clinic_id,
            doctors=[item],
            total_closed_encounters=item.closed_encounters,
            total_prescribed_procedures=item.prescribed_procedures,
            total_performed_procedures=item.performed_procedures,
        )

    async def doctors_summary(
        self,
        actor: User,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> DoctorProductivityReport:
        """Сводка по всем врачам (бухгалтерия — вся сеть, главврач — своя клиника).

        Админ клиники сюда не входит: производительность врачей — это
        медицинская аналитика, а не управление людьми.
        """
        if not actor.has_any_role(
            RoleEnum.ACCOUNTANT,
            RoleEnum.HEAD_DOCTOR,
            RoleEnum.SUPER_ADMIN,
        ):
            raise PermissionDeniedError("Сводка доступна бухгалтерии и главврачу")

        # Бухгалтерия видит всю сеть; главврач — только свою клинику
        target_clinic = clinic_id
        if actor.has_role(RoleEnum.HEAD_DOCTOR) and not actor.has_role(RoleEnum.SUPER_ADMIN):
            target_clinic = actor.clinic_id
        stmt = (
            select(User)
            .options(selectinload(User.doctor_profile))
            .where(User.has_any_role_sql(RoleEnum.DOCTOR))
        )
        if target_clinic is not None:
            stmt = stmt.where(User.clinic_id == target_clinic)
        doctors = list((await self.session.execute(stmt)).scalars().all())

        items = [await self._build_item(doc, start_date, end_date) for doc in doctors]

        return DoctorProductivityReport(
            start_date=start_date,
            end_date=end_date,
            clinic_id=target_clinic,
            doctors=items,
            total_closed_encounters=sum(i.closed_encounters for i in items),
            total_prescribed_procedures=sum(i.prescribed_procedures for i in items),
            total_performed_procedures=sum(i.performed_procedures for i in items),
        )

    async def my_productivity(
        self,
        actor: User,
        start_date: date,
        end_date: date,
    ) -> DoctorProductivityReport:
        """Свои показатели — врач смотрит только себя."""
        if not actor.has_role(RoleEnum.DOCTOR):
            raise PermissionDeniedError("Доступно только врачам")
        return await self.doctor_productivity(actor, actor.id, start_date, end_date)

    # ----- Внутренние -----
    @staticmethod
    def _check_access(actor: User, doctor_id: int) -> None:
        """Врач — только себя; бухгалтерия и главврач — любого."""
        if actor.has_any_role(
            RoleEnum.ACCOUNTANT,
            RoleEnum.HEAD_DOCTOR,
            RoleEnum.SUPER_ADMIN,
        ):
            return
        if actor.has_role(RoleEnum.DOCTOR) and actor.id == doctor_id:
            return
        raise PermissionDeniedError("Нет доступа к показателям этого врача")

    async def salaries(
        self,
        actor: User,
        start_date: date,
        end_date: date,
        clinic_id: int | None = None,
    ) -> SalaryReport:
        """Ведомость зарплат врачей за период.

        Права: только бухгалтерия (🌐) и супер-админ (🌐). Главврач
        зарплаты не видит — это финансовая зона, а не медицинская.
        """
        if not actor.has_any_role(RoleEnum.ACCOUNTANT, RoleEnum.SUPER_ADMIN):
            raise PermissionDeniedError("Ведомость зарплат доступна бухгалтерии и супер-админу")

        stmt = (
            select(User)
            .options(selectinload(User.doctor_profile))
            .where(User.has_any_role_sql(RoleEnum.DOCTOR))
        )
        if clinic_id is not None:
            stmt = stmt.where(User.clinic_id == clinic_id)
        doctors = list((await self.session.execute(stmt)).scalars().all())

        items = [await self._build_salary_item(doc, start_date, end_date) for doc in doctors]

        return SalaryReport(
            start_date=start_date,
            end_date=end_date,
            clinic_id=clinic_id,
            doctors=items,
            total_appointments_revenue=str(sum(Decimal(i.appointments_revenue) for i in items)),
            total_procedures_revenue=str(sum(Decimal(i.procedures_revenue) for i in items)),
            total_salary=str(sum(Decimal(i.total_salary) for i in items)),
        )

    async def _build_salary_item(
        self,
        doctor: User,
        start_date: date,
        end_date: date,
    ) -> SalaryItem:
        """Расчёт зарплаты одного врача: приёмы + процедуры по его ставкам."""
        dt_start = datetime.combine(start_date, time.min)
        dt_end = datetime.combine(end_date, time.max)

        # Выручка по приёмам врача
        appointments_revenue = Decimal(
            str(
                (
                    await self.session.execute(
                        select(func.coalesce(func.sum(Service.price), 0))
                        .select_from(Appointment)
                        .join(Service, Appointment.service_id == Service.id, isouter=True)
                        .where(
                            Appointment.doctor_id == doctor.id,
                            Appointment.status == AppointmentStatus.COMPLETED,
                            Appointment.scheduled_at >= dt_start,
                            Appointment.scheduled_at <= dt_end,
                        )
                    )
                ).scalar_one()
            )
        )

        # Выручка по процедурам, которые врач выполнил лично
        procedures_revenue = Decimal(
            str(
                (
                    await self.session.execute(
                        select(func.coalesce(func.sum(Service.price), 0))
                        .select_from(ProcedureExecution)
                        .join(
                            Assignment,
                            ProcedureExecution.assignment_id == Assignment.id,
                        )
                        .join(Service, Assignment.service_id == Service.id, isouter=True)
                        .where(
                            ProcedureExecution.performed_by_id == doctor.id,
                            ProcedureExecution.performed_at >= dt_start,
                            ProcedureExecution.performed_at <= dt_end,
                        )
                    )
                ).scalar_one()
            )
        )

        profile = doctor.doctor_profile
        appt_percent = profile.appointment_fee_percent if profile else Decimal("30.00")
        proc_percent = profile.procedure_fee_percent if profile else Decimal("15.00")

        appointment_salary = (appointments_revenue * appt_percent / ONE_HUNDRED_PERCENT).quantize(
            Decimal("0.01")
        )
        procedure_salary = (procedures_revenue * proc_percent / ONE_HUNDRED_PERCENT).quantize(
            Decimal("0.01")
        )

        return SalaryItem(
            doctor_id=doctor.id,
            doctor_name=doctor.full_name,
            specialization=profile.specialization if profile else None,
            clinic_id=doctor.clinic_id,
            appointment_fee_percent=str(appt_percent),
            procedure_fee_percent=str(proc_percent),
            appointments_revenue=str(appointments_revenue),
            procedures_revenue=str(procedures_revenue),
            appointment_salary=str(appointment_salary),
            procedure_salary=str(procedure_salary),
            total_salary=str(appointment_salary + procedure_salary),
        )

    async def _build_item(
        self,
        doctor: User,
        start_date: date,
        end_date: date,
    ) -> DoctorProductivityItem:
        dt_start = datetime.combine(start_date, time.min)
        dt_end = datetime.combine(end_date, time.max)

        # 1. Закрытые приёмы (Encounter)
        encounters_stmt = select(func.count(Encounter.id)).where(
            Encounter.doctor_id == doctor.id,
            Encounter.status == EncounterStatus.CLOSED,
            Encounter.opened_at >= dt_start,
            Encounter.opened_at <= dt_end,
        )
        closed_encounters = int((await self.session.execute(encounters_stmt)).scalar_one())

        # 2. Назначенные процедуры
        prescribed_stmt = select(func.count(Assignment.id)).where(
            Assignment.doctor_id == doctor.id,
            Assignment.type == AssignmentType.PROCEDURE,
            Assignment.created_at >= dt_start,
            Assignment.created_at <= dt_end,
        )
        prescribed = int((await self.session.execute(prescribed_stmt)).scalar_one())

        # 3. Выполненные лично процедуры (ключевая третья цифра)
        performed_stmt = select(func.count(ProcedureExecution.id)).where(
            ProcedureExecution.performed_by_id == doctor.id,
            ProcedureExecution.performed_at >= dt_start,
            ProcedureExecution.performed_at <= dt_end,
        )
        performed = int((await self.session.execute(performed_stmt)).scalar_one())

        # Выручка по приёмам врача
        revenue_stmt = (
            select(func.coalesce(func.sum(Service.price), 0))
            .select_from(Appointment)
            .join(Service, Appointment.service_id == Service.id, isouter=True)
            .where(
                Appointment.doctor_id == doctor.id,
                Appointment.status == AppointmentStatus.COMPLETED,
                Appointment.scheduled_at >= dt_start,
                Appointment.scheduled_at <= dt_end,
            )
        )
        appointments_revenue = Decimal(str((await self.session.execute(revenue_stmt)).scalar_one()))

        # Ставка врача для расчёта зарплаты
        profile = doctor.doctor_profile
        fee_percent = profile.appointment_fee_percent if profile else Decimal("30.00")
        salary = (appointments_revenue * fee_percent / ONE_HUNDRED_PERCENT).quantize(
            Decimal("0.01")
        )

        return DoctorProductivityItem(
            doctor_id=doctor.id,
            doctor_name=doctor.full_name,
            specialization=profile.specialization if profile else None,
            clinic_id=doctor.clinic_id,
            closed_encounters=closed_encounters,
            prescribed_procedures=prescribed,
            performed_procedures=performed,
            appointments_revenue=str(appointments_revenue),
            procedures_revenue="0.00",
            calculated_salary=str(salary),
        )
