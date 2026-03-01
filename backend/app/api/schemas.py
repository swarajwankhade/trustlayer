from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RefundActionRequest(BaseModel):
    request_id: str
    user_id: str
    ticket_id: str
    refund_amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RefundActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: str
    decision: str
    reason_codes: list[str]
    policy_version: int | None = None
    model_version: str | None = None
