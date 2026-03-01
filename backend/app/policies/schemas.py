from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PolicyRules(BaseModel):
    per_action_max_amount: Decimal | None = Field(default=None, gt=0)
    daily_total_cap_amount: Decimal | None = Field(default=None, gt=0)
    per_user_daily_count_cap: int | None = Field(default=None, gt=0)
    per_user_daily_amount_cap: Decimal | None = Field(default=None, gt=0)
    near_cap_escalation_ratio: Decimal = Field(default=Decimal("0.9"), gt=0, le=1)


class ExposureContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    daily_total_amount: Decimal = Decimal("0")
    per_user_daily_count: int = 0
    per_user_daily_amount: Decimal = Decimal("0")
