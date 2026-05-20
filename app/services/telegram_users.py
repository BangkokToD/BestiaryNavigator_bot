"""Сервис управления Telegram-пользователями."""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.db.models import TelegramUser

_TELEGRAM_TEXT_MAX_LENGTH = 255


class TelegramUserRepository(Protocol):
    """Repository contract для управления моделью `TelegramUser`."""

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает Telegram-пользователя по Telegram ID.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            Модель пользователя или `None`.
        """

    def add(self, telegram_user: TelegramUser) -> None:
        """Добавляет Telegram-пользователя в unit of work.

        Args:
            telegram_user: Новая модель Telegram-пользователя.
        """

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""


class SqlAlchemyTelegramUserRepository:
    """SQLAlchemy-реализация repository для Telegram-пользователей."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        """Возвращает Telegram-пользователя по Telegram ID.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            Модель пользователя или `None`.
        """
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    def add(self, telegram_user: TelegramUser) -> None:
        """Добавляет Telegram-пользователя в текущую session.

        Args:
            telegram_user: Новая модель Telegram-пользователя.
        """
        self._session.add(telegram_user)

    async def flush(self) -> None:
        """Сбрасывает изменения в БД без commit."""
        await self._session.flush()


class TelegramUserService:
    """Сервис управления Telegram-пользователями.

    Сервис отвечает за idempotent upsert пользователя, обновление профиля,
    controlled refresh admin-cache и helpers доступа. Источником истины для
    admin-прав остаётся `TELEGRAM_ADMIN_ID` из runtime settings.
    """

    def __init__(self, *, repository: TelegramUserRepository, settings: Settings) -> None:
        """Инициализирует service.

        Args:
            repository: Repository для доступа к Telegram-пользователям.
            settings: Runtime settings с `telegram_admin_id`.
        """
        self._repository = repository
        self._settings = settings

    @classmethod
    def from_session(cls, *, session: AsyncSession, settings: Settings) -> "TelegramUserService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.
            settings: Runtime settings.

        Returns:
            Настроенный service.
        """
        return cls(
            repository=SqlAlchemyTelegramUserRepository(session),
            settings=settings,
        )

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт или обновляет Telegram-пользователя.

        Повторный вызов, например повторный `/start`, не создаёт дубль.
        У существующего пользователя обновляются username, display name,
        last_seen_at и controlled admin cache.

        Args:
            telegram_id: Внешний Telegram ID пользователя.
            username: Telegram username, если есть.
            display_name: Отображаемое имя, если есть.

        Returns:
            Созданная или обновлённая модель пользователя.

        Raises:
            ValueError: Если `telegram_id` некорректный.
        """
        normalized_telegram_id = _validate_telegram_id(telegram_id)
        normalized_username = _normalize_optional_text(username)
        normalized_display_name = _normalize_optional_text(display_name)
        seen_at = _utc_now()
        is_admin = self.is_admin_telegram_id(normalized_telegram_id)

        telegram_user = await self._repository.get_by_telegram_id(normalized_telegram_id)
        if telegram_user is None:
            telegram_user = TelegramUser(
                telegram_id=normalized_telegram_id,
                username=normalized_username,
                display_name=normalized_display_name,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
                is_admin_cached=is_admin,
            )
            self._repository.add(telegram_user)
        else:
            telegram_user.username = normalized_username
            telegram_user.display_name = normalized_display_name
            telegram_user.last_seen_at = seen_at
            telegram_user.is_admin_cached = is_admin

        await self._repository.flush()
        return telegram_user

    def is_admin_telegram_id(self, telegram_id: int) -> bool:
        """Проверяет, является ли Telegram ID админом.

        Args:
            telegram_id: Внешний Telegram ID пользователя.

        Returns:
            `True`, если ID совпадает с `TELEGRAM_ADMIN_ID` из settings.

        Raises:
            ValueError: Если `telegram_id` некорректный.
        """
        return _validate_telegram_id(telegram_id) == self._settings.telegram_admin_id

    def has_admin_access(self, telegram_user: TelegramUser | None) -> bool:
        """Проверяет admin-доступ пользователя.

        Проверка намеренно идёт через settings, а не через `is_admin_cached`,
        потому что cache-поле нужно для удобства отображения, но не является
        главным источником истины.

        Args:
            telegram_user: Модель Telegram-пользователя или `None`.

        Returns:
            `True`, если пользователь является админом по settings.
        """
        if telegram_user is None:
            return False

        return self.is_admin_telegram_id(telegram_user.telegram_id)


def _validate_telegram_id(value: int) -> int:
    """Проверяет Telegram ID.

    Args:
        value: Telegram ID.

    Returns:
        Проверенный положительный Telegram ID.

    Raises:
        ValueError: Если значение не является положительным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("telegram_id должен быть целым числом.")

    if value <= 0:
        raise ValueError("telegram_id должен быть положительным числом.")

    return value


def _normalize_optional_text(value: str | None) -> str | None:
    """Нормализует опциональное текстовое поле Telegram-профиля.

    Args:
        value: Сырой username или display name.

    Returns:
        Строка без пробелов по краям, обрезанная под DB-limit, или `None`.
    """
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return normalized[:_TELEGRAM_TEXT_MAX_LENGTH]


def _utc_now() -> datetime:
    """Возвращает текущее timezone-aware UTC время.

    Returns:
        Текущее время в UTC.
    """
    return datetime.now(UTC)


__all__ = [
    "SqlAlchemyTelegramUserRepository",
    "TelegramUserRepository",
    "TelegramUserService",
]
