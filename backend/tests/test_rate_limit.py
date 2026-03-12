import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
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


@pytest.fixture(autouse=True)
def isolate_rate_limit_data(db_session: Session) -> Iterator[None]:
    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy))
    db_session.execute(delete(KillSwitch))
    db_session.commit()
    yield
    db_session.execute(delete(DecisionEvent))
    db_session.execute(delete(Policy))
    db_session.execute(delete(KillSwitch))
    db_session.commit()


def test_requests_under_limit_proceed_normally(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTION_RATE_LIMIT_PER_MINUTE", "5")
    insert_active_policy(db_session, version=201, per_action_max_amount=20_000)

    response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-under",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"
    assert fake_exposure_store.financial_total_amount == Decimal("10.00")


def test_requests_over_limit_escalate_and_do_not_increment_exposure(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTION_RATE_LIMIT_PER_MINUTE", "1")
    insert_active_policy(db_session, version=202, per_action_max_amount=20_000)

    first_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-over",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    second_request_id = f"req-{uuid.uuid4()}"
    second_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": second_request_id,
            "user_id": "user-rate-over",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert first_response.status_code == 200
    assert first_response.json()["decision"] == "ALLOW"
    assert second_response.status_code == 200
    assert second_response.json()["decision"] == "ESCALATE"
    assert "RATE_LIMIT_EXCEEDED" in second_response.json()["reason_codes"]
    assert fake_exposure_store.financial_total_amount == Decimal("10.00")
    assert fake_exposure_store.per_user_daily_counts[("refund", "user-rate-over")] == 1

    event = db_session.scalar(select(DecisionEvent).where(DecisionEvent.request_id == second_request_id))
    assert event is not None
    assert event.decision == "ESCALATE"
    assert "RATE_LIMIT_EXCEEDED" in event.reason_codes


def test_rate_limit_behavior_works_for_credit_endpoint(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTION_RATE_LIMIT_PER_MINUTE", "1")
    insert_active_policy(db_session, version=203, per_action_max_amount=20_000)

    first_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-credit",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 1000,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    second_response = authorized_client.post(
        "/v1/actions/credit",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-credit",
            "ticket_id": "ticket-1",
            "credit_amount_cents": 1000,
            "currency": "USD",
            "credit_type": "courtesy",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert first_response.status_code == 200
    assert first_response.json()["decision"] == "ALLOW"
    assert second_response.status_code == 200
    assert second_response.json()["decision"] == "ESCALATE"
    assert "RATE_LIMIT_EXCEEDED" in second_response.json()["reason_codes"]
    assert fake_exposure_store.per_user_daily_counts[("credit_adjustment", "user-rate-credit")] == 1


def test_rate_limit_resets_across_minute_buckets(
    authorized_client: TestClient,
    db_session: Session,
    fake_exposure_store: FakeExposureStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTION_RATE_LIMIT_PER_MINUTE", "1")
    insert_active_policy(db_session, version=204, per_action_max_amount=20_000)

    class FakeDateTime(datetime):
        values = [
            datetime(2026, 3, 11, 12, 0, 10, tzinfo=timezone.utc),
            datetime(2026, 3, 11, 12, 1, 5, tzinfo=timezone.utc),
        ]

        @classmethod
        def now(cls, tz=None):
            value = cls.values.pop(0)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(action_service, "datetime", FakeDateTime)

    first_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-minute",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )
    second_response = authorized_client.post(
        "/v1/actions/refund",
        json={
            "request_id": f"req-{uuid.uuid4()}",
            "user_id": "user-rate-minute",
            "ticket_id": "ticket-1",
            "refund_amount_cents": 1000,
            "currency": "USD",
            "model_version": "gpt-test",
            "metadata": {},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["decision"] == "ALLOW"
    assert second_response.json()["decision"] == "ALLOW"
    assert fake_exposure_store.financial_total_amount == Decimal("20.00")
