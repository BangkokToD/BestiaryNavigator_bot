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
