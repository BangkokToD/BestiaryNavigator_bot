"""Настройки приложения BestiaryNavigator_bot.

Модуль является единственной точкой чтения runtime-конфигурации.
Остальные части приложения должны получать настройки через `get_settings()`
или через явно переданный экземпляр `Settings`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "dev", "test", "stage", "prod"]

_PLACEHOLDER_PREFIXES = ("replace-", "change-", "your-", "<")
_FORBIDDEN_SECRET_VALUES = {"", "...", "changeme", "change_me", "change-me"}


def _validate_database_url(value: str) -> str:
    """Проверяет URL подключения к PostgreSQL через asyncpg."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("DATABASE_URL не может быть пустым.")
    if not normalized.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL должен иметь префикс postgresql+asyncpg://.")
    return normalized


class Settings(BaseSettings):
    """Runtime-настройки приложения.

    Attributes:
        app_env: Окружение запуска приложения.
        app_base_url: Базовый публичный URL приложения.
        database_url: SQLAlchemy URL подключения к PostgreSQL через asyncpg.
        clash_api_base_url: Базовый URL Clash of Clans API.
        clash_api_token: Секретный Bearer/JWT token Clash API.
        clash_api_timeout_seconds: Timeout одного HTTP-запроса к Clash API.
        telegram_bot_token: Секретный токен Telegram-бота.
        telegram_admin_id: Telegram ID администратора из `.env`.
        web_session_secret: Секрет для подписи web-сессий/cookie.
        web_admin_cookie_name: Имя admin-cookie в веб-интерфейсе.
        sync_default_interval_seconds: Базовый интервал синхронизации worker.
        role_snapshot_max_age_minutes: Максимальный возраст snapshot ролей для `/warn`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: AppEnv = Field(validation_alias="APP_ENV")
    app_base_url: AnyHttpUrl = Field(validation_alias="APP_BASE_URL")

    database_url: str = Field(validation_alias="DATABASE_URL")

    clash_api_base_url: AnyHttpUrl = Field(validation_alias="CLASH_API_BASE_URL")
    clash_api_token: SecretStr = Field(validation_alias="CLASH_API_TOKEN")
    clash_api_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="CLASH_API_TIMEOUT_SECONDS",
        gt=0,
    )

    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_id: int = Field(validation_alias="TELEGRAM_ADMIN_ID", gt=0)

    web_session_secret: SecretStr = Field(validation_alias="WEB_SESSION_SECRET")
    web_admin_cookie_name: str = Field(
        validation_alias="WEB_ADMIN_COOKIE_NAME",
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]*$",
    )

    sync_default_interval_seconds: int = Field(
        validation_alias="SYNC_DEFAULT_INTERVAL_SECONDS",
        gt=0,
    )
    role_snapshot_max_age_minutes: int = Field(
        validation_alias="ROLE_SNAPSHOT_MAX_AGE_MINUTES",
        gt=0,
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Проверяет, что приложение использует asyncpg URL для PostgreSQL.

        Args:
            value: Значение `DATABASE_URL`.

        Returns:
            Нормализованное строковое значение без пробелов по краям.

        Raises:
            ValueError: Если URL пустой или не использует `postgresql+asyncpg://`.
        """
        return _validate_database_url(value)

    @field_validator("clash_api_token", "telegram_bot_token", "web_session_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        """Проверяет секреты на пустые и шаблонные значения.

        Args:
            value: Секретное значение.
            info: Контекст поля Pydantic.

        Returns:
            Исходный `SecretStr`, если значение валидно.

        Raises:
            ValueError: Если секрет пустой, похож на placeholder или слишком короткий.
        """
        raw_value = value.get_secret_value().strip()
        lowered_value = raw_value.lower()

        is_placeholder = lowered_value in _FORBIDDEN_SECRET_VALUES or lowered_value.startswith(
            _PLACEHOLDER_PREFIXES
        )
        if is_placeholder:
            raise ValueError(f"{info.field_name} должен быть заменён на реальное значение.")

        if info.field_name == "web_session_secret" and len(raw_value) < 32:
            raise ValueError("WEB_SESSION_SECRET должен быть не короче 32 символов.")

        if len(raw_value) < 8:
            raise ValueError(f"{info.field_name} должен быть не короче 8 символов.")

        return value


class DatabaseSettings(BaseSettings):
    """Настройки, необходимые DB-инструментам без загрузки внешних секретов."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(
        validation_alias=AliasChoices("ALEMBIC_DATABASE_URL", "DATABASE_URL"),
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Проверяет URL подключения к PostgreSQL через asyncpg."""
        return _validate_database_url(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает кешированный экземпляр настроек приложения.

    Returns:
        Загруженные и провалидированные настройки.
    """
    return Settings()


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    """Возвращает настройки, необходимые для DB-инструментов.

    Returns:
        Настройки подключения к базе данных.
    """
    return DatabaseSettings()
