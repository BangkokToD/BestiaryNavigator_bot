"""Тесты сервиса политик API errors."""

from datetime import UTC, datetime

from app.db.models import ApiError, NotificationLog, NotificationRoute
from app.domain import NotificationType
from app.services import (
    API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED,
    API_ERROR_STATUS_ADMIN_NOTIFIED,
    API_ERROR_STATUS_RATE_LIMITED,
    API_ERROR_STATUS_RETRY_NEXT_RUN,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
    ApiErrorPolicyService,
    NotificationLogService,
    RenderedNotification,
)
from app.services.notification_sender import TelegramSendResult


class InMemoryApiErrorPolicyRepository:
    """In-memory repository для ApiErrorPolicyService."""

    def __init__(
        self,
        *,
        api_errors: list[ApiError],
        routes: list[NotificationRoute] | None = None,
    ) -> None:
        """Инициализирует repository.

        Args:
            api_errors: Ошибки API.
            routes: Routes для fallback-уведомлений.
        """
        self.api_errors = api_errors
        self.routes = routes or []
        self.flush_count = 0

    async def list_unresolved_api_errors(self) -> tuple[ApiError, ...]:
        """Возвращает unresolved API errors.

        Returns:
            Tuple unresolved ошибок.
        """
        return tuple(
            api_error
            for api_error in self.api_errors
            if api_error.status == API_ERROR_STATUS_UNRESOLVED
        )

    async def list_admin_notification_routes(self) -> tuple[NotificationRoute, ...]:
        """Возвращает enabled api_errors_admin routes.

        Returns:
            Tuple routes.
        """
        return tuple(route for route in self.routes if route.enabled)

    async def flush(self) -> None:
        """Фиксирует flush."""
        self.flush_count += 1


class InMemoryNotificationLogRepository:
    """In-memory repository для NotificationLogService."""

    def __init__(self, logs: list[NotificationLog] | None = None) -> None:
        """Инициализирует repository.

        Args:
            logs: Начальный набор notification logs.
        """
        self.logs = logs or []
        self.added_logs: list[NotificationLog] = []
        self.flush_count = 0

    async def get_by_event_key(self, event_key: str) -> NotificationLog | None:
        """Возвращает notification log по event key.

        Args:
            event_key: Event key.

        Returns:
            NotificationLog или `None`.
        """
        for log in self.logs:
            if log.event_key == event_key:
                return log

        return None

    def add(self, notification_log: NotificationLog) -> None:
        """Добавляет notification log.

        Args:
            notification_log: Новый log.
        """
        self.added_logs.append(notification_log)
        self.logs.append(notification_log)

    async def flush(self) -> None:
        """Фиксирует flush."""
        self.flush_count += 1


class FakeTelegramNotificationSender:
    """Fake Telegram sender для API error policies."""

    def __init__(self, *, fail_direct: bool = False, fail_route: bool = False) -> None:
        """Инициализирует sender.

        Args:
            fail_direct: Имитировать ошибку direct-send.
            fail_route: Имитировать ошибку route-send.
        """
        self.fail_direct = fail_direct
        self.fail_route = fail_route
        self.direct_calls: list[tuple[int, str]] = []
        self.route_calls: list[tuple[int, str]] = []
        self._message_id = 100

    async def send_to_chat(
        self,
        *,
        chat_id: int,
        notification: RenderedNotification,
        message_thread_id: int | None = None,
    ) -> TelegramSendResult:
        """Имитирует direct-send админу.

        Args:
            chat_id: Telegram chat id.
            notification: Уведомление.
            message_thread_id: Thread id.

        Returns:
            Результат отправки.

        Raises:
            RuntimeError: Если включён fail_direct.
        """
        _ = message_thread_id
        self.direct_calls.append((chat_id, notification.text))
        if self.fail_direct:
            raise RuntimeError("admin private chat unavailable")

        self._message_id += 1
        return TelegramSendResult(message_id=self._message_id)

    async def send(
        self,
        *,
        route: NotificationRoute,
        notification: RenderedNotification,
    ) -> TelegramSendResult:
        """Имитирует отправку через route.

        Args:
            route: Route.
            notification: Уведомление.

        Returns:
            Результат отправки.

        Raises:
            RuntimeError: Если включён fail_route.
        """
        self.route_calls.append((route.chat_id, notification.text))
        if self.fail_route:
            raise RuntimeError("route send failed")

        self._message_id += 1
        return TelegramSendResult(message_id=self._message_id)


async def test_api_error_policy_sends_403_to_admin_direct() -> None:
    """Проверяет direct admin notification для 403."""
    api_error = _make_api_error(api_error_id=1, status_code=403)
    repository = InMemoryApiErrorPolicyRepository(api_errors=[api_error])
    log_repository = InMemoryNotificationLogRepository()
    sender = FakeTelegramNotificationSender()
    service = ApiErrorPolicyService(repository=repository)

    result = await service.apply_policies(
        observed_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        admin_chat_id=123456789,
        sender=sender,
        notification_log_service=NotificationLogService(repository=log_repository),
    )

    assert result.discovered_count == 1
    assert result.forbidden_count == 1
    assert result.direct_admin_sent_count == 1
    assert result.route_admin_sent_count == 0
    assert result.notification_failed_count == 0
    assert api_error.status == API_ERROR_STATUS_ADMIN_NOTIFIED
    assert len(sender.direct_calls) == 1
    assert sender.route_calls == []
    assert log_repository.logs[0].status == "sent"


async def test_api_error_policy_fallbacks_403_to_admin_route_when_direct_fails() -> None:
    """Проверяет fallback в api_errors_admin route при недоступной личке."""
    api_error = _make_api_error(api_error_id=1, status_code=403)
    route = _make_route(route_id=5)
    repository = InMemoryApiErrorPolicyRepository(api_errors=[api_error], routes=[route])
    log_repository = InMemoryNotificationLogRepository()
    sender = FakeTelegramNotificationSender(fail_direct=True)
    service = ApiErrorPolicyService(repository=repository)

    result = await service.apply_policies(
        observed_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        admin_chat_id=123456789,
        sender=sender,
        notification_log_service=NotificationLogService(repository=log_repository),
    )

    assert result.forbidden_count == 1
    assert result.direct_admin_sent_count == 0
    assert result.route_admin_sent_count == 1
    assert result.notification_failed_count == 1
    assert api_error.status == API_ERROR_STATUS_ADMIN_NOTIFIED
    assert len(sender.direct_calls) == 1
    assert sender.route_calls == [(-100123, sender.route_calls[0][1])]
    assert [log.status for log in log_repository.logs] == ["failed", "sent"]


async def test_api_error_policy_marks_403_failed_when_all_notifications_fail() -> None:
    """Проверяет статус notification_failed, если direct и fallback route упали."""
    api_error = _make_api_error(api_error_id=1, status_code=403)
    route = _make_route(route_id=5)
    repository = InMemoryApiErrorPolicyRepository(api_errors=[api_error], routes=[route])
    log_repository = InMemoryNotificationLogRepository()
    sender = FakeTelegramNotificationSender(fail_direct=True, fail_route=True)
    service = ApiErrorPolicyService(repository=repository)

    result = await service.apply_policies(
        observed_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        admin_chat_id=123456789,
        sender=sender,
        notification_log_service=NotificationLogService(repository=log_repository),
    )

    assert result.forbidden_count == 1
    assert result.route_admin_sent_count == 0
    assert result.notification_failed_count == 2
    assert api_error.status == API_ERROR_STATUS_ADMIN_NOTIFICATION_FAILED
    assert [log.status for log in log_repository.logs] == ["failed", "failed"]


async def test_api_error_policy_marks_404_429_5xx_and_timeout_statuses() -> None:
    """Проверяет политики stale/rate-limit/retry-next-run."""
    not_found = _make_api_error(api_error_id=1, status_code=404)
    rate_limit = _make_api_error(api_error_id=2, status_code=429)
    server_error = _make_api_error(api_error_id=3, status_code=500)
    timeout = _make_api_error(
        api_error_id=4,
        status_code=None,
        exception_class="ClashTimeoutError",
    )
    repository = InMemoryApiErrorPolicyRepository(
        api_errors=[not_found, rate_limit, server_error, timeout]
    )
    log_repository = InMemoryNotificationLogRepository()
    sender = FakeTelegramNotificationSender()
    service = ApiErrorPolicyService(repository=repository)

    result = await service.apply_policies(
        observed_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
        admin_chat_id=123456789,
        sender=sender,
        notification_log_service=NotificationLogService(repository=log_repository),
    )

    assert result.discovered_count == 4
    assert result.stale_count == 1
    assert result.rate_limited_count == 1
    assert result.retry_next_run_count == 2
    assert not_found.status == API_ERROR_STATUS_STALE
    assert rate_limit.status == API_ERROR_STATUS_RATE_LIMITED
    assert server_error.status == API_ERROR_STATUS_RETRY_NEXT_RUN
    assert timeout.status == API_ERROR_STATUS_RETRY_NEXT_RUN
    assert sender.direct_calls == []
    assert sender.route_calls == []
    assert log_repository.logs == []


def _make_api_error(
    *,
    api_error_id: int,
    status_code: int | None,
    exception_class: str = "ClashApiError",
) -> ApiError:
    """Создаёт ApiError для unit-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status_code: HTTP status code.
        exception_class: Имя exception class.

    Returns:
        Модель ApiError.
    """
    return ApiError(
        id=api_error_id,
        endpoint=f"clans/%23MAIN/error-{api_error_id}",
        method="GET",
        entity_type="clan",
        entity_tag="#MAIN",
        status_code=status_code,
        message=f"API error {api_error_id}",
        response_snippet='{"reason":"error"}',
        exception_class=exception_class,
        worker_name="sync_clans",
        retry_count=0,
        status=API_ERROR_STATUS_UNRESOLVED,
    )


def _make_route(*, route_id: int) -> NotificationRoute:
    """Создаёт NotificationRoute для unit-тестов.

    Args:
        route_id: DB ID route.

    Returns:
        Модель NotificationRoute.
    """
    return NotificationRoute(
        id=route_id,
        clan_id=7,
        notification_type=NotificationType.API_ERRORS_ADMIN.value,
        chat_id=-100123,
        message_thread_id=321,
        enabled=True,
    )
