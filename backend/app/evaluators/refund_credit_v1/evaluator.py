from __future__ import annotations

from typing import Any

from app.evaluators.base import EvaluationResult
from app.evaluators.refund_credit_v1.normalizer import NormalizedAction, normalize_action_payload
from app.evaluators.refund_credit_v1.schema import RefundCreditV1Exposure, RefundCreditV1Rules


class RefundCreditV1Evaluator:
    policy_type = "refund_credit_v1"

    def validate_rules(self, rules_json: dict[str, Any]) -> RefundCreditV1Rules:
        return RefundCreditV1Rules.model_validate(rules_json)

    def normalize_action(self, action_type: str, payload: dict[str, Any]) -> NormalizedAction:
        return normalize_action_payload(action_type=action_type, payload=payload)

    def evaluate(
        self,
        action: NormalizedAction,
        exposure_context: RefundCreditV1Exposure,
        rules: RefundCreditV1Rules,
    ) -> EvaluationResult:
        projected_daily_total_amount_cents = exposure_context.daily_total_amount_cents + action.amount_cents
        projected_financial_total_amount_cents = exposure_context.financial_total_amount_cents + action.amount_cents
        projected_user_daily_count = exposure_context.per_user_daily_count + 1
        projected_user_daily_amount_cents = exposure_context.per_user_daily_amount_cents + action.amount_cents

        risk_metrics = {
            "projected_daily_total_amount_cents": projected_daily_total_amount_cents,
            "projected_financial_total_amount_cents": projected_financial_total_amount_cents,
            "projected_user_daily_count": projected_user_daily_count,
            "projected_user_daily_amount_cents": projected_user_daily_amount_cents,
        }

        block_reason_codes: list[str] = []
        if rules.per_action_max_amount is not None and action.amount_cents > rules.per_action_max_amount:
            block_reason_codes.append("PER_ACTION_MAX_AMOUNT_EXCEEDED")
        if (
            rules.daily_total_cap_amount is not None
            and projected_financial_total_amount_cents > rules.daily_total_cap_amount
        ):
            block_reason_codes.append("DAILY_TOTAL_CAP_EXCEEDED")
        if (
            rules.per_user_daily_count_cap is not None
            and projected_user_daily_count > rules.per_user_daily_count_cap
        ):
            block_reason_codes.append("PER_USER_DAILY_COUNT_CAP_EXCEEDED")
        if (
            rules.per_user_daily_amount_cap is not None
            and projected_user_daily_amount_cents > rules.per_user_daily_amount_cap
        ):
            block_reason_codes.append("PER_USER_DAILY_AMOUNT_CAP_EXCEEDED")

        if block_reason_codes:
            return EvaluationResult("BLOCK", block_reason_codes, risk_metrics)

        near_cap_reason_codes: list[str] = []
        if _is_near_cap(
            projected_financial_total_amount_cents,
            rules.daily_total_cap_amount,
            rules.near_cap_escalation_ratio,
        ):
            near_cap_reason_codes.append("NEAR_DAILY_TOTAL_CAP")
        if _is_near_cap(
            projected_user_daily_count,
            rules.per_user_daily_count_cap,
            rules.near_cap_escalation_ratio,
        ):
            near_cap_reason_codes.append("NEAR_PER_USER_DAILY_COUNT_CAP")
        if _is_near_cap(
            projected_user_daily_amount_cents,
            rules.per_user_daily_amount_cap,
            rules.near_cap_escalation_ratio,
        ):
            near_cap_reason_codes.append("NEAR_PER_USER_DAILY_AMOUNT_CAP")

        if near_cap_reason_codes:
            return EvaluationResult("ESCALATE", near_cap_reason_codes, risk_metrics)

        return EvaluationResult("ALLOW", ["WITHIN_POLICY"], risk_metrics)


def _is_near_cap(projected_value: int, cap: int | None, ratio: float) -> bool:
    if cap is None:
        return False
    return projected_value >= int(cap * ratio)
