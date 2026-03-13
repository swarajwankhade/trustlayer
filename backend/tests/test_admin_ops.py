import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

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
    enabled: bool,
    observe_only: bool = False,
    reason: str,
    updated_by: str,
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


def test_get_kill_switch_returns_current_state(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(db_session, enabled=False, reason="ops normal", updated_by="pytest")

    response = authorized_client.get("/v1/admin/killswitch")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["observe_only"] is False
    assert response.json()["reason"] == "ops normal"
    assert response.json()["updated_by"] == "pytest"


def test_post_kill_switch_updates_state(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(db_session, enabled=False, reason="before update", updated_by="pytest")

    response = authorized_client.post(
        "/v1/admin/killswitch",
        json={"enabled": True, "observe_only": True, "reason": "incident", "updated_by": "ops-user"},
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["observe_only"] is True
    assert response.json()["reason"] == "incident"
    assert response.json()["updated_by"] == "ops-user"

    stored = db_session.get(KillSwitch, 1)
    assert stored is not None
    assert stored.enabled is True
    assert stored.observe_only is True

    _set_kill_switch(db_session, enabled=False, reason="reset", updated_by="pytest")


def test_kill_switch_forces_escalate_for_refund_and_credit(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    policy_id = insert_active_policy(db_session, version=50)
    _set_kill_switch(db_session, enabled=True, reason="incident", updated_by="ops-user")

    refund_request_id = f"req-{uuid.uuid4()}"
    credit_request_id = f"req-{uuid.uuid4()}"

    refund_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": refund_request_id,
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    credit_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": credit_request_id,
            "user_id": "user-1",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 1000,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert refund_response.status_code == 200
    assert refund_response.json()["decision"] == "ESCALATE"
    assert "KILL_SWITCH_ENABLED" in refund_response.json()["reason_codes"]
    assert credit_response.status_code == 200
    assert credit_response.json()["decision"] == "ESCALATE"
    assert "KILL_SWITCH_ENABLED" in credit_response.json()["reason_codes"]

    refund_event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == refund_request_id))
    credit_event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == credit_request_id))
    assert refund_event is not None
    assert credit_event is not None
    assert refund_event.decision == "ESCALATE"
    assert credit_event.decision == "ESCALATE"

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id.in_([refund_request_id, credit_request_id])))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
    _set_kill_switch(db_session, enabled=False, reason="reset", updated_by="pytest")


def test_get_decisions_returns_and_filters(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(db_session, enabled=False, reason="normal", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=51)
    request_id = f"req-{uuid.uuid4()}"

    action_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-decision-filter",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    assert action_response.status_code == 200

    all_response = authorized_client.get("/v1/admin/decisions", params={"limit": 50})
    by_request_response = authorized_client.get("/v1/admin/decisions", params={"request_id": request_id, "limit": 50})
    by_decision_response = authorized_client.get("/v1/admin/decisions", params={"decision": "ALLOW", "limit": 50})

    assert all_response.status_code == 200
    assert isinstance(all_response.json(), list)
    assert len(all_response.json()) > 0

    assert by_request_response.status_code == 200
    assert len(by_request_response.json()) == 1
    assert by_request_response.json()[0]["request_id"] == request_id

    assert by_decision_response.status_code == 200
    assert all(event["decision"] == "ALLOW" for event in by_decision_response.json())

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_get_decisions_supports_offset(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(db_session, enabled=False, reason="normal", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=52)
    user_id = f"user-offset-{uuid.uuid4()}"
    request_ids = [f"req-{uuid.uuid4()}" for _ in range(3)]

    for request_id in request_ids:
        action_response = authorized_client.post(
            "/v1/actions/refund",
            json={
                "request_id": request_id,
                "user_id": user_id,
                "ticket_id": "ticket-offset",
                "refund_amount_cents": 1000,
                "currency": "USD",
                "model_version": "gpt-test",
                "metadata": {},
            },
        )
        assert action_response.status_code == 200

    first_page = authorized_client.get("/v1/admin/decisions", params={"user_id": user_id, "limit": 1, "offset": 0})
    second_page = authorized_client.get("/v1/admin/decisions", params={"user_id": user_id, "limit": 1, "offset": 1})

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert len(first_page.json()) == 1
    assert len(second_page.json()) == 1
    assert first_page.json()[0]["request_id"] != second_page.json()[0]["request_id"]

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id.in_(request_ids)))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_get_decision_detail_returns_expected_event(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(db_session, enabled=False, reason="normal", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=60)
    request_id = f"req-{uuid.uuid4()}"

    create_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-detail",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    assert create_response.status_code == 200

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None

    detail_response = authorized_client.get(f"/v1/admin/decisions/{event.event_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["event_id"] == str(event.event_id)
    assert detail_response.json()["request_id"] == request_id

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_replay_returns_matching_decision_and_does_not_create_new_event(
    authorized_client: TestClient, db_session: Session
) -> None:
    _set_kill_switch(db_session, enabled=False, reason="normal", updated_by="pytest")
    policy_id = insert_active_policy(db_session, version=61)
    request_id = f"req-{uuid.uuid4()}"

    create_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-replay",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    assert create_response.status_code == 200
    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None

    before_count = db_session.scalar(select(func.count()).select_from(DecisionEvent))
    replay_response = authorized_client.post(f"/v1/admin/decisions/{event.event_id}/replay")
    after_count = db_session.scalar(select(func.count()).select_from(DecisionEvent))

    assert replay_response.status_code == 200
    assert replay_response.json()["event_id"] == str(event.event_id)
    assert replay_response.json()["matches_original"] is True
    assert replay_response.json()["original_decision"] == event.decision
    assert replay_response.json()["replayed_decision"] == event.decision
    assert before_count == after_count

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_replay_uses_stored_policy_version_not_current_active(
    authorized_client: TestClient, db_session: Session
) -> None:
    _set_kill_switch(db_session, enabled=False, reason="normal", updated_by="pytest")
    original_policy_id = insert_active_policy(
        db_session,
        version=70,
        per_action_max_amount=2_000,
        daily_total_cap_amount=50_000,
    )
    request_id = f"req-{uuid.uuid4()}"

    create_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-policy-replay",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1500,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["decision"] == "ALLOW"
    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None

    newer_policy_id = insert_active_policy(
        db_session,
        version=71,
        per_action_max_amount=1_000,
        daily_total_cap_amount=50_000,
    )

    replay_response = authorized_client.post(f"/v1/admin/decisions/{event.event_id}/replay")
    assert replay_response.status_code == 200
    assert replay_response.json()["matches_original"] is True
    assert replay_response.json()["original_decision"] == "ALLOW"
    assert replay_response.json()["replayed_decision"] == "ALLOW"

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id.in_([original_policy_id, newer_policy_id])))
    db_session.commit()


def test_decision_detail_and_replay_return_404_for_missing_event(authorized_client: TestClient) -> None:
    missing_event_id = uuid.uuid4()

    detail_response = authorized_client.get(f"/v1/admin/decisions/{missing_event_id}")
    replay_response = authorized_client.post(f"/v1/admin/decisions/{missing_event_id}/replay")

    assert detail_response.status_code == 404
    assert replay_response.status_code == 404


def test_observe_only_decision_detail_and_replay_use_would_decision(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    policy_id = insert_active_policy(db_session, version=79, per_action_max_amount=1_000)
    _set_kill_switch(db_session, enabled=False, observe_only=True, reason="observe mode", updated_by="ops-user")
    request_id = f"req-{uuid.uuid4()}"

    create_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": request_id,
            "user_id": "user-observe-detail",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1500,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["decision"] == "ALLOW"
    assert "OBSERVE_ONLY" in create_response.json()["reason_codes"]
    assert "WOULD_BLOCK" in create_response.json()["reason_codes"]

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == request_id))
    assert event is not None

    detail_response = authorized_client.get(f"/v1/admin/decisions/{event.event_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["decision"] == "ALLOW"
    assert detail_response.json()["would_decision"] == "BLOCK"
    assert "PER_ACTION_MAX_AMOUNT_EXCEEDED" in detail_response.json()["would_reason_codes"]

    replay_response = authorized_client.post(f"/v1/admin/decisions/{event.event_id}/replay")
    assert replay_response.status_code == 200
    assert replay_response.json()["original_decision"] == "ALLOW"
    assert replay_response.json()["original_would_decision"] == "BLOCK"
    assert replay_response.json()["replayed_decision"] == "BLOCK"
    assert replay_response.json()["matches_original"] is True

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id == request_id))
    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
    _set_kill_switch(db_session, enabled=False, observe_only=False, reason="reset", updated_by="pytest")


def test_simulate_refund_with_active_policy(authorized_client: TestClient, db_session: Session) -> None:
    policy_id = insert_active_policy(db_session, version=80, per_action_max_amount=2_000)

    response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "refund",
            "refund": {
                "user_id": "user-sim-refund",
                "refund_amount_cents": 1000,
                "currency": "USD",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action_type"] == "refund"
    assert body["decision"] == "ALLOW"
    assert body["policy_id"] == str(policy_id)
    assert body["policy_version"] == 80

    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_simulate_credit_with_active_policy(authorized_client: TestClient, db_session: Session) -> None:
    policy_id = insert_active_policy(db_session, version=81, per_action_max_amount=2_000)

    response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "credit_adjustment",
            "credit": {
                "user_id": "user-sim-credit",
                "credit_amount_cents": 1000,
                "currency": "USD",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action_type"] == "credit_adjustment"
    assert body["decision"] == "ALLOW"
    assert body["policy_id"] == str(policy_id)
    assert body["policy_version"] == 81

    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_simulate_uses_explicit_policy_id_and_version(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    active_policy_id = insert_active_policy(db_session, version=82, per_action_max_amount=2_000)
    explicit_policy_id = insert_active_policy(db_session, version=83, per_action_max_amount=500)

    response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "refund",
            "refund": {
                "user_id": "user-sim-explicit",
                "refund_amount_cents": 1000,
                "currency": "USD",
            },
            "policy_id": str(explicit_policy_id),
            "policy_version": 83,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["policy_id"] == str(explicit_policy_id)
    assert body["policy_version"] == 83
    assert body["decision"] == "BLOCK"

    db_session.execute(delete(Policy).where(Policy.id.in_([active_policy_id, explicit_policy_id])))
    db_session.commit()


def test_simulate_exposure_override_can_trigger_escalate_or_block(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    policy_id = insert_active_policy(
        db_session,
        version=84,
        per_action_max_amount=10_000,
        daily_total_cap_amount=10_000,
    )

    escalate_response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "refund",
            "refund": {
                "user_id": "user-sim-override",
                "refund_amount_cents": 1000,
                "currency": "USD",
            },
            "exposure_override": {
                "daily_total_amount_cents": 0,
                "per_user_daily_count": 0,
                "per_user_daily_amount_cents": 0,
                "financial_total_amount_cents": 8500,
            },
        },
    )
    block_response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "refund",
            "refund": {
                "user_id": "user-sim-override",
                "refund_amount_cents": 1000,
                "currency": "USD",
            },
            "exposure_override": {
                "daily_total_amount_cents": 0,
                "per_user_daily_count": 0,
                "per_user_daily_amount_cents": 0,
                "financial_total_amount_cents": 9500,
            },
        },
    )

    assert escalate_response.status_code == 200
    assert escalate_response.json()["decision"] == "ESCALATE"
    assert block_response.status_code == 200
    assert block_response.json()["decision"] == "BLOCK"

    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()


def test_simulate_does_not_create_decision_events(authorized_client: TestClient, db_session: Session) -> None:
    policy_id = insert_active_policy(db_session, version=85)

    before_count = db_session.scalar(select(func.count()).select_from(DecisionEvent))
    response = authorized_client.post(
        "/v1/admin/simulate",
        json={
            "action_type": "refund",
            "refund": {
                "user_id": "user-sim-readonly",
                "refund_amount_cents": 1000,
                "currency": "USD",
            },
        },
    )
    after_count = db_session.scalar(select(func.count()).select_from(DecisionEvent))

    assert response.status_code == 200
    assert before_count == after_count

    db_session.execute(delete(Policy).where(Policy.id == policy_id))
    db_session.commit()
