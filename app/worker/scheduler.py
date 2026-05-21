"""Scheduler и registry worker jobs.

Модуль содержит инфраструктуру запуска jobs. Конкретные worker jobs
подключаются через default registry и не должны попадать в runtime entrypoint.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import transactional_session

WorkerJobHandler = Callable[["WorkerJobContext"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class WorkerJobContext:
    """Контекст одного запуска worker job.

    Attributes:
        job_name: Уникальное имя job в registry.
        started_at: Время начала запуска job в UTC.
        stop_event: Общий event завершения worker runtime.
        session: DB session внутри транзакции или `None` для jobs без БД.
    """

    job_name: str
    started_at: datetime
    stop_event: asyncio.Event
    session: AsyncSession | None = None

    @property
    def should_stop(self) -> bool:
        """Проверяет, запрошено ли завершение worker runtime.

        Returns:
            `True`, если worker уже получил сигнал завершения.
        """
        return self.stop_event.is_set()


@dataclass(frozen=True, slots=True)
class WorkerJob:
    """Декларативное описание worker job.

    Attributes:
        name: Уникальное имя job для логов и registry.
        handler: Async callable с бизнес-логикой job.
        interval_seconds: Интервал повторного запуска. Если `None`, используется
            `WorkerJobRegistry.default_interval_seconds`.
        run_in_transaction: Нужно ли выполнять handler внутри DB-транзакции.
        run_on_start: Нужно ли запускать job сразу при старте scheduler loop.
    """

    name: str
    handler: WorkerJobHandler
    interval_seconds: int | None = None
    run_in_transaction: bool = True
    run_on_start: bool = True

    def __post_init__(self) -> None:
        """Проверяет декларацию job при создании.

        Raises:
            ValueError: Если имя пустое или interval некорректный.
        """
        if not self.name.strip():
            raise ValueError("Worker job name не может быть пустым.")

        if self.interval_seconds is not None:
            _validate_positive_interval(self.interval_seconds, field_name="interval_seconds")


@dataclass(frozen=True, slots=True)
class WorkerJobExecutionResult:
    """Результат одного запуска worker job."""

    job_name: str
    started_at: datetime
    duration_seconds: float
    success: bool
    skipped: bool = False
    error: BaseException | None = None


@dataclass(slots=True)
class WorkerJobRegistry:
    """Registry декларативно зарегистрированных worker jobs."""

    default_interval_seconds: int
    _jobs: dict[str, WorkerJob] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Проверяет настройки registry.

        Raises:
            ValueError: Если default interval некорректный.
        """
        _validate_positive_interval(
            self.default_interval_seconds,
            field_name="default_interval_seconds",
        )

    def register(self, job: WorkerJob) -> WorkerJob:
        """Регистрирует worker job.

        Args:
            job: Декларация worker job.

        Returns:
            Зарегистрированная декларация job.

        Raises:
            ValueError: Если job с таким именем уже зарегистрирована.
        """
        if job.name in self._jobs:
            raise ValueError(f"Worker job уже зарегистрирована: {job.name}.")

        self._jobs[job.name] = job
        return job

    def job(
        self,
        *,
        name: str,
        interval_seconds: int | None = None,
        run_in_transaction: bool = True,
        run_on_start: bool = True,
    ) -> Callable[[WorkerJobHandler], WorkerJobHandler]:
        """Создаёт decorator для декларативной регистрации job.

        Args:
            name: Уникальное имя job.
            interval_seconds: Индивидуальный интервал запуска.
            run_in_transaction: Нужно ли выполнять job внутри DB-транзакции.
            run_on_start: Нужно ли запускать job сразу после старта scheduler loop.

        Returns:
            Decorator, который регистрирует handler и возвращает его без обёртки.
        """

        def decorator(handler: WorkerJobHandler) -> WorkerJobHandler:
            """Регистрирует handler как worker job.

            Args:
                handler: Async callable worker job.

            Returns:
                Исходный handler без runtime-обёртки.
            """
            self.register(
                WorkerJob(
                    name=name,
                    handler=handler,
                    interval_seconds=interval_seconds,
                    run_in_transaction=run_in_transaction,
                    run_on_start=run_on_start,
                )
            )
            return handler

        return decorator

    def get(self, job_name: str) -> WorkerJob:
        """Возвращает job по имени.

        Args:
            job_name: Уникальное имя job.

        Returns:
            Декларация worker job.

        Raises:
            KeyError: Если job не зарегистрирована.
        """
        return self._jobs[job_name]

    def list_jobs(self) -> tuple[WorkerJob, ...]:
        """Возвращает зарегистрированные jobs в порядке регистрации.

        Returns:
            Tuple деклараций worker jobs.
        """
        return tuple(self._jobs.values())

    def is_empty(self) -> bool:
        """Проверяет, пуст ли registry.

        Returns:
            `True`, если jobs ещё не зарегистрированы.
        """
        return not self._jobs

    def resolve_interval_seconds(self, job: WorkerJob) -> int:
        """Возвращает итоговый interval для job.

        Args:
            job: Декларация worker job.

        Returns:
            Индивидуальный interval job или default interval registry.
        """
        return job.interval_seconds or self.default_interval_seconds


class WorkerScheduler:
    """Scheduler loop для зарегистрированных worker jobs."""

    def __init__(
        self,
        *,
        registry: WorkerJobRegistry,
        logger: logging.Logger | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        """Инициализирует scheduler.

        Args:
            registry: Registry worker jobs.
            logger: Logger runtime-сервиса.
            session_factory: Явная DB session factory для тестов или runtime.
        """
        self._registry = registry
        self._logger = logger or logging.getLogger(__name__)
        self._session_factory = session_factory
        self._job_locks: dict[str, asyncio.Lock] = {}

    async def run(self, *, stop_event: asyncio.Event) -> None:
        """Запускает scheduler loop до сигнала завершения.

        Args:
            stop_event: Общий event graceful shutdown.
        """
        jobs = self._registry.list_jobs()
        if not jobs:
            self._logger.info("Worker job registry is empty")
            await stop_event.wait()
            return

        self._logger.info("Worker scheduler started with %s job(s)", len(jobs))
        job_tasks = [
            asyncio.create_task(
                self._run_job_loop(job, stop_event=stop_event),
                name=f"worker-job:{job.name}",
            )
            for job in jobs
        ]

        await stop_event.wait()
        self._logger.info("Worker scheduler stopping")
        await asyncio.gather(*job_tasks, return_exceptions=True)
        self._logger.info("Worker scheduler stopped")

    async def run_job_once(
        self,
        job_name: str,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> WorkerJobExecutionResult:
        """Запускает одну job вручную с lock-guard от параллельного запуска.

        Args:
            job_name: Имя зарегистрированной job.
            stop_event: Общий event завершения. Если не передан, создаётся
                локальный event для одиночного запуска.

        Returns:
            Результат запуска job.
        """
        job = self._registry.get(job_name)
        job_stop_event = stop_event if stop_event is not None else asyncio.Event()
        lock = self._job_locks.setdefault(job.name, asyncio.Lock())

        if lock.locked():
            now = datetime.now(UTC)
            self._logger.warning(
                "Worker job skipped because previous run is still active: %s",
                job.name,
            )
            return WorkerJobExecutionResult(
                job_name=job.name,
                started_at=now,
                duration_seconds=0.0,
                success=False,
                skipped=True,
            )

        async with lock:
            return await self._execute_job(job, stop_event=job_stop_event)

    async def _run_job_loop(self, job: WorkerJob, *, stop_event: asyncio.Event) -> None:
        """Запускает бесконечный loop одной job.

        Args:
            job: Декларация worker job.
            stop_event: Общий event graceful shutdown.
        """
        if job.run_on_start and not stop_event.is_set():
            await self.run_job_once(job.name, stop_event=stop_event)

        while not stop_event.is_set():
            interval_seconds = self._registry.resolve_interval_seconds(job)
            stopped = await _sleep_or_stop(stop_event, delay_seconds=interval_seconds)
            if stopped:
                break

            await self.run_job_once(job.name, stop_event=stop_event)

    async def _execute_job(
        self,
        job: WorkerJob,
        *,
        stop_event: asyncio.Event,
    ) -> WorkerJobExecutionResult:
        """Выполняет handler job и изолирует runtime-ошибки.

        Args:
            job: Декларация worker job.
            stop_event: Общий event graceful shutdown.

        Returns:
            Результат запуска job.
        """
        started_at = datetime.now(UTC)
        started_perf = time.perf_counter()
        self._logger.info("Worker job started: %s", job.name)

        try:
            if job.run_in_transaction:
                async with transactional_session(self._session_factory) as session:
                    await job.handler(
                        WorkerJobContext(
                            job_name=job.name,
                            started_at=started_at,
                            stop_event=stop_event,
                            session=session,
                        )
                    )
            else:
                await job.handler(
                    WorkerJobContext(
                        job_name=job.name,
                        started_at=started_at,
                        stop_event=stop_event,
                        session=None,
                    )
                )
        except Exception as exc:
            duration_seconds = time.perf_counter() - started_perf
            self._logger.exception(
                "Worker job failed: %s duration_seconds=%.3f",
                job.name,
                duration_seconds,
            )
            return WorkerJobExecutionResult(
                job_name=job.name,
                started_at=started_at,
                duration_seconds=duration_seconds,
                success=False,
                error=exc,
            )

        duration_seconds = time.perf_counter() - started_perf
        self._logger.info(
            "Worker job finished: %s duration_seconds=%.3f",
            job.name,
            duration_seconds,
        )
        return WorkerJobExecutionResult(
            job_name=job.name,
            started_at=started_at,
            duration_seconds=duration_seconds,
            success=True,
        )


def create_default_worker_registry(*, default_interval_seconds: int) -> WorkerJobRegistry:
    """Создаёт default registry worker jobs для runtime-процесса.

    Imports worker jobs выполняются внутри функции, чтобы scheduler оставался
    базовой инфраструктурой и не создавал циклические импорты при загрузке
    job-модулей.

    Args:
        default_interval_seconds: Default interval из runtime settings.

    Returns:
        Registry с jobs, доступными текущему worker runtime.
    """
    from app.worker.jobs import (
        register_detect_kick_candidates_job,
        register_detect_missed_cwl_attacks_job,
        register_detect_missed_war_attacks_job,
        register_detect_raid_violations_job,
        register_detect_unlinked_accounts_job,
        register_expire_warnings_by_cwl_season_job,
        register_send_scheduled_notifications_job,
        register_sync_clans_job,
        register_sync_current_wars_job,
        register_sync_cwl_job,
        register_sync_members_job,
        register_sync_player_profiles_job,
        register_sync_raids_job,
    )

    registry = WorkerJobRegistry(default_interval_seconds=default_interval_seconds)
    register_sync_clans_job(registry)
    register_sync_members_job(registry)
    register_sync_player_profiles_job(registry)
    register_sync_current_wars_job(registry)
    register_sync_cwl_job(registry)
    register_sync_raids_job(registry)
    register_detect_unlinked_accounts_job(registry)
    register_detect_kick_candidates_job(registry)
    register_detect_missed_war_attacks_job(registry)
    register_detect_missed_cwl_attacks_job(registry)
    register_detect_raid_violations_job(registry)
    register_expire_warnings_by_cwl_season_job(registry)
    register_send_scheduled_notifications_job(registry)
    return registry


async def _sleep_or_stop(stop_event: asyncio.Event, *, delay_seconds: int) -> bool:
    """Ждёт interval или сигнал graceful shutdown.

    Args:
        stop_event: Общий event завершения runtime.
        delay_seconds: Длительность ожидания в секундах.

    Returns:
        `True`, если ожидание завершилось из-за shutdown event.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
    except TimeoutError:
        return False

    return True


def _validate_positive_interval(value: int, *, field_name: str) -> None:
    """Проверяет положительный interval.

    Args:
        value: Значение interval.
        field_name: Название поля для текста ошибки.

    Raises:
        ValueError: Если значение не является положительным int.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} должен быть целым числом.")

    if value <= 0:
        raise ValueError(f"{field_name} должен быть больше 0.")


__all__ = [
    "WorkerJob",
    "WorkerJobContext",
    "WorkerJobExecutionResult",
    "WorkerJobHandler",
    "WorkerJobRegistry",
    "WorkerScheduler",
    "create_default_worker_registry",
]
