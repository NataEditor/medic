from loguru import logger

from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.notifications.send_temp_password_sms",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_temp_password_sms(
    self,
    phone: str,
    code: str,
    ttl_hours: int,
) -> dict[str, str]:
    """Отправляет пациенту временный пароль для входа в личный кабинет по SMS.

    В текущей реализации это заглушка: реальный SMS-шлюз (SMS.ru, Twilio и т.п.)
    подключается здесь. Пароль попадает только в очередь и в лог —
    в базу открытый пароль не сохраняется.
    """
    try:
        logger.info(
            "SMS: на номер {} отправлен временный пароль для входа в ЛК (действует {} ч)",
            phone,
            ttl_hours,
        )
        # Интеграция со шлюзом:
        # sms_provider.send(phone=phone, text=f"Ваш код входа: {code}. Действует {ttl_hours} ч.")
        return {"status": "sent", "phone": phone}
    except Exception as exc:
        logger.error("Ошибка отправки SMS на {}: {}", phone, exc)
        raise self.retry(exc=exc) from exc
