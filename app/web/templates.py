"""Jinja2 и static paths для web UI."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
STATIC_DIR = PROJECT_ROOT / "frontend" / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
"""Единый Jinja2Templates instance для SSR-страниц."""
