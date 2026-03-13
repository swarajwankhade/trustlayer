from app.evaluators.registry import get_evaluator
from app.evaluators.refund_credit_v1 import RefundCreditV1Exposure


def test_registry_resolves_refund_credit_v1() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    assert evaluator.policy_type == "refund_credit_v1"


def test_normalizer_maps_refund_payload() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    action = evaluator.normalize_action(
        "refund",
        {
            "user_id": "user-1",
            "refund_amount_cents": 1500,
            "currency": "USD",
            "ticket_id": "ticket-1",
            "model_version": "model-a",
            "metadata": {"source": "test"},
        },
    )

    assert action.action_type == "refund"
    assert action.user_id == "user-1"
    assert action.amount_cents == 1500
    assert action.currency == "USD"
    assert action.ticket_id == "ticket-1"
    assert action.model_version == "model-a"
    assert action.metadata == {"source": "test"}
    assert action.credit_type is None


def test_normalizer_maps_credit_payload() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    action = evaluator.normalize_action(
        "credit_adjustment",
        {
            "user_id": "user-2",
            "credit_amount_cents": 2000,
            "currency": "USD",
            "ticket_id": "ticket-2",
            "credit_type": "courtesy",
            "model_version": "model-b",
            "metadata": {"source": "test"},
        },
    )

    assert action.action_type == "credit_adjustment"
    assert action.amount_cents == 2000
    assert action.credit_type == "courtesy"


def test_typed_evaluator_allows_when_within_policy() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    rules = evaluator.validate_rules(
        {
            "per_action_max_amount": 10000,
            "daily_total_cap_amount": 50000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20000,
            "near_cap_escalation_ratio": 0.9,
        }
    )
    action = evaluator.normalize_action(
        "refund",
        {"user_id": "user-1", "refund_amount_cents": 1000, "currency": "USD"},
    )

    result = evaluator.evaluate(action, RefundCreditV1Exposure(), rules)

    assert result.decision == "ALLOW"
    assert result.reason_codes == ["WITHIN_POLICY"]


def test_typed_evaluator_escalates_when_near_cap() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    rules = evaluator.validate_rules(
        {
            "per_action_max_amount": 10000,
            "daily_total_cap_amount": 10000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20000,
            "near_cap_escalation_ratio": 0.9,
        }
    )
    action = evaluator.normalize_action(
        "refund",
        {"user_id": "user-1", "refund_amount_cents": 1000, "currency": "USD"},
    )
    exposure = RefundCreditV1Exposure(financial_total_amount_cents=8500)

    result = evaluator.evaluate(action, exposure, rules)

    assert result.decision == "ESCALATE"
    assert "NEAR_DAILY_TOTAL_CAP" in result.reason_codes


def test_typed_evaluator_blocks_on_hard_limit() -> None:
    evaluator = get_evaluator("refund_credit_v1")
    rules = evaluator.validate_rules(
        {
            "per_action_max_amount": 1000,
            "daily_total_cap_amount": 50000,
            "per_user_daily_count_cap": 5,
            "per_user_daily_amount_cap": 20000,
            "near_cap_escalation_ratio": 0.9,
        }
    )
    action = evaluator.normalize_action(
        "refund",
        {"user_id": "user-1", "refund_amount_cents": 1500, "currency": "USD"},
    )

    result = evaluator.evaluate(action, RefundCreditV1Exposure(), rules)

    assert result.decision == "BLOCK"
    assert "PER_ACTION_MAX_AMOUNT_EXCEEDED" in result.reason_codes
