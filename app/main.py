import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_v1_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import logger, request_id_ctx, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Жизненный цикл приложения (startup / shutdown)."""
    setup_logging()
    logger.info("Старт приложения MEDIC (режим: {})", settings.app_env)

    # Первичная инициализация: создаём первого супер-админа и клинику по умолчанию,
    # если база ещё пустая (решает проблему «курицы и яйца»).
    await _bootstrap_if_needed()

    yield
    logger.info("Остановка приложения MEDIC")


async def _bootstrap_if_needed() -> None:
    """Мягкая инициализация БД при старте.

    Ошибки подключения к БД на стадии старта не должны валить приложение:
    логируем предупреждение и продолжаем (например, БД ещё не поднята).
    """
    if not settings.bootstrap_admin_on_startup:
        return

    try:
        from app.db.session import async_session_factory
        from app.services.bootstrap_service import BootstrapService

        async with async_session_factory() as session:
            service = BootstrapService(session)
            clinic = await service.ensure_default_clinic()
            created = await service.ensure_super_admin_on_startup()
            if created and created.clinic_id is None:
                created.clinic_id = clinic.id
            await session.commit()
    except Exception as exc:
        logger.warning("Пропуск bootstrap при старте: {}", exc)


app = FastAPI(
    title="MEDIC — Медицинская информационная система",
    description="""
API МИС для сети медицинских клиник.

### Роли и разграничение доступа:
* **Пациент** — доступ к своей медкарте, анализам и записи на приём.
* **Врач** — просмотр расписания, внесение осмотра в ЭМК, назначения, просмотр процедур.
* **Главный врач (Админ)** — доступ по всем филиалам, управление клиникой и финотчёты.
* **Медицинская сестра** — просмотр назначений, отметка о выполнении процедур.
* **Ресепшен** — создание пациентов, запись на приём, касса и приём оплат.
* **Бухгалтерия** — отчёты по приёмам врачей, проведённым процедурам и расчёт выплат.
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — разрешаем запросы от фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next) -> Response:
    """Генерирует или пробрасывает X-Request-ID и измеряет время ответа."""
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_ctx.set(req_id)
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
        process_time_ms = (time.perf_counter() - start_time) * 1000
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = f"{process_time_ms:.2f}"
        return response
    finally:
        request_id_ctx.reset(token)


@app.exception_handler(AppError)
async def domain_exception_handler(_: Request, exc: AppError) -> JSONResponse:
    """Единый формат ответов на доменные ошибки бизнес-логики."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.get("/health", tags=["Служебные"], summary="Проверка жизнеспособности сервиса")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


app.include_router(api_v1_router)
