import uuid
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.exposure.store import ExposureStoreUnavailableError
from app.models import Policy
from app.policies.schemas import ExposureContext


class FakeExposureStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.daily_total_amounts: dict[str, Decimal] = {}
        self.per_user_daily_amounts: dict[tuple[str, str], Decimal] = {}
        self.per_user_daily_counts: dict[tuple[str, str], int] = {}
        self.financial_total_amount = Decimal("0.00")

    def get_exposure(self, action_type: str, user_id: str, date: date_type) -> ExposureContext:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        return ExposureContext(
            daily_total_amount=self.daily_total_amounts.get(action_type, Decimal("0.00")),
            per_user_daily_count=self.per_user_daily_counts.get((action_type, user_id), 0),
            per_user_daily_amount=self.per_user_daily_amounts.get((action_type, user_id), Decimal("0.00")),
            financial_total_amount_cents=int(self.financial_total_amount * 100),
        )

    def apply_allow(self, action_type: str, user_id: str, amount: Decimal, date: date_type) -> ExposureContext:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        self.daily_total_amounts[action_type] = self.daily_total_amounts.get(action_type, Decimal("0.00")) + amount
        user_amount_key = (action_type, user_id)
        self.per_user_daily_amounts[user_amount_key] = self.per_user_daily_amounts.get(user_amount_key, Decimal("0.00")) + amount
        self.per_user_daily_counts[user_amount_key] = self.per_user_daily_counts.get(user_amount_key, 0) + 1
        return self.get_exposure(action_type, user_id, date)

    def get_financial_total(self, date: date_type) -> int:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        return int(self.financial_total_amount * 100)

    def increment_financial_total(self, amount: Decimal, date: date_type) -> int:
        _ = date
        if self.fail:
            raise ExposureStoreUnavailableError("Redis unavailable")
        self.financial_total_amount += amount
        return int(self.financial_total_amount * 100)


def insert_active_policy(
    db_session: Session,
    *,
    version: int,
    per_action_max_amount: int = 10_000,
    daily_total_cap_amount: int = 50_000,
    per_user_daily_count_cap: int = 5,
    per_user_daily_amount_cap: int = 20_000,
    near_cap_escalation_ratio: float = 0.9,
) -> uuid.UUID:
    db_session.execute(
        update(Policy).where(func.lower(Policy.status) == "active").values(status="INACTIVE")
    )

    policy_id = uuid.uuid4()
    db_session.add(
        Policy(
            id=policy_id,
            name=f"active-policy-{policy_id}",
            version=version,
            status="ACTIVE",
            rules_json={
                "per_action_max_amount": per_action_max_amount,
                "daily_total_cap_amount": daily_total_cap_amount,
                "per_user_daily_count_cap": per_user_daily_count_cap,
                "per_user_daily_amount_cap": per_user_daily_amount_cap,
                "near_cap_escalation_ratio": near_cap_escalation_ratio,
            },
            created_by="pytest",
        )
    )
    db_session.commit()
    return policy_id
