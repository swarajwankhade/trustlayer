from app.evaluators.base import Evaluator
from app.evaluators.refund_credit_v1 import RefundCreditV1Evaluator

_EVALUATORS: dict[str, Evaluator] = {
    "refund_credit_v1": RefundCreditV1Evaluator(),
}


def get_evaluator(policy_type: str) -> Evaluator:
    try:
        return _EVALUATORS[policy_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported policy_type: {policy_type}") from exc
