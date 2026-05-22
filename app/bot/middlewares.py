"""Middlewares Telegram bot слоя."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import Settings
from app.db.models import TelegramUser
from app.services import TelegramUserService


class TelegramUserUpsertService(Protocol):
    """Contract сервиса TelegramUser для middleware."""

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт или обновляет Telegram-пользователя.

        Args:
            telegram_id: Telegram ID пользователя.
            username: Telegram username.
            display_name: Отображаемое имя.

        Returns:
            Модель TelegramUser.
        """


class TelegramUserServiceFactory(Protocol):
    """Contract фабрики TelegramUser service."""

    def __call__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
    ) -> TelegramUserUpsertService:
        """Создаёт сервис TelegramUser.

        Args:
            session: Async SQLAlchemy session.
            settings: Runtime settings.

        Returns:
            Сервис TelegramUser.
        """


class BotSessionFactory(Protocol):
    """Contract фабрики DB-session для bot middleware."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Создаёт async context manager DB-session.

        Returns:
            Async context manager SQLAlchemy session.
        """


type BotHandler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class TelegramUserMiddleware(BaseMiddleware):
    """Создаёт/обновляет TelegramUser и прокидывает зависимости handler-ам.

    Middleware работает на уровне update, поэтому подходит для message,
    callback query и будущих inline-сценариев. Если update не содержит
    `from_user`, handler всё равно получает DB-session и service, но
    `telegram_user` будет `None`.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: BotSessionFactory,
        telegram_user_service_factory: TelegramUserServiceFactory | None = None,
    ) -> None:
        """Инициализирует middleware.

        Args:
            settings: Runtime settings приложения.
            session_factory: Фабрика DB-session.
            telegram_user_service_factory: Явная фабрика сервиса для тестов.
        """
        self._settings = settings
        self._session_factory = session_factory
        self._telegram_user_service_factory = (
            telegram_user_service_factory or _default_telegram_user_service_factory
        )

    async def __call__(
        self,
        handler: BotHandler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Открывает session, обновляет TelegramUser и вызывает handler.

        Args:
            handler: Следующий handler/middleware.
            event: Aiogram update/event.
            data: Mutable context aiogram update.

        Returns:
            Результат handler-а.

        Raises:
            Exception: Любая ошибка handler-а после rollback.
        """
        async with self._session_factory() as session:
            telegram_user_service = self._telegram_user_service_factory(
                session=session,
                settings=self._settings,
            )
            data["db_session"] = session
            data["settings"] = self._settings
            data["telegram_user_service"] = telegram_user_service
            data["telegram_user"] = await self._upsert_user_from_event(
                event=event,
                telegram_user_service=telegram_user_service,
            )

            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise

            await session.commit()
            return result

    async def _upsert_user_from_event(
        self,
        *,
        event: TelegramObject,
        telegram_user_service: TelegramUserUpsertService,
    ) -> TelegramUser | None:
        """Создаёт/обновляет TelegramUser из update.

        Args:
            event: Aiogram update/event.
            telegram_user_service: Сервис TelegramUser.

        Returns:
            Модель TelegramUser или `None`, если update без `from_user`.
        """
        user = _extract_from_user(event)
        if user is None or bool(getattr(user, "is_bot", False)):
            return None

        return await telegram_user_service.upsert_telegram_user(
            telegram_id=int(user.id),
            username=_optional_text(getattr(user, "username", None)),
            display_name=_display_name_from_user(user),
        )


def _default_telegram_user_service_factory(
    *,
    session: AsyncSession,
    settings: Settings,
) -> TelegramUserUpsertService:
    """Создаёт production TelegramUserService.

    Args:
        session: Async SQLAlchemy session.
        settings: Runtime settings.

    Returns:
        Сервис TelegramUser.
    """
    return TelegramUserService.from_session(session=session, settings=settings)


def _extract_from_user(event: object) -> object | None:
    """Достаёт `from_user` из aiogram event/update.

    Args:
        event: Aiogram object или тестовый объект с совместимыми атрибутами.

    Returns:
        Пользователь Telegram или `None`.
    """
    direct_user = getattr(event, "from_user", None)
    if direct_user is not None:
        return direct_user

    for attribute_name in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "callback_query",
        "inline_query",
        "chosen_inline_result",
        "shipping_query",
        "pre_checkout_query",
        "poll_answer",
        "chat_member",
        "my_chat_member",
    ):
        nested_event = getattr(event, attribute_name, None)
        if nested_event is None:
            continue

        user = _extract_from_user(nested_event)
        if user is not None:
            return user

    return None


def _display_name_from_user(user: object) -> str | None:
    """Формирует отображаемое имя из Telegram user object.

    Args:
        user: Aiogram User или совместимый тестовый объект.

    Returns:
        Отображаемое имя или `None`.
    """
    full_name = _optional_text(getattr(user, "full_name", None))
    if full_name is not None:
        return full_name

    first_name = _optional_text(getattr(user, "first_name", None))
    last_name = _optional_text(getattr(user, "last_name", None))
    parts = [part for part in (first_name, last_name) if part]

    if parts:
        return " ".join(parts)

    return _optional_text(getattr(user, "username", None))


def _optional_text(value: object) -> str | None:
    """Нормализует опциональное текстовое значение.

    Args:
        value: Исходное значение.

    Returns:
        Непустая строка или `None`.
    """
    if value is None:
        return None

    normalized = str(value).strip()
    return normalized or None


__all__ = [
    "BotHandler",
    "BotSessionFactory",
    "TelegramUserMiddleware",
    "TelegramUserServiceFactory",
    "TelegramUserUpsertService",
]
