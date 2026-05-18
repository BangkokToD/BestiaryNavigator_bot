"""Тесты runtime-настроек приложения."""

import pytest
from pydantic import ValidationError

from app.core.settings import Settings

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


def make_settings(**overrides: object) -> Settings:
    """Создаёт настройки на основе валидных значений и точечных переопределений.

    Args:
        **overrides: Значения, которыми нужно заменить базовый набор настроек.

    Returns:
        Провалидированный экземпляр `Settings`.
    """
    values = VALID_SETTINGS | overrides
    return Settings(**values)


def test_settings_accepts_valid_values() -> None:
    """Проверяет, что валидный набор переменных окружения принимается."""
    settings = make_settings()

    assert settings.app_env == "local"
    assert str(settings.app_base_url).rstrip("/") == "http://localhost:8000"
    assert settings.database_url == "postgresql+asyncpg://bn:bn@postgres:5432/bestiary"
    assert settings.telegram_admin_id == 123456789
    assert settings.sync_default_interval_seconds == 900
    assert settings.role_snapshot_max_age_minutes == 30


def test_settings_rejects_invalid_database_url() -> None:
    """Проверяет запрет не-asyncpg URL для базы данных."""
    with pytest.raises(ValidationError):
        make_settings(DATABASE_URL="postgresql://bn:bn@postgres:5432/bestiary")


def test_settings_rejects_placeholder_secret() -> None:
    """Проверяет, что placeholder вместо секрета не проходит валидацию."""
    with pytest.raises(ValidationError):
        make_settings(CLASH_API_TOKEN="replace-with-clash-api-token")


def test_settings_rejects_short_web_session_secret() -> None:
    """Проверяет минимальную длину секрета web-сессии."""
    with pytest.raises(ValidationError):
        make_settings(WEB_SESSION_SECRET="short")


def test_settings_rejects_non_positive_admin_id() -> None:
    """Проверяет, что Telegram admin ID должен быть положительным числом."""
    with pytest.raises(ValidationError):
        make_settings(TELEGRAM_ADMIN_ID=0)


def test_settings_redacts_secret_values_in_repr() -> None:
    """Проверяет, что секреты не раскрываются в repr/model_dump."""
    settings = make_settings()

    rendered_settings = f"{settings!r} {settings.model_dump()!r}"

    assert "test-clash-token-123" not in rendered_settings
    assert "test-telegram-token-123" not in rendered_settings
    assert "test-session-secret-value-1234567890" not in rendered_settings
    assert "**********" in rendered_settings