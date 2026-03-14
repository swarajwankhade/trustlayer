from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models import Policy
from app.policies.schemas import PolicyRules


@dataclass(frozen=True)
class ActivePolicy:
    rules: PolicyRules
    policy_id: UUID | None = None
    policy_version: int | None = None
    policy_type: str = "refund_credit_v1"
    base_reason_codes: list[str] = field(default_factory=list)


def load_active_policy(db: Session) -> ActivePolicy:
    policy = db.scalar(
        select(Policy)
        .where(func.lower(Policy.status) == "active")
        .order_by(desc(Policy.version), desc(Policy.created_at))
        .limit(1)
    )
    if policy is None:
        return ActivePolicy(
            policy_id=None,
            policy_version=None,
            policy_type="refund_credit_v1",
            rules=PolicyRules(
                per_action_max_amount=None,
                daily_total_cap_amount=None,
                per_user_daily_count_cap=None,
                per_user_daily_amount_cap=None,
                near_cap_escalation_ratio=0.9,
            ),
            base_reason_codes=["NO_ACTIVE_POLICY"],
        )

    return ActivePolicy(
        policy_id=policy.id,
        policy_version=policy.version,
        policy_type=policy.policy_type or "refund_credit_v1",
        rules=PolicyRules.model_validate(policy.rules_json),
    )
