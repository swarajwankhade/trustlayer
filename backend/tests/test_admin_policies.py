import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.db.session import get_db_session, get_session_factory
from app.main import app
from app.models import Policy

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL is not set"),
]


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _unique_version() -> int:
    return (uuid.uuid4().int % 1_000_000) + 1


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


def test_list_policies_returns_created_policies(authorized_client: TestClient, db_session: Session) -> None:
    name_one = _unique_name("list-test-policy-one")
    name_two = _unique_name("list-test-policy-two")
    version_one = _unique_version()
    version_two = version_one + 1

    policy_one = Policy(
        id=uuid.uuid4(),
        name=name_one,
        version=version_one,
        status="INACTIVE",
        rules_json={
            "per_action_max_amount": 10_000,
            "daily_total_cap_amount": 50_000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    policy_two = Policy(
        id=uuid.uuid4(),
        name=name_two,
        version=version_two,
        status="INACTIVE",
        rules_json={
            "per_action_max_amount": 20_000,
            "daily_total_cap_amount": 60_000,
            "per_user_daily_count_cap": 6,
            "per_user_daily_amount_cap": 30_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    db_session.add(policy_one)
    db_session.add(policy_two)
    db_session.commit()

    response = authorized_client.get("/v1/admin/policies")

    assert response.status_code == 200
    names = {policy["name"] for policy in response.json()}
    assert name_one in names
    assert name_two in names

    db_session.execute(delete(Policy).where(Policy.id.in_([policy_one.id, policy_two.id])))
    db_session.commit()


def test_create_policy_validates_schema(authorized_client: TestClient, db_session: Session) -> None:
    valid_name = _unique_name("create-test-policy")
    invalid_name = _unique_name("invalid-policy")
    valid_version = _unique_version()
    invalid_version = valid_version + 1

    valid_response = authorized_client.post(
        "/v1/admin/policies",
        json={
            "name": valid_name,
            "version": valid_version,
            "rules_json": {
                "per_action_max_amount": 10_000,
                "daily_total_cap_amount": 50_000,
                "per_user_daily_count_cap": 5,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            },
            "created_by": "pytest",
        },
    )
    invalid_response = authorized_client.post(
        "/v1/admin/policies",
        json={
            "name": invalid_name,
            "version": invalid_version,
            "rules_json": {
                "per_action_max_amount": -5,
                "daily_total_cap_amount": 50_000,
                "per_user_daily_count_cap": 5,
                "per_user_daily_amount_cap": 20_000,
                "near_cap_escalation_ratio": 0.9,
            },
            "created_by": "pytest",
        },
    )

    assert valid_response.status_code == 201
    assert valid_response.json()["status"] == "INACTIVE"
    assert valid_response.json()["name"] == valid_name
    assert invalid_response.status_code == 422

    created_policy_id = uuid.UUID(valid_response.json()["id"])
    db_session.execute(delete(Policy).where(Policy.id == created_policy_id))
    db_session.commit()


def test_activate_policy_makes_exactly_one_active(authorized_client: TestClient, db_session: Session) -> None:
    name_one = _unique_name("activate-test-policy-one")
    name_two = _unique_name("activate-test-policy-two")
    version_one = _unique_version()
    version_two = version_one + 1

    policy_one = Policy(
        id=uuid.uuid4(),
        name=name_one,
        version=version_one,
        status="ACTIVE",
        rules_json={
            "per_action_max_amount": 10_000,
            "daily_total_cap_amount": 50_000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    policy_two = Policy(
        id=uuid.uuid4(),
        name=name_two,
        version=version_two,
        status="INACTIVE",
        rules_json={
            "per_action_max_amount": 15_000,
            "daily_total_cap_amount": 55_000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 25_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    db_session.add(policy_one)
    db_session.add(policy_two)
    db_session.commit()

    response = authorized_client.post(f"/v1/admin/policies/{policy_two.id}/activate")

    assert response.status_code == 200
    assert response.json()["id"] == str(policy_two.id)
    assert response.json()["status"] == "ACTIVE"

    active_count = db_session.scalar(select(func.count()).select_from(Policy).where(Policy.status == "ACTIVE"))
    assert active_count == 1

    db_session.execute(delete(Policy).where(Policy.id.in_([policy_one.id, policy_two.id])))
    db_session.commit()


def test_active_policy_endpoint_returns_correct_version(authorized_client: TestClient, db_session: Session) -> None:
    name_one = _unique_name("active-endpoint-policy-one")
    name_two = _unique_name("active-endpoint-policy-two")
    version_one = _unique_version()
    version_two = version_one + 1

    policy_one = Policy(
        id=uuid.uuid4(),
        name=name_one,
        version=version_one,
        status="INACTIVE",
        rules_json={
            "per_action_max_amount": 10_000,
            "daily_total_cap_amount": 50_000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    policy_two = Policy(
        id=uuid.uuid4(),
        name=name_two,
        version=version_two,
        status="INACTIVE",
        rules_json={
            "per_action_max_amount": 15_000,
            "daily_total_cap_amount": 55_000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 25_000,
            "near_cap_escalation_ratio": 0.9,
        },
        created_by="pytest",
    )
    db_session.add(policy_one)
    db_session.add(policy_two)
    db_session.commit()

    activate_response = authorized_client.post(f"/v1/admin/policies/{policy_two.id}/activate")
    active_response = authorized_client.get("/v1/admin/policies/active")

    assert activate_response.status_code == 200
    assert active_response.status_code == 200
    assert active_response.json()["id"] == str(policy_two.id)
    assert active_response.json()["version"] == version_two
    assert active_response.json()["status"] == "ACTIVE"

    db_session.execute(delete(Policy).where(Policy.id.in_([policy_one.id, policy_two.id])))
    db_session.commit()
