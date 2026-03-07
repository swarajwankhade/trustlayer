from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RefundActionRequest(BaseModel):
    request_id: str
    user_id: str
    ticket_id: str | None = None
    refund_amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreditActionRequest(BaseModel):
    request_id: str
    user_id: str
    ticket_id: str | None = None
    credit_amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    credit_type: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    decision: str
    reason_codes: list[str]
    policy_version: int | None = None
    model_version: str | None = None


class CreatePolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: int = Field(gt=0)
    rules_json: dict[str, Any]
    created_by: str = Field(min_length=1, max_length=255)


class PolicyResponse(BaseModel):
    id: UUID
    name: str
    version: int
    status: str
    rules_json: dict[str, Any]
    created_by: str


def cents_to_decimal(amount_cents: int) -> Decimal:
    return (Decimal(amount_cents) / Decimal("100")).quantize(Decimal("0.01"))
