from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT,
    DEFAULT_DOCTOR_PROCEDURE_FEE_PERCENT,
)
from app.core.utils import build_full_name, utcnow
from app.db.base import Base, TimestampMixin
from app.models.enums import (
    AppointmentStatus,
    AssignmentStatus,
    AssignmentType,
    PaymentMethod,
    PaymentStatus,
    ProcedureExecutionStatus,
    RoleEnum,
)

# Для совместимости с SQLite в unit-тестах: используем Text, если JSONB недоступен
JSONType = JSONB().with_variant(Text, "sqlite")


class Clinic(Base, TimestampMixin):
    """Клиника / филиал сети."""

    __tablename__ = "clinics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="clinic")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="clinic")


class User(Base, TimestampMixin):
    """Пользователь системы (врач, пациент, медсестра, главврач, ресепшен, бухгалтер)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clinic_id: Mapped[int | None] = mapped_column(
        ForeignKey("clinics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(
        Enum(RoleEnum, name="user_role_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    clinic: Mapped[Clinic | None] = relationship(back_populates="users")
    doctor_profile: Mapped["DoctorProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    patient_profile: Mapped["Patient | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def full_name(self) -> str:
        return build_full_name(self.last_name, self.first_name, self.middle_name)


class DoctorProfile(Base, TimestampMixin):
    """Профиль врача: специальность, кабинет, проценты для расчёта зарплаты."""

    __tablename__ = "doctor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    specialization: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cabinet_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    appointment_fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT, nullable=False
    )
    procedure_fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=DEFAULT_DOCTOR_PROCEDURE_FEE_PERCENT, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="doctor_profile")


class Patient(Base, TimestampMixin):
    """Карточка пациента."""

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)  # "M" / "F"
    phone: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snils: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)
    oms_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="patient_profile")
    medical_record: Mapped["MedicalRecord | None"] = relationship(
        back_populates="patient", uselist=False, cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")

    @property
    def full_name(self) -> str:
        return build_full_name(self.last_name, self.first_name, self.middle_name)


class MedicalRecord(Base, TimestampMixin):
    """Электронная медицинская карта (ЭМК)."""

    __tablename__ = "medical_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    record_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    blood_group: Mapped[str | None] = mapped_column(String(10), nullable=True)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    chronic_diseases: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    patient: Mapped[Patient] = relationship(back_populates="medical_record")
    entries: Mapped[list["MedicalRecordEntry"]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="record", cascade="all, delete-orphan"
    )


class MedicalRecordEntry(Base, TimestampMixin):
    """Запись врача в медицинской карте по итогам приёма."""

    __tablename__ = "medical_record_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    complaints: Mapped[str] = mapped_column(Text, nullable=False)  # Жалобы
    anamnesis: Mapped[str | None] = mapped_column(Text, nullable=True)  # Анамнез
    objective_status: Mapped[str | None] = mapped_column(Text, nullable=True)  # Объективный статус
    diagnosis_icd10: Mapped[str] = mapped_column(String(20), nullable=False)  # Код МКБ-10
    diagnosis_text: Mapped[str] = mapped_column(Text, nullable=False)  # Развёрнутый диагноз
    treatment_plan: Mapped[str | None] = mapped_column(Text, nullable=True)  # План лечения

    record: Mapped[MedicalRecord] = relationship(back_populates="entries")
    doctor: Mapped[User] = relationship()


class Service(Base, TimestampMixin):
    """Каталог медицинских услуг (консультации, процедуры, анализы)."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_APPOINTMENT_DURATION_MINUTES, nullable=False
    )
    is_procedure: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Appointment(Base, TimestampMixin):
    """Приём пациента врачом."""

    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_APPOINTMENT_DURATION_MINUTES, nullable=False
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status_enum", native_enum=False),
        default=AppointmentStatus.SCHEDULED,
        nullable=False,
        index=True,
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    clinic: Mapped[Clinic] = relationship(back_populates="appointments")
    patient: Mapped[Patient] = relationship(back_populates="appointments")
    doctor: Mapped[User] = relationship(foreign_keys=[doctor_id])
    service: Mapped[Service | None] = relationship()
    invoice: Mapped["Invoice | None"] = relationship(back_populates="appointment", uselist=False)

    __table_args__ = (Index("ix_appointments_doctor_schedule", "doctor_id", "scheduled_at"),)


class Assignment(Base, TimestampMixin):
    """Назначение врача (лекарство, процедура, анализ)."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[AssignmentType] = mapped_column(
        Enum(AssignmentType, name="assignment_type_enum", native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    dosage: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Дозировка
    frequency: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 2 раза в день
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Курс (дней)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, name="assignment_status_enum", native_enum=False),
        default=AssignmentStatus.PRESCRIBED,
        nullable=False,
        index=True,
    )

    record: Mapped[MedicalRecord] = relationship(back_populates="assignments")
    doctor: Mapped[User] = relationship()
    service: Mapped[Service | None] = relationship()
    executions: Mapped[list["ProcedureExecution"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AnalysisResult"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class ProcedureExecution(Base, TimestampMixin):
    """Отметка медсестры о выполнении процедуры/манипуляции."""

    __tablename__ = "procedure_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nurse_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ProcedureExecutionStatus] = mapped_column(
        Enum(ProcedureExecutionStatus, name="procedure_execution_status_enum", native_enum=False),
        default=ProcedureExecutionStatus.COMPLETED,
        nullable=False,
    )
    materials_used: Mapped[str | None] = mapped_column(Text, nullable=True)  # Расходники
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    assignment: Mapped[Assignment] = relationship(back_populates="executions")
    nurse: Mapped[User] = relationship()


class AnalysisResult(Base, TimestampMixin):
    """Результаты лабораторного анализа."""

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lab_worker_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # Заключение
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # PDF / скан
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assignment: Mapped[Assignment] = relationship(back_populates="analyses")
    patient: Mapped[Patient] = relationship()
    lab_worker: Mapped[User | None] = relationship()


class Invoice(Base, TimestampMixin):
    """Счёт на оплату (приём, процедуры, анализы)."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status_enum", native_enum=False),
        default=PaymentStatus.PENDING,
        nullable=False,
        index=True,
    )

    appointment: Mapped[Appointment | None] = relationship(back_populates="invoice")
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class Payment(Base, TimestampMixin):
    """Факт проведения оплаты (ресепшен/касса)."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method_enum", native_enum=False),
        nullable=False,
    )
    cashier_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    invoice: Mapped[Invoice] = relationship(back_populates="payments")
    cashier: Mapped[User] = relationship()


class AuditLog(Base):
    """Журнал аудита действий (врачебная тайна, изменения назначений и карт)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changes: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-строка diff'а
    ip_address: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True,
    )
