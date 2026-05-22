"""Тесты middleware Telegram bot слоя."""

from collections.abc import Callable
from dataclasses import dataclass
from inspect import iscoroutinefunction
from typing import Any

import pytest

from app.bot.middlewares import TelegramUserMiddleware
from app.core.settings import Settings
from app.db.models import TelegramUser

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


@dataclass(slots=True)
class FakeTelegramUser:
    """Fake Telegram user для middleware-тестов."""

    id: int
    username: str | None
    full_name: str | None
    is_bot: bool = False


@dataclass(slots=True)
class FakeMessage:
    """Fake message event."""

    from_user: FakeTelegramUser | None


@dataclass(slots=True)
class FakeUpdate:
    """Fake update event."""

    message: FakeMessage | None = None


class FakeSession:
    """Fake async DB-session."""

    def __init__(self) -> None:
        """Инициализирует fake session."""
        self.commit_count = 0
        self.rollback_count = 0

    async def __aenter__(self) -> "FakeSession":
        """Открывает fake context manager."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Закрывает fake context manager."""

    async def commit(self) -> None:
        """Фиксирует commit."""
        self.commit_count += 1

    async def rollback(self) -> None:
        """Фиксирует rollback."""
        self.rollback_count += 1


class FakeSessionFactory:
    """Fake session factory."""

    def __init__(self) -> None:
        """Инициализирует factory."""
        self.session = FakeSession()
        self.call_count = 0

    def __call__(self) -> FakeSession:
        """Возвращает fake session.

        Returns:
            Fake session.
        """
        self.call_count += 1
        return self.session


class FakeTelegramUserService:
    """Fake TelegramUser service."""

    def __init__(self) -> None:
        """Инициализирует fake service."""
        self.calls: list[dict[str, object]] = []

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Фиксирует upsert TelegramUser."""
        self.calls.append(
            {
                "telegram_id": telegram_id,
                "username": username,
                "display_name": display_name,
            }
        )
        return TelegramUser(
            telegram_id=telegram_id,
            username=username,
            display_name=display_name,
            is_admin_cached=False,
        )


class FakeAccountLinkingService:
    """Fake account linking service для проверки DI wiring."""

    def __init__(self) -> None:
        """Инициализирует fake service."""


def make_settings() -> Settings:
    """Создаёт settings для middleware-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


@pytest.mark.asyncio
async def test_telegram_user_middleware_upserts_user_and_commits() -> None:
    """Проверяет upsert TelegramUser на update с from_user."""
    session_factory = FakeSessionFactory()
    fake_service = FakeTelegramUserService()
    fake_account_linking_service = FakeAccountLinkingService()
    middleware = TelegramUserMiddleware(
        settings=make_settings(),
        session_factory=session_factory,  # type: ignore[arg-type]
        telegram_user_service_factory=lambda **_: fake_service,
        account_linking_service_factory=lambda **_: fake_account_linking_service,
    )
    event = FakeUpdate(
        message=FakeMessage(
            from_user=FakeTelegramUser(
                id=123456789,
                username=" bangkok ",
                full_name=" Bangkok ToD ",
            )
        )
    )
    captured_data: dict[str, Any] = {}

    async def handler(_: object, data: dict[str, Any]) -> str:
        captured_data.update(data)
        return "ok"

    result = await middleware(handler, event, {})

    assert result == "ok"
    assert fake_service.calls == [
        {
            "telegram_id": 123456789,
            "username": "bangkok",
            "display_name": "Bangkok ToD",
        }
    ]
    assert isinstance(captured_data["telegram_user"], TelegramUser)
    assert captured_data["telegram_user_service"] is fake_service
    assert captured_data["account_linking_service"] is fake_account_linking_service
    assert captured_data["settings"] == make_settings()
    assert session_factory.session.commit_count == 1
    assert session_factory.session.rollback_count == 0


@pytest.mark.asyncio
async def test_telegram_user_middleware_rolls_back_on_handler_error() -> None:
    """Проверяет rollback при ошибке handler-а."""
    session_factory = FakeSessionFactory()
    fake_service = FakeTelegramUserService()
    fake_account_linking_service = FakeAccountLinkingService()
    middleware = TelegramUserMiddleware(
        settings=make_settings(),
        session_factory=session_factory,  # type: ignore[arg-type]
        telegram_user_service_factory=lambda **_: fake_service,
        account_linking_service_factory=lambda **_: fake_account_linking_service,
    )
    event = FakeUpdate(
        message=FakeMessage(
            from_user=FakeTelegramUser(
                id=123456789,
                username="bangkok",
                full_name="Bangkok ToD",
            )
        )
    )

    async def handler(_: object, __: dict[str, Any]) -> str:
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError, match="handler failed"):
        await middleware(handler, event, {})

    assert fake_service.calls == [
        {
            "telegram_id": 123456789,
            "username": "bangkok",
            "display_name": "Bangkok ToD",
        }
    ]
    assert session_factory.session.commit_count == 0
    assert session_factory.session.rollback_count == 1


@pytest.mark.asyncio
async def test_telegram_user_middleware_skips_update_without_user() -> None:
    """Проверяет update без from_user."""
    session_factory = FakeSessionFactory()
    fake_service = FakeTelegramUserService()
    fake_account_linking_service = FakeAccountLinkingService()
    middleware = TelegramUserMiddleware(
        settings=make_settings(),
        session_factory=session_factory,  # type: ignore[arg-type]
        telegram_user_service_factory=lambda **_: fake_service,
        account_linking_service_factory=lambda **_: fake_account_linking_service,
    )
    captured_data: dict[str, Any] = {}

    async def handler(_: object, data: dict[str, Any]) -> str:
        captured_data.update(data)
        return "ok"

    result = await middleware(handler, FakeUpdate(message=None), {})

    assert result == "ok"
    assert fake_service.calls == []
    assert captured_data["telegram_user"] is None
    assert session_factory.session.commit_count == 1
    assert session_factory.session.rollback_count == 0


def test_bot_middleware_type_contract_accepts_async_handler() -> None:
    """Проверяет, что handler contract остаётся async-callable."""

    async def handler(_: object, __: dict[str, Any]) -> str:
        return "ok"

    assert isinstance(handler, Callable)
    assert iscoroutinefunction(handler)
