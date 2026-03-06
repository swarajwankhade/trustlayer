from fastapi.testclient import TestClient

from app.db.session import get_db_session
from app.exposure.store import get_exposure_store
from app.main import app


def test_credit_requires_api_key(client: TestClient) -> None:
    def override_db_session():
        yield None

    def override_exposure_store():
        return None

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_exposure_store] = override_exposure_store
    try:
        response = client.post(
            "/v1/actions/credit",
            json={
                "request_id": "req-credit-auth-1",
                "user_id": "user-1",
                "ticket_id": "ticket-1",
                "credit_amount_cents": 1000,
                "currency": "USD",
                "credit_type": "courtesy",
                "model_version": "gpt-test",
                "metadata": {},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
