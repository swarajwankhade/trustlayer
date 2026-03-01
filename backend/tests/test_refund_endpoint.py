import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.main import app
from app.models import DecisionEvent

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL is not set"),
]


@pytest.fixture
def db_session() -> Iterator[Session]:
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session
        session.rollback()


@pytest.fixture
def authorized_client(db_session: Session) -> Iterator[TestClient]:
    def override_db_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[get_db_session] = override_db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_first_refund_call_persists_one_decision_event(authorized_client: TestClient, db_session: Session) -> None:
    request_id = f"req-{uuid.uuid4()}"

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount": "10.00",
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": request_id,
        "decision": "ALLOW",
        "reason_codes": ["PLACEHOLDER_ALLOW"],
        "policy_version": None,
        "model_version": "gpt-test",
    }

    events = db_session.scalars(select(DecisionEvent).where(DecisionEvent.request_id == request_id)).all()
    assert len(events) == 1
    assert events[0].action_type == "refund"
    assert events[0].exposure_snapshot_json == {}

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.commit()


def test_second_refund_call_with_same_request_id_is_idempotent(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    payload = {
        "request_id": request_id,
        "user_id": "user-1",
        "ticket_id": "ticket-1",
        "refund_amount": "10.00",
        "currency": "USD",
        "model_version": "gpt-test",
        "metadata": {"channel": "chat"},
    }

    first_response = authorized_client.post("/v1/actions/refund", json=payload)
    second_response = authorized_client.post("/v1/actions/refund", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()

    event_count = db_session.scalar(
        select(func.count()).select_from(DecisionEvent).where(DecisionEvent.request_id == request_id)
    )
    assert event_count == 1

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.commit()
