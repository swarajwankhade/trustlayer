from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exposure.store import ExposureStore, ExposureStoreUnavailableError
from app.models import DecisionEvent, KillSwitch
from app.policies.engine import evaluate_action
from app.policies.schemas import ExposureContext
from app.policies.service import load_active_policy


@dataclass(frozen=True)
class ActionAuthorizationInput:
    action_type: str
    request_id: str
    user_id: str
    amount: Decimal
    model_version: str | None
    payload_json: dict[str, Any]


def authorize_action(
    action: ActionAuthorizationInput,
    db: Session,
    exposure_store: ExposureStore,
) -> DecisionEvent:
    existing_event = db.scalar(select(DecisionEvent).where(DecisionEvent.request_id == action.request_id))
    if existing_event is not None:
        return existing_event

    kill_switch = get_or_init_kill_switch(db)
    if kill_switch.enabled:
        decision_event = DecisionEvent(
            action_type=action.action_type,
            request_id=action.request_id,
            decision="ESCALATE",
            reason_codes=["KILL_SWITCH_ENABLED"],
            model_version=action.model_version,
            policy_id=None,
            policy_version=None,
            exposure_snapshot_json=ExposureContext().model_dump(mode="json"),
            action_payload_json=action.payload_json,
        )
        db.add(decision_event)
        db.commit()
        db.refresh(decision_event)
        return decision_event

    active_policy = load_active_policy(db)
    decision_date = datetime.now(timezone.utc).date()

    try:
        financial_total_amount_cents = exposure_store.get_financial_total(decision_date)
        exposure_context = exposure_store.get_exposure(
            action_type=action.action_type,
            user_id=action.user_id,
            date=decision_date,
        ).model_copy(update={"financial_total_amount_cents": financial_total_amount_cents})
        decision, reason_codes, _risk_metrics = evaluate_action(
            amount=action.amount,
            exposure_context=exposure_context,
            policy=active_policy.rules,
        )
        if decision == "ALLOW":
            exposure_store.apply_allow(
                action_type=action.action_type,
                user_id=action.user_id,
                amount=action.amount,
                date=decision_date,
            )
            exposure_store.increment_financial_total(
                amount=action.amount,
                date=decision_date,
            )
    except ExposureStoreUnavailableError:
        exposure_context = ExposureContext()
        decision = "ESCALATE"
        reason_codes = ["REDIS_UNAVAILABLE"]

    decision_event = DecisionEvent(
        action_type=action.action_type,
        request_id=action.request_id,
        decision=decision,
        reason_codes=active_policy.base_reason_codes + reason_codes,
        model_version=action.model_version,
        policy_id=active_policy.policy_id,
        policy_version=active_policy.policy_version,
        exposure_snapshot_json=exposure_context.model_dump(mode="json"),
        action_payload_json=action.payload_json,
    )
    db.add(decision_event)
    db.commit()
    db.refresh(decision_event)
    return decision_event


def get_or_init_kill_switch(db: Session) -> KillSwitch:
    kill_switch = db.get(KillSwitch, 1)
    if kill_switch is None:
        kill_switch = KillSwitch(id=1, enabled=False, reason="initial state", updated_by="system")
        db.add(kill_switch)
        db.commit()
        db.refresh(kill_switch)
    return kill_switch
