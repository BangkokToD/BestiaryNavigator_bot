"""Тесты worker job применения политик API errors."""

import asyncio
from datetime import UTC, datetime

from app.services import ApiErrorPolicyResult, NotificationLogService
from app.services.notification_sender import TelegramSendResult
from app.worker.jobs import HANDLE_API_ERROR_POLICIES_JOB_NAME, HandleApiErrorPoliciesJob
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class FakeApiErrorPolicyProcessor:
    """Fake processor API error policies."""

    def __init__(self) -> None:
        """Инициализирует fake processor."""
        self.calls: list[tuple[datetime, int]] = []

    async def apply_policies(
        self,
        *,
        observed_at: datetime,
        admin_chat_id: int,
        sender: object,
        notification_log_service: NotificationLogService,
    ) -> ApiErrorPolicyResult:
        """Фиксирует вызов policy processor.

        Args:
            observed_at: Время запуска.
            admin_chat_id: Telegram ID админа.
            sender: Sender.
            notification_log_service: Сервис notification logs.

        Returns:
            Fake result.
        """
        _ = sender, notification_log_service
        self.calls.append((observed_at, admin_chat_id))
        return ApiErrorPolicyResult(
            discovered_count=3,
            forbidden_count=1,
            stale_count=1,
            rate_limited_count=1,
            retry_next_run_count=0,
            direct_admin_sent_count=1,
            route_admin_sent_count=0,
            notification_failed_count=0,
        )


class FakeNotificationLogService:
    """Fake NotificationLogService для worker job."""


class FakeTelegramNotificationSender:
    """Fake sender для worker job."""

    async def send_to_chat(self, **_: object) -> TelegramSendResult:
        """Имитирует direct send.

        Returns:
            Результат отправки.
        """
        return TelegramSendResult(message_id=1)

    async def send(self, **_: object) -> TelegramSendResult:
        """Имитирует route send.

        Returns:
            Результат отправки.
        """
        return TelegramSendResult(message_id=2)


async def test_handle_api_error_policies_job_runs_processor() -> None:
    """Проверяет запуск processor-а из worker job."""
    observed_at = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    processor = FakeApiErrorPolicyProcessor()
    job = HandleApiErrorPoliciesJob(
        api_error_policy_processor=processor,
        notification_log_service=FakeNotificationLogService(),  # type: ignore[arg-type]
        notification_sender=FakeTelegramNotificationSender(),
        admin_chat_id=123456789,
        clock=lambda: observed_at,
    )

    result = await job.run(_build_context())

    assert result.processed_count == 3
    assert result.direct_admin_sent_count == 1
    assert result.route_admin_sent_count == 0
    assert result.notification_failed_count == 0
    assert processor.calls == [(observed_at, 123456789)]


def test_default_worker_registry_registers_handle_api_error_policies_job() -> None:
    """Проверяет, что default registry подключает api error policies job."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(HANDLE_API_ERROR_POLICIES_JOB_NAME)

    assert job.name == HANDLE_API_ERROR_POLICIES_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _build_context() -> WorkerJobContext:
    """Создаёт WorkerJobContext для unit-тестов job.

    Returns:
        Контекст worker job.
    """
    return WorkerJobContext(
        job_name=HANDLE_API_ERROR_POLICIES_JOB_NAME,
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
