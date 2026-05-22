"""Route-level тесты admin clan routes."""

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
from app.services import ClanNotFoundError
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


class FakeAdminClanService:
    """Fake service для route-level тестов admin clan routes."""

    def __init__(
        self,
        *,
        clans: list[Clan] | None = None,
        verified_clan: ClashClan | None = None,
        error: Exception | None = None,
    ) -> None:
        """Инициализирует fake service.

        Args:
            clans: Локальные кланы для list route.
            verified_clan: DTO проверенного клана.
            error: Ошибка, которую fake должен выбросить.
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
        """Проверяет клан через fake Clash API."""
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

        return _make_clan(
            name="Fresh name",
            level=20,
            badge_url="https://example.test/fresh-badge.png",
            sync_status="ok",
        )

    async def update_clan_type(self, *, clan_tag: str, clan_type: ClanType | str) -> Clan:
        """Меняет тип fake-клана."""
        normalized_type = clan_type.value if isinstance(clan_type, ClanType) else clan_type
        self.calls.append(("update_clan_type", clan_tag, normalized_type))
        self._raise_if_needed()

        return _make_clan(clan_type=normalized_type)

    async def deactivate_clan(self, *, clan_tag: str) -> Clan:
        """Деактивирует fake-клан без физического удаления."""
        self.calls.append(("deactivate_clan", clan_tag))
        self._raise_if_needed()

        return _make_clan(is_active=False)

    def _raise_if_needed(self) -> None:
        """Выбрасывает настроенную ошибку, если она есть."""
        if self.error is not None:
            raise self.error


def make_settings() -> Settings:
    """Создаёт settings для admin route-тестов.

    Returns:
        Провалидированный settings.
    """
    return Settings(**VALID_SETTINGS)


def test_admin_clan_routes_reject_anonymous_user() -> None:
    """Проверяет, что anonymous user не может читать admin clan routes."""
    service = FakeAdminClanService()

    with _override_admin_clan_service(service), TestClient(app) as client:
        response = client.get("/admin/clans")

    assert response.status_code == 403
    assert service.calls == []


def test_admin_clan_routes_list_clans_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет JSON route списка кланов для админа."""
    clan = _make_clan()
    service = FakeAdminClanService(clans=[clan])

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.get("/admin/clans")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "clans": [
            {
                "id": 1,
                "tag": "#2ABC",
                "name": "Bestiary",
                "type": "main",
                "level": 17,
                "badge_url": "https://example.test/badge.png",
                "is_active": True,
                "last_sync_at": "2026-05-22T12:00:00Z",
                "sync_status": "ok",
            }
        ],
    }
    assert service.calls == [("list_clans",)]


def test_admin_check_clan_route_returns_verified_clan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет route проверки клана через Clash API."""
    verified_clan = _make_verified_clan()
    service = FakeAdminClanService(verified_clan=verified_clan)

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/check", json={"clan_tag": "2abc"})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "clan": {
            "tag": "#2ABC",
            "name": "Bestiary",
            "level": 17,
            "badge_url": "https://example.test/badge.png",
            "members_count": 44,
        },
    }
    assert service.calls == [("check_clan", "2abc")]


def test_admin_add_clan_route_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет route добавления клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/clans",
            json={"clan_tag": "2abc", "clan_type": "academy"},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["clan"]["tag"] == "#2ABC"
    assert response.json()["clan"]["type"] == "academy"
    assert service.calls == [("add_clan", "2abc", "academy")]


def test_admin_update_clan_type_route_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет route смены типа клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post(
            "/admin/clans/type",
            json={"clan_tag": "#2ABC", "clan_type": "freezer"},
        )

    assert response.status_code == 200
    assert response.json()["clan"]["type"] == "freezer"
    assert service.calls == [("update_clan_type", "#2ABC", "freezer")]


def test_admin_refresh_clan_route_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет route ручного обновления клана."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/refresh", json={"clan_tag": "#2ABC"})

    assert response.status_code == 200
    assert response.json()["clan"]["name"] == "Fresh name"
    assert response.json()["clan"]["level"] == 20
    assert service.calls == [("refresh_clan", "#2ABC")]


def test_admin_deactivate_clan_route_is_soft_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет, что delete/deactivate route делает soft deactivate."""
    service = FakeAdminClanService()

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/deactivate", json={"clan_tag": "#2ABC"})

    assert response.status_code == 200
    assert response.json()["clan"]["is_active"] is False
    assert service.calls == [("deactivate_clan", "#2ABC")]


def test_admin_clan_route_maps_missing_local_clan_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет понятную 404-ошибку для отсутствующего локального клана."""
    service = FakeAdminClanService(error=ClanNotFoundError("Клан #2ABC не найден."))

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/refresh", json={"clan_tag": "#2ABC"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "clan_not_found",
        "message": "Клан #2ABC не найден.",
    }


def test_admin_clan_route_maps_clash_not_found_to_clear_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверяет понятную ошибку, если Clash API не нашёл клан."""
    service = FakeAdminClanService(
        error=ClashNotFoundError(
            "Clash API returned HTTP 404 for GET clans/%232ABC.",
            endpoint="clans/%232ABC",
            method="GET",
            status_code=404,
            response_snippet=None,
        )
    )

    with _admin_client(monkeypatch=monkeypatch, service=service) as client:
        response = client.post("/admin/clans/check", json={"clan_tag": "#2ABC"})

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "clash_clan_not_found",
        "message": "Клан не найден в Clash of Clans API.",
        "clash_status_code": 404,
    }


@contextmanager
def _admin_client(
    *,
    monkeypatch: pytest.MonkeyPatch,
    service: FakeAdminClanService,
) -> Iterator[TestClient]:
    """Создаёт TestClient с валидной admin-cookie.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        service: Fake service admin clan routes.

    Yields:
        TestClient с admin-cookie.
    """
    settings = make_settings()
    cookie_value = build_admin_cookie_value(
        telegram_id=settings.telegram_admin_id,
        secret=settings.web_session_secret,
    )
    monkeypatch.setattr("app.web.context.get_settings", lambda: settings)

    with _override_admin_clan_service(service), TestClient(app) as client:
        client.cookies.set(settings.web_admin_cookie_name, cookie_value)
        yield client


@contextmanager
def _override_admin_clan_service(service: FakeAdminClanService) -> Iterator[None]:
    """Подменяет dependency сервиса кланов.

    Args:
        service: Fake service admin clan routes.

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
    """Создаёт модель клана для route-тестов.

    Args:
        clan_type: Тип клана.
        name: Название клана.
        level: Уровень клана.
        badge_url: URL badge.
        is_active: Признак активного мониторинга.
        sync_status: Статус синхронизации.

    Returns:
        Модель клана.
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
