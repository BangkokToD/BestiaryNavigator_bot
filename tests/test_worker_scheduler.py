"""Тесты worker scheduler и registry."""

import asyncio

from app.worker.scheduler import WorkerJobContext, WorkerJobRegistry, WorkerScheduler


async def test_worker_job_registry_registers_job_declaratively() -> None:
    """Проверяет декларативную регистрацию и одиночный запуск job."""
    registry = WorkerJobRegistry(default_interval_seconds=30)
    calls: list[str] = []

    @registry.job(name="sample_job", interval_seconds=5, run_in_transaction=False)
    async def sample_job(context: WorkerJobContext) -> None:
        """Тестовая job без доступа к БД."""
        calls.append(context.job_name)
        assert context.session is None
        assert context.should_stop is False

    registered_job = registry.get("sample_job")
    scheduler = WorkerScheduler(registry=registry)

    result = await scheduler.run_job_once("sample_job")

    assert registered_job.name == "sample_job"
    assert registered_job.interval_seconds == 5
    assert registry.resolve_interval_seconds(registered_job) == 5
    assert result.success is True
    assert result.skipped is False
    assert calls == ["sample_job"]


async def test_worker_scheduler_waits_with_empty_registry_until_stop_event() -> None:
    """Проверяет, что пустой registry не считается ошибкой worker runtime."""
    registry = WorkerJobRegistry(default_interval_seconds=1)
    scheduler = WorkerScheduler(registry=registry)
    stop_event = asyncio.Event()

    scheduler_task = asyncio.create_task(scheduler.run(stop_event=stop_event))
    await asyncio.sleep(0)

    assert scheduler_task.done() is False

    stop_event.set()
    await asyncio.wait_for(scheduler_task, timeout=1)


async def test_worker_scheduler_isolates_job_errors() -> None:
    """Проверяет, что ошибка одной job не мешает запуску другой job."""
    registry = WorkerJobRegistry(default_interval_seconds=30)
    calls: list[str] = []

    @registry.job(name="broken_job", run_in_transaction=False)
    async def broken_job(_: WorkerJobContext) -> None:
        """Тестовая job, имитирующая runtime-ошибку."""
        raise RuntimeError("boom")

    @registry.job(name="healthy_job", run_in_transaction=False)
    async def healthy_job(context: WorkerJobContext) -> None:
        """Тестовая job, которая должна выполниться после ошибки соседней job."""
        calls.append(context.job_name)

    scheduler = WorkerScheduler(registry=registry)

    failed_result = await scheduler.run_job_once("broken_job")
    successful_result = await scheduler.run_job_once("healthy_job")

    assert failed_result.success is False
    assert failed_result.skipped is False
    assert isinstance(failed_result.error, RuntimeError)
    assert successful_result.success is True
    assert calls == ["healthy_job"]


async def test_worker_scheduler_skips_parallel_run_of_same_job() -> None:
    """Проверяет lock-guard от параллельного запуска одной job."""
    registry = WorkerJobRegistry(default_interval_seconds=30)
    started_event = asyncio.Event()
    release_event = asyncio.Event()

    @registry.job(name="slow_job", run_in_transaction=False)
    async def slow_job(_: WorkerJobContext) -> None:
        """Тестовая job, которая удерживает lock до явного освобождения."""
        started_event.set()
        await release_event.wait()

    scheduler = WorkerScheduler(registry=registry)
    first_run_task = asyncio.create_task(scheduler.run_job_once("slow_job"))

    await asyncio.wait_for(started_event.wait(), timeout=1)
    skipped_result = await scheduler.run_job_once("slow_job")

    assert skipped_result.success is False
    assert skipped_result.skipped is True

    release_event.set()
    first_result = await asyncio.wait_for(first_run_task, timeout=1)

    assert first_result.success is True
    assert first_result.skipped is False
