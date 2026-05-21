"""Runtime entrypoint worker-сервиса."""

import asyncio
import signal

from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.worker.scheduler import (
    WorkerJobRegistry,
    WorkerScheduler,
    create_default_worker_registry,
)

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


async def run_worker(
    *,
    registry: WorkerJobRegistry | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Запускает worker runtime с scheduler loop.

    Args:
        registry: Явный registry для тестов или специальных сценариев.
            Если не передан, создаётся default registry без jobs.
        stop_event: Явный shutdown event для тестов. Если не передан,
            устанавливаются signal handlers runtime-процесса.
    """
    runtime_stop_event = stop_event if stop_event is not None else asyncio.Event()
    if stop_event is None:
        install_signal_handlers(runtime_stop_event)

    worker_registry = registry or create_default_worker_registry(
        default_interval_seconds=get_settings().sync_default_interval_seconds,
    )
    scheduler = WorkerScheduler(registry=worker_registry, logger=logger)

    logger.info("Worker service started")
    try:
        await scheduler.run(stop_event=runtime_stop_event)
    finally:
        logger.info("Worker service stopped")


def main() -> None:
    """Запускает async worker runtime."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
