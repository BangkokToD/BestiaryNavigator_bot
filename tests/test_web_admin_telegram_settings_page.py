"""Тесты SSR-страницы Telegram settings."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from tests.web_dashboard_helpers import override_dashboard_service

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan, NotificationRoute, TelegramChat
from app.domain import ClanType, NotificationType
from app.web.admin_telegram_settings import (
    TelegramSettingsDependencies,
    get_telegram_settings_dependencies,
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


class FakeTelegramSettingsClanService:
    """Fake clan service страницы Telegram settings."""

    def __init__(self, clans: list[Clan] | None = None) -> None:
        """Инициализирует fake clan service.

        Args:
            clans: Кланы страницы.
        """
        self.clans = clans or []
        self.calls: list[tuple[object, ...]] = []

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает fake-кланы."""
        self.calls.append(("list_clans",))
        return tuple(self.clans)


class FakeTelegramSettingsRouteService:
    """Fake notification route service страницы Telegram settings."""

    def __init__(self, routes: list[NotificationRoute] | None = None) -> None:
        """Инициализирует fake route service.

        Args:
            routes: Routes страницы.
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


def make_settings() -> Settings:
    """Создаёт settings для тестов страницы.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_telegram_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к странице Telegram settings."""
    dependencies = _make_dependencies(clans=[_make_clan()])

    with _override_dependencies(dependencies), TestClient(app) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 403
    assert dependencies.clan_service.calls == []
    assert dependencies.route_service.calls == []


def test_telegram_settings_page_renders_clans_types_and_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет рендер страницы Telegram settings."""
    clan = _make_clan()
    enabled_route = _make_route(route_id=1, clan=clan, enabled=True, message_thread_id=321)
    disabled_route = _make_route(
        route_id=2,
        clan=clan,
        enabled=False,
        chat_id=-100456,
        chat_title="Archive topic",
        message_thread_id=None,
    )
    dependencies = _make_dependencies(clans=[clan], routes=[enabled_route, disabled_route])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 200
    assert "Настройки Telegram" in response.text
    assert "Bestiary" in response.text
    assert "#2ABC" in response.text
    assert "Война началась" in response.text
    assert "/register #2ABC war_started" in response.text
    assert "War topic" in response.text
    assert "Archive topic" in response.text
    assert "topic:" in response.text
    assert "321" in response.text
    assert "без топика" in response.text
    assert 'data-copy-command="/register #2ABC war_started"' in response.text
    assert 'data-route-test-url="/admin/notification-routes/1/test"' in response.text
    assert 'data-route-disable-url="/admin/notification-routes/1/disable"' in response.text
    assert 'data-route-id="2"' in response.text
    assert "bn-route-row--disabled" in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-button' in response.text
    assert 'class="bn-badge' in response.text
    assert 'style="' not in response.text
    assert dependencies.clan_service.calls == [("list_clans",)]
    assert dependencies.route_service.calls == [("list_all_routes", True)]


def test_telegram_settings_page_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет пустое состояние без кланов."""
    dependencies = _make_dependencies(clans=[])

    with _admin_client(monkeypatch=monkeypatch, dependencies=dependencies) as client:
        response = client.get("/admin/settings/telegram")

    assert response.status_code == 200
    assert "Кланы ещё не добавлены" in response.text
    assert "/admin/settings/clans" in response.text


def test_admin_sidebar_shows_telegram_settings_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет admin-only ссылку на Telegram settings в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with override_dashboard_service(), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/telegram" in response.text
    assert "Настройки Telegram" in response.text


def test_telegram_settings_static_assets_are_served() -> None:
    """Проверяет CSS и JS страницы Telegram settings."""
    with TestClient(app) as client:
        css_response = client.get("/static/css/pages/admin_telegram.css")
        js_response = client.get("/static/js/pages/admin_telegram_settings.js")

    assert css_response.status_code == 200
    assert ".bn-admin-telegram" in css_response.text
    assert ".bn-notification-type" in css_response.text
    assert js_response.status_code == 200
    assert "navigator.clipboard.writeText" in js_response.text
    assert 'method: "POST"' in js_response.text


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    dependencies: TelegramSettingsDependencies,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        dependencies: Fake dependencies страницы.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_dependencies(dependencies), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_dependencies(dependencies: TelegramSettingsDependencies) -> Iterator[None]:
    """Подменяет зависимости страницы Telegram settings.

    Args:
        dependencies: Fake dependencies.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_telegram_settings_dependencies)
    app.dependency_overrides[get_telegram_settings_dependencies] = lambda: dependencies

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_telegram_settings_dependencies, None)
        else:
            app.dependency_overrides[get_telegram_settings_dependencies] = previous_override


def _make_dependencies(
    *,
    clans: list[Clan] | None = None,
    routes: list[NotificationRoute] | None = None,
) -> TelegramSettingsDependencies:
    """Создаёт fake dependencies страницы.

    Args:
        clans: Кланы.
        routes: Routes.

    Returns:
        Dependencies страницы.
    """
    return TelegramSettingsDependencies(
        clan_service=FakeTelegramSettingsClanService(clans),
        route_service=FakeTelegramSettingsRouteService(routes),
    )


def _make_clan() -> Clan:
    """Создаёт клан для тестов.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=7,
        tag="#2ABC",
        name="Bestiary",
        type=ClanType.MAIN.value,
        badge_url="https://example.test/badge.png",
        is_active=True,
    )


def _make_route(
    *,
    route_id: int,
    clan: Clan,
    enabled: bool,
    chat_id: int = -100123,
    chat_title: str = "War topic",
    message_thread_id: int | None,
) -> NotificationRoute:
    """Создаёт route для тестов.

    Args:
        route_id: DB ID route.
        clan: Клан route.
        enabled: Флаг активности.
        chat_id: Telegram chat id.
        chat_title: Название чата.
        message_thread_id: Topic/thread id.

    Returns:
        Модель NotificationRoute.
    """
    chat = TelegramChat(
        id=route_id + 100,
        chat_id=chat_id,
        title=chat_title,
        type="supergroup",
        is_forum=message_thread_id is not None,
    )
    return NotificationRoute(
        id=route_id,
        clan_id=clan.id,
        clan=clan,
        notification_type=NotificationType.WAR_STARTED.value,
        chat_id=chat_id,
        chat=chat,
        message_thread_id=message_thread_id,
        enabled=enabled,
    )
