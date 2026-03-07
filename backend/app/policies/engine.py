from decimal import Decimal
from typing import Any

from app.policies.schemas import ExposureContext, PolicyRules


def evaluate_action(
    amount: Decimal,
    exposure_context: ExposureContext,
    policy: PolicyRules,
) -> tuple[str, list[str], dict[str, Any]]:
    projected_daily_total_amount = exposure_context.daily_total_amount + amount
    projected_financial_total_amount_cents = exposure_context.financial_total_amount_cents + _to_cents(amount)
    projected_user_daily_count = exposure_context.per_user_daily_count + 1
    projected_user_daily_amount = exposure_context.per_user_daily_amount + amount
    projected_user_daily_amount_cents = _to_cents(projected_user_daily_amount)

    risk_metrics = {
        "projected_daily_total_amount": str(projected_daily_total_amount),
        "projected_financial_total_amount_cents": projected_financial_total_amount_cents,
        "projected_user_daily_count": projected_user_daily_count,
        "projected_user_daily_amount": str(projected_user_daily_amount),
        "projected_user_daily_amount_cents": projected_user_daily_amount_cents,
    }

    block_reason_codes: list[str] = []
    amount_cents = _to_cents(amount)
    if policy.per_action_max_amount is not None and amount_cents > policy.per_action_max_amount:
        block_reason_codes.append("PER_ACTION_MAX_AMOUNT_EXCEEDED")
    if (
        policy.daily_total_cap_amount is not None
        and projected_financial_total_amount_cents > policy.daily_total_cap_amount
    ):
        block_reason_codes.append("DAILY_TOTAL_CAP_EXCEEDED")
    if policy.per_user_daily_count_cap is not None and projected_user_daily_count > policy.per_user_daily_count_cap:
        block_reason_codes.append("PER_USER_DAILY_COUNT_CAP_EXCEEDED")
    if (
        policy.per_user_daily_amount_cap is not None
        and projected_user_daily_amount_cents > policy.per_user_daily_amount_cap
    ):
        block_reason_codes.append("PER_USER_DAILY_AMOUNT_CAP_EXCEEDED")
    if block_reason_codes:
        return "BLOCK", block_reason_codes, risk_metrics

    near_cap_reason_codes: list[str] = []
    if _is_near_cap(
        projected_financial_total_amount_cents,
        policy.daily_total_cap_amount,
        policy.near_cap_escalation_ratio,
    ):
        near_cap_reason_codes.append("NEAR_DAILY_TOTAL_CAP")
    if _is_near_cap(
        projected_user_daily_count,
        policy.per_user_daily_count_cap,
        policy.near_cap_escalation_ratio,
    ):
        near_cap_reason_codes.append("NEAR_PER_USER_DAILY_COUNT_CAP")
    if _is_near_cap(
        projected_user_daily_amount_cents,
        policy.per_user_daily_amount_cap,
        policy.near_cap_escalation_ratio,
    ):
        near_cap_reason_codes.append("NEAR_PER_USER_DAILY_AMOUNT_CAP")
    if near_cap_reason_codes:
        return "ESCALATE", near_cap_reason_codes, risk_metrics

    return "ALLOW", ["WITHIN_POLICY"], risk_metrics


def _is_near_cap(projected_value: int, cap: int | None, ratio: float) -> bool:
    if cap is None:
        return False
    return projected_value >= int(cap * ratio)


def _to_cents(amount: Decimal) -> int:
    return int((amount * Decimal("100")).quantize(Decimal("1")))
