import enum


class RoleEnum(str, enum.Enum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    HEAD_DOCTOR = "head_doctor"
    NURSE = "nurse"
    RECEPTIONIST = "receptionist"
    ACCOUNTANT = "accountant"


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"          # Запланирован
    WAITING = "waiting"              # Пациент ожидает в клинике
    IN_PROGRESS = "in_progress"      # Идёт приём
    COMPLETED = "completed"          # Завершён
    CANCELLED = "cancelled"          # Отменён
    NO_SHOW = "no_show"              # Не явился


class AssignmentType(str, enum.Enum):
    MEDICATION = "medication"        # Лекарственный препарат
    PROCEDURE = "procedure"          # Процедура / манипуляция (укол, капельница)
    ANALYSIS = "analysis"            # Лабораторный анализ
    DIAGNOSTIC = "diagnostic"        # Инструментальная диагностика (УЗИ, МРТ)


class AssignmentStatus(str, enum.Enum):
    PRESCRIBED = "prescribed"        # Назначено
    IN_PROGRESS = "in_progress"      # Выполняется (например, курс капельниц)
    COMPLETED = "completed"          # Выполнено
    CANCELLED = "cancelled"          # Отменено врачом


class ProcedureExecutionStatus(str, enum.Enum):
    COMPLETED = "completed"          # Выполнено медсестрой
    REFUSED = "refused"              # Пациент отказался
    CONTRAINDICATED = "contraindicated"  # Противопоказание


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"              # Ожидает оплаты
    PAID = "paid"                    # Оплачено
    REFUNDED = "refunded"            # Возврат
    CANCELLED = "cancelled"          # Отменён


class PaymentMethod(str, enum.Enum):
    CASH = "cash"                    # Наличные
    CARD = "card"                    # Терминал
    SBP = "sbp"                      # Система быстрых платежей
    INSURANCE = "insurance"          # ОМС / ДМС
