import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.exposure.store import get_exposure_store
from app.main import app
from app.models import DecisionEvent
from tests.action_test_utils import FakeExposureStore

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
def isolate_decision_events(db_session: Session) -> Iterator[None]:
    db_session.execute(delete(DecisionEvent))
    db_session.commit()
    yield
    db_session.execute(delete(DecisionEvent))
    db_session.commit()


def _insert_metric_event(
    db_session: Session,
    *,
    action_type: str,
    decision: str,
    reason_codes: list[str],
    would_decision: str | None = None,
    would_reason_codes: list[str] | None = None,
) -> str:
    request_id = f"metrics-{uuid.uuid4()}"
    db_session.add(
        DecisionEvent(
            action_type=action_type,
            request_id=request_id,
            decision=decision,
            reason_codes=reason_codes,
            would_decision=would_decision,
            would_reason_codes=would_reason_codes,
            model_version="metrics-test",
            policy_id=None,
            policy_version=None,
            exposure_snapshot_json={
                "daily_total_amount": "0.00",
                "per_user_daily_count": 0,
                "per_user_daily_amount": "0.00",
                "financial_total_amount_cents": 0,
            },
            action_payload_json={"user_id": "metrics-user"},
        )
    )
    db_session.commit()
    return request_id


def test_decision_metrics_aggregates_and_counts_observe_only(authorized_client: TestClient, db_session: Session) -> None:
    request_ids = [
        _insert_metric_event(
            db_session,
            action_type="refund",
            decision="ALLOW",
            reason_codes=["WITHIN_POLICY"],
        ),
        _insert_metric_event(
            db_session,
            action_type="credit_adjustment",
            decision="BLOCK",
            reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
        ),
        _insert_metric_event(
            db_session,
            action_type="refund",
            decision="ESCALATE",
            reason_codes=["NEAR_DAILY_TOTAL_CAP"],
        ),
        _insert_metric_event(
            db_session,
            action_type="refund",
            decision="ALLOW",
            reason_codes=["OBSERVE_ONLY", "WOULD_BLOCK"],
            would_decision="BLOCK",
            would_reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
        ),
        _insert_metric_event(
            db_session,
            action_type="credit_adjustment",
            decision="ALLOW",
            reason_codes=["OBSERVE_ONLY", "WOULD_ESCALATE"],
            would_decision="ESCALATE",
            would_reason_codes=["NEAR_DAILY_TOTAL_CAP"],
        ),
    ]

    response = authorized_client.get("/v1/admin/metrics/decisions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_decisions"] == 5
    assert payload["allow_count"] == 3
    assert payload["escalate_count"] == 1
    assert payload["block_count"] == 1
    assert payload["observe_only_count"] == 2
    assert payload["would_block_count"] == 1
    assert payload["would_escalate_count"] == 1
    assert payload["counts_by_action_type"]["refund"] == 3
    assert payload["counts_by_action_type"]["credit_adjustment"] == 2
    assert payload["counts_by_reason_code"]["OBSERVE_ONLY"] == 2
    assert payload["counts_by_reason_code"]["WOULD_BLOCK"] == 1
    assert payload["counts_by_reason_code"]["WOULD_ESCALATE"] == 1

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id.in_(request_ids)))
    db_session.commit()


def test_decision_metrics_filter_by_action_type(authorized_client: TestClient, db_session: Session) -> None:
    refund_request_id = _insert_metric_event(
        db_session,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    credit_request_id = _insert_metric_event(
        db_session,
        action_type="credit_adjustment",
        decision="BLOCK",
        reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )

    response = authorized_client.get(
        "/v1/admin/metrics/decisions",
        params={"action_type": "credit_adjustment"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_decisions"] == 1
    assert payload["block_count"] == 1
    assert payload["allow_count"] == 0
    assert payload["counts_by_action_type"] == {"credit_adjustment": 1}

    db_session.execute(delete(DecisionEvent).where(DecisionEvent.request_id.in_([refund_request_id, credit_request_id])))
    db_session.commit()


def test_exposure_metrics_returns_zeros_when_missing(authorized_client: TestClient) -> None:
    response = authorized_client.get("/v1/admin/metrics/exposure")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date_bucket_utc"] == datetime.now(timezone.utc).date().isoformat()
    assert payload["refund_daily_total_amount_cents"] == 0
    assert payload["credit_daily_total_amount_cents"] == 0
    assert payload["financial_total_amount_cents"] == 0


def test_exposure_metrics_returns_current_values(
    authorized_client: TestClient,
    fake_exposure_store: FakeExposureStore,
) -> None:
    fake_exposure_store.daily_total_amounts["refund"] = Decimal("12.34")
    fake_exposure_store.daily_total_amounts["credit_adjustment"] = Decimal("45.67")
    fake_exposure_store.financial_total_amount = Decimal("58.01")

    response = authorized_client.get("/v1/admin/metrics/exposure")

    assert response.status_code == 200
    payload = response.json()
    assert payload["refund_daily_total_amount_cents"] == 1234
    assert payload["credit_daily_total_amount_cents"] == 4567
    assert payload["financial_total_amount_cents"] == 5801
