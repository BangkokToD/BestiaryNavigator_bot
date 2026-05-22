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
