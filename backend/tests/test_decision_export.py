import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

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


def _insert_event(
    db_session: Session,
    *,
    timestamp: datetime,
    action_type: str,
    decision: str,
    reason_codes: list[str],
    would_decision: str | None = None,
    would_reason_codes: list[str] | None = None,
    event_schema_version: str | None = None,
    normalized_input_json: dict[str, object] | None = None,
) -> str:
    request_id = f"export-{uuid.uuid4()}"
    db_session.add(
        DecisionEvent(
            timestamp=timestamp,
            action_type=action_type,
            request_id=request_id,
            decision=decision,
            reason_codes=reason_codes,
            would_decision=would_decision,
            would_reason_codes=would_reason_codes,
            model_version="export-test",
            event_schema_version=event_schema_version,
            policy_id=None,
            policy_version=None,
            exposure_snapshot_json={
                "daily_total_amount": "0.00",
                "per_user_daily_count": 0,
                "per_user_daily_amount": "0.00",
                "financial_total_amount_cents": 0,
            },
            action_payload_json={
                "user_id": "export-user",
                "ticket_id": "export-ticket",
                "currency": "USD",
            },
            normalized_input_json=normalized_input_json,
        )
    )
    db_session.commit()
    return request_id


def test_export_returns_inserted_events_and_would_fields(
    authorized_client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    older_request_id = _insert_event(
        db_session,
        timestamp=now - timedelta(minutes=1),
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    newer_request_id = _insert_event(
        db_session,
        timestamp=now,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["OBSERVE_ONLY", "WOULD_BLOCK"],
        would_decision="BLOCK",
        would_reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
        event_schema_version="1",
        normalized_input_json={
            "action_type": "refund",
            "user_id": "export-user",
            "amount_cents": 1000,
            "currency": "USD",
            "ticket_id": "export-ticket",
            "model_version": "export-test",
            "metadata": {},
            "credit_type": None,
        },
    )

    response = authorized_client.get("/v1/admin/decisions/export")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["request_id"] == newer_request_id
    assert body[1]["request_id"] == older_request_id
    assert body[0]["would_decision"] == "BLOCK"
    assert body[0]["would_reason_codes"] == ["PER_ACTION_MAX_AMOUNT_EXCEEDED"]
    assert "policy_type" in body[0]
    assert "runtime_mode" in body[0]
    assert body[0]["event_schema_version"] == "1"
    assert body[0]["normalized_input_json"] is not None
    assert body[0]["normalized_input_json"]["action_type"] == "refund"


def test_export_filters_by_action_type(authorized_client: TestClient, db_session: Session) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_event(
        db_session,
        timestamp=now,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    credit_request_id = _insert_event(
        db_session,
        timestamp=now + timedelta(seconds=1),
        action_type="credit_adjustment",
        decision="BLOCK",
        reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )

    response = authorized_client.get(
        "/v1/admin/decisions/export",
        params={"action_type": "credit_adjustment"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == credit_request_id
    assert body[0]["action_type"] == "credit_adjustment"


def test_export_filters_by_decision(authorized_client: TestClient, db_session: Session) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_event(
        db_session,
        timestamp=now,
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    block_request_id = _insert_event(
        db_session,
        timestamp=now + timedelta(seconds=1),
        action_type="refund",
        decision="BLOCK",
        reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )

    response = authorized_client.get("/v1/admin/decisions/export", params={"decision": "BLOCK"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == block_request_id
    assert body[0]["decision"] == "BLOCK"


def test_export_filters_by_from_to_timestamp(authorized_client: TestClient, db_session: Session) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    _insert_event(
        db_session,
        timestamp=base - timedelta(minutes=2),
        action_type="refund",
        decision="ALLOW",
        reason_codes=["WITHIN_POLICY"],
    )
    mid_request_id = _insert_event(
        db_session,
        timestamp=base,
        action_type="refund",
        decision="ESCALATE",
        reason_codes=["NEAR_DAILY_TOTAL_CAP"],
    )
    _insert_event(
        db_session,
        timestamp=base + timedelta(minutes=2),
        action_type="refund",
        decision="BLOCK",
        reason_codes=["PER_ACTION_MAX_AMOUNT_EXCEEDED"],
    )

    response = authorized_client.get(
        "/v1/admin/decisions/export",
        params={"from": base.isoformat(), "to": base.isoformat()},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == mid_request_id


def test_export_limit_is_applied(authorized_client: TestClient, db_session: Session) -> None:
    base = datetime.now(timezone.utc).replace(microsecond=0)
    newest_request_id = ""
    for index in range(3):
        request_id = _insert_event(
            db_session,
            timestamp=base + timedelta(seconds=index),
            action_type="refund",
            decision="ALLOW",
            reason_codes=["WITHIN_POLICY"],
        )
        if index == 2:
            newest_request_id = request_id

    response = authorized_client.get("/v1/admin/decisions/export", params={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == newest_request_id
