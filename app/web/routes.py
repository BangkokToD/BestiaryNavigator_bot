"""Базовые web routes приложения."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.web.admin_clans import router as admin_clans_router
from app.web.admin_notification_routes import router as admin_notification_routes_router
from app.web.auth import router as auth_router
from app.web.context import build_template_context
from app.web.templates import templates

router = APIRouter(include_in_schema=False)
router.include_router(auth_router)
router.include_router(admin_clans_router)
router.include_router(admin_notification_routes_router)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    """Отдаёт пустой dashboard skeleton.

    Args:
        request: FastAPI request, необходимый Jinja2 для `url_for`.

    Returns:
        HTML-страница dashboard.
    """
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        build_template_context(
            request,
            page_title="Dashboard",
        ),
    )
