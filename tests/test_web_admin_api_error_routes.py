"""Route-level тесты admin API errors routes."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core.settings import Settings
from app.db.models import ApiError
from app.services.api_error_policies import (
    API_ERROR_STATUS_STALE,
    API_ERROR_STATUS_UNRESOLVED,
)
from app.services.api_errors import API_ERROR_STATUS_RESOLVED, ApiErrorNotFoundError
from app.web.admin_api_errors import get_admin_api_error_service
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


class FakeAdminApiErrorService:
    """Fake service API errors для route-level тестов."""

    def __init__(self, errors: list[ApiError] | None = None) -> None:
        """Инициализирует fake service.

        Args:
            errors: API errors.
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

    async def resolve_error(
        self,
        *,
        api_error_id: int,
        resolved_at: datetime,
    ) -> ApiError:
        """Помечает fake API error как resolved."""
        self.calls.append(("resolve_error", api_error_id))
        for api_error in self.errors:
            if api_error.id == api_error_id:
                api_error.status = API_ERROR_STATUS_RESOLVED
                api_error.resolved_at = resolved_at
                return api_error

        raise ApiErrorNotFoundError(f"API error {api_error_id} не найдена.")


def make_settings() -> Settings:
    """Создаёт settings для route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_admin_api_errors_reject_anonymous_user() -> None:
    """Проверяет, что API errors доступны только админу."""
    service = FakeAdminApiErrorService([_make_api_error()])

    with _override_admin_api_error_service(service), TestClient(app) as client:
        response = client.get("/admin/api-errors")

    assert response.status_code == 403
    assert service.calls == []


def test_admin_api_errors_summary_mode_hides_debug_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет summary mode без debug-полей."""
    api_error = _make_api_error(
        response_snippet='{"authorization":"Bearer secret"}',
        exception_class="ClashForbiddenError",
    )
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "mode": "summary",
        "count": 1,
        "limit": 50,
        "errors": [
            {
                "id": 1,
                "endpoint": "clans/%23MAIN/currentwar",
                "method": "GET",
                "entity_type": "clan",
                "entity_tag": "#MAIN",
                "status_code": 403,
                "message": "Forbidden by Clash API",
                "worker_name": "sync_current_wars",
                "retry_count": 2,
                "status": "unresolved",
                "is_stale": False,
                "created_at": "2026-05-22T12:00:00Z",
            }
        ],
    }
    assert "authorization" not in response.text.lower()
    assert "response_snippet" not in response.text
    assert "exception_class" not in response.text


def test_admin_api_errors_debug_mode_sanitizes_sensitive_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет debug mode и редактирование секретов."""
    api_error = _make_api_error(
        status=API_ERROR_STATUS_STALE,
        response_snippet='{"token":"secret-token-value"}',
        exception_class="ClashNotFoundError",
    )
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors", params={"mode": "debug"})

    assert response.status_code == 200
    payload = response.json()

    assert payload["mode"] == "debug"
    assert payload["errors"][0]["is_stale"] is True
    assert payload["errors"][0]["response_snippet"] == "[redacted]"
    assert payload["errors"][0]["exception_class"] == "ClashNotFoundError"
    assert "secret-token-value" not in response.text


def test_admin_api_errors_status_filter_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет status filter и limit."""
    stale_error = _make_api_error(api_error_id=1, status=API_ERROR_STATUS_STALE)
    unresolved_error = _make_api_error(api_error_id=2, status=API_ERROR_STATUS_UNRESOLVED)
    service = FakeAdminApiErrorService([stale_error, unresolved_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get(
            "/admin/api-errors",
            params={"status": API_ERROR_STATUS_STALE, "limit": "1"},
        )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["status_filter"] == API_ERROR_STATUS_STALE
    assert response.json()["errors"][0]["status"] == API_ERROR_STATUS_STALE
    assert service.calls == [("list_errors", API_ERROR_STATUS_STALE, 1)]


def test_admin_api_errors_limit_has_max_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет FastAPI validation для limit max 200."""
    service = FakeAdminApiErrorService([_make_api_error()])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/api-errors", params={"limit": "201"})

    assert response.status_code == 422
    assert service.calls == []


def test_admin_api_errors_resolve_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет ручное закрытие API error."""
    api_error = _make_api_error(status=API_ERROR_STATUS_UNRESOLVED)
    service = FakeAdminApiErrorService([api_error])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/api-errors/1/resolve")

    assert response.status_code == 200
    assert response.json()["api_error"]["status"] == API_ERROR_STATUS_RESOLVED
    assert response.json()["api_error"]["resolved_at"] is not None
    assert api_error.status == API_ERROR_STATUS_RESOLVED
    assert api_error.resolved_at is not None
    assert service.calls == [("resolve_error", 1)]


def test_admin_api_errors_resolve_unknown_error_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет 404 для неизвестной API error."""
    service = FakeAdminApiErrorService([])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/api-errors/404/resolve")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "api_error_not_found",
        "message": "API error 404 не найдена.",
    }


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeAdminApiErrorService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с admin-cookie и fake service.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service API errors.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_admin_api_error_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_admin_api_error_service(service: FakeAdminApiErrorService) -> Iterator[None]:
    """Подменяет dependency admin API errors.

    Args:
        service: Fake service API errors.

    Yields:
        Управление тесту.
    """
    previous_override = app.dependency_overrides.get(get_admin_api_error_service)
    app.dependency_overrides[get_admin_api_error_service] = lambda: service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_admin_api_error_service, None)
        else:
            app.dependency_overrides[get_admin_api_error_service] = previous_override


def _make_api_error(
    *,
    api_error_id: int = 1,
    status: str = API_ERROR_STATUS_UNRESOLVED,
    status_code: int | None = 403,
    response_snippet: str | None = '{"reason":"accessDenied"}',
    exception_class: str | None = "ClashForbiddenError",
) -> ApiError:
    """Создаёт ApiError для route-тестов.

    Args:
        api_error_id: DB ID ошибки.
        status: Статус ошибки.
        status_code: HTTP status code.
        response_snippet: Response snippet.
        exception_class: Имя exception class.

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
        exception_class=exception_class,
        worker_name="sync_current_wars",
        retry_count=2,
        status=status,
        created_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
    )
