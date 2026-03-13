from pydantic import BaseModel, Field


class RefundCreditV1Rules(BaseModel):
    per_action_max_amount: int | None = Field(default=None, gt=0)
    daily_total_cap_amount: int | None = Field(default=None, gt=0)
    per_user_daily_count_cap: int | None = Field(default=None, gt=0)
    per_user_daily_amount_cap: int | None = Field(default=None, gt=0)
    near_cap_escalation_ratio: float = Field(default=0.9, gt=0, le=1)


class RefundCreditV1Exposure(BaseModel):
    daily_total_amount_cents: int = 0
    per_user_daily_count: int = 0
    per_user_daily_amount_cents: int = 0
    financial_total_amount_cents: int = 0
