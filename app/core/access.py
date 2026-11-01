"""Матрица прав и объектные проверки доступа (единый модуль).

Здесь собрана вся логика разграничения доступа, чтобы не дублировать её
по роутерам и сервисам:

1. КОНСТАНТЫ РОЛЕЙ — именованные наборы ролей (кто в принципе допущен).
2. ПРЕДИКАТЫ — быстрые проверки «есть ли у пользователя такая роль».
3. КИРПИЧИКИ — атомарные объектные проверки (своё / клиника / касание).
4. СОСТАВНЫЕ GUARD-ФУНКЦИИ — собираются из кирпичиков (ЭМК, карточка, анализы).
5. РЕЗОЛВЕРЫ — достают patient_id из связанных сущностей (record, assignment).

Модуль НЕ зависит от FastAPI и от app.api.deps — его можно вызывать
и из роутеров, и из сервисов. FastAPI-зависимости (require_roles и т.п.)
живут в app.api.deps и импортируют отсюда.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.entities import (
    AnalysisResult,
    Appointment,
    MedicalRecord,
    User,
)
from app.models.enums import RoleEnum

# ═══════════════════════════════════════════════════════════════════
# 1. КОНСТАНТЫ РОЛЕЙ (матрица прав, 8 ролей, мультиролевая модель)
# ═══════════════════════════════════════════════════════════════════

# Администраторы платформы/клиники: управление людьми, графиками, доступами.
# CLINIC_ADMIN — административный директор (НЕ читает ЭМК).
# HEAD_DOCTOR — главный врач (медицинское качество, НЕ управляет людьми).
ADMIN_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN, RoleEnum.HEAD_DOCTOR}
)

# Медперсонал: доступ к процедурам, назначениям и анализам.
# ВАЖНО: это НЕ доступ к ЭМК. Медсестра выполняет процедуры и грузит
# анализы, но врачебные записи (осмотры, диагнозы, МКБ-10) не читает —
# для этого отдельный набор EMR_READ_ROLES.
MEDICAL_STAFF_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.DOCTOR, RoleEnum.NURSE, RoleEnum.HEAD_DOCTOR, RoleEnum.SUPER_ADMIN}
)

# Чтение ЭМК (врачебные записи: осмотры, диагнозы, МКБ-10).
# Медсестра и админ клиники сюда НЕ входят: они работают с карточкой,
# процедурами и анализами, но не с историей болезни.
EMR_READ_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.DOCTOR, RoleEnum.HEAD_DOCTOR, RoleEnum.SUPER_ADMIN}
)

# Запись в ЭМК (осмотр, МКБ-10) — только врач в своём Encounter,
# главврач по своей клинике, супер-админ.
EMR_WRITE_ROLES: frozenset[RoleEnum] = EMR_READ_ROLES

# Роли, которые могут создавать карту пациента (регистратура и выше).
# Врач карту НЕ создаёт — только читает и ведёт приём.
PATIENT_CREATE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.SUPER_ADMIN,
    }
)

# Роли, работающие с базой пациентов (поиск, просмотр карточки).
PATIENT_REGISTRY_ROLES: frozenset[RoleEnum] = MEDICAL_STAFF_ROLES | {
    RoleEnum.RECEPTIONIST,
    RoleEnum.CLINIC_ADMIN,
}

# Финансы: счета, оплаты, отчёты, зарплаты.
# Бухгалтерия — единая на всю сеть и НЕ видит медданные и ПДн.
FINANCE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.ACCOUNTANT,
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.HEAD_DOCTOR,
    }
)

# Роли, которые могут принимать оплату в кассе.
CASHIER_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.RECEPTIONIST, RoleEnum.ACCOUNTANT, RoleEnum.CLINIC_ADMIN, RoleEnum.SUPER_ADMIN}
)

# Роли, которые могут подтверждать возврат (двойной контроль: заявка ≠ подтверждение).
REFUND_APPROVE_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.ACCOUNTANT, RoleEnum.SUPER_ADMIN})

# Редактирование профиля пациента (паспорт, контакты) — регистратура и выше.
# Врач профиль не правит: он ведёт приём, а не документы.
PATIENT_UPDATE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.SUPER_ADMIN,
    }
)

# Скан паспорта (загрузка и просмотр). Админ клиники ПДн не читает.
PASSPORT_SCAN_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.DOCTOR,
        RoleEnum.NURSE,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.PATIENT,
    }
)

# Просмотр скана паспорта (чтение ПДн). Ресепшен сюда НЕ входит:
# он только загружает скан, но не читает его (152-ФЗ).
PASSPORT_SCAN_VIEW_ROLES: frozenset[RoleEnum] = PASSPORT_SCAN_ROLES - {RoleEnum.RECEPTIONIST}

# Выдача доступа в личный кабинет пациента (SMS-пароль).
PORTAL_ACCESS_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.SUPER_ADMIN,
    }
)

# Управление графиком врача (смены, отпуска, больничные).
SCHEDULE_MANAGE_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.SUPER_ADMIN,
    }
)

# Просмотр свободных слотов: все, кроме бухгалтерии.
SLOT_VIEW_ROLES: frozenset[RoleEnum] = frozenset(
    {
        RoleEnum.RECEPTIONIST,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.DOCTOR,
        RoleEnum.NURSE,
        RoleEnum.SUPER_ADMIN,
        RoleEnum.PATIENT,
    }
)

# Управление сотрудниками: регистрация и блокировка.
# Главврач людьми не управляет — это зона админа клиники.
STAFF_MANAGE_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN})

# Просмотр списка сотрудников.
STAFF_VIEW_ROLES: frozenset[RoleEnum] = frozenset(
    {RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN, RoleEnum.HEAD_DOCTOR}
)

# Журнал аудита: супер-админ — вся сеть, админ клиники — своя клиника.
AUDIT_VIEW_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.SUPER_ADMIN, RoleEnum.CLINIC_ADMIN})

# Роли со сквозным доступом к медданным всей сети (🌐).
NETWORK_MEDICAL_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.SUPER_ADMIN, RoleEnum.HEAD_DOCTOR})

# Роли со сквозным доступом к финансам всей сети (🌐).
NETWORK_FINANCE_ROLES: frozenset[RoleEnum] = frozenset({RoleEnum.SUPER_ADMIN, RoleEnum.ACCOUNTANT})


# ═══════════════════════════════════════════════════════════════════
# 2. ПРЕДИКАТЫ (быстрые проверки ролей)
# ═══════════════════════════════════════════════════════════════════


def _has_any(user: User, roles: frozenset[RoleEnum]) -> bool:
    """Есть ли у пользователя хотя бы одна роль из набора (мультиролевость)."""
    return any(role.value in user.roles for role in roles)


def is_admin(user: User) -> bool:
    """True для администраторов (супер-админ, админ клиники, главврач)."""
    return _has_any(user, ADMIN_ROLES)


def is_super_admin(user: User) -> bool:
    """True, если пользователь — владелец платформы (супер-админ)."""
    return user.has_role(RoleEnum.SUPER_ADMIN)


def is_medical_staff(user: User) -> bool:
    """True для медперсонала (врач, медсестра, главврач) — доступ к медданным."""
    return _has_any(user, MEDICAL_STAFF_ROLES)


def is_clinic_admin(user: User) -> bool:
    """True для административного директора клиники (без доступа к ЭМК)."""
    return user.has_role(RoleEnum.CLINIC_ADMIN)


def can_view_emr(user: User) -> bool:
    """Может ли пользователь читать врачебные записи ЭМК.

    Только SA, главврач и врач. Медсестра выполняет процедуры и видит
    назначения/анализы, но историю болезни (осмотры, диагнозы, МКБ-10)
    не читает — это зона врача. Админ клиники и ресепшен видят карточку
    пациента без ЭМК.
    """
    return _has_any(user, EMR_READ_ROLES)


def can_view_patient_card_and_procedures(user: User) -> bool:
    """Может ли пользователь видеть карточку пациента, назначения и анализы.

    Это зона медсестры: ФИО, дата рождения, телефон, свои процедуры,
    результаты анализов. Без врачебных записей ЭМК.
    """
    return is_medical_staff(user)


# Обратная совместимость: старое имя предиката.
can_read_emk = can_view_emr


# ═══════════════════════════════════════════════════════════════════
# 3. КИРПИЧИКИ (атомарные объектные проверки)
# ═══════════════════════════════════════════════════════════════════


def ensure_patient_self(user: User, patient_id: int) -> None:
    """Пациент может работать только со своей картой.

    Единая точка вместо копипасты «not patient_profile or id != patient_id»
    по роутерам. Для не-пациентов проверка не применяется (их ограничивает
    ролевой guard в роутере).
    """
    if not user.has_role(RoleEnum.PATIENT):
        return
    if not user.patient_profile or user.patient_profile.id != patient_id:
        raise PermissionDeniedError("Пациент может работать только со своей картой")


def ensure_clinic_scope(user: User, target_clinic_id: int | None) -> None:
    """Изоляция по клинике (🏥).

    Администраторы (супер-админ, админ клиники, главврач) видят любую
    клинику сети. Остальные сотрудники ограничены своей клиникой.
    """
    if is_admin(user):
        return
    if user.clinic_id is None or user.clinic_id != target_clinic_id:
        raise PermissionDeniedError("Нет доступа к данным другой клиники")


# Обратная совместимость: старое имя.
check_clinic_access = ensure_clinic_scope


async def ensure_doctor_touch(
    db: AsyncSession,
    user: User,
    patient_id: int,
) -> None:
    """Правило «по касанию»: у врача есть приём с этим пациентом.

    Карта общая для сети, но врач не «гуляет» по реестру — доступ только
    к пациентам, с которыми есть связь через Encounter/Appointment.
    """
    stmt = (
        select(Appointment.id)
        .where(
            Appointment.doctor_id == user.id,
            Appointment.patient_id == patient_id,
        )
        .limit(1)
    )
    has_link = (await db.execute(stmt)).scalar_one_or_none() is not None
    if not has_link:
        raise PermissionDeniedError("Нет доступа к карте: у вас нет приёмов с этим пациентом")


async def ensure_nurse_clinic_link(
    db: AsyncSession,
    user: User,
    patient_id: int,
) -> None:
    """Медсестра работает с пациентами своей клиники (связь через приём)."""
    if user.clinic_id is None:
        raise PermissionDeniedError("Медсестра не прикреплена к клинике")
    stmt = (
        select(Appointment.id)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.clinic_id == user.clinic_id,
        )
        .limit(1)
    )
    has_link = (await db.execute(stmt)).scalar_one_or_none() is not None
    if not has_link:
        raise PermissionDeniedError("Нет доступа: у пациента нет приёмов в вашей клинике")


# ═══════════════════════════════════════════════════════════════════
# 4. СОСТАВНЫЕ GUARD-ФУНКЦИИ (собираются из кирпичиков)
# ═══════════════════════════════════════════════════════════════════


async def ensure_medical_access(
    db: AsyncSession,
    current_user: User,
    patient_id: int,
) -> None:
    """Правило «по касанию»: доступ к врачебным записям ЭМК пациента.

    Карта общая для сети, но врач не «гуляет» по реестру:
    - супер-админ, главврач — доступ к любой карте сети;
    - врач — только если с пациентом есть связь: назначенный приём
      (текущий или будущий) либо приём в прошлом;
    - медсестра, админ клиники, ресепшен, бухгалтерия — доступа к ЭМК нет.
      Медсестре доступны карточка, назначения и анализы — но не история
      болезни (см. ensure_patient_card_access).
    """
    if _has_any(current_user, NETWORK_MEDICAL_ROLES):
        return
    if current_user.has_role(RoleEnum.DOCTOR):
        await ensure_doctor_touch(db, current_user, patient_id)
        return
    raise PermissionDeniedError("Доступ к медицинской карте запрещён")


async def ensure_patient_card_access(
    db: AsyncSession,
    current_user: User,
    patient_id: int,
) -> None:
    """Доступ к карточке пациента, назначениям и анализам (без ЭМК).

    Это зона медсестры: она видит, кому и что назначено, отмечает
    выполнение процедур и загружает результаты анализов. Врачебные
    записи (осмотры, диагнозы) при этом остаются закрыты.

    - супер-админ, главврач — любая карта сети;
    - медсестра — пациенты, с которыми есть приём в её клинике;
    - врач — только «по касанию» (есть приём с пациентом);
    - админ клиники, ресепшен — карточка без медданных (проверяется
      отдельно в роутере, здесь доступа к назначениям не даём);
    - бухгалтерия — запрещено.

    У пациента нет собственного clinic_id: принадлежность определяется
    через приёмы (Appointment.clinic_id), поэтому изоляция проверяется
    по связи «пациент ↔ приём ↔ клиника».
    """
    if _has_any(current_user, NETWORK_MEDICAL_ROLES):
        return
    if current_user.has_role(RoleEnum.NURSE):
        await ensure_nurse_clinic_link(db, current_user, patient_id)
        return
    if current_user.has_role(RoleEnum.DOCTOR):
        await ensure_doctor_touch(db, current_user, patient_id)
        return
    raise PermissionDeniedError("Доступ к данным пациента запрещён")


async def ensure_analysis_access(
    db: AsyncSession,
    current_user: User,
    patient_id: int,
) -> None:
    """Доступ к результатам анализов пациента.

    Отличается от ensure_patient_card_access тем, что медсестре достаточно
    любой связи пациента с её клиникой: приём ИЛИ анализ, загруженный
    сотрудником этой клиники. Второй случай важен для walk-in пациентов,
    которые пришли без предварительной записи — анализ у них есть,
    а приёма может не быть.
    """
    if _has_any(current_user, NETWORK_MEDICAL_ROLES):
        return

    if current_user.has_role(RoleEnum.NURSE):
        if current_user.clinic_id is None:
            raise PermissionDeniedError("Медсестра не прикреплена к клинике")

        # Связь 1: приём пациента в клинике медсестры
        appointment_stmt = (
            select(Appointment.id)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.clinic_id == current_user.clinic_id,
            )
            .limit(1)
        )
        if (await db.execute(appointment_stmt)).scalar_one_or_none() is not None:
            return

        # Связь 2: анализ загружен сотрудником клиники медсестры
        clinic_staff = select(User.id).where(User.clinic_id == current_user.clinic_id)
        analysis_stmt = (
            select(AnalysisResult.id)
            .where(
                AnalysisResult.patient_id == patient_id,
                AnalysisResult.lab_worker_id.in_(clinic_staff),
            )
            .limit(1)
        )
        if (await db.execute(analysis_stmt)).scalar_one_or_none() is not None:
            return

        raise PermissionDeniedError("Нет доступа: пациент не связан с вашей клиникой")

    # Врач, админ клиники, ресепшен, бухгалтерия — по общим правилам карты
    await ensure_patient_card_access(db, current_user, patient_id)


async def ensure_appointment_access(
    current_user: User,
    appointment_doctor_id: int,
    appointment_patient_id: int,
) -> None:
    """Проверяет право на изменение/просмотр приёма (мультиролевая модель).

    Правила:
    - супер-админ, админ клиники, главврач, ресепшен — любой приём;
    - врач — только свои приёмы (где он лечащий врач);
    - медсестра — приёмы клиники (нужны для отметки «пациент прибыл»);
    - пациент — только свой приём.
    """
    if current_user.has_any_role(
        RoleEnum.SUPER_ADMIN,
        RoleEnum.CLINIC_ADMIN,
        RoleEnum.HEAD_DOCTOR,
        RoleEnum.RECEPTIONIST,
    ):
        return

    if current_user.has_role(RoleEnum.DOCTOR):
        if appointment_doctor_id != current_user.id:
            raise PermissionDeniedError("Врач может изменять только свои приёмы")
        return

    if current_user.has_role(RoleEnum.NURSE):
        # Медсестра ведёт поток пациентов клиники
        return

    if current_user.has_role(RoleEnum.PATIENT):
        if (
            not current_user.patient_profile
            or appointment_patient_id != current_user.patient_profile.id
        ):
            raise PermissionDeniedError("Пациент может видеть только свои приёмы")
        return

    raise PermissionDeniedError("Недостаточно прав для работы с приёмом")


# ═══════════════════════════════════════════════════════════════════
# 5. РЕЗОЛВЕРЫ (достают patient_id из связанных сущностей)
# ═══════════════════════════════════════════════════════════════════


async def resolve_patient_id_from_record(db: AsyncSession, record_id: int) -> int:
    """Возвращает patient_id по id медкарты (или 404, если карты нет)."""
    record = await db.get(MedicalRecord, record_id)
    if record is None:
        raise NotFoundError("Медкарта не найдена")
    return record.patient_id


async def resolve_patient_id_from_assignment(db: AsyncSession, assignment_id: int) -> int:
    """Возвращает patient_id по id назначения (через его медкарту)."""
    from app.models.entities import Assignment

    assignment = await db.get(Assignment, assignment_id)
    if assignment is None:
        raise NotFoundError("Назначение не найдено")
    if assignment.record_id is None:
        raise NotFoundError("У назначения отсутствует медкарта")
    return await resolve_patient_id_from_record(db, assignment.record_id)


# ═══════════════════════════════════════════════════════════════════
# 6. АДМИНИСТРАТИВНЫЙ ДОСТУП К КАРТЕ (по приёмам + клиника регистрации)
# ═══════════════════════════════════════════════════════════════════


async def ensure_patient_admin_access(
    db: AsyncSession,
    current_user: User,
    patient_id: int,
) -> None:
    """Право на административные операции над картой пациента.

    Карта общая для сети, поэтому «принадлежность» определяется не меткой
    на карте, а фактами взаимодействия:

    - супер-админ, главврач — любая карта сети (🌐);
    - сотрудник клиники — если у пациента есть приём в его клинике
      (пациент реально обращался сюда);
    - для карт БЕЗ приёмов (новый черновик) — если карту завёл сотрудник
      его клиники (created_by_clinic_id == user.clinic_id).

    Так закрывается единственная непроверяемая операция: админ/ресепшен
    чужого филиала не может действовать над «висящей в воздухе» картой.
    """
    from app.models.entities import Patient

    if _has_any(current_user, NETWORK_MEDICAL_ROLES):
        return
    if current_user.clinic_id is None:
        raise PermissionDeniedError("Сотрудник не прикреплён к клинике")

    # 1. Есть ли у пациента приём в клинике сотрудника?
    linked = (
        select(Appointment.id)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.clinic_id == current_user.clinic_id,
        )
        .limit(1)
    )
    if (await db.execute(linked)).scalar_one_or_none() is not None:
        return

    # 2. Есть ли у пациента приёмы вообще? Если нет — карта «пустая»,
    #    и права определяются клиникой регистрации.
    any_appt = select(Appointment.id).where(Appointment.patient_id == patient_id).limit(1)
    if (await db.execute(any_appt)).scalar_one_or_none() is None:
        patient = await db.get(Patient, patient_id)
        if patient is not None and patient.created_by_clinic_id == current_user.clinic_id:
            return
        raise PermissionDeniedError("Карта заведена другой клиникой — нет прав на операцию")

    raise PermissionDeniedError("Пациент не связан с вашей клиникой")


async def ensure_doctor_clinic_access(
    db: AsyncSession,
    current_user: User,
    doctor_id: int,
) -> None:
    """Право управлять расписанием врача (смены, отпуска).

    Расписание — административная зона клиники (🏥):
    - супер-админ — любой врач сети (🌐);
    - остальные — только врач своей клиники.

    Главврач здесь ограничен своей клиникой: управление графиком — это
    зона админа клиники/ресепшена, а не медицинская функция.
    """
    if is_super_admin(current_user):
        return
    if current_user.clinic_id is None:
        raise PermissionDeniedError("Сотрудник не прикреплён к клинике")

    doctor = await db.get(User, doctor_id)
    if doctor is None:
        raise NotFoundError("Врач не найден")
    if doctor.clinic_id != current_user.clinic_id:
        raise PermissionDeniedError("Врач работает в другой клинике")


__all__ = [
    "ADMIN_ROLES",
    "AUDIT_VIEW_ROLES",
    "CASHIER_ROLES",
    "EMR_READ_ROLES",
    "EMR_WRITE_ROLES",
    "FINANCE_ROLES",
    "MEDICAL_STAFF_ROLES",
    "NETWORK_FINANCE_ROLES",
    "NETWORK_MEDICAL_ROLES",
    "PASSPORT_SCAN_ROLES",
    "PASSPORT_SCAN_VIEW_ROLES",
    "PATIENT_CREATE_ROLES",
    "PATIENT_REGISTRY_ROLES",
    "PATIENT_UPDATE_ROLES",
    "PORTAL_ACCESS_ROLES",
    "REFUND_APPROVE_ROLES",
    "SCHEDULE_MANAGE_ROLES",
    "SLOT_VIEW_ROLES",
    "STAFF_MANAGE_ROLES",
    "STAFF_VIEW_ROLES",
    "can_read_emk",
    "can_view_emr",
    "can_view_patient_card_and_procedures",
    "check_clinic_access",
    "ensure_analysis_access",
    "ensure_appointment_access",
    "ensure_clinic_scope",
    "ensure_doctor_clinic_access",
    "ensure_doctor_touch",
    "ensure_medical_access",
    "ensure_nurse_clinic_link",
    "ensure_patient_admin_access",
    "ensure_patient_card_access",
    "ensure_patient_self",
    "is_admin",
    "is_clinic_admin",
    "is_medical_staff",
    "is_super_admin",
    "resolve_patient_id_from_assignment",
    "resolve_patient_id_from_record",
]
