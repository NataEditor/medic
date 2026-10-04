from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Appointment, Patient, User
from app.models.enums import AppointmentStatus
from app.tasks.celery_app import celery_app


def _get_sync_session() -> Session:
    """Для воркеров Celery используем синхронный psycopg."""
    engine = create_engine(settings.sync_database_url)
    return Session(engine)


@celery_app.task(name="app.tasks.scheduled.remind_upcoming_appointments")
def remind_upcoming_appointments() -> int:
    """Ищет приёмы, которые состоятся завтра, и отправляет уведомления."""
    now = datetime.now(UTC)
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = tomorrow_start + timedelta(days=1)

    sent_count = 0
    with _get_sync_session() as session:
        stmt = (
            select(Appointment, Patient, User)
            .join(Patient, Appointment.patient_id == Patient.id)
            .join(User, Appointment.doctor_id == User.id)
            .where(
                Appointment.status == AppointmentStatus.SCHEDULED,
                Appointment.scheduled_at >= tomorrow_start,
                Appointment.scheduled_at < tomorrow_end,
            )
        )
        results = session.execute(stmt).all()

        for appt, patient, doctor in results:
            logger.info(
                "Напоминание пациенту {} (тел: {}) о приёме к врачу {} в {}",
                patient.full_name,
                patient.phone,
                doctor.full_name,
                appt.scheduled_at.strftime("%H:%M"),
            )
            # Здесь вызывается SMS/Email шлюз
            sent_count += 1

    return sent_count


@celery_app.task(name="app.tasks.scheduled.generate_monthly_financial_report")
def generate_monthly_financial_report() -> dict[str, str]:
    """Фоновая генерация ежемесячного финансового отчёта для бухгалтерии."""
    logger.info("Запуск ежемесячной агрегации доходов клиник")
    # Логика агрегации и отправки на почту главврачу/бухгалтерии
    return {"status": "success", "period": "previous_month"}
