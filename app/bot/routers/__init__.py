"""Routers Telegram bot слоя."""

from aiogram import Router

from app.bot.routers.system import create_system_router


def create_root_router() -> Router:
    """Создаёт корневой router Telegram bot.

    Returns:
        Router, в который подключены модульные routers бота.
    """
    router = Router(name="bot")
    router.include_router(create_system_router())

    return router


__all__ = [
    "create_root_router",
]
