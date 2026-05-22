"""Фабрики Bot и Dispatcher для Telegram runtime."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.errors import log_handler_error
from app.bot.middlewares import TelegramUserMiddleware
from app.bot.routers import create_root_router
from app.core.logging import get_logger
from app.core.settings import Settings
from app.db.session import get_session_factory

logger = get_logger(__name__)


class BotSessionFactory(Protocol):
    """Contract фабрики async DB-session для bot middleware."""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Создаёт async context manager DB-session.

        Returns:
            Async context manager SQLAlchemy session.
        """


def create_bot(*, settings: Settings) -> Bot:
    """Создаёт aiogram Bot.

    Args:
        settings: Runtime settings приложения.

    Returns:
        Экземпляр aiogram Bot.
    """
    return Bot(token=settings.telegram_bot_token.get_secret_value())


def create_dispatcher(
    *,
    settings: Settings,
    session_factory: BotSessionFactory | None = None,
) -> Dispatcher:
    """Создаёт и настраивает aiogram Dispatcher.

    Args:
        settings: Runtime settings приложения.
        session_factory: Явная фабрика DB-session для тестов. Если не передана,
            используется глобальная session factory приложения.

    Returns:
        Настроенный dispatcher с routers, middleware и lifecycle hooks.
    """
    dispatcher = Dispatcher()
    resolved_session_factory = session_factory or get_session_factory()

    dispatcher.update.outer_middleware(
        TelegramUserMiddleware(
            settings=settings,
            session_factory=resolved_session_factory,
        )
    )
    dispatcher.include_router(create_root_router())
    dispatcher.errors.register(log_handler_error)
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    return dispatcher


async def on_startup(bot: Bot) -> None:
    """Логирует запуск dispatcher-а.

    Args:
        bot: Экземпляр aiogram Bot.
    """
    bot_id = getattr(bot, "id", None)
    logger.info("Bot dispatcher started: bot_id=%s", bot_id or "unknown")


async def on_shutdown(bot: Bot) -> None:
    """Логирует остановку dispatcher-а.

    Args:
        bot: Экземпляр aiogram Bot.
    """
    bot_id = getattr(bot, "id", None)
    logger.info("Bot dispatcher stopped: bot_id=%s", bot_id or "unknown")


__all__ = [
    "BotSessionFactory",
    "create_bot",
    "create_dispatcher",
    "on_shutdown",
    "on_startup",
]
