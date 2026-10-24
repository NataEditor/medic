import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

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
    Time,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import (
    DEFAULT_APPOINTMENT_DURATION_MINUTES,
    DEFAULT_DOCTOR_APPOINTMENT_FEE_PERCENT,
    DEFAULT_DOCTOR_PROCEDURE_FEE_PERCENT,
    LONG_STRING_LENGTH,
    MEDIUM_STRING_LENGTH,
    PASSPORT_NUMBER_LENGTH,
    PASSPORT_SERIES_LENGTH,
    SHORT_STRING_LENGTH,
    TITLE_STRING_LENGTH,
)
from app.core.utils import build_full_name, utcnow
from app.db.base import Base, TimestampMixin
from app.models.enums import (
    AppointmentStatus,
    AssignmentStatus,
    AssignmentType,
    EncounterStatus,
    PatientStatus,
    PaymentMethod,
    PaymentStatus,
    ProcedureExecutionStatus,
    RoleEnum,
)

# Для совместимости с SQLite в unit-тестах: используем Text, если JSONB недоступен
JSONType = JSONB().with_variant(Text, "sqlite")


class StringListType(TypeDecorator):
    """Список строк, хранимый как JSON.

    На PostgreSQL — JSONB, на SQLite — TEXT с JSON-сериализацией.
    Нужен, чтобы мультиролевая модель (users.roles) работала в обоих движках:
    SQLite не умеет биндить Python-список напрямую.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return json.dumps(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, str):
            return json.loads(value)
        return list(value)


class Clinic(Base, TimestampMixin):
    """Клиника или филиал медицинской сети.

    Роль в системе:
    Центральный организационный узел. Все сотрудники (кроме главного врача,
    обладающего сквозным доступом) и приёмы привязаны к конкретной клинике.

    Поля:
    - id: Уникальный суррогатный идентификатор клиники.
    - name: Полное наименование клиники/филиала (например, 'Медицинский центр на Озерковской).
    - code: Уникальный мнемонический код филиала (например, 'CENTRAL', 'NORTH').
    - address: Фактический юридический и физический адрес.
    - phone: Контактный телефон регистратуры.
    - email: Официальный email для корреспонденции.
    - is_active: Флаг активности (при False запись пациентов и приёмы блокируются).

    Связи:
    - users: Список сотрудников, закреплённых за клиникой.
    - appointments: Все приёмы, проведённые или запланированные в этой клинике.
    """

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
    """Пользователь системы МИС (аккаунт для входа).

    Роль в системе:
    Единая модель аутентификации для всех ролей: врачей, пациентов,
    главного врача, медсестёр, администраторов ресепшена и бухгалтерии.

    Поля:
    - id: Уникальный идентификатор пользователя.
    - clinic_id: Филиал клиники (SET NULL при удалении клиники; у главврача может быть None).
    - email: Уникальный логин для входа.
    - phone: Контактный телефон для SMS/2FA.
    - hashed_password: Хеш пароля по алгоритму Argon2id.
    - role: Роль в системе (RoleEnum), определяющая права доступа (RBAC).
    - first_name, last_name, middle_name: ФИО сотрудника или пациента.
    - is_active: Флаг блокировки учётной записи (при увольнении сотрудника).

    Свойства:
    - full_name: Сформированная строка 'Фамилия Имя Отчество' (через utils.build_full_name).

    Связи:
    - clinic: Прикреплённый филиал клиники.
    - doctor_profile: 1-к-1 профиль врача (если role == DOCTOR).
    - patient_profile: 1-к-1 профиль пациента (если role == PATIENT).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clinic_id: Mapped[int | None] = mapped_column(
        ForeignKey("clinics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Мультиролевая модель: у одного человека может быть несколько ролей
    # (врач + главврач, админ + ресепшен). Права суммируются.
    # Хранится как JSON-массив строк, например ["doctor", "head_doctor"].
    roles: Mapped[list[str]] = mapped_column(
        StringListType(),
        nullable=False,
        default=list,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # --- Первый вход в ЛК пациента по временному паролю ---
    # True, если пароль выдан ресепшеном/SMS и требует обязательной смены при входе.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    temp_password_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    @property
    def role(self) -> RoleEnum:
        """Основная роль (первая в списке) — для обратной совместимости.

        Новый код должен использовать has_role()/has_any_role().
        """
        if not self.roles:
            return RoleEnum.PATIENT
        return RoleEnum(self.roles[0])

    @role.setter
    def role(self, value: RoleEnum | str) -> None:
        """Сеттер для обратной совместимости: User(role=RoleEnum.DOCTOR).

        Устанавливает единственную роль. Для нескольких ролей используйте roles.
        """
        raw = value.value if isinstance(value, RoleEnum) else str(value)
        self.roles = [raw]

    @property
    def role_enums(self) -> list[RoleEnum]:
        """Все роли пользователя в виде перечисления."""
        return [RoleEnum(r) for r in self.roles]

    def has_role(self, role: RoleEnum) -> bool:
        """Есть ли у пользователя конкретная роль."""
        return role.value in self.roles

    def has_any_role(self, *roles: RoleEnum) -> bool:
        """Есть ли хотя бы одна из перечисленных ролей."""
        return any(r.value in self.roles for r in roles)

    @staticmethod
    def has_any_role_sql(*roles: RoleEnum) -> Any:
        """SQL-условие: у пользователя есть хотя бы одна из ролей.

        Работает и на PostgreSQL (JSONB), и на SQLite (TEXT с JSON).
        Используется в запросах вместо User.role.in_(...).
        """
        from sqlalchemy import String, cast, or_

        return or_(
            *[
                cast(User.roles, String).like(f'%"{role.value}"%')
                for role in roles
            ]
        )


class DoctorProfile(Base, TimestampMixin):
    """Профессиональный профиль врача и финансовые ставки.

    Роль в системе:
    Расширяет модель User дополнительными данными, необходимыми для
    формирования расписания и расчёта заработной платы бухгалтерией.

    Поля:
    - id: Уникальный идентификатор профиля.
    - user_id: Ссылка на User (1-к-1, каскадное удаление при удалении пользователя).
    - specialization: Специальность (Терапевт, Кардиолог, Хирург и т.д.).
    - cabinet_number: Номер постоянного кабинета в клинике.
    - appointment_fee_percent: Процент врача от стоимости проведённого приёма (по умолчанию 30%).
    - procedure_fee_percent: Процент врача от проведённых им процедур (по умолчанию 15%).

    Связи:
    - user: Учётная запись врача.
    """

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


class DoctorSchedule(Base, TimestampMixin):
    """Шаблон рабочей смены врача по дню недели.

    Роль в системе:
    Задаёт регулярный график врача: в какие дни недели и часы он принимает
    пациентов, когда у него обеденный перерыв и в каком кабинете.
    Из этих шаблонов расписание на конкретную дату рассчитывается «на лету»
    (слоты не хранятся в БД — их занимают записи Appointment).

    Поля:
    - id: Идентификатор смены.
    - doctor_id: Врач, которому принадлежит смена (каскадное удаление с профилем).
    - clinic_id: Филиал, в котором работает врач в эту смену.
    - weekday: День недели (0 = понедельник ... 6 = воскресенье).
    - start_time: Время начала смены (например, 09:00).
    - end_time: Время окончания смены (например, 18:00).
    - break_start: Начало обеденного перерыва (может быть пустым).
    - break_end: Конец обеденного перерыва (может быть пустым).
    - cabinet_number: Кабинет приёма в рамках этой смены.
    - is_active: Флаг активности смены (для временного отключения без удаления).

    Ограничения:
    - Гарантируется, что смена врача в один день недели не дублируется (по doctor_id + weekday).
    """

    __tablename__ = "doctor_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Пн ... 6=Вс
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    cabinet_number: Mapped[str | None] = mapped_column(String(SHORT_STRING_LENGTH), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    doctor: Mapped[User] = relationship()
    clinic: Mapped[Clinic] = relationship()

    __table_args__ = (UniqueConstraint("doctor_id", "weekday", name="uq_doctor_schedule_weekday"),)


class TimeOff(Base, TimestampMixin):
    """Отсутствие врача: отпуск, больничный, обучение (конкретный период).

    Роль в системе:
    Временно исключает врача из расписания на заданный интервал дат.
    В отличие от DoctorSchedule (регулярный шаблон), TimeOff перекрывает
    конкретные календарные даты: в этот период врач пациента не принимает.

    Поля:
    - id: Идентификатор периода отсутствия.
    - doctor_id: Отсутствующий врач (каскадное удаление с профилем).
    - start_date: Первый день отсутствия (включительно).
    - end_date: Последний день отсутствия (включительно).
    - reason: Причина (отпуск, больничный и т.п.).
    """

    __tablename__ = "time_offs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(TITLE_STRING_LENGTH), nullable=True)

    doctor: Mapped[User] = relationship()


class Patient(Base, TimestampMixin):
    """Карточка пациента (персональные данные и идентификация).

    Роль в системе:
    Основная медицинская сущность учёта граждан. Пациент идентифицируется
    по паспорту (серия 4 цифры + номер 6 цифр). Номер телефона используется
    для напоминаний и связи, но не является уникальным (один телефон может
    быть у членов одной семьи).

    Поля:
    - id: Уникальный внутренний идентификатор пациента.
    - user_id: Связь с учётной записью User (опционально, если пациент пользуется ЛК).
    - first_name, last_name, middle_name: ФИО пациента.
    - passport_series: Серия паспорта РФ (ровно 4 цифры, часть уникального ключа).
    - passport_number: Номер паспорта РФ (ровно 6 цифр, часть уникального ключа).
    - passport_scan_path: Внутренний путь к скану паспорта в private_media.
    - passport_scan_uploaded_at: Дата и время загрузки скана.
    - birth_date: Дата рождения (обязательна для расчёта возраста и дозировок).
    - gender: Пол пациента ('M' — мужской, 'F' — женский).
    - phone: Контактный телефон (не уникален).
    - email: Электронная почта для отправки бланков анализов.
    - snils: СНИЛС (11 цифр, уникален при наличии).
    - oms_number: Номер полиса обязательного медицинского страхования (ОМС).
    - address: Адрес фактического проживания.

    Ограничения:
    - UniqueConstraint('passport_series', 'passport_number'): исключает дублирование карт.

    Свойства:
    - full_name: 'Фамилия Имя Отчество'.
    - passport: Отформатированная строка '1234 567890'.

    Связи:
    - medical_record: 1-к-1 электронная медкарта (создаётся автоматически).
    - appointments: История приёмов пациента.
    """

    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    first_name: Mapped[str] = mapped_column(String(MEDIUM_STRING_LENGTH), nullable=False)
    last_name: Mapped[str] = mapped_column(String(MEDIUM_STRING_LENGTH), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(MEDIUM_STRING_LENGTH), nullable=True)

    # --- Паспортные данные: серия + номер образуют уникальный ключ ---
    passport_series: Mapped[str] = mapped_column(
        String(PASSPORT_SERIES_LENGTH), nullable=False, index=True
    )
    passport_number: Mapped[str] = mapped_column(
        String(PASSPORT_NUMBER_LENGTH), nullable=False, index=True
    )
    # Скан паспорта (jpg) лежит в закрытом хранилище private_media, Nginx его не отдаёт
    passport_scan_path: Mapped[str | None] = mapped_column(
        String(LONG_STRING_LENGTH), nullable=True
    )
    passport_scan_uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Жизненный цикл карты ---
    # draft: создана онлайн-записью, паспорт ещё не сверен.
    # active: ресепшен сверил паспорт, ЭМК работает.
    # archived: карта закрыта, новые приёмы не создаются.
    # anonymized: ПДн скрыты (152-ФЗ), медзаписи сохранены.
    status: Mapped[PatientStatus] = mapped_column(
        Enum(PatientStatus, name="patient_status_enum", native_enum=False),
        default=PatientStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)  # "M" / "F"
    # Телефон нужен для SMS-напоминаний, но уникальность пациента не определяет
    phone: Mapped[str | None] = mapped_column(
        String(SHORT_STRING_LENGTH), nullable=True, index=True
    )
    email: Mapped[str | None] = mapped_column(String(TITLE_STRING_LENGTH), nullable=True)
    snils: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True, index=True)
    oms_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(LONG_STRING_LENGTH), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="patient_profile")
    medical_record: Mapped["MedicalRecord | None"] = relationship(
        back_populates="patient", uselist=False, cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")

    __table_args__ = (
        UniqueConstraint(
            "passport_series",
            "passport_number",
            name="uq_patients_passport",
        ),
    )

    @property
    def full_name(self) -> str:
        return build_full_name(self.last_name, self.first_name, self.middle_name)

    @property
    def passport(self) -> str:
        """Паспорт в человекочитаемом виде: '1234 567890'."""
        return f"{self.passport_series} {self.passport_number}"


class MedicalRecord(Base, TimestampMixin):
    """Электронная медицинская карта (ЭМК).

    Роль в системе:
    Центральный медицинский документ пациента. Создаётся автоматически
    при регистрации пациента на ресепшене. Хранит постоянную медицинскую
    информацию (группа крови, аллергии, хроника) и объединяет все осмотры
    и назначения.

    Поля:
    - id: Уникальный идентификатор ЭМК.
    - patient_id: 1-к-1 ссылка на Patient (каскадное удаление).
    - record_number: Уникальный номер карты формата 'MED-ГГГГ-ID:06d'.
    - blood_group: Группа крови и резус-фактор (например, 'A(II) Rh+').
    - allergies: Аллергические реакции и непереносимость препаратов (критично для врача!).
    - chronic_diseases: Анамнез хронических заболеваний.
    - notes: Особые отметки и примечания медицинского персонала.

    Связи:
    - patient: Пациент-владелец карты.
    - entries: История клинических записей врачей по итогам приёмов.
    - assignments: Все назначения (лекарства, процедуры, анализы).
    """

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
    """Клиническая запись врача по итогам проведённого приёма.

    Роль в системе:
    Фиксирует результат осмотра пациента врачом. Соответствует стандарту
    первичного/повторного осмотра: жалобы -> анамнез -> статус -> диагноз МКБ-10 -> план.
    Любое внесение записи логируется в AuditLog (врачебная тайна).

    Поля:
    - id: Идентификатор записи.
    - record_id: Медкарта, в которую вносится запись.
    - doctor_id: Врач, проводивший осмотр и подписавший запись.
    - appointment_id: Приём, в рамках которого состоялся осмотр (SET NULL при удалении приёма).
    - complaints: Жалобы со слов пациента (субъективный статус).
    - anamnesis: Анамнез настоящего заболевания (история развития болезни).
    - objective_status: Объективный осмотр: АД, ЧСС, температура, аускультация.
    - diagnosis_icd10: Код заболевания по международному классификатору МКБ-10 (напр. 'I10').
    - diagnosis_text: Развёрнутый клинический диагноз лечащего врача.
    - treatment_plan: Рекомендации и план дальнейшего обследования/лечения.

    Связи:
    - record: ЭМК пациента.
    - doctor: Врач-автор записи.
    """

    __tablename__ = "medical_record_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )
    # Обращение, в рамках которого сделана запись (основная связь по ТЗ).
    encounter_id: Mapped[int | None] = mapped_column(
        ForeignKey("encounters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    complaints: Mapped[str] = mapped_column(Text, nullable=False)  # Жалобы
    anamnesis: Mapped[str | None] = mapped_column(Text, nullable=True)  # Анамнез
    objective_status: Mapped[str | None] = mapped_column(Text, nullable=True)  # Объективный статус
    diagnosis_icd10: Mapped[str] = mapped_column(String(20), nullable=False)  # Код МКБ-10
    diagnosis_text: Mapped[str] = mapped_column(Text, nullable=False)  # Развёрнутый диагноз
    treatment_plan: Mapped[str | None] = mapped_column(Text, nullable=True)  # План лечения
    # Корректирующая запись: ссылка на исходную, если это исправление после закрытия.
    corrects_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("medical_record_entries.id", ondelete="SET NULL"), nullable=True
    )

    record: Mapped[MedicalRecord] = relationship(back_populates="entries")
    doctor: Mapped[User] = relationship()
    encounter: Mapped["Encounter | None"] = relationship(back_populates="entries")


class Encounter(Base, TimestampMixin):
    """Медицинское обращение (Encounter) — событие «пациент пришёл, его приняли».

    Роль в системе:
    Отдельная сущность от Appointment (слот в расписании), как в стандарте FHIR.
    Appointment — это бронь времени. Encounter — факт медицинского приёма,
    внутри которого врач документирует осмотр, ставит диагноз и создаёт назначения.

    Правило связи: один Appointment порождает ноль или один Encounter.
    Не пришёл — слот отменён, обращения нет. Это честно видно в истории:
    «отменён» и «принят» — разные вещи.

    Поля:
    - id: Идентификатор обращения.
    - appointment_id: Слот, из которого открыто обращение (1-к-1, SET NULL).
    - patient_id: Пациент (денормализация для быстрых выборок).
    - doctor_id: Врач, ведущий обращение.
    - clinic_id: Клиника, где состоялся приём.
    - record_id: Медкарта пациента.
    - status: OPEN → CLOSED / CANCELLED.
    - opened_at: Время открытия обращения (начало приёма).
    - closed_at: Время закрытия (окончание приёма).
    - chief_complaint: Краткая причина обращения (одной строкой).

    Связи:
    - appointment: Слот расписания, породивший обращение.
    - entries: Клинические записи в рамках обращения.
    """

    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    record_id: Mapped[int] = mapped_column(
        ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[EncounterStatus] = mapped_column(
        Enum(EncounterStatus, name="encounter_status_enum", native_enum=False),
        default=EncounterStatus.OPEN,
        nullable=False,
        index=True,
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chief_complaint: Mapped[str | None] = mapped_column(Text, nullable=True)

    appointment: Mapped["Appointment | None"] = relationship(back_populates="encounter")
    patient: Mapped[Patient] = relationship()
    doctor: Mapped[User] = relationship()
    clinic: Mapped[Clinic] = relationship()
    record: Mapped[MedicalRecord] = relationship()
    entries: Mapped[list["MedicalRecordEntry"]] = relationship(
        back_populates="encounter",
        primaryjoin="Encounter.id == MedicalRecordEntry.encounter_id",
    )


class Service(Base, TimestampMixin):
    """Каталог медицинских услуг клиники (прайс-лист).

    Роль в системе:
    Единый справочник всех платных и страховых услуг: консультации, процедуры,
    лабораторные анализы, диагностика. Используется при записи на приём
    (формирование счёта) и при создании назначений.

    Поля:
    - id: Уникальный идентификатор услуги.
    - code: Уникальный артикул услуги (используется в интеграции с 1С/бухгалтерией).
    - name: Название услуги для прайса и чека.
    - description: Подробное описание услуги и подготовки к ней.
    - price: Стоимость услуги в рублях (Numeric(10, 2) — точность до копеек).
    - duration_minutes: Нормативная длительность оказания услуги в минутах.
    - is_procedure: True — манипуляция с участием медсестры (требует отметки выполнения).
    - is_active: Флаг доступности услуги в прайсе (архивные услуги скрываются).
    """

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
    """Запись пациента на приём к врачу (слот в расписании).

    Роль в системе:
    Основной операционный документ расписания. Связывает пациента, врача,
    клинику и услугу. Управляется ресепшеном (создание, перенос, отмена)
    и врачом (ведение приёма). Для платных услуг автоматически порождает Invoice.

    Поля:
    - id: Идентификатор записи на приём.
    - clinic_id: Филиал, в котором состоится приём (RESTRICT — нельзя удалить клинику с приёмами).
    - patient_id: Пациент, записавшийся на приём (каскадное удаление при удалении пациента).
    - doctor_id: Врач, ведущий приём (RESTRICT — нельзя удалить врача с историей приёмов).
    - service_id: Оказываемая услуга (SET NULL при удалении услуги из каталога).
    - scheduled_at: Дата и время начала приёма (с таймзоной, индекс для расписания).
    - duration_minutes: Фактическая длительность приёма в минутах.
    - status: Текущий статус жизненного цикла приёма (AppointmentStatus).
    - cancel_reason: Причина отмены (заполняется при статусе CANCELLED).
    - notes: Служебные примечания администратора/врача.

    Индексы:
    - ix_appointments_doctor_schedule (doctor_id, scheduled_at): быстрый поиск занятых слотов
      и защита от двойного бронирования одного времени одним врачом.

    Связи:
    - clinic, patient, doctor, service: Связанные сущности.
    - invoice: 1-к-1 счёт на оплату (создаётся автоматически для платной услуги).
    """

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
    # Обращение, открытое из этого слота (0 или 1). Не пришёл — обращения нет.
    encounter: Mapped["Encounter | None"] = relationship(
        back_populates="appointment", uselist=False
    )

    __table_args__ = (Index("ix_appointments_doctor_schedule", "doctor_id", "scheduled_at"),)


class Assignment(Base, TimestampMixin):
    """Врачебное назначение пациенту (лекарство, процедура, анализ, диагностика).

    Роль в системе:
    Создаётся и редактируется только врачом. Для процедур служит заданием
    медсестре, а факт выполнения фиксируется отдельными записями ProcedureExecution.
    Для анализов — заданием лаборатории, результат сохраняется в AnalysisResult.

    Поля:
    - id: Идентификатор назначения.
    - record_id: Медкарта, в рамках которой выдано назначение.
    - doctor_id: Врач-автор назначения (только он может его изменить/удалить).
    - service_id: Связанная услуга из прайса (для расчёта стоимости и процентов).
    - type: Тип назначения (AssignmentType): лекарство/процедура/анализ/диагностика.
    - title: Название препарата или манипуляции (например, 'Цефтриаксон').
    - dosage: Дозировка (например, '1.0 г').
    - frequency: Кратность приёма (например, '2 раза в день').
    - duration_days: Длительность курса в днях.
    - instructions: Дополнительные указания и особые отметки.
    - status: Статус выполнения назначения (AssignmentStatus).

    Связи:
    - record: Медкарта пациента.
    - doctor: Врач, выдавший назначение.
    - service: Услуга из прайса.
    - executions: Факты выполнения процедур медсёстрами.
    - analyses: Результаты лабораторных исследований по назначению.
    """

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[int] = mapped_column(
        ForeignKey("medical_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clinic_id: Mapped[int] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
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
    invoice: Mapped["Invoice | None"] = relationship(back_populates="assignment", uselist=False)
    executions: Mapped[list["ProcedureExecution"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AnalysisResult"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )


class ProcedureExecution(Base, TimestampMixin):
    """Факт выполнения процедуры медицинской сестрой.

    Роль в системе:
    Медсестра отмечает выполнение каждой манипуляции по назначению врача.
    Служит доказательством оказания услуги и основанием для списания расходников
    и расчёта зарплаты (через проценты врача/медсестры в бухгалтерии).

    Поля:
    - id: Идентификатор факта выполнения.
    - assignment_id: Назначение-процедура, которое выполнялось.
    - nurse_id: Медсестра, фактически выполнившая процедуру.
    - executed_at: Дата и время выполнения манипуляции.
    - status: Результат выполнения (ProcedureExecutionStatus): выполнено/отказ/противопоказание.
    - materials_used: Списанные расходные материалы (шприцы, растворы, перевязочные средства).
    - notes: Примечания медсестры (например, самочувствие пациента после укола).

    Связи:
    - assignment: Выполняемое назначение.
    - nurse: Медсестра-исполнитель.
    """

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
    """Результат лабораторного анализа или инструментального исследования.

    Роль в системе:
    Хранит готовые результаты исследований. Пациент видит только свои анализы,
    врач видит все анализы своих пациентов. Файл (PDF-бланк или скан) сохраняется
    в public media и при необходимости оптимизируется задачей Pillow.

    Поля:
    - id: Идентификатор результата.
    - assignment_id: Назначение-анализ, к которому относится результат.
    - patient_id: Пациент, которому принадлежит результат (денормализация для быстрых выборок).
    - lab_worker_id: Сотрудник, загрузивший результат (лаборант/медсестра), SET NULL при удалении.
    - title: Название исследования (например, 'Общий анализ крови').
    - summary: Текст заключения и ключевые показатели.
    - file_path: Путь к файлу бланка/скана результата.
    - performed_at: Дата и время фактического проведения исследования.

    Связи:
    - assignment: Исходное назначение.
    - patient: Пациент.
    - lab_worker: Сотрудник, загрузивший результат.
    """

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
    """Счёт на оплату медицинских услуг.

    Роль в системе:
    Финансовый документ, выставляемый пациенту за услуги. Для платных приёмов
    создаётся автоматически при записи. Статус счёта меняется при поступлении
    оплат через кассу ресепшена.

    Поля:
    - id: Идентификатор счёта.
    - appointment_id: Связанный приём (1-к-1, SET NULL при удалении приёма).
    - patient_id: Пациент-плательщик (RESTRICT — нельзя удалить плательщика со счетами).
    - clinic_id: Клиника, выставившая счёт (RESTRICT).
    - amount: Итоговая сумма к оплате в рублях.
    - status: Статус оплаты (PaymentStatus): ожидает/оплачен/возврат/отменён.

    Связи:
    - appointment: Приём, к которому относится счёт.
    - payments: Список фактических платежей по счёту (может быть частичная оплата).
    """

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="SET NULL"), unique=True, nullable=True
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
    assignment: Mapped["Assignment | None"] = relationship(back_populates="invoice")
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )


class Payment(Base, TimestampMixin):
    """Факт поступления денежных средств по счёту (чек кассы).

    Роль в системе:
    Фиксирует каждую транзакцию оплаты. Оформляется администратором ресепшена
    или кассиром. При достижении суммы всех платежей размера счёта Invoice
    автоматически переводится в статус PAID.

    Поля:
    - id: Идентификатор платежа.
    - invoice_id: Оплачиваемый счёт (каскадное удаление вместе со счётом).
    - amount: Сумма конкретного платежа (поддерживается частичная оплата).
    - method: Способ оплаты (PaymentMethod): наличные/карта/СБП/страховка.
    - cashier_id: Сотрудник, принявший оплату (RESTRICT — нельзя удалить кассира с проводками).

    Связи:
    - invoice: Оплаченный счёт.
    - cashier: Кассир/администратор.
    """

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
    """Журнал аудита действий пользователей (защита врачебной тайны).

    Роль в системе:
    Неизменяемый след всех значимых действий: кто, когда, что и с какими
    данными сделал. Критично для медицинских систем (152-ФЗ о персональных
    данных, разбирательства и контроль доступа к врачебной тайне).

    Поля:
    - id: Идентификатор записи журнала.
    - user_id: Пользователь, выполнивший действие (SET NULL при удалении пользователя).
    - action: Тип действия (например, 'create_entry', 'cancel_assignment', 'view_passport').
    - entity_name: Имя затронутой сущности/таблицы.
    - entity_id: Идентификатор затронутого объекта.
    - changes: JSON-строка с diff'ом изменений (старое и новое значение).
    - ip_address: IP-адрес, с которого выполнено действие.
    - created_at: Точное время события (UTC).

    Примечание:
    Не наследует TimestampMixin: запись в журнал неизменяема (только created_at,
    без updated_at).
    """

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
