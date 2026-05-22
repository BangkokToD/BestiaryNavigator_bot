"""Web UI слой приложения."""

from app.web.context import (
    CurrentWebUser,
    WebRequestContext,
    build_template_context,
    build_web_request_context,
    get_current_web_user,
    get_web_request_context,
    is_admin_request,
    require_admin_context,
    require_admin_request,
    set_web_request_context,
    web_template_context_processor,
)
from app.web.routes import router as web_router
from app.web.templates import STATIC_DIR, TEMPLATES_DIR, templates

__all__ = [
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "CurrentWebUser",
    "WebRequestContext",
    "build_template_context",
    "build_web_request_context",
    "get_current_web_user",
    "get_web_request_context",
    "is_admin_request",
    "require_admin_context",
    "require_admin_request",
    "set_web_request_context",
    "templates",
    "web_router",
    "web_template_context_processor",
]
