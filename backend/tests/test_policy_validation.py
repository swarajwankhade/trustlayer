import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import routes as api_routes
from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.main import app
from app.models import Policy

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


def test_policy_validation_valid_rules_returns_valid_true(authorized_client: TestClient) -> None:
    response = authorized_client.post(
        "/v1/admin/policies/validate",
        json={
            "rules_json": {
                "per_action_max_amount": 10_000,
                "daily_total_cap_amount": 20_000,
                "per_user_daily_count_cap": 10,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["warnings"] == []


def test_policy_validation_invalid_rules_returns_errors(authorized_client: TestClient) -> None:
    response = authorized_client.post(
        "/v1/admin/policies/validate",
        json={
            "rules_json": {
                "per_action_max_amount": -1,
                "daily_total_cap_amount": 20_000,
                "per_user_daily_count_cap": 10,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 2.0,
            }
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is False
    assert len(payload["errors"]) >= 1
    assert payload["warnings"] == []


def test_policy_validation_does_not_create_policy_rows(authorized_client: TestClient, db_session: Session) -> None:
    baseline_name = f"baseline-{uuid.uuid4()}"
    db_session.add(
        Policy(
            id=uuid.uuid4(),
            name=baseline_name,
            version=1,
            status="INACTIVE",
            rules_json={
                "per_action_max_amount": 10_000,
                "daily_total_cap_amount": 20_000,
                "per_user_daily_count_cap": 10,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            },
            created_by="pytest",
        )
    )
    db_session.commit()

    before_count = db_session.scalar(select(func.count()).select_from(Policy))
    response = authorized_client.post(
        "/v1/admin/policies/validate",
        json={
            "rules_json": {
                "per_action_max_amount": 10_000,
                "daily_total_cap_amount": 20_000,
                "per_user_daily_count_cap": 10,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            }
        },
    )
    after_count = db_session.scalar(select(func.count()).select_from(Policy))

    assert response.status_code == 200
    assert before_count == after_count


def test_policy_validation_requires_api_key(client: TestClient) -> None:
    def override_db_session():
        yield None

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        response = client.post(
            "/v1/admin/policies/validate",
            json={
                "rules_json": {
                    "per_action_max_amount": 10_000,
                    "daily_total_cap_amount": 20_000,
                    "per_user_daily_count_cap": 10,
                    "per_user_daily_amount_cap": 20_000,
                    "near_cap_escalation_ratio": 0.9,
                }
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


def test_policy_validation_uses_evaluator_registry(
    authorized_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_get_evaluator = api_routes.get_evaluator
    requested_policy_types: list[str] = []

    def _spy_get_evaluator(policy_type: str):
        requested_policy_types.append(policy_type)
        return real_get_evaluator(policy_type)

    monkeypatch.setattr(api_routes, "get_evaluator", _spy_get_evaluator)

    response = authorized_client.post(
        "/v1/admin/policies/validate",
        json={
            "rules_json": {
                "per_action_max_amount": 10_000,
                "daily_total_cap_amount": 20_000,
                "per_user_daily_count_cap": 10,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            }
        },
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert requested_policy_types == ["refund_credit_v1"]
