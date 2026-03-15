from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    policy_type: str | None = Field(default=None, min_length=1, max_length=64)
    rules_json: dict[str, Any]
    created_by: str = Field(min_length=1, max_length=255)


class ValidatePolicyRequest(BaseModel):
    policy_type: str | None = Field(default=None, min_length=1, max_length=64)
    rules_json: dict[str, Any]


class ValidatePolicyResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


class DemoBootstrapResponse(BaseModel):
    created_kill_switch: bool
    created_policy: bool
    activated_policy: bool
    policy_id: str | None
    policy_version: int | None


class DemoGenerateResponse(BaseModel):
    generated_count: int
    request_ids: list[str]
    decisions: list[str]


class DemoResetResponse(BaseModel):
    decision_events_deleted: int
    policies_deleted: int
    redis_keys_deleted: int
    kill_switch_enabled: bool


class PolicyResponse(BaseModel):
    id: UUID
    policy_id: UUID | None = None
    name: str
    version: int
    status: str
    policy_type: str | None = None
    rules_json: dict[str, Any]
    created_by: str
    created_at: datetime
    is_active: bool = False

    @model_validator(mode="after")
    def set_derived_fields(self) -> "PolicyResponse":
        self.policy_id = self.id
        self.is_active = self.status == "ACTIVE"
        return self


class KillSwitchUpdateRequest(BaseModel):
    enabled: bool
    observe_only: bool
    reason: str = Field(min_length=1)
    updated_by: str = Field(min_length=1, max_length=255)


class KillSwitchResponse(BaseModel):
    id: int
    enabled: bool
    observe_only: bool
    reason: str | None
    updated_at: datetime
    updated_by: str


class DecisionEventResponse(BaseModel):
    event_id: UUID
    timestamp: datetime
    action_type: str
    request_id: str
    decision: str
    reason_codes: list[str]
    would_decision: str | None
    would_reason_codes: list[str] | None
    model_version: str | None
    policy_type: str | None
    runtime_mode: str | None
    event_schema_version: str
    policy_id: UUID | None
    policy_version: int | None
    exposure_snapshot_json: dict[str, Any]
    action_payload_json: dict[str, Any] | None
    normalized_input_json: dict[str, Any] | None
    normalized_input_hash: str | None


class DecisionReplayResponse(BaseModel):
    event_id: UUID
    original_decision: str
    original_reason_codes: list[str]
    original_would_decision: str | None = None
    original_would_reason_codes: list[str] | None = None
    replayed_decision: str
    replayed_reason_codes: list[str]
    matches_original: bool


class SimulateRefundPayload(BaseModel):
    user_id: str
    refund_amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    ticket_id: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulateCreditPayload(BaseModel):
    user_id: str
    credit_amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    credit_type: str | None = None
    ticket_id: str | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulationExposureOverride(BaseModel):
    daily_total_amount_cents: int = Field(default=0, ge=0)
    per_user_daily_count: int = Field(default=0, ge=0)
    per_user_daily_amount_cents: int = Field(default=0, ge=0)
    financial_total_amount_cents: int = Field(default=0, ge=0)


class SimulationRequest(BaseModel):
    action_type: Literal["refund", "credit_adjustment"]
    refund: SimulateRefundPayload | None = None
    credit: SimulateCreditPayload | None = None
    policy_id: UUID | None = None
    policy_version: int | None = Field(default=None, gt=0)
    exposure_override: SimulationExposureOverride | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "SimulationRequest":
        if self.action_type == "refund":
            if self.refund is None or self.credit is not None:
                raise ValueError("refund payload is required for action_type=refund")
        if self.action_type == "credit_adjustment":
            if self.credit is None or self.refund is not None:
                raise ValueError("credit payload is required for action_type=credit_adjustment")
        if (self.policy_id is None) != (self.policy_version is None):
            raise ValueError("policy_id and policy_version must be provided together")
        return self


class SimulationResponse(BaseModel):
    action_type: Literal["refund", "credit_adjustment"]
    decision: str
    reason_codes: list[str]
    policy_id: UUID | None
    policy_version: int | None
    exposure_context_used: dict[str, Any]


class DecisionMetricsResponse(BaseModel):
    total_decisions: int
    allow_count: int
    escalate_count: int
    block_count: int
    observe_only_count: int
    would_block_count: int
    would_escalate_count: int
    counts_by_action_type: dict[str, int]
    counts_by_reason_code: dict[str, int]


class ExposureMetricsResponse(BaseModel):
    date_bucket_utc: str
    refund_daily_total_amount_cents: int
    credit_daily_total_amount_cents: int
    financial_total_amount_cents: int


class DashboardRuntimeControls(BaseModel):
    kill_switch_enabled: bool
    observe_only: bool
    reason: str | None
    updated_at: datetime
    updated_by: str


class DashboardActivePolicy(BaseModel):
    policy_id: UUID
    name: str
    version: int
    status: str
    policy_type: str | None
    rules_json: dict[str, Any]


class DashboardResponse(BaseModel):
    runtime_controls: DashboardRuntimeControls
    active_policy: DashboardActivePolicy | None
    decision_metrics: DecisionMetricsResponse
    exposure_metrics: ExposureMetricsResponse
    recent_decisions: list[DecisionEventResponse]


def cents_to_decimal(amount_cents: int) -> Decimal:
    return (Decimal(amount_cents) / Decimal("100")).quantize(Decimal("0.01"))
