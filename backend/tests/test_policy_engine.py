from decimal import Decimal

from app.policies.engine import evaluate_action
from app.policies.schemas import ExposureContext, PolicyRules


def test_evaluate_action_allows_when_within_policy() -> None:
    decision, reason_codes, risk_metrics = evaluate_action(
        amount=Decimal("25.00"),
        exposure_context=ExposureContext(
            daily_total_amount=Decimal("100.00"),
            per_user_daily_count=1,
            per_user_daily_amount=Decimal("50.00"),
        ),
        policy=PolicyRules(
            per_action_max_amount=Decimal("100.00"),
            daily_total_cap_amount=Decimal("500.00"),
            per_user_daily_count_cap=5,
            per_user_daily_amount_cap=Decimal("200.00"),
            near_cap_escalation_ratio=Decimal("0.9"),
        ),
    )

    assert decision == "ALLOW"
    assert reason_codes == ["WITHIN_POLICY"]
    assert risk_metrics["projected_daily_total_amount"] == "125.00"


def test_evaluate_action_escalates_when_near_cap() -> None:
    decision, reason_codes, _risk_metrics = evaluate_action(
        amount=Decimal("10.00"),
        exposure_context=ExposureContext(
            daily_total_amount=Decimal("85.00"),
            per_user_daily_count=1,
            per_user_daily_amount=Decimal("40.00"),
        ),
        policy=PolicyRules(
            per_action_max_amount=Decimal("100.00"),
            daily_total_cap_amount=Decimal("100.00"),
            per_user_daily_count_cap=5,
            per_user_daily_amount_cap=Decimal("200.00"),
            near_cap_escalation_ratio=Decimal("0.9"),
        ),
    )

    assert decision == "ESCALATE"
    assert reason_codes == ["NEAR_DAILY_TOTAL_CAP"]


def test_evaluate_action_escalates_when_near_user_amount_cap() -> None:
    decision, reason_codes, risk_metrics = evaluate_action(
        amount=Decimal("15.00"),
        exposure_context=ExposureContext(
            daily_total_amount=Decimal("10.00"),
            per_user_daily_count=1,
            per_user_daily_amount=Decimal("75.00"),
        ),
        policy=PolicyRules(
            per_action_max_amount=Decimal("100.00"),
            daily_total_cap_amount=Decimal("500.00"),
            per_user_daily_count_cap=5,
            per_user_daily_amount_cap=Decimal("100.00"),
            near_cap_escalation_ratio=Decimal("0.9"),
        ),
    )

    assert decision == "ESCALATE"
    assert reason_codes == ["NEAR_PER_USER_DAILY_AMOUNT_CAP"]
    assert risk_metrics["projected_user_daily_amount"] == "90.00"


def test_evaluate_action_blocks_on_hard_violation() -> None:
    decision, reason_codes, _risk_metrics = evaluate_action(
        amount=Decimal("120.00"),
        exposure_context=ExposureContext(),
        policy=PolicyRules(
            per_action_max_amount=Decimal("100.00"),
            daily_total_cap_amount=Decimal("500.00"),
            per_user_daily_count_cap=5,
            per_user_daily_amount_cap=Decimal("200.00"),
            near_cap_escalation_ratio=Decimal("0.9"),
        ),
    )

    assert decision == "BLOCK"
    assert reason_codes == ["PER_ACTION_MAX_AMOUNT_EXCEEDED"]
