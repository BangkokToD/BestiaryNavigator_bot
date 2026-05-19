"""Тесты раннего web-skeleton."""

from fastapi.testclient import TestClient

from app.api.main import app


def test_dashboard_returns_html_page() -> None:
    """Проверяет, что `/` отдаёт HTML dashboard skeleton."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "BestiaryNavigator_bot" in response.text
    assert "Dashboard" in response.text
    assert "/static/css/app.css" in response.text


def test_static_css_is_served() -> None:
    """Проверяет раздачу CSS через static mount."""
    with TestClient(app) as client:
        response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert ".bn-card" in response.text
    assert "--bn-bg-page" in response.text
