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
