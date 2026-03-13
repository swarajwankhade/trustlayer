import os
import uuid

import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models import Policy

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL is not set"),
]


def test_can_create_and_read_policy_row() -> None:
    policy_id = uuid.uuid4()
    session_factory = get_session_factory()

    with session_factory() as session:
        policy = Policy(
            id=policy_id,
            name=f"test-policy-{policy_id}",
            version=1,
            status="active",
            policy_type="refund_credit_v1",
            rules_json={"max_amount": 1000},
            created_by="pytest",
        )
        session.add(policy)
        session.commit()

    with session_factory() as session:
        saved_policy = session.scalar(select(Policy).where(Policy.id == policy_id))

        assert saved_policy is not None
        assert saved_policy.name == f"test-policy-{policy_id}"
        assert saved_policy.policy_type == "refund_credit_v1"
        assert saved_policy.rules_json == {"max_amount": 1000}

        session.delete(saved_policy)
        session.commit()
