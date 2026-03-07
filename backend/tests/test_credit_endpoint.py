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


def test_credit_first_request_persists_one_decision_event(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=4)

    response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": request_id,
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 1500,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert response.json()["policy_version"] == 4
    assert fake_exposure_store.daily_total_amounts["credit_adjustment"] == Decimal("15.00")
    assert fake_exposure_store.per_user_daily_counts[("credit_adjustment", "user-1")] == 1
    assert fake_exposure_store.financial_total_amount == Decimal("15.00")

    events = db_session.scalars(select(DecisionEvent).where(DecisionEvent.request_id == request_id)).all()
    assert len(events) == 1
    assert events[0].action_type == "credit_adjustment"
    assert events[0].policy_id == policy_id
    assert events[0].policy_version == 4
    assert events[0].decision == "ALLOW"

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_credit_duplicate_request_id_replays_response(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=3)
    payload = {
        "request_id": request_id,
        "user_id": "user-1",
        "ticket_id": "ticket-1",
        "credit_amount_cents": 1000,
        "currency": "USD",
        "credit_type": "courtesy",
        "model_version": "gpt-test",
        "metadata": {"channel": "chat"},
    }

    first_response = authorized_client.post("/v1/actions/credit", json=payload)
    second_response = authorized_client.post("/v1/actions/credit", json=payload)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json() == first_response.json()

    event_count = db_session.scalar(
        select(func.count()).select_from(DecisionEvent).where(DecisionEvent.request_id == request_id)
    )
    assert event_count == 1
    assert fake_exposure_store.daily_total_amounts["credit_adjustment"] == Decimal("10.00")
    assert fake_exposure_store.financial_total_amount == Decimal("10.00")

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_credit_policy_allows_and_blocks_by_amount(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    policy_id = insert_active_policy(db_session, version=2, per_action_max_amount=2_000)

    allow_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 1000,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )
    block_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 2500,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )

    assert allow_response.status_code == 200
    assert allow_response.json()["decision"] == "ALLOW"
    assert block_response.status_code == 200
    assert block_response.json()["decision"] == "BLOCK"
    assert "PER_ACTION_MAX_AMOUNT_EXCEEDED" in block_response.json()["reason_codes"]

    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_refund_and_credit_stack_against_combined_daily_cap(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    policy_id = insert_active_policy(
        db_session,
        version=5,
        daily_total_cap_amount=10_000,
        per_action_max_amount=20_000,
        per_user_daily_amount_cap=20_000,
    )

    refund_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 6000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    credit_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 5000,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert refund_response.status_code == 200
    assert refund_response.json()["decision"] == "ALLOW"
    assert credit_response.status_code == 200
    assert credit_response.json()["decision"] == "BLOCK"
    assert "DAILY_TOTAL_CAP_EXCEEDED" in credit_response.json()["reason_codes"]
    assert fake_exposure_store.financial_total_amount == Decimal("60.00")

    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
