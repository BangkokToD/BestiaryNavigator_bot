"""Тесты runtime entrypoints."""

from fastapi.testclient import TestClient

from app.api.main import app


def test_health_endpoint_returns_ok() -> None:
    """Проверяет health endpoint backend-приложения."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "backend",
    }