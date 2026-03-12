from app.api import routes
from fastapi.testclient import TestClient


def test_healthcheck_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_ready_when_dependencies_healthy(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(routes, "_postgres_ready", lambda: True)
    monkeypatch.setattr(routes, "_redis_ready", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "postgres": "ok", "redis": "ok"}


def test_readiness_returns_503_when_postgres_unhealthy(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(routes, "_postgres_ready", lambda: False)
    monkeypatch.setattr(routes, "_redis_ready", lambda: True)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "postgres": "error", "redis": "ok"}


def test_readiness_returns_503_when_redis_unhealthy(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(routes, "_postgres_ready", lambda: True)
    monkeypatch.setattr(routes, "_redis_ready", lambda: False)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "postgres": "ok", "redis": "error"}
