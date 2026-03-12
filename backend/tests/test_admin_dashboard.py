import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
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


@pytest.fixture(autouse=True)
def isolate_dashboard_data(db_session: Session) -> Iterator[None]:
    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy))
    db_session.execute(delete(KillSwitch))
    db_session.commit()
    yield
    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy))
    db_session.execute(delete(KillSwitch))
    db_session.commit()


def _set_kill_switch(
    db_session: Session,
    *,
    enabled: bool,
    observe_only: bool,
    reason: str,
    updated_by: str,
) -> None:
    kill_switch = KillSwitch(
        id=1,
        enabled=enabled,
        observe_only=observe_only,
        reason=reason,
        updated_by=updated_by,
    )
    db_session.add(kill_switch)
    db_session.commit()


def _insert_decision_event(
    db_session: Session,
    *,
    timestamp: datetime,
    request_id: str,
    action_type: str,
    decision: str,
    reason_codes: list[str],
    would_decision: str | None = None,
    would_reason_codes: list[str] | None = None,
) -> None:
    db_session.add(
        DecisionEvent(
            timestamp=timestamp,
            action_type=action_type,
            request_id=request_id,
            decision=decision,
            reason_codes=reason_codes,
            would_decision=would_decision,
            would_reason_codes=would_reason_codes,
            model_version="dashboard-test",
            policy_id=None,
            policy_version=None,
            exposure_snapshot_json={
                "daily_total_amount": "0.00",
                "per_user_daily_count": 0,
                "per_user_daily_amount": "0.00",
                "financial_total_amount_cents": 0,
            },
            action_payload_json={"user_id": "dashboard-user"},
        )
    )
    db_session.commit()


def test_dashboard_returns_kill_switch_state(authorized_client: TestClient, db_session: Session) -> None:
    _set_kill_switch(
        db_session,
        enabled=True,
        observe_only=True,
        reason="ops incident",
        updated_by="ops-user",
    )

    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_controls"]["kill_switch_enabled"] is True
    assert payload["runtime_controls"]["observe_only"] is True
    assert payload["runtime_controls"]["reason"] == "ops incident"
    assert payload["runtime_controls"]["updated_by"] == "ops-user"


def test_dashboard_returns_active_policy_when_present(authorized_client: TestClient, db_session: Session) -> None:
    policy_id = insert_active_policy(db_session, version=101, per_action_max_amount=2_000)

    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_policy"] is not None
    assert payload["active_policy"]["policy_id"] == str(policy_id)
    assert payload["active_policy"]["version"] == 101
    assert payload["active_policy"]["status"] == "ACTIVE"


def test_dashboard_returns_null_active_policy_when_absent(authorized_client: TestClient) -> None:
    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    assert response.json()["active_policy"] is None


def test_dashboard_includes_decision_metrics_summary(authorized_client: TestClient, db_session: Session) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_decision_event(
        db_session,
        timestamp=now,
        request_id=f"dash-{uuid.uuid4()}",
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    _insert_decision_event(
        db_session,
        timestamp=now + timedelta(seconds=1),
        request_id=f"dash-{uuid.uuid4()}",
        action_type="credit_adjustment",
        decision="BLOCK",
        reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )
    _insert_decision_event(
        db_session,
        timestamp=now + timedelta(seconds=2),
        request_id=f"dash-{uuid.uuid4()}",
        action_type="refund",
        decision="ALLOW",
        reason_codes=["OBSERVE_ONLY", "WOULD_BLOCK"],
        would_decision="BLOCK",
        would_reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )

    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    metrics = response.json()["decision_metrics"]
    assert metrics["total_decisions"] == 3
    assert metrics["allow_count"] == 2
    assert metrics["block_count"] == 1
    assert metrics["observe_only_count"] == 1
    assert metrics["would_block_count"] == 1


def test_dashboard_includes_exposure_metrics_summary(
    authorized_client: TestClient,
    fake_exposure_store: FakeExposureStore,
) -> None:
    fake_exposure_store.daily_total_amounts["refund"] = Decimal("10.50")
    fake_exposure_store.daily_total_amounts["credit_adjustment"] = Decimal("20.25")
    fake_exposure_store.financial_total_amount = Decimal("30.75")

    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    metrics = response.json()["exposure_metrics"]
    assert metrics["refund_daily_total_amount_cents"] == 1050
    assert metrics["credit_daily_total_amount_cents"] == 2025
    assert metrics["financial_total_amount_cents"] == 3075


def test_dashboard_recent_decisions_ordered_newest_first(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    oldest_request_id = f"dash-{uuid.uuid4()}"
    middle_request_id = f"dash-{uuid.uuid4()}"
    newest_request_id = f"dash-{uuid.uuid4()}"

    _insert_decision_event(
        db_session,
        timestamp=base,
        request_id=oldest_request_id,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    _insert_decision_event(
        db_session,
        timestamp=base + timedelta(seconds=1),
        request_id=middle_request_id,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    _insert_decision_event(
        db_session,
        timestamp=base + timedelta(seconds=2),
        request_id=newest_request_id,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )

    response = authorized_client.get("/v1/admin/dashboard")

    assert response.status_code == 200
    recent = response.json()["recent_decisions"]
    assert [recent[0]["request_id"], recent[1]["request_id"], recent[2]["request_id"]] == [
        newest_request_id,
        middle_request_id,
        oldest_request_id,
    ]
