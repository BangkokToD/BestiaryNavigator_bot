"""Runtime entrypoint backend-сервиса."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.logging import configure_logging, get_logger
from app.web import STATIC_DIR, web_router

SERVICE_NAME = "backend"

configure_logging(SERVICE_NAME)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Управляет lifecycle backend-приложения.

    Args:
        _: Экземпляр FastAPI, который пока не требует доступа.

    Yields:
        Управление runtime-серверу FastAPI.
    """
    logger.info("Backend service started")
    try:
        yield
    finally:
        logger.info("Backend service stopped")


app = FastAPI(
    title="BestiaryNavigator_bot",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(web_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Возвращает состояние backend-сервиса.

    Returns:
        Минимальный health payload.
    """
    return {
        "status": "ok",
        "service": SERVICE_NAME,
    }
