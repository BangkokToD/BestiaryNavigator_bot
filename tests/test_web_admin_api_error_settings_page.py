"""Тесты SSR-страницы Dev API errors."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from tests.web_dashboard_helpers import override_dashboard_service

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

    with override_dashboard_service(), TestClient(app) as client:
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
