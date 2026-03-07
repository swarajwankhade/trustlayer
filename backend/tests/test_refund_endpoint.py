import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.exposure.store import get_exposure_store
from app.main import app
from app.models import DecisionEvent, Policy
from tests.action_test_utils import FakeExposureStore, insert_active_policy

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
def fake_exposure_store() -> FakeExposureStore:
    return FakeExposureStore()


@pytest.fixture
def authorized_client(db_session: Session, fake_exposure_store: FakeExposureStore) -> Iterator[TestClient]:
    def override_db_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_exposure_store] = lambda: fake_exposure_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_first_refund_call_persists_one_decision_event(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=2)

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
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
    assert fake_exposure_store.daily_total_amounts["refund"] == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_amounts[("refund", "user-1")] == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts[("refund", "user-1")] == 1
    assert fake_exposure_store.financial_total_amount == Decimal("10.00")

    events = db_session.scalars(select(DecisionEvent).where(DecisionEvent.request_id == request_id)).all()
    assert len(events) == 1
    assert events[0].action_type == "refund"
    assert events[0].policy_id == policy_id
    assert events[0].policy_version == 2
    assert events[0].exposure_snapshot_json == {
        "daily_total_amount": "0.00",
        "per_user_daily_count": 0,
        "per_user_daily_amount": "0.00",
        "financial_total_amount_cents": 0,
    }

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_refund_idempotent_request_replays_response(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=1)
    payload = {
        "request_id": request_id,
        "user_id": "user-1",
        "ticket_id": "ticket-1",
        "refund_amount_cents": 1000,
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
    assert fake_exposure_store.daily_total_amounts["refund"] == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts[("refund", "user-1")] == 1
    assert fake_exposure_store.financial_total_amount == Decimal("10.00")

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
