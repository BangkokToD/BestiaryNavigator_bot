"""Тесты сборки Telegram bot dispatcher."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiogram import Router
from aiogram.types import ErrorEvent, Update

from app.bot.dispatcher import create_dispatcher
from app.bot.errors import log_handler_error
from app.bot.routers import create_root_router
from app.core.settings import Settings

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "123456:test-telegram-token",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeSessionFactory:
    """Fake session factory для dispatcher-тестов."""

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[Any]:
        """Возвращает пустой async context manager.

        Yields:
            Fake session object.
        """
        yield object()


def make_settings() -> Settings:
    """Создаёт settings для dispatcher-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_create_root_router_includes_modular_routers() -> None:
    """Проверяет модульное подключение routers."""
    router = create_root_router()

    assert isinstance(router, Router)
    assert router.name == "bot"
    assert [sub_router.name for sub_router in router.sub_routers] == [
        "system",
        "start",
        "account_linking",
        "warn",
        "register",
    ]


def test_create_dispatcher_wires_root_router_and_middleware() -> None:
    """Проверяет сборку dispatcher-а без запуска polling."""
    dispatcher = create_dispatcher(
        settings=make_settings(),
        session_factory=FakeSessionFactory(),  # type: ignore[arg-type]
    )

    assert [router.name for router in dispatcher.sub_routers] == ["bot"]


@pytest.mark.asyncio
async def test_log_handler_error_writes_exception_to_log(caplog: pytest.LogCaptureFixture) -> None:
    """Проверяет логирование handler errors."""
    error = RuntimeError("telegram handler failed")
    event = ErrorEvent(
        update=Update(update_id=1),
        exception=error,
    )

    await log_handler_error(event)

    assert "Telegram handler failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "telegram handler failed" in caplog.text
