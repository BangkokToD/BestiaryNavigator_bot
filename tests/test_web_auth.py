"""Тесты Telegram Login и admin-cookie web-слоя."""

import hmac
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from fastapi.testclient import TestClient
from tests.web_dashboard_helpers import override_dashboard_service

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

    with override_dashboard_service(), TestClient(app) as client:
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

    with override_dashboard_service(), TestClient(app) as client:
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
