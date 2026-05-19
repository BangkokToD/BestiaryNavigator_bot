"""Тесты базового Clash API HTTP client."""

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.integrations.clash import (
    ClashApiClient,
    ClashApiError,
    ClashForbiddenError,
    ClashNotFoundError,
    ClashRateLimitError,
    ClashServerError,
    ClashTimeoutError,
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


@pytest.mark.asyncio
async def test_clash_api_client_sends_request_with_base_url_and_bearer_token() -> None:
    """Проверяет base URL, private request method и Bearer token."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает тестовый JSON response."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"ok": True})

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token=SecretStr("secret-clash-token"),
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        response = await client._request(
            "get",
            "/clans/%232ABC",
            params={"limit": 1},
        )
    finally:
        await client.aclose()

    assert response.json() == {"ok": True}
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/clans/%232ABC?limit=1"
    assert captured_request.method == "GET"
    assert captured_request.headers["accept"] == "application/json"
    assert captured_request.headers["authorization"] == "Bearer secret-clash-token"


@pytest.mark.asyncio
async def test_clash_api_client_from_settings_uses_runtime_configuration() -> None:
    """Проверяет создание клиента из settings без прямого чтения env."""
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        """Сохраняет request и возвращает успешный response."""
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"ok": True})

    settings = Settings(**VALID_SETTINGS)

    async with ClashApiClient.from_settings(
        settings,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = await client._request("GET", "players/%232ABC")

    assert response.json() == {"ok": True}
    assert captured_request is not None
    assert str(captured_request.url) == "https://api.clashofclans.com/v1/players/%232ABC"
    assert captured_request.headers["authorization"] == "Bearer test-clash-token-123"
    assert client.is_closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error_class"),
    [
        (400, ClashApiError),
        (403, ClashForbiddenError),
        (404, ClashNotFoundError),
        (429, ClashRateLimitError),
        (500, ClashServerError),
        (503, ClashServerError),
    ],
)
async def test_clash_api_client_maps_error_status_to_typed_exception(
    status_code: int,
    expected_error_class: type[ClashApiError],
) -> None:
    """Проверяет mapping HTTP-статусов в typed Clash API exceptions."""
    response_body = "x" * 3000
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(status_code, text=response_body)),
    )

    try:
        with pytest.raises(expected_error_class) as exc_info:
            await client._request("GET", "clans/%232ABC")
    finally:
        await client.aclose()

    error = exc_info.value
    assert type(error) is expected_error_class
    assert error.endpoint == "clans/%232ABC"
    assert error.method == "GET"
    assert error.status_code == status_code
    assert error.response_snippet == response_body[:2048]
    assert len(error.response_snippet) == 2048
    assert "secret-clash-token" not in str(error)


@pytest.mark.asyncio
async def test_clash_api_client_maps_timeout_to_typed_exception() -> None:
    """Проверяет mapping timeout в ClashTimeoutError."""

    async def handler(request: httpx.Request) -> httpx.Response:
        """Имитирует timeout transport-уровня."""
        raise httpx.TimeoutException("Request timed out.", request=request)

    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(ClashTimeoutError) as exc_info:
            await client._request("GET", "clans/%232ABC")
    finally:
        await client.aclose()

    error = exc_info.value
    assert error.endpoint == "clans/%232ABC"
    assert error.method == "GET"
    assert error.status_code is None
    assert error.response_snippet is None


@pytest.mark.asyncio
async def test_clash_api_client_closes_connections() -> None:
    """Проверяет закрытие внутреннего HTTP client."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )

    assert client.is_closed is False

    await client.aclose()

    assert client.is_closed is True


def test_clash_api_client_repr_does_not_expose_token() -> None:
    """Проверяет, что token не попадает в стандартное представление объекта."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True})),
    )

    assert "secret-clash-token" not in repr(client)
