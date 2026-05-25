# PR-14 failing tests context



## FILE: tests/test_web_admin_api_error_settings_page.py

```python
"""Тесты SSR-страницы Dev API errors."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_RETRY_NEXT_RUN,
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import API_ERROR_STATUS_RESOLVED
from app.web.admin_api_error_settings import get_api_error_settings_service
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


class FakeApiErrorSettingsService:
    """Fake service Dev API errors страницы."""

    def __init__(self, errors: list[ApiError] | None = None) -> None:
        """Инициализирует fake service.

        Args:
            errors: API errors для страницы.
        """
        self.errors = errors or []
        self.calls: list[tuple[object, ...]] = []

    async def list_errors(
        self,
        *,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> tuple[ApiError, ...]:
        """Возвращает fake API errors."""
        self.calls.append(("list_errors", status_filter, limit))
        errors = [
            api_error
            for api_error in self.errors
            if status_filter is None or api_error.status == status_filter
        ]

        return tuple(errors[:limit])


def make_settings() -> Settings:
    """Создаёт settings для route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_api_error_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к Dev API errors page."""
    service = FakeApiErrorSettingsService([_make_api_error()])

    with _override_service(service), TestClient(app) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 403
    assert service.calls == []


def test_api_error_settings_page_renders_summary_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет карточки, summary и раскрываемый debug."""
    service = FakeApiErrorSettingsService(
        [
            _make_api_error(
                status=API_ERROR_STATUS_UNRESOLVED,
                response_snippet='{"authorization":"Bearer secret"}',
            ),
            _make_api_error(
                api_error_id=2,
                status=API_ERROR_STATUS_RETRY_NEXT_RUN,
                status_code=500,
                response_snippet='{"reason":"serverError"}',
            ),
            _make_api_error(
                api_error_id=3,
                status=API_ERROR_STATUS_RESOLVED,
                status_code=404,
                response_snippet='{"reason":"notFound"}',
                resolved_at=datetime(2026, 5, 22, 13, 0, tzinfo=UTC),
            ),
        ]
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 200
    assert "Dev API errors" in response.text
    assert "clans/%23MAIN/currentwar" in response.text
    assert "GET" in response.text
    assert "clan" in response.text
    assert "#MAIN" in response.text
    assert "HTTP 403" in response.text
    assert "Retry count" in response.text
    assert "sync_current_wars" in response.text
    assert "ClashForbiddenError" in response.text
    assert "Unresolved" in response.text
    assert "Retrying" in response.text
    assert "Resolved" in response.text
    assert "<details" in response.text
    assert "Response snippet" in response.text
    assert "[redacted]" in response.text
    assert "Bearer secret" not in response.text
    assert 'data-api-error-resolve-url="/admin/api-errors/1/resolve"' in response.text
    assert 'data-api-error-resolve-url="/admin/api-errors/3/resolve"' not in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-badge' in response.text
    assert 'style="' not in response.text
    assert service.calls == [("list_errors", None, 50)]


def test_api_error_settings_page_applies_status_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет фильтр status и limit."""
    service = FakeApiErrorSettingsService(
        [
            _make_api_error(status=API_ERROR_STATUS_STALE),
            _make_api_error(api_error_id=2, status=API_ERROR_STATUS_UNRESOLVED),
        ]
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get(
            "/admin/settings/api-errors",
            params={"status": API_ERROR_STATUS_STALE, "limit": "1"},
        )

    assert response.status_code == 200
    assert "Stale" in response.text
    assert "Показано записей: 1" in response.text
    assert service.calls == [("list_errors", API_ERROR_STATUS_STALE, 1)]


def test_api_error_settings_page_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет empty state."""
    service = FakeApiErrorSettingsService([])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/api-errors")

    assert response.status_code == 200
    assert "API errors не найдены" in response.text


def test_admin_sidebar_shows_api_errors_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет admin-only ссылку на Dev API errors в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/api-errors" in response.text
    assert "Dev API errors" in response.text


def test_api_error_settings_static_assets_are_served() -> None:
    """Проверяет CSS и JS страницы Dev API errors."""
    with TestClient(app) as client:
        css_response = client.get("/static/css/pages/admin_api_errors.css")
        js_response = client.get("/static/js/pages/admin_api_errors.js")

    assert css_response.status_code == 200
    assert ".bn-admin-api-errors" in css_response.text
    assert ".bn-api-error-card" in css_response.text
    assert js_response.status_code == 200
    assert "data-api-error-resolve-url" in js_response.text
    assert 'method: "POST"' in js_response.text


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeApiErrorSettingsService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service страницы.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_service(service: FakeApiErrorSettingsService) -> Iterator[None]:
    """Подменяет dependency сервиса страницы.

    Args:
        service: Fake service страницы.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_api_error_settings_service)
    app.dependency_overrides[get_api_error_settings_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_api_error_settings_service, None)
        else:
            app.dependency_overrides[get_api_error_settings_service] = previous_override


def _make_api_error(
    *,
    api_error_id: int = 1,
    status: str = API_ERROR_STATUS_UNRESOLVED,
    status_code: int | None = 403,
    response_snippet: str | None = '{"reason":"accessDenied"}',
    resolved_at: datetime | None = None,
) -> ApiError:
    """Создаёт ApiError для page-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status: Статус ошибки.
        status_code: HTTP status code.
        response_snippet: Response snippet.
        resolved_at: Время закрытия ошибки.

    Returns:
        Модель ApiError.
    """
    return ApiError(
        id=api_error_id,
        endpoint="clans/%23MAIN/currentwar",
        method="GET",
        entity_type="clan",
        entity_tag="#MAIN",
        status_code=status_code,
        message="Forbidden by Clash API",
        response_snippet=response_snippet,
        exception_class="ClashForbiddenError",
        worker_name="sync_current_wars",
        retry_count=2,
        status=status,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        resolved_at=resolved_at,
    )

```


## FILE: tests/test_web_admin_clan_settings_page.py

```python
"""Тесты SSR-страницы настроек кланов."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import Clan
from app.domain import ClanType
from app.integrations.clash import ClashClan, ClashNotFoundError
from app.web.admin_clans import get_admin_clan_service
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


class FakeClanSettingsService:
    """Fake service для SSR-страницы настроек кланов."""

    def __init__(
        self,
        *,
        clans: list[Clan] | None = None,
        verified_clan: ClashClan | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake service.

        Args:
            clans: Кланы для страницы.
            verified_clan: DTO проверенного клана.
            error: Ошибка для action methods.
        """
        self.clans = clans or []
        self.verified_clan = verified_clan or _make_verified_clan()
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    async def list_clans(self) -> tuple[Clan, ...]:
        """Возвращает список fake-кланов."""
        self.calls.append(("list_clans",))
        return tuple(self.clans)

    async def check_clan(self, *, clan_tag: str) -> ClashClan:
        """Проверяет fake-клан."""
        self.calls.append(("check_clan", clan_tag))
        self._raise_if_needed()
        return self.verified_clan

    async def add_clan(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Добавляет fake-клан."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("add_clan", clan_tag, normalized_type))
        self._raise_if_needed()
        return _make_clan(clan_type=normalized_type)

    async def refresh_clan(self, *, clan_tag: str) -> Clan:
        """Обновляет fake-клан."""
        self.calls.append(("refresh_clan", clan_tag))
        self._raise_if_needed()
        return _make_clan(name="Fresh name")

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип fake-клана."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("update_clan_type", clan_tag, normalized_type))
        self._raise_if_needed()
        return _make_clan(clan_type=normalized_type)

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Деактивирует fake-клан."""
        self.calls.append(("deactivate_clan", clan_tag))
        self._raise_if_needed()
        return _make_clan(is_active=False)

    def _raise_if_needed(self) -> None:
        """Выбрасывает заданную ошибку, если она есть."""
        if self.error is not None:
            raise self.error


def make_settings() -> Settings:
    """Создаёт settings для тестов страницы.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_clan_settings_page_rejects_anonymous_user() -> None:
    """Проверяет admin-only доступ к странице настроек кланов."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _override_clan_service(service), TestClient(app) as client:
        response = client.get("/admin/settings/clans")

    assert response.status_code == 403
    assert service.calls == []


def test_clan_settings_page_renders_admin_cards_and_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет SSR-страницу настроек кланов."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/settings/clans")

    assert response.status_code == 200
    assert "Настройки кланов" in response.text
    assert 'class="bn-card' in response.text
    assert 'class="bn-button' in response.text
    assert 'class="bn-badge' in response.text
    assert "Основа" in response.text
    assert "#2ABC" in response.text
    assert "/admin/settings/clans/add" in response.text
    assert "/admin/settings/clans/check" in response.text
    assert "/admin/settings/clans/type" in response.text
    assert "/admin/settings/clans/refresh" in response.text
    assert "/admin/settings/clans/deactivate" in response.text
    assert 'style="' not in response.text
    assert service.calls == [("list_clans",)]


def test_admin_sidebar_shows_clan_settings_link_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет admin-only ссылку на настройки кланов в sidebar."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert "/admin/settings/clans" in response.text
    assert "Настройки кланов" in response.text


def test_clan_settings_check_action_renders_verified_clan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет проверку тега через Clash API с отображением результата."""
    service = FakeClanSettingsService(clans=[_make_clan()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/check",
            data={"clan_tag": "2abc"},
        )

    assert response.status_code == 200
    assert "Клан найден" in response.text
    assert "Bestiary" in response.text
    assert "#2ABC" in response.text
    assert service.calls == [("check_clan", "2abc"), ("list_clans",)]


def test_clan_settings_add_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет успешное добавление клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/add",
            data={"clan_tag": "2abc", "clan_type": "academy"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_added"
    assert service.calls == [("add_clan", "2abc", "academy")]


def test_clan_settings_update_type_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет смену типа клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/type",
            data={"clan_tag": "#2ABC", "clan_type": "freezer"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_type_updated"
    assert service.calls == [("update_clan_type", "#2ABC", "freezer")]


def test_clan_settings_refresh_action_redirects_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет ручное обновление клана через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/refresh",
            data={"clan_tag": "#2ABC"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_refreshed"
    assert service.calls == [("refresh_clan", "#2ABC")]


def test_clan_settings_deactivate_action_is_soft_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет soft deactivate через форму."""
    service = FakeClanSettingsService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/deactivate",
            data={"clan_tag": "#2ABC"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/clans?notice=clan_deactivated"
    assert service.calls == [("deactivate_clan", "#2ABC")]


def test_clan_settings_form_error_is_rendered_near_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет отображение ошибки рядом с формой."""
    service = FakeClanSettingsService(
        clans=[_make_clan()],
        error=ClashNotFoundError(
            "Clash API returned HTTP 404 for GET clans/%23BAD.",
            endpoint="clans/%23BAD",
            method="GET",
            status_code=404,
            response_snippet=None,
        ),
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/settings/clans/add",
            data={"clan_tag": "#BAD", "clan_type": "main"},
        )

    assert response.status_code == 400
    assert 'data-action-target="add"' in response.text
    assert "Клан не найден в Clash of Clans API." in response.text
    assert service.calls == [("add_clan", "#BAD", "main"), ("list_clans",)]


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeClanSettingsService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie и fake service.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake clan settings service.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_clan_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_clan_service(service: FakeClanSettingsService) -> Iterator[None]:
    """Подменяет dependency сервиса кланов.

    Args:
        service: Fake clan settings service.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_clan_service)
    app.dependency_overrides[get_admin_clan_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_clan_service, None)
        else:
            app.dependency_overrides[get_admin_clan_service] = previous_override


def _make_clan(
    *,
    clan_type: str = ClanType.MAIN.value,
    name: str = "Bestiary",
    level: int | None = 17,
    badge_url: str | None = "https://example.test/badge.png",
    is_active: bool = True,
    sync_status: str | None = "ok",
) -> Clan:
    """Создаёт модель клана для тестов страницы.

    Args:
        clan_type: Тип клана.
        name: Название клана.
        level: Уровень клана.
        badge_url: URL badge.
        is_active: Признак активного мониторинга.
        sync_status: Статус синхронизации.

    Returns:
        Модель Clan.
    """
    return Clan(
        id=1,
        tag="#2ABC",
        name=name,
        type=clan_type,
        level=level,
        badge_url=badge_url,
        is_active=is_active,
        last_sync_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        sync_status=sync_status,
    )


def _make_verified_clan() -> ClashClan:
    """Создаёт DTO проверенного клана.

    Returns:
        DTO ClashClan.
    """
    return ClashClan(
        tag="#2ABC",
        name="Bestiary",
        level=17,
        badge_url="https://example.test/badge.png",
        members_count=44,
    )

```


## FILE: tests/test_web_admin_telegram_settings_page.py

```python
"""Тесты SSR-страницы Telegram settings."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

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

    with TestClient(app) as client:
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

```


## FILE: tests/test_web_auth.py

```python
"""Тесты Telegram Login и admin-cookie web-слоя."""

import hmac
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import TelegramUser
from app.web.auth import get_telegram_login_user_service, get_web_auth_settings
from app.web.security import (
    build_admin_cookie_value,
    verify_admin_cookie_value,
    verify_telegram_login_payload,
)

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


class FakeTelegramLoginUserService:
    """In-memory fake сервиса Telegram-пользователей для route-тестов."""

    def __init__(self, *, admin_telegram_id: int) -> None:
        """Инициализирует fake service.

        Args:
            admin_telegram_id: Telegram ID администратора.
        """
        self.admin_telegram_id = admin_telegram_id
        self.upsert_calls: list[dict[str, object]] = []

    async def upsert_telegram_user(
        self,
        *,
        telegram_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramUser:
        """Создаёт TelegramUser без обращения к БД.

        Args:
            telegram_id: Telegram ID пользователя.
            username: Telegram username.
            display_name: Отображаемое имя.

        Returns:
            Модель TelegramUser.
        """
        self.upsert_calls.append(
            {
                "telegram_id": telegram_id,
                "username": username,
                "display_name": display_name,
            }
        )
        return TelegramUser(
            telegram_id=telegram_id,
            username=username,
            display_name=display_name,
            is_admin_cached=telegram_id == self.admin_telegram_id,
        )

    def has_admin_access(self, telegram_user: TelegramUser | None) -> bool:
        """Проверяет admin-доступ через fake settings.

        Args:
            telegram_user: Модель TelegramUser или `None`.

        Returns:
            `True`, если пользователь является админом.
        """
        return telegram_user is not None and telegram_user.telegram_id == self.admin_telegram_id


def make_settings(
    *,
    telegram_admin_id: int = 123456789,
    telegram_bot_token: str = "test-telegram-token-123",
) -> Settings:
    """Создаёт settings для web-auth тестов.

    Args:
        telegram_admin_id: Telegram ID администратора.
        telegram_bot_token: Токен Telegram-бота.

    Returns:
        Провалидированный экземпляр settings.
    """
    return Settings(
        **(
            VALID_SETTINGS
            | {
                "TELEGRAM_ADMIN_ID": telegram_admin_id,
                "TELEGRAM_BOT_TOKEN": telegram_bot_token,
            }
        )
    )


def test_verify_telegram_login_payload_accepts_valid_payload() -> None:
    """Проверяет успешную проверку Telegram Login подписи."""
    bot_token = "test-telegram-token-123"
    now_ts = int(time.time())
    payload = _build_telegram_login_payload(
        bot_token=bot_token,
        telegram_id=123456789,
        auth_date=now_ts,
        username="admin",
        first_name="Admin",
        last_name="User",
    )

    verified_payload = verify_telegram_login_payload(
        payload,
        bot_token=bot_token,
        now_ts=now_ts,
    )

    assert verified_payload.telegram_id == 123456789
    assert verified_payload.username == "admin"
    assert verified_payload.display_name == "Admin User"


def test_verify_telegram_login_payload_rejects_invalid_hash() -> None:
    """Проверяет отказ при невалидной подписи Telegram Login."""
    bot_token = "test-telegram-token-123"
    now_ts = int(time.time())
    payload = _build_telegram_login_payload(
        bot_token=bot_token,
        telegram_id=123456789,
        auth_date=now_ts,
    )
    payload["hash"] = "0" * 64

    try:
        verify_telegram_login_payload(payload, bot_token=bot_token, now_ts=now_ts)
    except ValueError as exc:
        assert "Подпись Telegram Login payload невалидна" in str(exc)
    else:
        raise AssertionError("Невалидная подпись должна отклоняться.")


def test_verify_telegram_login_payload_rejects_stale_payload() -> None:
    """Проверяет отказ от устаревшего Telegram Login payload."""
    bot_token = "test-telegram-token-123"
    now_ts = int(time.time())
    payload = _build_telegram_login_payload(
        bot_token=bot_token,
        telegram_id=123456789,
        auth_date=now_ts - 90_000,
    )

    try:
        verify_telegram_login_payload(payload, bot_token=bot_token, now_ts=now_ts)
    except ValueError as exc:
        assert "устарел" in str(exc)
    else:
        raise AssertionError("Устаревший payload должен отклоняться.")


def test_admin_cookie_roundtrip() -> None:
    """Проверяет подпись и чтение admin-cookie."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )

    assert (
        verify_admin_cookie_value(
            cookie_value,
            secret=settings.web_session_secret,
        )
        == settings.telegram_admin_id
    )


def test_admin_cookie_rejects_tampered_value() -> None:
    """Проверяет отказ от изменённой admin-cookie."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )

    assert (
        verify_admin_cookie_value(
            f"42.{cookie_value.split('.', maxsplit=1)[1]}",
            secret=settings.web_session_secret,
        )
        is None
    )


def test_telegram_login_route_sets_admin_cookie_for_admin() -> None:
    """Проверяет, что admin получает подписанную admin-cookie."""
    settings = make_settings(telegram_admin_id=123456789)
    fake_service = FakeTelegramLoginUserService(admin_telegram_id=settings.telegram_admin_id)
    payload = _build_telegram_login_payload(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        telegram_id=settings.telegram_admin_id,
    )

    with (
        _override_auth_dependencies(settings=settings, service=fake_service),
        TestClient(app) as client,
    ):
        response = client.get(
            "/auth/telegram",
            params=payload,
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert fake_service.upsert_calls == [
        {
            "telegram_id": settings.telegram_admin_id,
            "username": "admin",
            "display_name": "Admin User",
        }
    ]

    cookie_value = response.cookies.get(settings.web_admin_cookie_name)
    assert cookie_value is not None
    assert (
        verify_admin_cookie_value(
            cookie_value,
            secret=settings.web_session_secret,
        )
        == settings.telegram_admin_id
    )


def test_telegram_login_route_does_not_set_admin_cookie_for_regular_user() -> None:
    """Проверяет, что обычный пользователь не получает admin-cookie."""
    settings = make_settings(telegram_admin_id=999)
    fake_service = FakeTelegramLoginUserService(admin_telegram_id=settings.telegram_admin_id)
    payload = _build_telegram_login_payload(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        telegram_id=123456789,
    )

    with (
        _override_auth_dependencies(settings=settings, service=fake_service),
        TestClient(app) as client,
    ):
        response = client.get(
            "/auth/telegram",
            params=payload,
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert fake_service.upsert_calls == [
        {
            "telegram_id": 123456789,
            "username": "admin",
            "display_name": "Admin User",
        }
    ]

    cookie_value = response.cookies.get(settings.web_admin_cookie_name)
    assert (
        verify_admin_cookie_value(
            cookie_value or "",
            secret=settings.web_session_secret,
        )
        is None
    )


def test_telegram_login_route_rejects_invalid_payload() -> None:
    """Проверяет отказ route от невалидного Telegram Login payload."""
    settings = make_settings()
    fake_service = FakeTelegramLoginUserService(admin_telegram_id=settings.telegram_admin_id)
    payload = _build_telegram_login_payload(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        telegram_id=settings.telegram_admin_id,
    )
    payload["hash"] = "0" * 64

    with (
        _override_auth_dependencies(settings=settings, service=fake_service),
        TestClient(app) as client,
    ):
        response = client.get(
            "/auth/telegram",
            params=payload,
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert fake_service.upsert_calls == []


def test_logout_route_deletes_admin_cookie() -> None:
    """Проверяет удаление admin-cookie через logout route."""
    settings = make_settings()
    fake_service = FakeTelegramLoginUserService(admin_telegram_id=settings.telegram_admin_id)

    with (
        _override_auth_dependencies(settings=settings, service=fake_service),
        TestClient(app) as client,
    ):
        response = client.get("/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert settings.web_admin_cookie_name in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_dashboard_uses_admin_context_from_valid_admin_cookie(monkeypatch: Any) -> None:
    """Проверяет, что dashboard видит admin mode из подписанной cookie."""
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        response = client.get("/")

    assert response.status_code == 200
    assert 'data-bn-admin="true"' in response.text
    assert 'data-bn-readonly="false"' in response.text
    assert 'data-admin-only="true"' in response.text
    assert "/auth/logout" in response.text


def test_dashboard_ignores_invalid_admin_cookie(monkeypatch: Any) -> None:
    """Проверяет, что невалидная cookie не даёт admin mode."""
    settings = make_settings()
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, "bad.cookie")
        response = client.get("/")

    assert response.status_code == 200
    assert 'data-bn-admin="false"' in response.text
    assert 'data-bn-readonly="true"' in response.text
    assert 'data-admin-only="true"' not in response.text


@contextmanager
def _override_auth_dependencies(
    *,
    settings: Settings,
    service: FakeTelegramLoginUserService,
) -> Iterator[None]:
    """Временно подменяет FastAPI dependencies web-auth слоя.

    Args:
        settings: Settings для теста.
        service: Fake service Telegram-пользователей.

    Yields:
        Управление тесту.
    """
    previous_settings_override = app.dependency_overrides.get(get_web_auth_settings)
    previous_service_override = app.dependency_overrides.get(get_telegram_login_user_service)

    app.dependency_overrides[get_web_auth_settings] = lambda: settings
    app.dependency_overrides[get_telegram_login_user_service] = lambda: service

    try:
        yield
    finally:
        if previous_settings_override is None:
            app.dependency_overrides.pop(get_web_auth_settings, None)
        else:
            app.dependency_overrides[get_web_auth_settings] = previous_settings_override

        if previous_service_override is None:
            app.dependency_overrides.pop(get_telegram_login_user_service, None)
        else:
            app.dependency_overrides[get_telegram_login_user_service] = previous_service_override


def _build_telegram_login_payload(
    *,
    bot_token: str,
    telegram_id: int,
    auth_date: int | None = None,
    username: str | None = "admin",
    first_name: str | None = "Admin",
    last_name: str | None = "User",
) -> dict[str, str]:
    """Создаёт подписанный Telegram Login payload для тестов.

    Args:
        bot_token: Telegram bot token.
        telegram_id: Telegram ID пользователя.
        auth_date: Unix timestamp авторизации.
        username: Username.
        first_name: Имя.
        last_name: Фамилия.

    Returns:
        Payload с корректным `hash`.
    """
    payload = {
        "id": str(telegram_id),
        "auth_date": str(auth_date or int(time.time())),
    }

    if username is not None:
        payload["username"] = username

    if first_name is not None:
        payload["first_name"] = first_name

    if last_name is not None:
        payload["last_name"] = last_name

    payload["hash"] = _sign_telegram_login_payload(payload, bot_token=bot_token)

    return payload


def _sign_telegram_login_payload(
    payload: Mapping[str, str],
    *,
    bot_token: str,
) -> str:
    """Подписывает Telegram Login payload по алгоритму Telegram."""
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(payload.items()) if key != "hash"
    )
    secret_key = sha256(bot_token.encode("utf-8")).digest()

    return hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        sha256,
    ).hexdigest()

```


## FILE: tests/web_dashboard_helpers.py

```text
MISSING
```


## FILE: tests/test_web_skeleton.py

```python
"""Тесты раннего web-skeleton."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app
from app.services.web_read_models import DashboardSummaryView, DashboardView
from app.web.feature_pages import get_web_feature_page_service

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
MACROS_DIR = TEMPLATES_DIR / "shared" / "macros"


class FakeDashboardService:
    """Fake service для skeleton smoke-тестов."""

    async def get_dashboard(self) -> DashboardView:
        """Возвращает пустой Dashboard.

        Returns:
            View model пустого Dashboard.
        """
        return DashboardView(
            summary=DashboardSummaryView(
                total_clans=0,
                total_accounts=0,
                linked_accounts=0,
                real_people=0,
                problems=0,
                last_sync_text="ещё не было",
            ),
            groups=(),
        )


def test_dashboard_returns_html_page() -> None:
    """Проверяет, что `/` отдаёт HTML Dashboard."""
    with _override_dashboard_service(FakeDashboardService()), TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BestiaryNavigator_bot" in response.text
    assert "Dashboard" in response.text
    assert "/static/css/app.css" in response.text
    assert 'class="bn-layout"' in response.text
    assert 'data-bn-admin="false"' in response.text
    assert 'data-bn-readonly="true"' in response.text
    assert 'data-admin-only="true"' not in response.text
    assert "/admin/settings/clans" not in response.text
    assert "/admin/settings/telegram" not in response.text
    assert "/admin/settings/api-errors" not in response.text
    assert 'class="bn-sidebar"' in response.text
    assert 'class="bn-topbar"' in response.text
    assert 'class="bn-page"' in response.text
    assert 'class="bn-section-header"' in response.text
    assert 'class="bn-section bn-dashboard"' in response.text
    assert "bn-empty-state" in response.text
    assert "Кланы ещё не добавлены" in response.text
    assert "bn-badge" in response.text
    assert "UI skeleton" not in response.text


def test_static_css_is_served() -> None:
    """Проверяет раздачу CSS через static mount."""
    with TestClient(app) as client:
        response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert '@import url("./tokens.css");' in response.text
    assert '@import url("./base.css");' in response.text
    assert '@import url("./components.css");' in response.text
    assert '@import url("./utilities.css");' in response.text
    assert '@import url("./pages/dashboard.css");' in response.text
    assert '@import url("./pages/admin_clans.css");' in response.text
    assert '@import url("./pages/admin_telegram.css");' in response.text
    assert '@import url("./pages/admin_api_errors.css");' in response.text


def test_css_layers_are_served() -> None:
    """Проверяет раздачу CSS-слоёв через static mount."""
    with TestClient(app) as client:
        tokens_response = client.get("/static/css/tokens.css")
        base_response = client.get("/static/css/base.css")
        components_response = client.get("/static/css/components.css")
        utilities_response = client.get("/static/css/utilities.css")
        dashboard_response = client.get("/static/css/pages/dashboard.css")
        admin_clans_response = client.get("/static/css/pages/admin_clans.css")
        admin_telegram_response = client.get("/static/css/pages/admin_telegram.css")
        admin_api_errors_response = client.get("/static/css/pages/admin_api_errors.css")

    assert tokens_response.status_code == 200
    assert base_response.status_code == 200
    assert components_response.status_code == 200
    assert utilities_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert admin_clans_response.status_code == 200
    assert admin_telegram_response.status_code == 200
    assert admin_api_errors_response.status_code == 200

    assert "--bn-bg-page" in tokens_response.text
    assert "color-scheme: dark" in tokens_response.text
    assert "body" in base_response.text
    assert ".bn-card" in components_response.text
    assert ".bn-layout" in components_response.text
    assert ".bn-sidebar" in components_response.text
    assert ".bn-topbar" in components_response.text
    assert ".bn-page" in components_response.text
    assert ".bn-section" in components_response.text
    assert ".bn-card-header" in components_response.text
    assert ".bn-card-body" in components_response.text
    assert ".bn-card-footer" in components_response.text
    assert ".bn-badge" in components_response.text
    assert ".bn-button" in components_response.text
    assert ".bn-empty-state" in components_response.text
    assert "minmax(0, 1fr)" in components_response.text
    assert ".bn-sr-only" in utilities_response.text
    assert ".bn-dashboard" in dashboard_response.text
    assert ".bn-admin-clans" in admin_clans_response.text
    assert "minmax(16rem, 1fr)" in admin_clans_response.text
    assert ".bn-admin-telegram" in admin_telegram_response.text
    assert ".bn-notification-type" in admin_telegram_response.text
    assert ".bn-admin-api-errors" in admin_api_errors_response.text
    assert ".bn-api-error-card" in admin_api_errors_response.text


def test_templates_do_not_use_inline_styles() -> None:
    """Проверяет, что ранние шаблоны не используют inline CSS."""
    with _override_dashboard_service(FakeDashboardService()), TestClient(app) as client:
        response = client.get("/")

    assert 'style="' not in response.text


def test_shared_macro_files_exist() -> None:
    """Проверяет наличие всех shared Jinja macro-файлов."""
    expected_macro_files = {
        "assets.html",
        "badges.html",
        "buttons.html",
        "cards.html",
        "empty_states.html",
        "frames.html",
        "progress.html",
        "status.html",
        "tables.html",
    }

    actual_macro_files = {path.name for path in MACROS_DIR.iterdir() if path.is_file()}

    assert expected_macro_files == actual_macro_files


def test_base_template_imports_shared_macros() -> None:
    """Проверяет, что base layout импортирует shared macros."""
    base_template = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

    for macro_name in (
        "assets",
        "badges",
        "buttons",
        "cards",
        "empty_states",
        "frames",
        "progress",
        "status",
        "tables",
    ):
        assert f"as {macro_name}" in base_template


def test_dashboard_uses_macro_rendered_components() -> None:
    """Проверяет, что macro-rendered badge/card видны в HTML."""
    with _override_dashboard_service(FakeDashboardService()), TestClient(app) as client:
        response = client.get("/")

    assert 'class="bn-badge bn-badge--info"' in response.text
    assert "bn-card" in response.text
    assert "bn-empty-state" in response.text


@contextmanager
def _override_dashboard_service(service: FakeDashboardService) -> Iterator[None]:
    """Подменяет dependency Dashboard service.

    Args:
        service: Fake service.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_web_feature_page_service)
    app.dependency_overrides[get_web_feature_page_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_web_feature_page_service, None)
        else:
            app.dependency_overrides[get_web_feature_page_service] = previous_override

```


## FILE: tests/test_web_feature_dashboard.py

```python
"""Тесты пользовательской Dashboard-страницы."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.main import app
from app.services.web_read_models import (
    DashboardClanCardView,
    DashboardClanGroupView,
    DashboardCwlLineView,
    DashboardRaidLineView,
    DashboardSummaryView,
    DashboardView,
    DashboardWarLineView,
)
from app.web.feature_pages import get_web_feature_page_service


class FakeDashboardService:
    """Fake read-service Dashboard для route/template тестов."""

    def __init__(self, dashboard: DashboardView) -> None:
        """Инициализирует fake service.

        Args:
            dashboard: View model, которую должен вернуть service.
        """
        self._dashboard = dashboard

    async def get_dashboard(self) -> DashboardView:
        """Возвращает подготовленный Dashboard.

        Returns:
            View model Dashboard.
        """
        return self._dashboard


def test_dashboard_renders_grouped_clan_cards() -> None:
    """Проверяет группировку и карточки кланов на Dashboard."""
    dashboard = _dashboard_with_clans()

    with _override_dashboard_service(FakeDashboardService(dashboard)), TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Клановый штаб: составы, войны, рейды, ЛВК" in response.text
    assert "Основные" in response.text
    assert "Академии" in response.text
    assert "Морозилки" in response.text
    assert "Clan Main" in response.text
    assert "Academy One" in response.text
    assert "Freezer Alpha" in response.text
    assert "18/30" in response.text
    assert "120/300" in response.text
    assert "Round 3" in response.text
    assert "Боевые блоки скрыты для морозилки" in response.text
    assert "/clans/1" in response.text
    assert 'data-admin-only="true"' not in response.text


def test_dashboard_renders_empty_state() -> None:
    """Проверяет пустое состояние Dashboard."""
    dashboard = _empty_dashboard()

    with _override_dashboard_service(FakeDashboardService(dashboard)), TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Кланы ещё не добавлены" in response.text
    assert "Настройки кланов доступны только админу" in response.text
    assert "UI skeleton" not in response.text


@contextmanager
def _override_dashboard_service(service: FakeDashboardService) -> Iterator[None]:
    """Подменяет dependency Dashboard service.

    Args:
        service: Fake service.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_web_feature_page_service)
    app.dependency_overrides[get_web_feature_page_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_web_feature_page_service, None)
        else:
            app.dependency_overrides[get_web_feature_page_service] = previous_override


def _empty_dashboard() -> DashboardView:
    """Создаёт пустой Dashboard.

    Returns:
        View model пустого Dashboard.
    """
    return DashboardView(
        summary=DashboardSummaryView(
            total_clans=0,
            total_accounts=0,
            linked_accounts=0,
            real_people=0,
            problems=0,
            last_sync_text="ещё не было",
        ),
        groups=(
            DashboardClanGroupView(title="Основные", clan_type="main", clans=()),
            DashboardClanGroupView(title="Академии", clan_type="academy", clans=()),
            DashboardClanGroupView(title="Морозилки", clan_type="freezer", clans=()),
        ),
    )


def _dashboard_with_clans() -> DashboardView:
    """Создаёт Dashboard с main/academy/freezer кланами.

    Returns:
        View model Dashboard.
    """
    summary = DashboardSummaryView(
        total_clans=3,
        total_accounts=90,
        linked_accounts=72,
        real_people=51,
        problems=8,
        last_sync_text="2026-05-23 19:20 UTC",
    )

    main = DashboardClanCardView(
        id=1,
        tag="#MAIN",
        name="Clan Main",
        type="main",
        type_label="Основа",
        type_icon="🛡",
        type_variant="gold",
        level_text="уровень 18",
        badge_url=None,
        sync_status_label="Sync: ok",
        sync_status_variant="ok",
        last_sync_text=datetime(2026, 5, 23, 19, 20, tzinfo=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        account_count=50,
        linked_accounts_count=42,
        real_people_count=28,
        unlinked_accounts_count=8,
        detail_url="/clans/1",
        war=DashboardWarLineView(
            state_label="inWar",
            attacks_text="18/30",
            score_text="23 — 20 звёзд",
            variant="ok",
        ),
        raid=DashboardRaidLineView(
            state_label="ongoing",
            attacks_text="120/300",
            loot_text="450 000",
            variant="warning",
        ),
        cwl=DashboardCwlLineView(
            state_label="inWar",
            season_text="2026-05",
            round_text="Round 3",
            stars_text="120 звёзд",
            variant="info",
        ),
    )
    academy = DashboardClanCardView(
        id=2,
        tag="#ACAD",
        name="Academy One",
        type="academy",
        type_label="Академия",
        type_icon="🎓",
        type_variant="info",
        level_text="уровень 12",
        badge_url=None,
        sync_status_label="Sync: ok",
        sync_status_variant="ok",
        last_sync_text="2026-05-23 19:10 UTC",
        account_count=28,
        linked_accounts_count=22,
        real_people_count=16,
        unlinked_accounts_count=6,
        detail_url="/clans/2",
        war=None,
        raid=None,
        cwl=None,
    )
    freezer = DashboardClanCardView(
        id=3,
        tag="#FREEZE",
        name="Freezer Alpha",
        type="freezer",
        type_label="Морозилка",
        type_icon="❄",
        type_variant="muted",
        level_text="уровень 9",
        badge_url=None,
        sync_status_label="Sync: ok",
        sync_status_variant="ok",
        last_sync_text="2026-05-23 19:00 UTC",
        account_count=12,
        linked_accounts_count=8,
        real_people_count=6,
        unlinked_accounts_count=4,
        detail_url="/clans/3",
        war=None,
        raid=None,
        cwl=None,
    )

    return DashboardView(
        summary=summary,
        groups=(
            DashboardClanGroupView(title="Основные", clan_type="main", clans=(main,)),
            DashboardClanGroupView(title="Академии", clan_type="academy", clans=(academy,)),
            DashboardClanGroupView(title="Морозилки", clan_type="freezer", clans=(freezer,)),
        ),
    )

```


## FILE: app/web/feature_pages.py

```python
"""Пользовательские web feature pages."""

from collections.abc import AsyncIterator
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.services.web_read_models import DashboardReadModelService, DashboardView
from app.web.context import build_template_context
from app.web.templates import templates

router = APIRouter(tags=["web-feature-pages"])


class DashboardPageService(Protocol):
    """Минимальный contract read-service для Dashboard."""

    async def get_dashboard(self) -> DashboardView:
        """Собирает Dashboard read model.

        Returns:
            View model Dashboard.
        """


async def get_web_feature_page_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncIterator[DashboardPageService]:
    """Создаёт read-only service пользовательских web-страниц.

    Args:
        session: Async SQLAlchemy session.

    Yields:
        Read service для feature pages.
    """
    yield DashboardReadModelService.from_session(session=session)


@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    feature_pages: Annotated[DashboardPageService, Depends(get_web_feature_page_service)],
) -> Response:
    """Отдаёт Dashboard с реальными read-only данными.

    Args:
        request: FastAPI request, необходимый Jinja2 для `url_for`.
        feature_pages: Read service пользовательских страниц.

    Returns:
        HTML-страница Dashboard.
    """
    dashboard = await feature_pages.get_dashboard()

    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        build_template_context(
            request,
            page_title="Dashboard",
            active_nav="dashboard",
            dashboard=dashboard,
        ),
    )


__all__ = [
    "DashboardPageService",
    "get_web_feature_page_service",
    "router",
]

```


## FILE: app/web/routes.py

```python
"""Базовые web routes приложения."""

from fastapi import APIRouter

from app.web.admin_api_error_settings import router as admin_api_error_settings_router
from app.web.admin_api_errors import router as admin_api_errors_router
from app.web.admin_clan_settings import router as admin_clan_settings_router
from app.web.admin_clans import router as admin_clans_router
from app.web.admin_notification_routes import router as admin_notification_routes_router
from app.web.admin_telegram_settings import router as admin_telegram_settings_router
from app.web.auth import router as auth_router
from app.web.feature_pages import router as feature_pages_router

router = APIRouter(include_in_schema=False)
router.include_router(auth_router)
router.include_router(feature_pages_router)
router.include_router(admin_api_errors_router)
router.include_router(admin_api_error_settings_router)
router.include_router(admin_clan_settings_router)
router.include_router(admin_clans_router)
router.include_router(admin_telegram_settings_router)
router.include_router(admin_notification_routes_router)

```


## FILE: app/services/web_read_models.py

```python
"""Read models для пользовательских web-страниц.

Модуль собирает агрегированные данные для SSR-страниц. Он не вызывает Clash API,
не отправляет Telegram-сообщения и не меняет состояние БД.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Clan,
    ClanMemberSnapshot,
    CwlSeason,
    CwlWar,
    PlayerAccount,
    RaidMember,
    RaidSeason,
    WarSnapshot,
)
from app.domain.enums import ClanType, RaidMemberStatus

_SYNC_STATUS_OK = "ok"
_CLAN_TYPE_LABELS = {
    ClanType.MAIN.value: "Основа",
    ClanType.ACADEMY.value: "Академия",
    ClanType.FREEZER.value: "Морозилка",
}
_CLAN_TYPE_ICONS = {
    ClanType.MAIN.value: "🛡",
    ClanType.ACADEMY.value: "🎓",
    ClanType.FREEZER.value: "❄",
}
_CLAN_TYPE_VARIANTS = {
    ClanType.MAIN.value: "gold",
    ClanType.ACADEMY.value: "info",
    ClanType.FREEZER.value: "muted",
}


@dataclass(frozen=True, slots=True)
class DashboardMetricView:
    """View model верхней метрики Dashboard.

    Attributes:
        label: Название метрики.
        value: Основное значение.
        helper: Вспомогательный текст.
        variant: Семантический вариант отображения.
        icon: Текстовая иконка.
    """

    label: str
    value: str
    helper: str
    variant: str
    icon: str


@dataclass(frozen=True, slots=True)
class DashboardSummaryView:
    """Сводка Dashboard.

    Attributes:
        total_clans: Количество активных кланов.
        total_accounts: Количество аккаунтов в текущих составах.
        linked_accounts: Количество аккаунтов с Telegram-привязкой.
        real_people: Количество уникальных Telegram-пользователей.
        problems: Количество проблем/предупреждений для сводки.
        last_sync_text: Текст последней синхронизации.
    """

    total_clans: int
    total_accounts: int
    linked_accounts: int
    real_people: int
    problems: int
    last_sync_text: str

    @property
    def linked_percent_text(self) -> str:
        """Возвращает процент привязанных аккаунтов.

        Returns:
            Текст процента или `0%`, если аккаунтов нет.
        """
        if self.total_accounts <= 0:
            return "0%"

        return f"{round(self.linked_accounts / self.total_accounts * 100, 1)}%"

    @property
    def metrics(self) -> tuple[DashboardMetricView, ...]:
        """Возвращает карточки верхних метрик.

        Returns:
            Tuple метрик Dashboard.
        """
        return (
            DashboardMetricView(
                label="Всего кланов",
                value=str(self.total_clans),
                helper=f"Последняя синхронизация: {self.last_sync_text}",
                variant="info",
                icon="🛡",
            ),
            DashboardMetricView(
                label="Аккаунтов всего",
                value=str(self.total_accounts),
                helper="в текущих составах",
                variant="info",
                icon="👤",
            ),
            DashboardMetricView(
                label="Привязано Telegram",
                value=str(self.linked_accounts),
                helper=f"{self.linked_percent_text} от аккаунтов",
                variant="ok",
                icon="✈",
            ),
            DashboardMetricView(
                label="Реальных людей",
                value=str(self.real_people),
                helper="по Telegram",
                variant="gold",
                icon="👥",
            ),
            DashboardMetricView(
                label="Проблем / предупреждений",
                value=str(self.problems),
                helper="требуют внимания",
                variant="danger" if self.problems else "ok",
                icon="⚠",
            ),
        )


@dataclass(frozen=True, slots=True)
class DashboardWarLineView:
    """Краткая строка текущей войны для карточки клана."""

    state_label: str
    attacks_text: str
    score_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardRaidLineView:
    """Краткая строка рейдов для карточки клана."""

    state_label: str
    attacks_text: str
    loot_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardCwlLineView:
    """Краткая строка ЛВК для карточки клана."""

    state_label: str
    season_text: str
    round_text: str
    stars_text: str
    variant: str


@dataclass(frozen=True, slots=True)
class DashboardClanCardView:
    """View model карточки клана на Dashboard.

    Attributes:
        id: DB ID клана.
        tag: Тег клана.
        name: Название клана.
        type: Тип клана.
        type_label: Человекочитаемый тип.
        type_icon: Иконка типа.
        type_variant: Семантический вариант типа.
        level_text: Текст уровня.
        badge_url: URL badge из Clash API.
        sync_status_label: Подпись статуса синхронизации.
        sync_status_variant: Семантический вариант sync status.
        last_sync_text: Текст последней синхронизации.
        account_count: Количество аккаунтов в current-составе.
        linked_accounts_count: Количество привязанных аккаунтов.
        real_people_count: Количество уникальных TelegramUser.
        unlinked_accounts_count: Количество непривязанных аккаунтов.
        detail_url: URL будущей страницы клана.
        war: Краткая строка КВ.
        raid: Краткая строка рейдов.
        cwl: Краткая строка ЛВК.
    """

    id: int
    tag: str
    name: str
    type: str
    type_label: str
    type_icon: str
    type_variant: str
    level_text: str
    badge_url: str | None
    sync_status_label: str
    sync_status_variant: str
    last_sync_text: str
    account_count: int
    linked_accounts_count: int
    real_people_count: int
    unlinked_accounts_count: int
    detail_url: str
    war: DashboardWarLineView | None
    raid: DashboardRaidLineView | None
    cwl: DashboardCwlLineView | None

    @property
    def is_freezer(self) -> bool:
        """Проверяет, является ли клан морозилкой.

        Returns:
            `True`, если тип клана — `freezer`.
        """
        return self.type == ClanType.FREEZER.value


@dataclass(frozen=True, slots=True)
class DashboardClanGroupView:
    """Группа кланов на Dashboard."""

    title: str
    clan_type: str
    clans: tuple[DashboardClanCardView, ...]

    @property
    def has_clans(self) -> bool:
        """Проверяет наличие карточек в группе.

        Returns:
            `True`, если в группе есть кланы.
        """
        return bool(self.clans)


@dataclass(frozen=True, slots=True)
class DashboardView:
    """Полная view model Dashboard."""

    summary: DashboardSummaryView
    groups: tuple[DashboardClanGroupView, ...]

    @property
    def has_clans(self) -> bool:
        """Проверяет наличие активных кланов.

        Returns:
            `True`, если есть хотя бы один клан.
        """
        return any(group.has_clans for group in self.groups)


@dataclass(frozen=True, slots=True)
class _ClanCardBuildResult:
    """Внутренний результат сборки карточки клана."""

    card: DashboardClanCardView
    telegram_user_ids: frozenset[int]


class DashboardReadModelRepository(Protocol):
    """Repository contract для Dashboard read model."""

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает активные кланы."""

    async def list_current_members(self, *, clan_id: int) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current-состав клана."""

    async def list_active_linked_accounts(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты с Telegram-привязкой по тегам."""

    async def get_latest_war(self, *, clan_id: int) -> WarSnapshot | None:
        """Возвращает последний snapshot войны клана."""

    async def get_latest_raid(self, *, clan_id: int) -> RaidSeason | None:
        """Возвращает последний рейдовый сезон клана."""

    async def list_raid_members(self, *, raid_season_id: int) -> tuple[RaidMember, ...]:
        """Возвращает участников рейдового сезона."""

    async def get_latest_cwl_season(self, *, clan_id: int) -> CwlSeason | None:
        """Возвращает последний сезон ЛВК клана."""

    async def get_latest_cwl_war(self, *, cwl_season_id: int) -> CwlWar | None:
        """Возвращает последнюю войну сезона ЛВК."""


class SqlAlchemyDashboardReadModelRepository:
    """SQLAlchemy repository для Dashboard read model."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует repository.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def list_active_clans(self) -> tuple[Clan, ...]:
        """Возвращает активные кланы.

        Returns:
            Tuple активных кланов.
        """
        result = await self._session.execute(select(Clan).where(Clan.is_active.is_(True)))
        clans = tuple(result.scalars().all())

        return tuple(
            sorted(
                clans,
                key=lambda clan: (
                    _clan_type_sort_index(clan.type),
                    clan.name.lower(),
                    clan.tag,
                ),
            )
        )

    async def list_current_members(self, *, clan_id: int) -> tuple[ClanMemberSnapshot, ...]:
        """Возвращает current-состав клана."""
        result = await self._session.execute(
            select(ClanMemberSnapshot)
            .where(
                ClanMemberSnapshot.clan_id == clan_id,
                ClanMemberSnapshot.is_current.is_(True),
            )
            .order_by(
                ClanMemberSnapshot.town_hall_level.desc().nullslast(),
                ClanMemberSnapshot.name.asc(),
                ClanMemberSnapshot.player_tag.asc(),
            )
        )
        return tuple(result.scalars().all())

    async def list_active_linked_accounts(
        self,
        *,
        player_tags: tuple[str, ...],
    ) -> tuple[PlayerAccount, ...]:
        """Возвращает активные аккаунты с Telegram-привязкой по тегам."""
        if not player_tags:
            return ()

        result = await self._session.execute(
            select(PlayerAccount).where(
                PlayerAccount.player_tag.in_(player_tags),
                PlayerAccount.is_active.is_(True),
                PlayerAccount.telegram_user_id.is_not(None),
            )
        )
        return tuple(result.scalars().all())

    async def get_latest_war(self, *, clan_id: int) -> WarSnapshot | None:
        """Возвращает последний snapshot войны клана."""
        result = await self._session.execute(
            select(WarSnapshot)
            .where(WarSnapshot.clan_id == clan_id)
            .order_by(WarSnapshot.snapshot_at.desc(), WarSnapshot.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_raid(self, *, clan_id: int) -> RaidSeason | None:
        """Возвращает последний рейдовый сезон клана."""
        result = await self._session.execute(
            select(RaidSeason)
            .where(RaidSeason.clan_id == clan_id)
            .order_by(
                RaidSeason.start_time.desc(),
                RaidSeason.snapshot_at.desc(),
                RaidSeason.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_raid_members(self, *, raid_season_id: int) -> tuple[RaidMember, ...]:
        """Возвращает участников рейдового сезона."""
        result = await self._session.execute(
            select(RaidMember)
            .where(RaidMember.raid_season_id == raid_season_id)
            .order_by(RaidMember.attacks.asc(), RaidMember.name.asc())
        )
        return tuple(result.scalars().all())

    async def get_latest_cwl_season(self, *, clan_id: int) -> CwlSeason | None:
        """Возвращает последний сезон ЛВК клана."""
        result = await self._session.execute(
            select(CwlSeason)
            .where(CwlSeason.clan_id == clan_id)
            .order_by(CwlSeason.started_at.desc(), CwlSeason.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_cwl_war(self, *, cwl_season_id: int) -> CwlWar | None:
        """Возвращает последнюю войну сезона ЛВК."""
        result = await self._session.execute(
            select(CwlWar)
            .where(CwlWar.cwl_season_id == cwl_season_id)
            .order_by(CwlWar.round_number.desc(), CwlWar.start_time.desc(), CwlWar.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class DashboardReadModelService:
    """Сервис сборки Dashboard read model."""

    def __init__(self, *, repository: DashboardReadModelRepository) -> None:
        """Инициализирует service.

        Args:
            repository: Repository чтения dashboard-данных.
        """
        self._repository = repository

    @classmethod
    def from_session(cls, *, session: AsyncSession) -> "DashboardReadModelService":
        """Создаёт service поверх SQLAlchemy session.

        Args:
            session: Async SQLAlchemy session.

        Returns:
            Настроенный read model service.
        """
        return cls(repository=SqlAlchemyDashboardReadModelRepository(session))

    async def get_dashboard(self) -> DashboardView:
        """Собирает Dashboard из сохранённых snapshot-данных.

        Returns:
            View model Dashboard.
        """
        clans = await self._repository.list_active_clans()
        build_results = [await self._build_clan_card(clan) for clan in clans]
        cards = tuple(result.card for result in build_results)
        people_ids = frozenset(
            telegram_user_id
            for result in build_results
            for telegram_user_id in result.telegram_user_ids
        )

        return DashboardView(
            summary=_build_summary(cards=cards, real_people_count=len(people_ids)),
            groups=_build_groups(cards),
        )

    async def _build_clan_card(self, clan: Clan) -> _ClanCardBuildResult:
        """Собирает карточку одного клана.

        Args:
            clan: Модель клана.

        Returns:
            Карточка клана и служебные TelegramUser IDs.
        """
        clan_id = _required_model_id(clan, model_name="Clan")
        current_members = await self._repository.list_current_members(clan_id=clan_id)
        current_player_tags = tuple(member.player_tag for member in current_members)
        linked_accounts = await self._repository.list_active_linked_accounts(
            player_tags=current_player_tags
        )
        linked_by_tag = {account.player_tag: account for account in linked_accounts}

        telegram_user_ids = frozenset(
            account.telegram_user_id
            for account in linked_accounts
            if account.telegram_user_id is not None
        )
        linked_accounts_count = sum(1 for tag in current_player_tags if tag in linked_by_tag)
        unlinked_accounts_count = max(len(current_members) - linked_accounts_count, 0)
        is_freezer = clan.type == ClanType.FREEZER.value

        war = None
        if not is_freezer:
            latest_war = await self._repository.get_latest_war(clan_id=clan_id)
            war = _build_war_line(latest_war)
        raid = None
        if not is_freezer:
            raid_season = await self._repository.get_latest_raid(clan_id=clan_id)
            raid = await self._build_raid_line(raid_season)

        cwl = None
        if not is_freezer:
            cwl_season = await self._repository.get_latest_cwl_season(clan_id=clan_id)
            cwl = await self._build_cwl_line(cwl_season)

        card = DashboardClanCardView(
            id=clan_id,
            tag=clan.tag,
            name=clan.name,
            type=clan.type,
            type_label=_clan_type_label(clan.type),
            type_icon=_clan_type_icon(clan.type),
            type_variant=_clan_type_variant(clan.type),
            level_text=_format_level(clan.level),
            badge_url=clan.badge_url,
            sync_status_label=_sync_status_label(clan.sync_status),
            sync_status_variant=_sync_status_variant(clan.sync_status),
            last_sync_text=_format_optional_datetime(clan.last_sync_at),
            account_count=len(current_members),
            linked_accounts_count=linked_accounts_count,
            real_people_count=len(telegram_user_ids),
            unlinked_accounts_count=unlinked_accounts_count,
            detail_url=f"/clans/{clan_id}",
            war=war,
            raid=raid,
            cwl=cwl,
        )

        return _ClanCardBuildResult(card=card, telegram_user_ids=telegram_user_ids)

    async def _build_raid_line(
        self, raid_season: RaidSeason | None
    ) -> DashboardRaidLineView | None:
        """Собирает строку рейдов.

        Args:
            raid_season: Последний рейдовый сезон.

        Returns:
            View model строки рейдов или `None`.
        """
        if raid_season is None:
            return None

        raid_season_id = _required_model_id(raid_season, model_name="RaidSeason")
        members = await self._repository.list_raid_members(raid_season_id=raid_season_id)
        used_attacks = sum(member.attacks for member in members)
        expected_attacks = sum(member.project_expected_attacks for member in members)
        problem_count = sum(
            1 for member in members if member.status != RaidMemberStatus.RAID_FULL.value
        )

        return DashboardRaidLineView(
            state_label=_state_label(raid_season.state),
            attacks_text=_format_ratio(used_attacks, expected_attacks, fallback="нет участников"),
            loot_text=f"{raid_season.capital_total_loot:,}".replace(",", " "),
            variant="warning" if problem_count else "ok",
        )

    async def _build_cwl_line(self, cwl_season: CwlSeason | None) -> DashboardCwlLineView | None:
        """Собирает строку ЛВК.

        Args:
            cwl_season: Последний сезон ЛВК.

        Returns:
            View model строки ЛВК или `None`.
        """
        if cwl_season is None:
            return None

        cwl_season_id = _required_model_id(cwl_season, model_name="CwlSeason")
        latest_war = await self._repository.get_latest_cwl_war(cwl_season_id=cwl_season_id)

        if latest_war is None:
            round_text = "раундов нет"
            stars_text = "звёзды —"
        else:
            round_text = f"Round {latest_war.round_number}"
            stars_text = f"{latest_war.our_stars} звёзд"

        return DashboardCwlLineView(
            state_label=_state_label(cwl_season.state),
            season_text=cwl_season.season,
            round_text=round_text,
            stars_text=stars_text,
            variant="info",
        )


def _build_war_line(war: WarSnapshot | None) -> DashboardWarLineView | None:
    """Собирает строку обычной войны.

    Args:
        war: Последний snapshot войны.

    Returns:
        View model строки войны или `None`.
    """
    if war is None:
        return None

    available_attacks = war.team_size * war.attacks_per_member
    return DashboardWarLineView(
        state_label=_state_label(war.state),
        attacks_text=_format_ratio(war.our_attacks, available_attacks, fallback="атак нет"),
        score_text=f"{war.our_stars} — {war.opponent_stars} звёзд",
        variant="ok" if war.our_stars >= war.opponent_stars else "warning",
    )


def _build_summary(
    *,
    cards: tuple[DashboardClanCardView, ...],
    real_people_count: int,
) -> DashboardSummaryView:
    """Собирает верхнюю сводку Dashboard.

    Args:
        cards: Карточки кланов.
        real_people_count: Количество уникальных TelegramUser.

    Returns:
        View model сводки.
    """
    problems = sum(card.unlinked_accounts_count for card in cards) + sum(
        1 for card in cards if card.sync_status_variant != "ok"
    )

    last_sync_candidates = [
        card.last_sync_text for card in cards if card.last_sync_text != "ещё не было"
    ]
    last_sync_text = last_sync_candidates[0] if last_sync_candidates else "ещё не было"

    return DashboardSummaryView(
        total_clans=len(cards),
        total_accounts=sum(card.account_count for card in cards),
        linked_accounts=sum(card.linked_accounts_count for card in cards),
        real_people=real_people_count,
        problems=problems,
        last_sync_text=last_sync_text,
    )


def _build_groups(cards: tuple[DashboardClanCardView, ...]) -> tuple[DashboardClanGroupView, ...]:
    """Группирует карточки кланов по типам.

    Args:
        cards: Карточки кланов.

    Returns:
        Tuple групп в порядке из ТЗ.
    """
    cards_by_type: dict[str, list[DashboardClanCardView]] = {
        ClanType.MAIN.value: [],
        ClanType.ACADEMY.value: [],
        ClanType.FREEZER.value: [],
    }

    for card in cards:
        cards_by_type.setdefault(card.type, []).append(card)

    return (
        DashboardClanGroupView(
            title="Основные",
            clan_type=ClanType.MAIN.value,
            clans=tuple(cards_by_type[ClanType.MAIN.value]),
        ),
        DashboardClanGroupView(
            title="Академии",
            clan_type=ClanType.ACADEMY.value,
            clans=tuple(cards_by_type[ClanType.ACADEMY.value]),
        ),
        DashboardClanGroupView(
            title="Морозилки",
            clan_type=ClanType.FREEZER.value,
            clans=tuple(cards_by_type[ClanType.FREEZER.value]),
        ),
    )


def _clan_type_sort_index(value: str) -> int:
    """Возвращает индекс сортировки типа клана."""
    if value == ClanType.MAIN.value:
        return 0

    if value == ClanType.ACADEMY.value:
        return 1

    if value == ClanType.FREEZER.value:
        return 2

    return 99


def _clan_type_label(value: str) -> str:
    """Возвращает подпись типа клана."""
    return _CLAN_TYPE_LABELS.get(value, value)


def _clan_type_icon(value: str) -> str:
    """Возвращает иконку типа клана."""
    return _CLAN_TYPE_ICONS.get(value, "●")


def _clan_type_variant(value: str) -> str:
    """Возвращает badge-вариант типа клана."""
    return _CLAN_TYPE_VARIANTS.get(value, "muted")


def _sync_status_label(value: str | None) -> str:
    """Возвращает подпись sync status."""
    if value == _SYNC_STATUS_OK:
        return "Sync: ok"

    if value:
        return f"Sync: {value}"

    return "Sync: нет данных"


def _sync_status_variant(value: str | None) -> str:
    """Возвращает семантический вариант sync status."""
    if value == _SYNC_STATUS_OK:
        return "ok"

    if value is None:
        return "muted"

    return "warning"


def _state_label(value: str) -> str:
    """Нормализует состояние внешнего события для UI."""
    normalized = value.replace("_", " ").replace("-", " ").strip()
    return normalized or "нет данных"


def _format_level(value: int | None) -> str:
    """Форматирует уровень клана."""
    if value is None:
        return "уровень —"

    return f"уровень {value}"


def _format_ratio(value: int, max_value: int, *, fallback: str) -> str:
    """Форматирует отношение чисел.

    Args:
        value: Текущее значение.
        max_value: Максимальное значение.
        fallback: Текст для случая, когда максимум неизвестен.

    Returns:
        Текст `value/max_value` или fallback.
    """
    if max_value <= 0:
        return fallback

    return f"{value}/{max_value}"


def _format_optional_datetime(value: datetime | None) -> str:
    """Форматирует datetime для Dashboard.

    Args:
        value: Datetime или `None`.

    Returns:
        Текст даты.
    """
    if value is None:
        return "ещё не было"

    return value.strftime("%Y-%m-%d %H:%M UTC")


def _format_decimal(value: Decimal) -> str:
    """Форматирует Decimal без лишних нулей."""
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _required_model_id(model: object, *, model_name: str) -> int:
    """Достаёт обязательный DB id из SQLAlchemy model.

    Args:
        model: SQLAlchemy model.
        model_name: Имя модели для текста ошибки.

    Returns:
        Положительный DB id.

    Raises:
        RuntimeError: Если модель ещё не сохранена.
    """
    model_id = getattr(model, "id", None)
    if isinstance(model_id, int) and model_id > 0:
        return model_id

    raise RuntimeError(f"{model_name} должен быть сохранён в БД.")


__all__ = [
    "DashboardClanCardView",
    "DashboardClanGroupView",
    "DashboardCwlLineView",
    "DashboardMetricView",
    "DashboardRaidLineView",
    "DashboardReadModelRepository",
    "DashboardReadModelService",
    "DashboardSummaryView",
    "DashboardView",
    "DashboardWarLineView",
    "SqlAlchemyDashboardReadModelRepository",
]

```
