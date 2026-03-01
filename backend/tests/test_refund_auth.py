from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.main import app


def test_refund_requires_api_key(client: TestClient) -> None:
    def override_db_session():
        yield None

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        response = client.post(
            "/v1/actions/refund",
            json={
                "request_id": "req-auth-1",
                "user_id": "user-1",
                "ticket_id": "ticket-1",
                "refund_amount": "10.00",
                "currency": "USD",
                "model_version": "gpt-test",
                "metadata": {},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


def test_refund_rejects_invalid_api_key(client: TestClient) -> None:
    def override_db_session():
        yield None

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        response = client.post(
            "/v1/actions/refund",
            headers={"X-API-Key": "wrong-key"},
            json={
                "request_id": "req-auth-2",
                "user_id": "user-1",
                "ticket_id": "ticket-1",
                "refund_amount": "10.00",
                "currency": "USD",
                "model_version": "gpt-test",
                "metadata": {},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
