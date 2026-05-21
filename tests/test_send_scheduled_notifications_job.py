"""Тесты worker job отправки scheduled notifications."""

import asyncio
from datetime import UTC, datetime

from app.db.models import Clan, NotificationLog, NotificationRoute
from app.domain import NotificationType
from app.services import NotificationLogService, RenderedNotification
from app.services.notification_sender import TelegramSendResult
from app.worker.jobs import SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME, SendScheduledNotificationsJob
from app.worker.jobs.send_scheduled_notifications import (
    NotificationScheduleConfig,
    ScheduledNotificationEvent,
)
from app.worker.scheduler import WorkerJobContext, create_default_worker_registry


class InMemoryScheduledNotificationRepository:
    """In-memory repository для scheduled notification sender."""

    def __init__(
        self,
        *,
        events: list[ScheduledNotificationEvent],
        routes: list[NotificationRoute],
    ) -> None:
        """Инициализирует repository.

        Args:
            events: Due notification events.
            routes: Enabled routes.
        """
        self.events = events
        self.routes = routes
        self.flush_count = 0
        self.list_due_calls: list[datetime] = []
        self.list_route_calls: list[tuple[int, NotificationType]] = []

    async def list_due_events(
        self,
        *,
        observed_at: datetime,
        schedule_config: NotificationScheduleConfig,
        renderer: object,
    ) -> tuple[ScheduledNotificationEvent, ...]:
        """Возвращает due events.

        Args:
            observed_at: Время проверки.
            schedule_config: Настройки расписания.
            renderer: Renderer уведомлений.

        Returns:
            Tuple событий.
        """
        _ = schedule_config, renderer
        self.list_due_calls.append(observed_at)
        return tuple(self.events)

    async def list_enabled_routes(
        self,
        *,
        clan_id: int,
        notification_type: NotificationType,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает routes по clan/type.

        Args:
            clan_id: DB ID клана.
            notification_type: Тип уведомления.

        Returns:
            Tuple маршрутов.
        """
        self.list_route_calls.append((clan_id, notification_type))
        return tuple(
            route
            for route in self.routes
            if route.clan_id == clan_id
            and route.notification_type == notification_type.value
            and route.enabled
        )

    async def flush(self) -> None:
        """Фиксирует flush."""
        self.flush_count += 1


class InMemoryNotificationLogRepository:
    """In-memory repository для NotificationLogService."""

    def __init__(self, logs: list[NotificationLog] | None = None) -> None:
        """Инициализирует repository.

        Args:
            logs: Начальный набор logs.
        """
        self.logs = logs or []
        self.added_logs: list[NotificationLog] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает log по event key.

        Args:
            event_key: Event key.

        Returns:
            Log или `None`.
        """
        for log in self.logs:
            if log.event_key == event_key:
                return log

        return None

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет log в память.

        Args:
            notification_log: Новый log.
        """
        self.added_logs.append(notification_log)
        self.logs.append(notification_log)

    async def flush(self) -> None:
        """Фиксирует flush."""
        self.flush_count += 1


class FakeTelegramNotificationSender:
    """Fake sender для unit-тестов scheduled notifications."""

    def __init__(self, *, fail: bool = False) -> None:
        """Инициализирует sender.

        Args:
            fail: Нужно ли имитировать ошибку отправки.
        """
        self.fail = fail
        self.calls: list[tuple[int, int | None, str]] = []
        self._message_id = 1000

    async def send(
        self,
        *,
        route: NotificationRoute,
        notification: RenderedNotification,
    ) -> TelegramSendResult:
        """Имитирует отправку уведомления.

        Args:
            route: Route доставки.
            notification: Отрендеренное уведомление.

        Returns:
            Результат отправки.

        Raises:
            RuntimeError: Если sender настроен на ошибку.
        """
        self.calls.append((route.chat_id, route.message_thread_id, notification.text))
        if self.fail:
            raise RuntimeError("Telegram timeout")

        self._message_id += 1
        return TelegramSendResult(message_id=self._message_id)


async def test_send_scheduled_notifications_job_deduplicates_sent_event_by_route() -> None:
    """Проверяет event_key dedup с учётом route identity."""
    clan = _make_clan()
    first_route = _make_route(route_id=1, clan_id=7)
    second_route = _make_route(route_id=2, clan_id=7, chat_id=-100456)
    event = _make_event(clan=clan)
    existing_sent_log = _make_log(
        event_key="notify:war_started:7:war-key:1",
        route=first_route,
        status="sent",
    )
    notification_log_repository = InMemoryNotificationLogRepository([existing_sent_log])
    sender = FakeTelegramNotificationSender()
    repository = InMemoryScheduledNotificationRepository(
        events=[event],
        routes=[first_route, second_route],
    )
    job = SendScheduledNotificationsJob(
        repository=repository,
        notification_log_service=NotificationLogService(repository=notification_log_repository),
        notification_sender=sender,
        schedule_config=_make_schedule_config(),
        clock=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )

    result = await job.run(_build_context())

    assert result.discovered_event_count == 1
    assert result.route_count == 2
    assert result.skipped_sent_count == 1
    assert result.sent_count == 1
    assert result.failed_count == 0
    assert sender.calls == [(-100456, 321, "War started")]
    assert repository.flush_count == 1

    assert len(notification_log_repository.logs) == 2
    created_log = notification_log_repository.added_logs[0]
    assert created_log.event_key == "notify:war_started:7:war-key:2"
    assert created_log.status == "sent"
    assert created_log.route_id == 2
    assert created_log.chat_id == -100456


async def test_send_scheduled_notifications_job_failed_log_does_not_block_retry() -> None:
    """Проверяет, что failed log не блокирует следующую успешную отправку."""
    clan = _make_clan()
    route = _make_route(route_id=1, clan_id=7)
    event = _make_event(clan=clan)
    notification_log_repository = InMemoryNotificationLogRepository()
    repository = InMemoryScheduledNotificationRepository(events=[event], routes=[route])
    failing_sender = FakeTelegramNotificationSender(fail=True)
    first_job = SendScheduledNotificationsJob(
        repository=repository,
        notification_log_service=NotificationLogService(repository=notification_log_repository),
        notification_sender=failing_sender,
        schedule_config=_make_schedule_config(),
        clock=lambda: datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
    )

    first_result = await first_job.run(_build_context())

    assert first_result.failed_count == 1
    assert notification_log_repository.logs[0].status == "failed"

    successful_sender = FakeTelegramNotificationSender()
    second_job = SendScheduledNotificationsJob(
        repository=repository,
        notification_log_service=NotificationLogService(repository=notification_log_repository),
        notification_sender=successful_sender,
        schedule_config=_make_schedule_config(),
        clock=lambda: datetime(2026, 5, 20, 12, 5, tzinfo=UTC),
    )

    second_result = await second_job.run(_build_context())

    assert second_result.sent_count == 1
    assert second_result.skipped_sent_count == 0
    assert len(notification_log_repository.logs) == 1
    assert notification_log_repository.logs[0].status == "sent"
    assert notification_log_repository.logs[0].telegram_message_id == 1001


def test_default_worker_registry_registers_send_scheduled_notifications_job() -> None:
    """Проверяет, что default registry подключает scheduled sender."""
    registry = create_default_worker_registry(default_interval_seconds=900)

    job = registry.get(SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME)

    assert job.name == SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME
    assert job.run_in_transaction is True
    assert job.run_on_start is True
    assert registry.resolve_interval_seconds(job) == 900


def _make_clan() -> Clan:
    """Создаёт Clan для unit-тестов.

    Returns:
        Модель Clan.
    """
    return Clan(id=7, tag="#MAIN", name="Bestiary", type="main", is_active=True)


def _make_route(
    *,
    route_id: int,
    clan_id: int,
    chat_id: int = -100123,
    message_thread_id: int | None = 321,
) -> NotificationRoute:
    """Создаёт NotificationRoute для unit-тестов.

    Args:
        route_id: DB ID route.
        clan_id: DB ID клана.
        chat_id: Telegram chat id.
        message_thread_id: Telegram topic id.

    Returns:
        Модель NotificationRoute.
    """
    return NotificationRoute(
        id=route_id,
        clan_id=clan_id,
        notification_type=NotificationType.WAR_STARTED.value,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        enabled=True,
    )


def _make_event(*, clan: Clan) -> ScheduledNotificationEvent:
    """Создаёт scheduled notification event.

    Args:
        clan: Клан события.

    Returns:
        Scheduled event.
    """
    return ScheduledNotificationEvent(
        clan=clan,
        notification_type=NotificationType.WAR_STARTED,
        event_parts=(7, "war-key"),
        rendered=RenderedNotification(
            notification_type=NotificationType.WAR_STARTED,
            text="War started",
            payload_summary="War started for Bestiary",
        ),
    )


def _make_log(*, event_key: str, route: NotificationRoute, status: str) -> NotificationLog:
    """Создаёт NotificationLog для unit-тестов.

    Args:
        event_key: Event key.
        route: Route лога.
        status: Статус лога.

    Returns:
        Модель NotificationLog.
    """
    return NotificationLog(
        route_id=route.id,
        route=route,
        notification_type=route.notification_type,
        event_key=event_key,
        chat_id=route.chat_id,
        message_thread_id=route.message_thread_id,
        status=status,
        telegram_message_id=123 if status == "sent" else None,
        payload_summary="War started for Bestiary",
        error_text=None if status == "sent" else "Telegram timeout",
    )


def _make_schedule_config() -> NotificationScheduleConfig:
    """Создаёт config расписания для тестов.

    Returns:
        Config scheduled notifications.
    """
    return NotificationScheduleConfig(
        evening_hour_utc=18,
        daily_report_hour_utc=9,
    )


def _build_context() -> WorkerJobContext:
    """Создаёт WorkerJobContext для unit-тестов.

    Returns:
        Контекст worker job.
    """
    return WorkerJobContext(
        job_name=SEND_SCHEDULED_NOTIFICATIONS_JOB_NAME,
        started_at=datetime(2026, 5, 20, tzinfo=UTC),
        stop_event=asyncio.Event(),
        session=None,
    )
