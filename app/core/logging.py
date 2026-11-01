import logging
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import settings

# Контекст запроса: идентификатор запроса и пользователь, выполняющий действие.
# Заполняется middleware (request_id) и зависимостью get_current_user (user_id),
# чтобы каждая строка лога была привязана к конкретному запросу и человеку.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
user_id_ctx: ContextVar[str] = ContextVar("user_id", default="-")


class InterceptHandler(logging.Handler):
    """Перехватывает стандартные логи uvicorn/sqlalchemy в Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _patch_record(record: dict) -> None:
    """Добавляет в каждую запись контекст запроса и пользователя."""
    record["extra"].setdefault("request_id", request_id_ctx.get())
    record["extra"].setdefault("user_id", user_id_ctx.get())


def bind_user_context(user_id: int | str | None) -> None:
    """Привязывает идентификатор пользователя к текущему контексту логирования.

    Вызывается после успешной аутентификации, чтобы все последующие записи
    в рамках запроса содержали, кто именно выполнил действие.
    """
    user_id_ctx.set(str(user_id) if user_id is not None else "-")


def log_security_event(event: str, message: str, **fields: Any) -> None:
    """Пишет событие безопасности в отдельный журнал (logs/security_*.log).

    Сюда попадают входы, отказы в доступе, блокировки и просмотры ПДн —
    всё, что важно для разбора инцидентов и аудита (152-ФЗ).
    """
    logger.bind(security=True, event=event).warning(
        "[БЕЗОПАСНОСТЬ] {event}: {message} | {fields}",
        event=event,
        message=message,
        fields=fields or "-",
    )


def setup_logging() -> None:
    logger.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[request_id]}</cyan> | "
        "<magenta>user={extra[user_id]}</magenta> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    logger.configure(patcher=_patch_record)

    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=fmt,
        colorize=True,
        backtrace=settings.debug,
        diagnose=settings.debug,
    )

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # Общий журнал приложения: все события уровня log_level и выше.
    logger.add(
        log_dir / "medic_{time:YYYY-MM-DD}.log",
        level=settings.log_level,
        format=fmt,
        rotation="00:00",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=settings.debug,
    )

    # Отдельный журнал безопасности: только события с меткой security=True.
    # Хранится дольше (90 дней) — этого требует разбор инцидентов с ПДн.
    logger.add(
        log_dir / "security_{time:YYYY-MM-DD}.log",
        level="INFO",
        format=fmt,
        filter=lambda record: record["extra"].get("security") is True,
        rotation="00:00",
        retention="90 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "amqp"):
        logging.getLogger(noisy).handlers = [InterceptHandler()]


__all__ = [
    "bind_user_context",
    "log_security_event",
    "logger",
    "request_id_ctx",
    "setup_logging",
    "user_id_ctx",
]
