"""Тесты раннего web-skeleton."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
MACROS_DIR = TEMPLATES_DIR / "shared" / "macros"


def test_dashboard_returns_html_page() -> None:
    """Проверяет, что `/` отдаёт HTML dashboard skeleton."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BestiaryNavigator_bot" in response.text
    assert "Dashboard" in response.text
    assert "/static/css/app.css" in response.text
    assert 'class="bn-layout"' in response.text
    assert 'data-bn-admin="false"' in response.text
    assert 'data-bn-readonly="true"' in response.text
    assert 'data-admin-only="true"' not in response.text
    assert "/admin/settings/clans" not in response.text
    assert 'class="bn-sidebar"' in response.text
    assert 'class="bn-topbar"' in response.text
    assert 'class="bn-page"' in response.text
    assert 'class="bn-section-header"' in response.text
    assert 'class="bn-card-body"' in response.text
    assert 'class="bn-section bn-dashboard"' in response.text
    assert 'class="bn-card bn-empty-state"' in response.text
    assert "bn-badge" in response.text
    assert "bn-button" in response.text


def test_static_css_is_served() -> None:
    """Проверяет раздачу CSS через static mount."""
    with TestClient(app) as client:
        response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert '@import url("./tokens.css");' in response.text
    assert '@import url("./base.css");' in response.text
    assert '@import url("./components.css");' in response.text
    assert '@import url("./utilities.css");' in response.text
    assert '@import url("./pages/dashboard.css");' in response.text
    assert '@import url("./pages/admin_clans.css");' in response.text


def test_css_layers_are_served() -> None:
    """Проверяет раздачу CSS-слоёв через static mount."""
    with TestClient(app) as client:
        tokens_response = client.get("/static/css/tokens.css")
        base_response = client.get("/static/css/base.css")
        components_response = client.get("/static/css/components.css")
        utilities_response = client.get("/static/css/utilities.css")
        dashboard_response = client.get("/static/css/pages/dashboard.css")
        admin_clans_response = client.get("/static/css/pages/admin_clans.css")

    assert tokens_response.status_code == 200
    assert base_response.status_code == 200
    assert components_response.status_code == 200
    assert utilities_response.status_code == 200
    assert dashboard_response.status_code == 200
    assert admin_clans_response.status_code == 200

    assert "--bn-bg-page" in tokens_response.text
    assert "color-scheme: dark" in tokens_response.text
    assert "body" in base_response.text
    assert ".bn-card" in components_response.text
    assert ".bn-layout" in components_response.text
    assert ".bn-sidebar" in components_response.text
    assert ".bn-topbar" in components_response.text
    assert ".bn-page" in components_response.text
    assert ".bn-section" in components_response.text
    assert ".bn-card-header" in components_response.text
    assert ".bn-card-body" in components_response.text
    assert ".bn-card-footer" in components_response.text
    assert ".bn-badge" in components_response.text
    assert ".bn-button" in components_response.text
    assert ".bn-empty-state" in components_response.text
    assert "minmax(0, 1fr)" in components_response.text
    assert ".bn-sr-only" in utilities_response.text
    assert ".bn-dashboard" in dashboard_response.text
    assert ".bn-admin-clans" in admin_clans_response.text
    assert "minmax(16rem, 1fr)" in admin_clans_response.text


def test_templates_do_not_use_inline_styles() -> None:
    """Проверяет, что ранние шаблоны не используют inline CSS."""
    with TestClient(app) as client:
        response = client.get("/")

    assert 'style="' not in response.text


def test_shared_macro_files_exist() -> None:
    """Проверяет наличие всех shared Jinja macro-файлов."""
    expected_macro_files = {
        "assets.html",
        "badges.html",
        "buttons.html",
        "cards.html",
        "empty_states.html",
        "frames.html",
        "progress.html",
        "status.html",
        "tables.html",
    }

    actual_macro_files = {path.name for path in MACROS_DIR.iterdir() if path.is_file()}

    assert expected_macro_files == actual_macro_files


def test_base_template_imports_shared_macros() -> None:
    """Проверяет, что base layout импортирует shared macros."""
    base_template = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

    for macro_name in (
        "assets",
        "badges",
        "buttons",
        "cards",
        "empty_states",
        "frames",
        "progress",
        "status",
        "tables",
    ):
        assert f"as {macro_name}" in base_template


def test_dashboard_uses_macro_rendered_components() -> None:
    """Проверяет, что macro-rendered badge/button/card видны в HTML."""
    with TestClient(app) as client:
        response = client.get("/")

    assert 'class="bn-badge bn-badge--info"' in response.text
    assert 'class="bn-card bn-empty-state"' in response.text
    assert 'class="bn-button bn-button--primary"' in response.text
    assert 'class="bn-button bn-button--ghost"' in response.text
