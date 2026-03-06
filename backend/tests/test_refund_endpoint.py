import os
import uuid
from collections.abc import Iterator
from datetime import date as date_type
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.exposure.store import ExposureStoreUnavailableError, get_exposure_store
from app.main import app
from app.models import DecisionEvent, Policy
from app.policies.schemas import ExposureContext

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL is not set"),
]


class FakeExposureStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.daily_total_amount = Decimal("0.00")
        self.per_user_daily_amounts: dict[str, Decimal] = {}
        self.per_user_daily_counts: dict[str, int] = {}

    def get_exposure(self, user_id: str, date: date_type) -> ExposureContext:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        return ExposureContext(
            daily_total_amount=self.daily_total_amount,
            per_user_daily_count=self.per_user_daily_counts.get(user_id, 0),
            per_user_daily_amount=self.per_user_daily_amounts.get(user_id, Decimal("0.00")),
        )

    def apply_allow(self, user_id: str, amount: Decimal, date: date_type) -> ExposureContext:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        self.daily_total_amount += amount
        self.per_user_daily_amounts[user_id] = self.per_user_daily_amounts.get(user_id, Decimal("0.00")) + amount
        self.per_user_daily_counts[user_id] = self.per_user_daily_counts.get(user_id, 0) + 1
        return self.get_exposure(user_id, date)


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
    assert fake_exposure_store.daily_total_amount == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_amounts["user-1"] == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts["user-1"] == 1

    events = db_session.scalars(select(DecisionEvent).where(DecisionEvent.request_id == request_id)).all()
    assert len(events) == 1
    assert events[0].action_type == "refund"
    assert events[0].policy_id == policy_id
    assert events[0].policy_version == 2
    assert events[0].exposure_snapshot_json == {
        "daily_total_amount": "0.00",
        "per_user_daily_count": 0,
        "per_user_daily_amount": "0.00",
    }

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_second_request_uses_updated_exposure_and_escalates(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    policy_id = uuid.uuid4()

    db_session.add(
        Policy(
            id=policy_id,
            name=f"active-policy-{policy_id}",
            version=1,
            status="ACTIVE",
            rules_json={
                "per_action_max_amount": "100.00",
                "daily_total_cap_amount": "20.00",
                "per_user_daily_count_cap": 5,
                "per_user_daily_amount_cap": "50.00",
                "near_cap_escalation_ratio": "0.9",
            },
            created_by="pytest",
        )
    )
    db_session.commit()

    first_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount": "10.00",
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )
    second_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount": "8.00",
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {"channel": "chat"},
        },
    )

    assert first_response.status_code == 200
    assert first_response.json()["decision"] == "ALLOW"
    assert second_response.status_code == 200
    assert second_response.json()["decision"] == "ESCALATE"
    assert second_response.json()["reason_codes"] == ["NEAR_DAILY_TOTAL_CAP"]
    assert fake_exposure_store.daily_total_amount == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts["user-1"] == 1

    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_idempotent_request_does_not_create_second_event(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
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

    event_count = db_session.scalar(
        select(func.count()).select_from(DecisionEvent).where(DecisionEvent.request_id == request_id)
    )
    assert event_count == 1
    assert fake_exposure_store.daily_total_amount == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts["user-1"] == 1

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_redis_unavailable_defaults_to_escalate(db_session: Session) -> None:
    policy_id = uuid.uuid4()
    failing_store = FakeExposureStore(fail=True)

    db_session.add(
        Policy(
            id=policy_id,
            name=f"active-policy-{policy_id}",
            version=3,
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

    def override_db_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_exposure_store] = lambda: failing_store
    try:
        response = TestClient(app).post(
            "/v1/actions/refund",
            json={
                "request_id": f"req-{uuid.uuid4()}",
                "user_id": "user-1",
                "ticket_id": "ticket-1",
                "refund_amount": "10.00",
                "currency": "USD",
                "model_version": "gpt-test",
                "metadata": {"channel": "chat"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["decision"] == "ESCALATE"
    assert response.json()["reason_codes"] == ["REDIS_UNAVAILABLE"]

    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
