"""Базовые web routes приложения."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.web.context import build_template_context
from app.web.templates import templates

router = APIRouter(include_in_schema=False)


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
