import os
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db.session import get_session_factory
from app.devtools.service import DEMO_POLICY_RULES, bootstrap_demo_data
from app.models import DecisionEvent, KillSwitch, Policy

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL is not set"),
]


def _clear_tables() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        session.execute(delete(DecisionEvent))
        session.execute(delete(Policy))
        session.execute(delete(KillSwitch))
        session.commit()


def test_bootstrap_creates_kill_switch_and_default_demo_policy() -> None:
    _clear_tables()
    session_factory = get_session_factory()

    with session_factory() as session:
        result = bootstrap_demo_data(session, activate_policy=True, created_by="pytest-bootstrap")

        assert result.created_kill_switch is True
        assert result.created_policy is True
        assert result.policy_version == 1

        assert result.policy_id is not None
        policy = session.scalar(select(Policy).where(Policy.id == uuid.UUID(result.policy_id)))
        assert policy is not None
        assert policy.status == "ACTIVE"
        assert policy.rules_json == DEMO_POLICY_RULES

        kill_switch = session.get(KillSwitch, 1)
        assert kill_switch is not None
        assert kill_switch.enabled is False

    _clear_tables()


def test_bootstrap_is_idempotent_for_default_policy() -> None:
    _clear_tables()
    session_factory = get_session_factory()

    with session_factory() as session:
        first = bootstrap_demo_data(session, activate_policy=True, created_by="pytest-bootstrap")
        second = bootstrap_demo_data(session, activate_policy=True, created_by="pytest-bootstrap")

        assert first.policy_id == second.policy_id
        assert second.created_policy is False

        policy_count = session.scalar(select(func.count()).select_from(Policy))
        assert policy_count == 1

    _clear_tables()
