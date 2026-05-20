"""Тесты сервиса notification logs."""

import pytest

from app.db.models import NotificationLog, NotificationRoute
from app.domain import DomainValidationError, NotificationType
from app.services import NotificationLogResult, NotificationLogService, NotificationLogServiceError


class InMemoryNotificationLogRepository:
    """In-memory repository для unit-тестов NotificationLogService."""

    def __init__(self, logs: list[NotificationLog] | None = None) -> None:
        """Инициализирует repository.

        Args:
            logs: Начальный набор notification logs.
        """
        self.logs = logs or []
        self.added_logs: list[NotificationLog] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key."""
        for log in self.logs:
            if log.event_key == event_key:
                return log

        return None

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log в in-memory storage."""
        self.added_logs.append(notification_log)
        self.logs.append(notification_log)

    async def flush(self) -> None:
        """Фиксирует факт flush без обращения к БД."""
        self.flush_count += 1


def make_route(
    *,
    route_id: int = 1,
    notification_type: NotificationType = NotificationType.WAR_STARTED,
    chat_id: int = -100123,
    message_thread_id: int | None = 321,
) -> NotificationRoute:
    """Создаёт NotificationRoute для unit-тестов."""
    return NotificationRoute(
        id=route_id,
        clan_id=7,
        notification_type=notification_type.value,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        enabled=True,
    )


def make_log(
    *,
    status: str,
    event_key: str = "notify:war_started:event-1",
    route_id: int | None = 1,
    notification_type: NotificationType = NotificationType.WAR_STARTED,
    chat_id: int = -100123,
    message_thread_id: int | None = 321,
    telegram_message_id: int | None = None,
    payload_summary: str = "War started for #MAIN",
    error_text: str | None = None,
) -> NotificationLog:
    """Создаёт NotificationLog для unit-тестов."""
    return NotificationLog(
        route_id=route_id,
        notification_type=notification_type.value,
        event_key=event_key,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        status=status,
        telegram_message_id=telegram_message_id,
        payload_summary=payload_summary,
        error_text=error_text,
    )


@pytest.mark.asyncio
async def test_notification_log_service_records_sent_notification() -> None:
    """Проверяет создание sent notification log."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)
    route = make_route()

    result = await service.record_sent(
        route=route,
        notification_type=NotificationType.WAR_STARTED,
        event_key="notify:war_started:event-1",
        telegram_message_id=123,
        payload_summary=" War started for #MAIN ",
    )

    assert isinstance(result, NotificationLogResult)
    assert result.created is True
    assert result.updated is False
    assert result.skipped is False
    assert repository.added_logs == [result.log]
    assert repository.flush_count == 1

    log = result.log
    assert log.route_id == 1
    assert log.route is route
    assert log.notification_type == NotificationType.WAR_STARTED.value
    assert log.event_key == "notify:war_started:event-1"
    assert log.chat_id == -100123
    assert log.message_thread_id == 321
    assert log.status == "sent"
    assert log.telegram_message_id == 123
    assert log.payload_summary == "War started for #MAIN"
    assert log.error_text is None


@pytest.mark.asyncio
async def test_notification_log_service_has_sent_returns_true_for_sent_event() -> None:
    """Проверяет `has_sent` по event_key."""
    existing_log = make_log(status="sent", telegram_message_id=123)
    repository = InMemoryNotificationLogRepository([existing_log])
    service = NotificationLogService(repository=repository)

    assert await service.has_sent(event_key="notify:war_started:event-1") is True
    assert await service.has_sent(event_key="notify:war_started:missing") is False


@pytest.mark.asyncio
async def test_notification_log_service_skips_duplicate_sent_event() -> None:
    """Проверяет запрет повторной отправки sent-event."""
    existing_log = make_log(status="sent", telegram_message_id=123)
    repository = InMemoryNotificationLogRepository([existing_log])
    service = NotificationLogService(repository=repository)

    result = await service.record_sent(
        notification_type=NotificationType.WAR_STARTED,
        event_key="notify:war_started:event-1",
        telegram_message_id=456,
        payload_summary="War started retry",
        chat_id=-100123,
        message_thread_id=321,
    )

    assert result.log is existing_log
    assert result.created is False
    assert result.updated is False
    assert result.skipped is True
    assert result.reason == "already_sent"
    assert existing_log.telegram_message_id == 123
    assert existing_log.payload_summary == "War started for #MAIN"
    assert repository.added_logs == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_log_service_updates_failed_log_to_sent() -> None:
    """Проверяет обновление existing failed log до sent."""
    existing_log = make_log(
        status="failed",
        telegram_message_id=None,
        error_text="Telegram timeout",
    )
    repository = InMemoryNotificationLogRepository([existing_log])
    service = NotificationLogService(repository=repository)

    result = await service.record_sent(
        notification_type=NotificationType.WAR_STARTED,
        event_key="notify:war_started:event-1",
        telegram_message_id=789,
        payload_summary="War started retry success",
        chat_id=-100123,
        message_thread_id=321,
    )

    assert result.log is existing_log
    assert result.created is False
    assert result.updated is True
    assert result.skipped is False
    assert repository.flush_count == 1
    assert existing_log.status == "sent"
    assert existing_log.telegram_message_id == 789
    assert existing_log.payload_summary == "War started retry success"
    assert existing_log.error_text is None


@pytest.mark.asyncio
async def test_notification_log_service_records_failed_notification() -> None:
    """Проверяет создание failed notification log."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    result = await service.record_failed(
        notification_type="war_started",
        event_key="notify:war_started:event-1",
        payload_summary="War started for #MAIN",
        error_text=" Telegram timeout ",
        chat_id=-100123,
        message_thread_id=None,
    )

    assert result.created is True
    assert result.updated is False
    assert repository.added_logs == [result.log]
    assert repository.flush_count == 1

    log = result.log
    assert log.route_id is None
    assert log.notification_type == NotificationType.WAR_STARTED.value
    assert log.event_key == "notify:war_started:event-1"
    assert log.chat_id == -100123
    assert log.message_thread_id is None
    assert log.status == "failed"
    assert log.telegram_message_id is None
    assert log.payload_summary == "War started for #MAIN"
    assert log.error_text == "Telegram timeout"


@pytest.mark.asyncio
async def test_notification_log_service_does_not_downgrade_sent_log_to_failed() -> None:
    """Проверяет, что sent log не обновляется до failed."""
    existing_log = make_log(status="sent", telegram_message_id=123)
    repository = InMemoryNotificationLogRepository([existing_log])
    service = NotificationLogService(repository=repository)

    result = await service.record_failed(
        notification_type=NotificationType.WAR_STARTED,
        event_key="notify:war_started:event-1",
        payload_summary="War started failed retry",
        error_text="Telegram timeout",
        chat_id=-100123,
        message_thread_id=321,
    )

    assert result.log is existing_log
    assert result.skipped is True
    assert result.reason == "already_sent"
    assert existing_log.status == "sent"
    assert existing_log.telegram_message_id == 123
    assert existing_log.error_text is None
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_log_service_allows_nullable_event_key_for_manual_send() -> None:
    """Проверяет nullable event_key для ручных/тестовых отправок."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    first_result = await service.record_sent(
        notification_type=NotificationType.WAR_STARTED,
        event_key=None,
        telegram_message_id=1,
        payload_summary="Manual test message",
        chat_id=-100123,
        message_thread_id=None,
    )
    second_result = await service.record_sent(
        notification_type=NotificationType.WAR_STARTED,
        event_key=None,
        telegram_message_id=2,
        payload_summary="Second manual test message",
        chat_id=-100123,
        message_thread_id=None,
    )

    assert first_result.created is True
    assert second_result.created is True
    assert len(repository.logs) == 2
    assert repository.flush_count == 2
    assert repository.logs[0].event_key is None
    assert repository.logs[1].event_key is None


@pytest.mark.asyncio
async def test_notification_log_service_records_skipped_notification() -> None:
    """Проверяет создание skipped notification log."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    result = await service.record_skipped(
        notification_type=NotificationType.WAR_STARTED,
        event_key="notify:war_started:event-1",
        payload_summary="Route disabled for #MAIN",
        reason="route_disabled",
        chat_id=-100123,
        message_thread_id=None,
    )

    assert result.created is True
    assert result.log.status == "skipped"
    assert result.log.error_text == "route_disabled"
    assert repository.flush_count == 1


@pytest.mark.asyncio
async def test_notification_log_service_rejects_sensitive_payload_summary() -> None:
    """Проверяет базовый guard от чувствительных данных в payload summary."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    with pytest.raises(NotificationLogServiceError):
        await service.record_sent(
            notification_type=NotificationType.WAR_STARTED,
            event_key="notify:war_started:event-1",
            telegram_message_id=123,
            payload_summary="Authorization Bearer secret",
            chat_id=-100123,
            message_thread_id=None,
        )

    assert repository.logs == []
    assert repository.added_logs == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_log_service_rejects_invalid_notification_type() -> None:
    """Проверяет валидацию notification type."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    with pytest.raises(DomainValidationError):
        await service.record_sent(
            notification_type="unknown_type",
            event_key="notify:unknown:event-1",
            telegram_message_id=123,
            payload_summary="Unknown notification",
            chat_id=-100123,
            message_thread_id=None,
        )

    assert repository.logs == []
    assert repository.flush_count == 0


@pytest.mark.asyncio
async def test_notification_log_service_rejects_zero_message_thread_id() -> None:
    """Проверяет валидацию message_thread_id."""
    repository = InMemoryNotificationLogRepository()
    service = NotificationLogService(repository=repository)

    with pytest.raises(DomainValidationError):
        await service.record_sent(
            notification_type=NotificationType.WAR_STARTED,
            event_key="notify:war_started:event-1",
            telegram_message_id=123,
            payload_summary="War started",
            chat_id=-100123,
            message_thread_id=0,
        )

    assert repository.logs == []
    assert repository.flush_count == 0
