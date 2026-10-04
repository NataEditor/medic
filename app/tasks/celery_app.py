from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "medic",
    broker=settings.rabbitmq_url,
    backend=settings.redis_url,
    include=["app.tasks.scheduled", "app.tasks.image_processing"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 минут максимум на задачу
    task_routes={
        "app.tasks.image_processing.*": {"queue": "images"},
        "app.tasks.scheduled.generate_*": {"queue": "reports"},
        "*": {"queue": "default"},
    },
    beat_schedule={
        # Каждые 30 минут рассылка напоминаний пациентам
        "remind-patients-every-30-min": {
            "task": "app.tasks.scheduled.remind_upcoming_appointments",
            "schedule": crontab(minute="*/30"),
        },
        # 1-го числа каждого месяца в 03:00 — генерация финотчёта за прошлый месяц
        "monthly-accounting-report": {
            "task": "app.tasks.scheduled.generate_monthly_financial_report",
            "schedule": crontab(day_of_month="1", hour="3", minute="0"),
        },
    },
)
