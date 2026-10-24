from datetime import date

from pydantic import Field

from app.schemas.common import ORMModel


class DoctorProductivityItem(ORMModel):
    """Показатели одного врача за период.

    Три счётчика, которые нужны бухгалтерии:
    1. closed_encounters — сколько приёмов врач закрыл (Encounter.doctor_id).
    2. prescribed_procedures — сколько процедур он назначил (Assignment.author).
    3. performed_procedures — сколько процедур выполнил лично (performed_by_id).
    """

    doctor_id: int
    doctor_name: str
    specialization: str | None = None
    clinic_id: int | None = None

    closed_encounters: int = Field(0, description="Закрытых приёмов за период")
    prescribed_procedures: int = Field(0, description="Назначено процедур")
    performed_procedures: int = Field(0, description="Выполнено лично процедур")

    # Денежные показатели для расчёта зарплаты
    appointments_revenue: str = Field("0.00", description="Выручка по приёмам")
    procedures_revenue: str = Field("0.00", description="Выручка по процедурам")
    calculated_salary: str = Field("0.00", description="Начисленная зарплата")


class DoctorProductivityReport(ORMModel):
    """Отчёт производительности: один врач или сводка по всем."""

    start_date: date
    end_date: date
    clinic_id: int | None = None
    doctors: list[DoctorProductivityItem] = []
    total_closed_encounters: int = 0
    total_prescribed_procedures: int = 0
    total_performed_procedures: int = 0
