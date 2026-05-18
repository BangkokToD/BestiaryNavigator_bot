"""Runtime entrypoint worker-сервиса."""

import asyncio
import signal

from app.core.logging import configure_logging, get_logger

SERVICE_NAME = "worker"
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


async def run_worker() -> None:
    """Запускает worker-заглушку без зарегистрированных jobs."""
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)

    logger.info("Worker service started")
    logger.info("Worker job registry is empty")

    await stop_event.wait()

    logger.info("Worker service stopped")


def main() -> None:
    """Запускает async worker runtime."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()