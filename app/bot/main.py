"""Runtime entrypoint Telegram bot-сервиса."""

import asyncio

from app.bot.dispatcher import create_bot, create_dispatcher
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings

SERVICE_NAME = "bot"

configure_logging(SERVICE_NAME)
logger = get_logger(__name__)


async def run_bot() -> None:
    """Запускает Telegram bot polling runtime."""
    settings = get_settings()
    bot = create_bot(settings=settings)
    dispatcher = create_dispatcher(settings=settings)

    logger.info("Bot service starting")

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        logger.info("Bot service stopped")


def main() -> None:
    """Запускает async Telegram bot runtime."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
