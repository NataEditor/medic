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


class SalaryItem(ORMModel):
    """Расчёт зарплаты одного врача за период.

    Зарплата = процент от приёмов + процент от процедур. Ставки берутся
    из DoctorProfile (appointment_fee_percent, procedure_fee_percent),
    поэтому бухгалтерия может их менять без правки кода.
    """

    doctor_id: int
    doctor_name: str
    specialization: str | None = None
    clinic_id: int | None = None

    appointment_fee_percent: str = Field("0.00", description="Ставка по приёмам, %")
    procedure_fee_percent: str = Field("0.00", description="Ставка по процедурам, %")

    appointments_revenue: str = Field("0.00", description="Выручка по приёмам")
    procedures_revenue: str = Field("0.00", description="Выручка по процедурам")

    appointment_salary: str = Field("0.00", description="Начислено за приёмы")
    procedure_salary: str = Field("0.00", description="Начислено за процедуры")
    total_salary: str = Field("0.00", description="Итого к выплате")


class SalaryReport(ORMModel):
    """Ведомость зарплат за период (только бухгалтерия и SA)."""

    start_date: date
    end_date: date
    clinic_id: int | None = None
    doctors: list[SalaryItem] = []
    total_appointments_revenue: str = "0.00"
    total_procedures_revenue: str = "0.00"
    total_salary: str = "0.00"
