import enum


class RoleEnum(str, enum.Enum):
    """Роли пользователей в системе МИС.

    У пользователя может быть НЕСКОЛЬКО ролей одновременно (user.roles):
    в маленькой клинике один человек бывает и врачом, и главврачом,
    и админом с ресепшеном. Права при этом суммируются.

    - SUPER_ADMIN: владелец платформы. Управляет клиниками, сотрудниками и тарифами
      всей сети. Не подчиняется главврачу. Защищён от самоудаления и удаления
      другого администратора, а также не может менять/удалять финансовые проводки.
    - CLINIC_ADMIN: административный директор клиники. Люди, графики, доступы,
      кассовые операции. НЕ читает ЭМК и не создаёт назначения — это зона главврача.
    - HEAD_DOCTOR: главный врач. Медицинское качество: ЭМК, назначения, контроль,
      финотчёты. Не управляет сотрудниками — это зона админа клиники.
    - DOCTOR: ведёт приёмы (Encounter), создаёт назначения, вносит записи в ЭМК.
      Видит карты только тех пациентов, с которыми есть связь (приём).
    - NURSE: просматривает назначения, вносит отметки о выполнении процедур
      и о прибытии пациента.
    - RECEPTIONIST: создаёт карты пациентов по паспорту, ведёт расписание,
      принимает оплату, выдаёт доступ в ЛК.
    - ACCOUNTANT: единая бухгалтерия сети. Финансовые отчёты, зарплаты, возвраты.
      Не имеет доступа к медданным и ПДн.
    - PATIENT: видит только своё — карту, приёмы, назначения, анализы, счёт.
    """

    SUPER_ADMIN = "super_admin"
    CLINIC_ADMIN = "clinic_admin"
    HEAD_DOCTOR = "head_doctor"
    DOCTOR = "doctor"
    NURSE = "nurse"
    RECEPTIONIST = "receptionist"
    ACCOUNTANT = "accountant"
    PATIENT = "patient"


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
