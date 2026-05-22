"""Route-level тесты admin notification route endpoints."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan, NotificationRoute, TelegramChat
from app.domain import ClanType, NotificationType
from app.services import NotificationRouteNotFoundError, RenderedNotification
from app.services.notification_routes import NotificationRouteStateResult
from app.services.notification_sender import TelegramNotificationSenderError, TelegramSendResult
from app.web.admin_notification_routes import (
    AdminNotificationRouteDependencies,
    get_admin_notification_route_dependencies,
)
from app.web.security import build_admin_cookie_value

VALID_SETTINGS = {
    "APP_ENV": "local",
    "APP_BASE_URL": "http://localhost:8000",
    "DATABASE_URL": "postgresql+asyncpg://bn:bn@postgres:5432/bestiary",
    "CLASH_API_BASE_URL": "https://api.clashofclans.com/v1",
    "CLASH_API_TOKEN": "test-clash-token-123",
    "CLASH_API_TIMEOUT_SECONDS": 7,
    "TELEGRAM_BOT_TOKEN": "test-telegram-token-123",
    "TELEGRAM_ADMIN_ID": 123456789,
    "WEB_SESSION_SECRET": "test-session-secret-value-1234567890",
    "WEB_ADMIN_COOKIE_NAME": "bn_admin_session",
    "SYNC_DEFAULT_INTERVAL_SECONDS": 900,
    "ROLE_SNAPSHOT_MAX_AGE_MINUTES": 30,
}


class FakeAdminNotificationRouteService:
    """Fake service notification routes для route-level тестов."""

    def __init__(self, *, routes: list[NotificationRoute] | None = None) -> None:
        """Инициализирует fake service.

        Args:
            routes: Набор маршрутов уведомлений.
        """
        self.routes = routes or []
        self.calls: list[tuple[object, ...]] = []

    async def list_all_routes(
        self,
        *,
        include_disabled: bool = True,
    ) -> tuple[NotificationRoute, ...]:
        """Возвращает fake routes."""
        self.calls.append(("list_all_routes", include_disabled))
        return tuple(route for route in self.routes if include_disabled or route.enabled)

    async def get_route_by_id(self, *, route_id: int) -> NotificationRoute:
        """Возвращает fake route по ID."""
        self.calls.append(("get_route_by_id", route_id))
        for route in self.routes:
            if route.id == route_id:
                return route

        raise NotificationRouteNotFoundError(f"Notification route {route_id} не найден.")

    async def disable_route(self, *, route_id: int) -> NotificationRouteStateResult:
        """Отключает fake route."""
        self.calls.append(("disable_route", route_id))
        route = await self.get_route_by_id(route_id=route_id)
        changed = route.enabled
        route.enabled = False

        return NotificationRouteStateResult(route=route, changed=changed)


class FakeNotificationLogService:
    """Fake notification log service для проверки test send."""

    def __init__(self) -> None:
        """Инициализирует fake log service."""
        self.sent_calls: list[dict[str, object]] = []
        self.failed_calls: list[dict[str, object]] = []

    async def record_sent(self, **kwargs: object) -> object:
        """Фиксирует successful log call."""
        self.sent_calls.append(kwargs)
        return object()

    async def record_failed(self, **kwargs: object) -> object:
        """Фиксирует failed log call."""
        self.failed_calls.append(kwargs)
        return object()


class FakeTelegramNotificationSender:
    """Fake sender для test notification endpoint."""

    def __init__(self, *, fail: bool = False) -> None:
        """Инициализирует fake sender.

        Args:
            fail: Нужно ли имитировать ошибку отправки.
        """
        self.fail = fail
        self.calls: list[tuple[int, int | None, str]] = []

    async def send(
        self,
        *,
        route: NotificationRoute,
        notification: RenderedNotification,
    ) -> TelegramSendResult:
        """Имитирует отправку уведомления."""
        self.calls.append((route.chat_id, route.message_thread_id, notification.text))
        if self.fail:
            raise TelegramNotificationSenderError("Telegram timeout")

        return TelegramSendResult(message_id=777)

    async def send_to_chat(
        self,
        *,
        chat_id: int,
        notification: RenderedNotification,
        message_thread_id: int | None = None,
    ) -> TelegramSendResult:
        """Имитирует прямую отправку в чат."""
        self.calls.append((chat_id, message_thread_id, notification.text))
        return TelegramSendResult(message_id=778)


def make_settings() -> Settings:
    """Создаёт settings для route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_admin_notification_routes_reject_anonymous_user() -> None:
    """Проверяет, что anonymous не видит notification routes."""
    dependencies = _make_dependencies(routes=[_make_route()])

    with _override_admin_notification_dependencies(dependencies), TestClient(app) as client:
        response = client.get("/admin/notification-routes")

    assert response.status_code == 403
    assert dependencies.route_service.calls == []


def test_register_command_endpoint_returns_single_copy_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет генерацию команды `/register` для одной пары clan/type."""
    dependencies = _make_dependencies()

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get(
            "/admin/notification-routes/register-command",
            params={"clan_tag": "2abc", "notification_type": "war_started"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "clan_tag": "#2ABC",
        "notification_type": "war_started",
        "command": "/register #2ABC war_started",
    }


def test_list_notification_routes_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет список notification routes для админа."""
    route = _make_route()
    dependencies = _make_dependencies(routes=[route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/notification-routes")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "routes": [_expected_route_payload(enabled=True)],
    }
    assert dependencies.route_service.calls == [("list_all_routes", True)]


def test_list_notification_routes_can_filter_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет фильтр disabled routes."""
    enabled_route = _make_route(route_id=1, enabled=True)
    disabled_route = _make_route(route_id=2, enabled=False)
    dependencies = _make_dependencies(routes=[enabled_route, disabled_route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get(
            "/admin/notification-routes",
            params={"include_disabled": "false"},
        )

    assert response.status_code == 200
    assert response.json()["routes"] == [_expected_route_payload(route_id=1, enabled=True)]
    assert dependencies.route_service.calls == [("list_all_routes", False)]


def test_notification_route_status_uses_route_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет status endpoint по route_id."""
    route = _make_route(route_id=7)
    dependencies = _make_dependencies(routes=[route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/notification-routes/7/status")

    assert response.status_code == 200
    assert response.json()["route"] == _expected_route_payload(route_id=7, enabled=True)
    assert dependencies.route_service.calls == [("get_route_by_id", 7)]


def test_disable_notification_route_does_not_delete_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет soft disable без удаления истории route."""
    route = _make_route(route_id=7, enabled=True)
    dependencies = _make_dependencies(routes=[route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.post("/admin/notification-routes/7/disable")

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert response.json()["route"] == _expected_route_payload(route_id=7, enabled=False)
    assert route in dependencies.route_service.routes
    assert route.enabled is False


def test_test_notification_route_sends_message_and_writes_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет реальную test-отправку через sender abstraction и log."""
    route = _make_route(route_id=7)
    dependencies = _make_dependencies(routes=[route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.post("/admin/notification-routes/7/test")

    assert response.status_code == 200
    assert response.json()["telegram_message_id"] == 777
    assert dependencies.sender.calls == [
        (
            -100123,
            321,
            "Тестовое уведомление BestiaryNavigator_bot\nКлан: Bestiary\nТип: war_started",
        )
    ]
    assert len(dependencies.log_service.sent_calls) == 1
    assert dependencies.log_service.sent_calls[0]["route"] is route
    assert (
        dependencies.log_service.sent_calls[0]["notification_type"] == NotificationType.WAR_STARTED
    )
    assert dependencies.log_service.sent_calls[0]["event_key"] is None
    assert dependencies.log_service.sent_calls[0]["telegram_message_id"] == 777


def test_test_notification_route_writes_failed_log_on_sender_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет failed log при ошибке sender-а."""
    route = _make_route(route_id=7)
    dependencies = _make_dependencies(
        routes=[route],
        sender=FakeTelegramNotificationSender(fail=True),
    )

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.post("/admin/notification-routes/7/test")

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "notification_test_send_failed",
        "message": "Telegram timeout",
    }
    assert dependencies.log_service.sent_calls == []
    assert len(dependencies.log_service.failed_calls) == 1
    failed_call = dependencies.log_service.failed_calls[0]

    assert failed_call["route"] is route
    assert "TelegramNotificationSenderError: Telegram timeout" in failed_call["error_text"]


def test_notification_route_not_found_maps_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет понятную 404-ошибку для неизвестного route."""
    dependencies = _make_dependencies(routes=[])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/notification-routes/404/status")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "notification_route_not_found",
        "message": "Notification route 404 не найден.",
    }


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    dependencies: AdminNotificationRouteDependencies,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie и fake dependencies.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        dependencies: Fake dependencies endpoint-ов.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_admin_notification_dependencies(dependencies), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_admin_notification_dependencies(
    dependencies: AdminNotificationRouteDependencies,
) -> Iterator[None]:
    """Подменяет dependency admin notification routes.

    Args:
        dependencies: Fake dependencies endpoint-ов.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_notification_route_dependencies)
    app.dependency_overrides[get_admin_notification_route_dependencies] = lambda: dependencies

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_notification_route_dependencies, None)
        else:
            app.dependency_overrides[get_admin_notification_route_dependencies] = previous_override


def _make_dependencies(
    *,
    routes: list[NotificationRoute] | None = None,
    sender: FakeTelegramNotificationSender | None = None,
) -> AdminNotificationRouteDependencies:
    """Создаёт fake dependencies для endpoint-тестов.

    Args:
        routes: Набор fake routes.
        sender: Fake sender.

    Returns:
        Dependencies admin notification routes.
    """
    return AdminNotificationRouteDependencies(
        route_service=FakeAdminNotificationRouteService(routes=routes),
        log_service=FakeNotificationLogService(),
        sender=sender or FakeTelegramNotificationSender(),
    )


def _make_route(
    *,
    route_id: int = 1,
    enabled: bool = True,
) -> NotificationRoute:
    """Создаёт notification route для тестов.

    Args:
        route_id: DB ID route.
        enabled: Флаг активности route.

    Returns:
        NotificationRoute model.
    """
    clan = Clan(
        id=7,
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        is_active=True,
    )
    chat = TelegramChat(
        id=11,
        chat_id=-100123,
        title="War topic",
        type="supergroup",
        is_forum=True,
    )
    return NotificationRoute(
        id=route_id,
        clan_id=7,
        clan=clan,
        notification_type=NotificationType.WAR_STARTED.value,
        chat_id=-100123,
        chat=chat,
        message_thread_id=321,
        enabled=enabled,
    )


def _expected_route_payload(*, route_id: int = 1, enabled: bool) -> dict[str, object]:
    """Возвращает ожидаемый JSON route payload.

    Args:
        route_id: DB ID route.
        enabled: Флаг активности route.

    Returns:
        Ожидаемый response payload.
    """
    return {
        "id": route_id,
        "clan_id": 7,
        "clan_tag": "#2ABC",
        "clan_name": "Bestiary",
        "clan_type": "main",
        "notification_type": "war_started",
        "chat_id": -100123,
        "chat_title": "War topic",
        "message_thread_id": 321,
        "enabled": enabled,
        "register_command": "/register #2ABC war_started",
    }
