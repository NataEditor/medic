import logging
import sys
from contextvars import ContextVar
from pathlib import Path

from loguru import logger

from app.core.config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


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
    record["extra"].setdefault("request_id", request_id_ctx.get())


def setup_logging() -> None:
    logger.remove()

    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[request_id]}</cyan> | "
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

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "amqp"):
        logging.getLogger(noisy).handlers = [InterceptHandler()]


__all__ = ["logger", "request_id_ctx", "setup_logging"]
