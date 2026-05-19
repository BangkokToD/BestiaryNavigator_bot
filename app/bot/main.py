"""Runtime entrypoint Telegram bot-сервиса."""

import asyncio
import signal

from app.core.logging import configure_logging, get_logger

SERVICE_NAME = "bot"
SHUTDOWN_SIGNALS = (signal.SIGINT, signal.SIGTERM)

configure_logging(SERVICE_NAME)
logger = get_logger(__name__)


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Подключает обработчики завершения runtime-процесса.

    Args:
        stop_event: Event, который переводит сервис в режим завершения.
    """
    loop = asyncio.get_running_loop()

    for shutdown_signal in SHUTDOWN_SIGNALS:
        loop.add_signal_handler(shutdown_signal, stop_event.set)


async def run_bot() -> None:
    """Запускает bot-заглушку без polling/webhook."""
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)

    logger.info("Bot service started")
    logger.info("Bot polling and webhook are disabled in bootstrap entrypoint")

    await stop_event.wait()

    logger.info("Bot service stopped")


def main() -> None:
    """Запускает async bot runtime."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
