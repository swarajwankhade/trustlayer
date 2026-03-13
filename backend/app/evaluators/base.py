from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class EvaluationResult:
    decision: str
    reason_codes: list[str]
    risk_metrics: dict[str, Any]


class Evaluator(Protocol):
    policy_type: str

    def validate_rules(self, rules_json: dict[str, Any]) -> Any:
        ...

    def normalize_action(self, action_type: str, payload: dict[str, Any]) -> Any:
        ...

    def evaluate(self, action: Any, exposure_context: Any, rules: Any) -> EvaluationResult:
        ...
