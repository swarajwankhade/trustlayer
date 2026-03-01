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
from app.models import DecisionEvent, Policy

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
    policy_id = uuid.uuid4()

    db_session.add(
        Policy(
            id=policy_id,
            name=f"active-policy-{policy_id}",
            version=2,
            status="ACTIVE",
            rules_json={
                "per_action_max_amount": "100.00",
                "daily_total_cap_amount": "500.00",
                "per_user_daily_count_cap": 5,
                "per_user_daily_amount_cap": "200.00",
                "near_cap_escalation_ratio": "0.9",
            },
            created_by="pytest",
        )
    )
    db_session.commit()

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
        "reason_codes": ["WITHIN_POLICY"],
        "policy_version": 2,
        "model_version": "gpt-test",
    }

    events = db_session.scalars(select(DecisionEvent).where(DecisionEvent.request_id == request_id)).all()
    assert len(events) == 1
    assert events[0].action_type == "refund"
    assert events[0].policy_id == policy_id
    assert events[0].policy_version == 2
    assert events[0].exposure_snapshot_json == {}

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_second_refund_call_with_same_request_id_is_idempotent(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = uuid.uuid4()
    payload = {
        "request_id": request_id,
        "user_id": "user-1",
        "ticket_id": "ticket-1",
        "refund_amount": "10.00",
        "currency": "USD",
        "model_version": "gpt-test",
        "metadata": {"channel": "chat"},
    }

    db_session.add(
        Policy(
            id=policy_id,
            name=f"active-policy-{policy_id}",
            version=1,
            status="ACTIVE",
            rules_json={
                "per_action_max_amount": "100.00",
                "daily_total_cap_amount": "500.00",
                "per_user_daily_count_cap": 5,
                "per_user_daily_amount_cap": "200.00",
                "near_cap_escalation_ratio": "0.9",
            },
            created_by="pytest",
        )
    )
    db_session.commit()

    first_response = authorized_client.post("/v1/actions/refund", json=payload)
    second_response = authorized_client.post("/v1/actions/refund", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()
    assert first_response.json()["reason_codes"] == ["WITHIN_POLICY"]
    assert first_response.json()["policy_version"] == 1

    event_count = db_session.scalar(
        select(func.count()).select_from(DecisionEvent).where(DecisionEvent.request_id == request_id)
    )
    assert event_count == 1

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
