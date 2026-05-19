"""Web UI слой приложения."""

from app.web.routes import router as web_router
from app.web.templates import STATIC_DIR, TEMPLATES_DIR, templates

__all__ = [
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "templates",
    "web_router",
]
