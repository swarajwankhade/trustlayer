from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exposure.store import ExposureStore, ExposureStoreUnavailableError
from app.models import DecisionEvent, KillSwitch
from app.config import get_settings
from app.evaluators import get_evaluator
from app.evaluators.refund_credit_v1.schema import RefundCreditV1Exposure
from app.policies.schemas import ExposureContext
from app.policies.service import load_active_policy

DEFAULT_POLICY_TYPE = "refund_credit_v1"


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
            would_decision=None,
            would_reason_codes=None,
            model_version=action.model_version,
            policy_type=DEFAULT_POLICY_TYPE,
            runtime_mode="kill_switch",
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
    resolved_policy_type = active_policy.policy_type or DEFAULT_POLICY_TYPE
    decision_ts = datetime.now(timezone.utc)
    decision_date = decision_ts.date()
    minute_bucket = decision_ts.strftime("%Y-%m-%dT%H:%M")
    rate_limit = max(get_settings().action_rate_limit_per_minute, 1)

    try:
        current_action_rate = exposure_store.increment_action_rate(
            action_type=action.action_type,
            minute_bucket=minute_bucket,
        )
        if current_action_rate > rate_limit:
            decision_event = DecisionEvent(
                action_type=action.action_type,
                request_id=action.request_id,
                decision="ESCALATE",
                reason_codes=active_policy.base_reason_codes + ["RATE_LIMIT_EXCEEDED"],
                would_decision=None,
                would_reason_codes=None,
                model_version=action.model_version,
                policy_type=resolved_policy_type,
                runtime_mode="enforce",
                policy_id=active_policy.policy_id,
                policy_version=active_policy.policy_version,
                exposure_snapshot_json=ExposureContext().model_dump(mode="json"),
                action_payload_json=action.payload_json,
            )
            db.add(decision_event)
            db.commit()
            db.refresh(decision_event)
            return decision_event

        financial_total_amount_cents = exposure_store.get_financial_total(decision_date)
        exposure_context = exposure_store.get_exposure(
            action_type=action.action_type,
            user_id=action.user_id,
            date=decision_date,
        ).model_copy(update={"financial_total_amount_cents": financial_total_amount_cents})
        evaluator = get_evaluator(resolved_policy_type)
        normalized_action = evaluator.normalize_action(action_type=action.action_type, payload=action.payload_json)
        typed_rules = evaluator.validate_rules(active_policy.rules.model_dump(mode="json"))
        typed_exposure = RefundCreditV1Exposure(
            daily_total_amount_cents=_to_cents(exposure_context.daily_total_amount),
            per_user_daily_count=exposure_context.per_user_daily_count,
            per_user_daily_amount_cents=_to_cents(exposure_context.per_user_daily_amount),
            financial_total_amount_cents=exposure_context.financial_total_amount_cents,
        )
        evaluation = evaluator.evaluate(action=normalized_action, exposure_context=typed_exposure, rules=typed_rules)
        evaluated_decision = evaluation.decision
        evaluated_reason_codes = evaluation.reason_codes
        actual_reason_codes = active_policy.base_reason_codes + evaluated_reason_codes
        if not kill_switch.observe_only and evaluated_decision == "ALLOW":
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
        evaluated_decision = "ESCALATE"
        actual_reason_codes = active_policy.base_reason_codes + ["REDIS_UNAVAILABLE"]

    if kill_switch.observe_only:
        decision = "ALLOW"
        reason_codes = ["OBSERVE_ONLY"]
        if evaluated_decision == "BLOCK":
            reason_codes.append("WOULD_BLOCK")
        elif evaluated_decision == "ESCALATE":
            reason_codes.append("WOULD_ESCALATE")
        would_decision = evaluated_decision
        would_reason_codes = actual_reason_codes
    else:
        decision = evaluated_decision
        reason_codes = actual_reason_codes
        would_decision = None
        would_reason_codes = None

    decision_event = DecisionEvent(
        action_type=action.action_type,
        request_id=action.request_id,
        decision=decision,
        reason_codes=reason_codes,
        would_decision=would_decision,
        would_reason_codes=would_reason_codes,
        model_version=action.model_version,
        policy_type=resolved_policy_type,
        runtime_mode="observe_only" if kill_switch.observe_only else "enforce",
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
        kill_switch = KillSwitch(
            id=1,
            enabled=False,
            observe_only=False,
            reason="initial state",
            updated_by="system",
        )
        db.add(kill_switch)
        db.commit()
        db.refresh(kill_switch)
    return kill_switch


def _to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))
