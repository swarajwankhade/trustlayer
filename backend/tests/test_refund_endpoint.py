import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.actions import service as action_service
from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.exposure.store import get_exposure_store
from app.main import app
from app.models import DecisionEvent, KillSwitch, Policy
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


def _set_kill_switch(
    db_session: Session,
    *,
    enabled: bool = False,
    observe_only: bool = False,
    reason: str = "pytest",
    updated_by: str = "pytest",
) -> None:
    kill_switch = db_session.get(KillSwitch, 1)
    if kill_switch is None:
        kill_switch = KillSwitch(
            id=1,
            enabled=enabled,
            observe_only=observe_only,
            reason=reason,
            updated_by=updated_by,
        )
    else:
        kill_switch.enabled = enabled
        kill_switch.observe_only = observe_only
        kill_switch.reason = reason
        kill_switch.updated_by = updated_by
    db_session.add(kill_switch)
    db_session.commit()


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
    assert events[0].policy_type == "refund_credit_v1"
    assert events[0].runtime_mode == "enforce"
    assert events[0].event_schema_version == "1"
    assert events[0].policy_id == policy_id
    assert events[0].policy_version == 2
    assert events[0].normalized_input_json is not None
    assert events[0].normalized_input_json["action_type"] == "refund"
    assert events[0].normalized_input_json["amount_cents"] == 1000
    assert events[0].normalized_input_json["user_id"] == "user-1"
    assert events[0].normalized_input_hash is not None
    assert len(events[0].normalized_input_hash) == 64
    assert events[0].exposure_snapshot_json == {
        "daily_total_amount": "0.00",
        "per_user_daily_count": 0,
        "per_user_daily_amount": "0.00",
        "financial_total_amount_cents": 0,
    }

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_live_refund_path_uses_evaluator_registry(
    authorized_client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=21)
    real_get_evaluator = action_service.get_evaluator
    requested_policy_types: list[str] = []

    def _spy_get_evaluator(policy_type: str):
        requested_policy_types.append(policy_type)
        return real_get_evaluator(policy_type)

    monkeypatch.setattr(action_service, "get_evaluator", _spy_get_evaluator)

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-registry",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert requested_policy_types == ["refund_credit_v1"]

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_normalized_input_hash_is_stable_for_equivalent_refund_inputs(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    first_request_id = f"req-{uuid.uuid4()}"
    second_request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=23)

    payload = {
        "user_id": "user-hash-stability",
        "ticket_id": "ticket-1",
        "refund_amount_cents": 1000,
        "currency": "USD",
        "model_version": "gpt-test",
        "metadata": {},
    }

    first_response = authorized_client.post("/v1/actions/refund", json={"request_id": first_request_id, **payload})
    second_response = authorized_client.post("/v1/actions/refund", json={"request_id": second_request_id, **payload})
    assert first_response.status_code == 200
    assert second_response.status_code == 200

    first_event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == first_request_id))
    second_event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == second_request_id))
    assert first_event is not None
    assert second_event is not None
    assert first_event.normalized_input_json == second_event.normalized_input_json
    assert first_event.normalized_input_hash == second_event.normalized_input_hash
    assert first_event.normalized_input_hash is not None

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id.in_([first_request_id, second_request_id])))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_refund_persists_policy_type_on_decision_event(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    request_id = f"req-{uuid.uuid4()}"
    policy_id = insert_active_policy(db_session, version=22)

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-legacy-policy-type",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None
    assert event.policy_type == "refund_credit_v1"
    assert event.runtime_mode == "enforce"

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


def test_observe_only_would_block_returns_allow_and_does_not_increment_exposure(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    _set_kill_switch(db_session, enabled=False, observe_only=True, reason="observe", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=9, per_action_max_amount=1_000)
    request_id = f"req-{uuid.uuid4()}"

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-observe-block",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1500,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert "OBSERVE_ONLY" in response.json()["reason_codes"]
    assert "WOULD_BLOCK" in response.json()["reason_codes"]
    assert "refund" not in fake_exposure_store.daily_total_amounts
    assert fake_exposure_store.financial_total_amount == Decimal("0.00")

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None
    assert event.decision == "ALLOW"
    assert event.policy_type == "refund_credit_v1"
    assert event.runtime_mode == "observe_only"
    assert event.would_decision == "BLOCK"
    assert event.would_reason_codes is not None
    assert "PER_ACTION_MAX_AMOUNT_EXCEEDED" in event.would_reason_codes

    _set_kill_switch(db_session, enabled=False, observe_only=False, reason="reset", updated_by="pytest")
    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_observe_only_would_escalate_returns_allow_and_does_not_increment_exposure(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
) -> None:
    _set_kill_switch(db_session, enabled=False, observe_only=True, reason="observe", updated_by="pytest")
    policy_id = insert_active_policy(
        db_session,
        version=10,
        per_action_max_amount=10_000,
        daily_total_cap_amount=10_000,
    )
    fake_exposure_store.financial_total_amount = Decimal("85.00")
    request_id = f"req-{uuid.uuid4()}"

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-observe-escalate",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert "OBSERVE_ONLY" in response.json()["reason_codes"]
    assert "WOULD_ESCALATE" in response.json()["reason_codes"]
    assert fake_exposure_store.financial_total_amount == Decimal("85.00")
    assert ("refund", "user-observe-escalate") not in fake_exposure_store.per_user_daily_counts

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None
    assert event.would_decision == "ESCALATE"
    assert event.would_reason_codes is not None
    assert "NEAR_DAILY_TOTAL_CAP" in event.would_reason_codes

    _set_kill_switch(db_session, enabled=False, observe_only=False, reason="reset", updated_by="pytest")
    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_normal_mode_refund_behavior_remains_unchanged(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    _set_kill_switch(db_session, enabled=False, observe_only=False, reason="normal", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=11, per_action_max_amount=1_000)
    request_id = f"req-{uuid.uuid4()}"

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-normal-block",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1500,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"
    assert "PER_ACTION_MAX_AMOUNT_EXCEEDED" in response.json()["reason_codes"]
    assert "OBSERVE_ONLY" not in response.json()["reason_codes"]

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None
    assert event.would_decision is None
    assert event.would_reason_codes is None

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
