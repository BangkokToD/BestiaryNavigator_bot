"""Тесты сервиса управления Telegram-пользователями."""

from datetime import UTC, datetime

import pytest

from app.core.settings import Settings
from app.db.models import TelegramUser
from app.services import TelegramUserService

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


class InMemoryTelegramUserRepository:
    """In-memory repository для unit-тестов TelegramUserService."""

    def __init__(self, users: list[TelegramUser] | None = None) -> None:
        """Инициализирует repository.

        Args:
            users: Начальный набор Telegram-пользователей.
        """
        self.users = {user.telegram_id: user for user in users or []}
        self.added_users: list[TelegramUser] = []
        self.flush_count = 0

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает пользователя по Telegram ID.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            Модель пользователя или `None`.
        """
        return self.users.get(telegram_id)

    def add(self, telegram_user: TelegramUser) -> None:
        """Добавляет пользователя в in-memory storage.

        Args:
            telegram_user: Модель Telegram-пользователя.
        """
        self.added_users.append(telegram_user)
        self.users[telegram_user.telegram_id] = telegram_user

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_settings(*, telegram_admin_id: int = 123456789) -> Settings:
    """Создаёт runtime settings для unit-тестов.

    Args:
        telegram_admin_id: Telegram ID администратора.

    Returns:
        Провалидированный экземпляр settings.
    """
    return Settings(**(VALID_SETTINGS | {"TELEGRAM_ADMIN_ID": telegram_admin_id}))


@pytest.mark.asyncio
async def test_telegram_user_service_creates_regular_user() -> None:
    """Проверяет создание обычного Telegram-пользователя."""
    repository = InMemoryTelegramUserRepository()
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=999),
    )

    telegram_user = await service.upsert_telegram_user(
        telegram_id=123456789,
        username=" bangkok ",
        display_name=" Bangkok ToD ",
    )

    assert repository.added_users == [telegram_user]
    assert repository.flush_count == 1
    assert repository.users == {123456789: telegram_user}
    assert telegram_user.telegram_id == 123456789
    assert telegram_user.username == "bangkok"
    assert telegram_user.display_name == "Bangkok ToD"
    assert telegram_user.first_seen_at is not None
    assert telegram_user.last_seen_at is not None
    assert telegram_user.is_admin_cached is False
    assert service.has_admin_access(telegram_user) is False


@pytest.mark.asyncio
async def test_telegram_user_service_repeated_start_updates_without_duplicate() -> None:
    """Проверяет, что повторный `/start` обновляет пользователя без дубля."""
    first_seen_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    old_last_seen_at = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    existing_user = TelegramUser(
        telegram_id=42,
        username="old_username",
        display_name="Old name",
        first_seen_at=first_seen_at,
        last_seen_at=old_last_seen_at,
        is_admin_cached=False,
    )
    repository = InMemoryTelegramUserRepository([existing_user])
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=999),
    )

    telegram_user = await service.upsert_telegram_user(
        telegram_id=42,
        username="new_username",
        display_name="New name",
    )

    assert telegram_user is existing_user
    assert repository.added_users == []
    assert repository.flush_count == 1
    assert len(repository.users) == 1
    assert telegram_user.username == "new_username"
    assert telegram_user.display_name == "New name"
    assert telegram_user.first_seen_at == first_seen_at
    assert telegram_user.last_seen_at != old_last_seen_at
    assert telegram_user.is_admin_cached is False


@pytest.mark.asyncio
async def test_telegram_user_service_marks_admin_from_settings() -> None:
    """Проверяет определение админа только через settings."""
    repository = InMemoryTelegramUserRepository()
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=42),
    )

    telegram_user = await service.upsert_telegram_user(
        telegram_id=42,
        username="admin",
        display_name="Admin",
    )

    assert telegram_user.is_admin_cached is True
    assert service.is_admin_telegram_id(42) is True
    assert service.has_admin_access(telegram_user) is True


@pytest.mark.asyncio
async def test_telegram_user_service_updates_admin_cache_controlled_by_settings() -> None:
    """Проверяет controlled update `is_admin_cached` при повторном upsert."""
    existing_user = TelegramUser(
        telegram_id=42,
        username="old_admin",
        display_name="Old Admin",
        first_seen_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        is_admin_cached=True,
    )
    repository = InMemoryTelegramUserRepository([existing_user])
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=999),
    )

    telegram_user = await service.upsert_telegram_user(
        telegram_id=42,
        username="regular",
        display_name="Regular",
    )

    assert telegram_user is existing_user
    assert telegram_user.is_admin_cached is False
    assert service.has_admin_access(telegram_user) is False
    assert repository.flush_count == 1


@pytest.mark.asyncio
async def test_telegram_user_service_normalizes_empty_profile_fields_to_none() -> None:
    """Проверяет нормализацию пустых profile fields."""
    repository = InMemoryTelegramUserRepository()
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=999),
    )

    telegram_user = await service.upsert_telegram_user(
        telegram_id=42,
        username="   ",
        display_name=None,
    )

    assert telegram_user.username is None
    assert telegram_user.display_name is None


@pytest.mark.asyncio
async def test_telegram_user_service_rejects_invalid_telegram_id() -> None:
    """Проверяет запрет некорректного Telegram ID."""
    repository = InMemoryTelegramUserRepository()
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=999),
    )

    with pytest.raises(ValueError):
        await service.upsert_telegram_user(
            telegram_id=0,
            username="user",
            display_name="User",
        )

    assert repository.added_users == []
    assert repository.flush_count == 0


def test_telegram_user_service_has_admin_access_returns_false_for_none() -> None:
    """Проверяет helper доступа для анонимного пользователя."""
    repository = InMemoryTelegramUserRepository()
    service = TelegramUserService(
        repository=repository,
        settings=make_settings(telegram_admin_id=42),
    )

    assert service.has_admin_access(None) is False
