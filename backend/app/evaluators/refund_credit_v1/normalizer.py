from typing import Any, Literal

from pydantic import BaseModel, Field


class NormalizedAction(BaseModel):
    action_type: Literal["refund", "credit_adjustment"]
    user_id: str
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    ticket_id: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    credit_type: str | None = None


def normalize_action_payload(action_type: str, payload: dict[str, Any]) -> NormalizedAction:
    if action_type == "refund":
        return NormalizedAction(
            action_type="refund",
            user_id=payload["user_id"],
            amount_cents=payload["refund_amount_cents"],
            currency=payload["currency"],
            ticket_id=payload.get("ticket_id"),
            model_version=payload.get("model_version"),
            metadata=payload.get("metadata", {}),
        )

    if action_type == "credit_adjustment":
        return NormalizedAction(
            action_type="credit_adjustment",
            user_id=payload["user_id"],
            amount_cents=payload["credit_amount_cents"],
            currency=payload["currency"],
            ticket_id=payload.get("ticket_id"),
            model_version=payload.get("model_version"),
            metadata=payload.get("metadata", {}),
            credit_type=payload.get("credit_type"),
        )

    raise ValueError(f"Unsupported action_type: {action_type}")
