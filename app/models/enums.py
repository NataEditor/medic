import enum


class RoleEnum(str, enum.Enum):
    """Роли пользователей в системе МИС.

    Определяет уровень доступа (RBAC):
    - SUPER_ADMIN: владелец платформы. Управляет клиниками, сотрудниками и тарифами
      всей сети. Не подчиняется главврачу. Защищён от самоудаления и удаления
      другого администратора, а также не может менять/удалять финансовые проводки.
    - PATIENT: видит только свою медкарту, анализы и свои приёмы.
    - DOCTOR: ведёт приёмы, создаёт/редактирует назначения, вносит записи в ЭМК.
    - HEAD_DOCTOR: руководитель клиники, доступ ко всем филиалам сети, отчётам и прайсу.
    - NURSE: просматривает назначения врача, вносит отметки о выполнении процедур.
    - RECEPTIONIST: регистрирует пациентов по паспорту, формирует расписание, принимает оплату.
    - ACCOUNTANT: финансовые отчёты, приёмы врачей, расчёт зарплат по процентам.
    """

    SUPER_ADMIN = "super_admin"
    PATIENT = "patient"
    DOCTOR = "doctor"
    HEAD_DOCTOR = "head_doctor"
    NURSE = "nurse"
    RECEPTIONIST = "receptionist"
    ACCOUNTANT = "accountant"


class AppointmentStatus(str, enum.Enum):
    """Жизненный цикл приёма пациента.

    Порядок перехода:
    SCHEDULED (запланирован)
      -> WAITING (пациент прибыл и ожидает у кабинета)
      -> IN_PROGRESS (врач начал приём)
      -> COMPLETED (приём завершён, внесена запись в ЭМК).

    Исключительные статусы:
    - CANCELLED: приём отменён пациентом или клиникой (с указанием причины).
    - NO_SHOW: пациент не явился ко времени приёма.
    """

    SCHEDULED = "scheduled"
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class AssignmentType(str, enum.Enum):
    """Тип врачебного назначения.

    - MEDICATION: приём лекарств (дозировка, кратность, длительность курса).
    - PROCEDURE: манипуляции, требующие участия медсестры (уколы, капельницы, перевязки).
    - ANALYSIS: направление на лабораторный анализ (кровь, моча, биохимия).
    - DIAGNOSTIC: инструментальные исследования (УЗИ, ЭКГ, рентген).
    """

    MEDICATION = "medication"
    PROCEDURE = "procedure"
    ANALYSIS = "analysis"
    DIAGNOSTIC = "diagnostic"


class AssignmentStatus(str, enum.Enum):
    """Статус врачебного назначения.

    - PRESCRIBED: назначено врачом, ожидает начала курса.
    - IN_PROGRESS: находится в процессе выполнения (медсестра начала процедуры).
    - COMPLETED: курс завершён / анализ сдан и загружен.
    - CANCELLED: отменено лечащим врачом.
    """

    PRESCRIBED = "prescribed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProcedureExecutionStatus(str, enum.Enum):
    """Результат выполнения процедуры медицинской сестрой.

    - COMPLETED: манипуляция успешно проведена (списаны расходники).
    - REFUSED: пациент отказался от процедуры на месте.
    - CONTRAINDICATED: процедура не проведена из-за противопоказания (давление, аллергия).
    """

    COMPLETED = "completed"
    REFUSED = "refused"
    CONTRAINDICATED = "contraindicated"


class PaymentStatus(str, enum.Enum):
    """Статус счёта на оплату медицинских услуг.

    - PENDING: счёт выставлен, оплата ожидается (ресепшен).
    - PAID: счёт полностью оплачен (нал/карта/СБП).
    - REFUNDED: оформлен возврат средств пациенту.
    - CANCELLED: счёт аннулирован (например, при отмене приёма).
    """

    PENDING = "pending"
    PAID = "paid"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class PaymentMethod(str, enum.Enum):
    """Способ проведения оплаты в кассе клиники.

    - CASH: наличные деньги.
    - CARD: банковская карта через POS-терминал.
    - SBP: система быстрых платежей (QR-код).
    - INSURANCE: полис ОМС или добровольного медицинского страхования (ДМС).
    """

    CASH = "cash"
    CARD = "card"
    SBP = "sbp"
    INSURANCE = "insurance"
