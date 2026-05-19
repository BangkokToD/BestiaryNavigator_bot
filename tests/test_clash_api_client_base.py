"""Тесты базового Clash API HTTP client."""

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.integrations.clash import ClashApiClient

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
async def test_clash_api_client_raises_for_error_status() -> None:
    """Проверяет базовое поведение 4xx/5xx до typed exception mapping."""
    client = ClashApiClient(
        base_url="https://api.clashofclans.com/v1",
        api_token="secret-clash-token",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"reason": "forbidden"})),
    )

    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client._request("GET", "clans/%232ABC")
    finally:
        await client.aclose()


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
