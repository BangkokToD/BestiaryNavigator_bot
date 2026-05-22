"""Error handlers Telegram bot слоя."""

from aiogram.types import ErrorEvent

from app.core.logging import get_logger

logger = get_logger(__name__)


async def log_handler_error(event: ErrorEvent) -> None:
    """Логирует ошибку aiogram handler-а.

    Handler намеренно не отправляет пользователю сообщение. User-facing
    обработка ошибок будет добавляться в конкретных command/callback flows.

    Args:
        event: Aiogram error event.
    """
    error = event.exception
    logger.error(
        "Telegram handler failed",
        exc_info=(type(error), error, error.__traceback__),
    )


__all__ = [
    "log_handler_error",
]
