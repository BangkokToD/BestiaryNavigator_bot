"""Тестовые helpers для Dashboard feature page."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from app.api.main import app
from app.services.web_read_models import DashboardSummaryView, DashboardView
from app.web.feature_pages import get_web_feature_page_service


class DashboardServiceStub(Protocol):
    """Минимальный contract fake Dashboard service."""

    async def get_dashboard(self) -> DashboardView:
        """Возвращает Dashboard view model.

        Returns:
            View model Dashboard.
        """


class EmptyDashboardService:
    """Fake Dashboard service с пустым Dashboard."""

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


@contextmanager
def override_dashboard_service(service: DashboardServiceStub | None = None) -> Iterator[None]:
    """Подменяет dependency Dashboard service.

    Args:
        service: Fake service. Если не передан, используется пустой Dashboard.

    Yields:
        Управление тесту.
    """
    dashboard_service = service or EmptyDashboardService()
    previous_override = app.dependency_overrides.get(get_web_feature_page_service)
    app.dependency_overrides[get_web_feature_page_service] = lambda: dashboard_service

    try:
        yield
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_web_feature_page_service, None)
        else:
            app.dependency_overrides[get_web_feature_page_service] = previous_override
