from decimal import Decimal
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from app.actions.service import ActionAuthorizationInput, authorize_action, get_or_init_kill_switch
from app.api.dependencies import require_api_key
from app.api.schemas import (
    ActionDecisionResponse,
    CreditActionRequest,
    CreatePolicyRequest,
    DecisionEventResponse,
    DecisionReplayResponse,
    KillSwitchResponse,
    KillSwitchUpdateRequest,
    PolicyResponse,
    RefundActionRequest,
    SimulationRequest,
    SimulationResponse,
    cents_to_decimal,
)
from app.db.session import get_db_session
from app.exposure.store import ExposureStore, get_exposure_store
from app.models import DecisionEvent, Policy
from app.policies.engine import evaluate_action
from app.policies.schemas import ExposureContext, PolicyRules
from app.policies.service import ActivePolicy, load_active_policy

router = APIRouter()
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@v1_router.post("/actions/refund", response_model=ActionDecisionResponse)
def create_refund_action(
    payload: RefundActionRequest,
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> ActionDecisionResponse:
    decision_event = authorize_action(
        action=ActionAuthorizationInput(
            action_type="refund",
            request_id=payload.request_id,
            user_id=payload.user_id,
            amount=cents_to_decimal(payload.refund_amount_cents),
            model_version=payload.model_version,
            payload_json=_serialize_payload(payload),
        ),
        db=db,
        exposure_store=exposure_store,
    )
    return _build_action_response(decision_event)


@v1_router.post("/actions/credit", response_model=ActionDecisionResponse)
def create_credit_action(
    payload: CreditActionRequest,
    db: Session = Depends(get_db_session),
    exposure_store: ExposureStore = Depends(get_exposure_store),
) -> ActionDecisionResponse:
    decision_event = authorize_action(
        action=ActionAuthorizationInput(
            action_type="credit_adjustment",
            request_id=payload.request_id,
            user_id=payload.user_id,
            amount=cents_to_decimal(payload.credit_amount_cents),
            model_version=payload.model_version,
            payload_json=_serialize_payload(payload),
        ),
        db=db,
        exposure_store=exposure_store,
    )
    return _build_action_response(decision_event)


@v1_router.get("/admin/policies", response_model=list[PolicyResponse])
def list_policies(db: Session = Depends(get_db_session)) -> list[PolicyResponse]:
    policies = db.scalars(select(Policy).order_by(desc(Policy.created_at))).all()
    return [PolicyResponse.model_validate(policy, from_attributes=True) for policy in policies]


@v1_router.post("/admin/policies", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
def create_policy(payload: CreatePolicyRequest, db: Session = Depends(get_db_session)) -> PolicyResponse:
    try:
        validated_rules = PolicyRules.model_validate(payload.rules_json)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()) from exc

    policy = Policy(
        name=payload.name,
        version=payload.version,
        status="INACTIVE",
        rules_json=validated_rules.model_dump(mode="json"),
        created_by=payload.created_by,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.post("/admin/policies/{policy_id}/activate", response_model=PolicyResponse)
def activate_policy(policy_id: UUID, db: Session = Depends(get_db_session)) -> PolicyResponse:
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")

    db.execute(update(Policy).values(status="INACTIVE"))
    db.execute(update(Policy).where(Policy.id == policy_id).values(status="ACTIVE"))
    db.commit()
    db.refresh(policy)
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.get("/admin/policies/active", response_model=PolicyResponse)
def get_active_policy(db: Session = Depends(get_db_session)) -> PolicyResponse:
    policy = db.scalar(
        select(Policy)
        .where(Policy.status == "ACTIVE")
        .order_by(desc(Policy.version), desc(Policy.created_at))
        .limit(1)
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active policy")
    return PolicyResponse.model_validate(policy, from_attributes=True)


@v1_router.get("/admin/killswitch", response_model=KillSwitchResponse)
def get_kill_switch(db: Session = Depends(get_db_session)) -> KillSwitchResponse:
    kill_switch = get_or_init_kill_switch(db)
    return KillSwitchResponse.model_validate(kill_switch, from_attributes=True)


@v1_router.post("/admin/killswitch", response_model=KillSwitchResponse)
def update_kill_switch(payload: KillSwitchUpdateRequest, db: Session = Depends(get_db_session)) -> KillSwitchResponse:
    kill_switch = get_or_init_kill_switch(db)
    kill_switch.enabled = payload.enabled
    kill_switch.observe_only = payload.observe_only
    kill_switch.reason = payload.reason
    kill_switch.updated_by = payload.updated_by
    db.add(kill_switch)
    db.commit()
    db.refresh(kill_switch)
    return KillSwitchResponse.model_validate(kill_switch, from_attributes=True)


@v1_router.get("/admin/decisions", response_model=list[DecisionEventResponse])
def list_decisions(
    action_type: str | None = None,
    decision: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
    limit: int = 50,
    db: Session = Depends(get_db_session),
) -> list[DecisionEventResponse]:
    normalized_limit = min(max(limit, 1), 200)
    query = select(DecisionEvent)

    if action_type:
        query = query.where(DecisionEvent.action_type == action_type)
    if decision:
        query = query.where(DecisionEvent.decision == decision)
    if request_id:
        query = query.where(DecisionEvent.request_id == request_id)
    if user_id:
        query = query.where(DecisionEvent.action_payload_json["user_id"].astext == user_id)
    if from_ts:
        query = query.where(DecisionEvent.timestamp >= from_ts)
    if to_ts:
        query = query.where(DecisionEvent.timestamp <= to_ts)

    events = db.scalars(query.order_by(desc(DecisionEvent.timestamp)).limit(normalized_limit)).all()
    return [DecisionEventResponse.model_validate(event, from_attributes=True) for event in events]


@v1_router.get("/admin/decisions/{event_id}", response_model=DecisionEventResponse)
def get_decision_detail(event_id: UUID, db: Session = Depends(get_db_session)) -> DecisionEventResponse:
    event = db.get(DecisionEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision event not found")
    return DecisionEventResponse.model_validate(event, from_attributes=True)


@v1_router.post("/admin/decisions/{event_id}/replay", response_model=DecisionReplayResponse)
def replay_decision(event_id: UUID, db: Session = Depends(get_db_session)) -> DecisionReplayResponse:
    event = db.get(DecisionEvent, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Decision event not found")

    if event.policy_id is None or event.policy_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored decision does not reference a policy version",
        )

    policy = db.scalar(
        select(Policy).where(Policy.id == event.policy_id, Policy.version == event.policy_version).limit(1)
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored policy version referenced by decision was not found",
        )

    if event.action_payload_json is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Stored action payload is missing for replay",
        )

    try:
        amount = _extract_amount_from_payload(event.action_type, event.action_payload_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored action payload is invalid for replay: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    try:
        policy_rules = PolicyRules.model_validate(policy.rules_json)
        exposure_context = ExposureContext.model_validate(event.exposure_snapshot_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Stored policy or exposure snapshot is invalid for replay: {exc}",
        ) from exc

    replayed_decision, replayed_reason_codes, _ = evaluate_action(
        amount=amount,
        exposure_context=exposure_context,
        policy=policy_rules,
    )
    original_decision = event.would_decision if event.would_decision is not None else event.decision
    original_reason_codes = (
        event.would_reason_codes if event.would_reason_codes is not None else event.reason_codes
    )

    return DecisionReplayResponse(
        event_id=event.event_id,
        original_decision=event.decision,
        original_reason_codes=event.reason_codes,
        original_would_decision=event.would_decision,
        original_would_reason_codes=event.would_reason_codes,
        replayed_decision=replayed_decision,
        replayed_reason_codes=replayed_reason_codes,
        matches_original=(original_decision == replayed_decision and original_reason_codes == replayed_reason_codes),
    )


@v1_router.post("/admin/simulate", response_model=SimulationResponse)
def simulate_action(payload: SimulationRequest, db: Session = Depends(get_db_session)) -> SimulationResponse:
    policy_context = _load_simulation_policy(db, payload)
    exposure_context = _resolve_simulation_exposure(payload)
    amount = _extract_simulation_amount(payload)

    decision, reason_codes, _risk_metrics = evaluate_action(
        amount=amount,
        exposure_context=exposure_context,
        policy=policy_context.rules,
    )

    return SimulationResponse(
        action_type=payload.action_type,
        decision=decision,
        reason_codes=policy_context.base_reason_codes + reason_codes,
        policy_id=policy_context.policy_id,
        policy_version=policy_context.policy_version,
        exposure_context_used=exposure_context.model_dump(mode="json"),
    )


def _build_action_response(event: DecisionEvent) -> ActionDecisionResponse:
    return ActionDecisionResponse(
        request_id=event.request_id,
        decision=event.decision,
        reason_codes=event.reason_codes,
        policy_version=event.policy_version,
        model_version=event.model_version,
    )


def _serialize_payload(payload: BaseModel) -> dict[str, Any]:
    return payload.model_dump(mode="json")


def _extract_amount_from_payload(action_type: str, payload: dict[str, Any]) -> Decimal:
    if action_type == "refund":
        parsed = RefundActionRequest.model_validate(payload)
        return cents_to_decimal(parsed.refund_amount_cents)
    if action_type == "credit_adjustment":
        parsed = CreditActionRequest.model_validate(payload)
        return cents_to_decimal(parsed.credit_amount_cents)
    raise ValueError(f"Unsupported action_type for replay: {action_type}")


def _load_simulation_policy(db: Session, payload: SimulationRequest) -> ActivePolicy:
    if payload.policy_id is None or payload.policy_version is None:
        return load_active_policy(db)

    policy = db.scalar(
        select(Policy).where(Policy.id == payload.policy_id, Policy.version == payload.policy_version).limit(1)
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Policy not found for provided policy_id and policy_version",
        )
    return ActivePolicy(
        policy_id=policy.id,
        policy_version=policy.version,
        rules=PolicyRules.model_validate(policy.rules_json),
    )


def _resolve_simulation_exposure(payload: SimulationRequest) -> ExposureContext:
    if payload.exposure_override is None:
        return ExposureContext()

    return ExposureContext(
        daily_total_amount=cents_to_decimal(payload.exposure_override.daily_total_amount_cents),
        per_user_daily_count=payload.exposure_override.per_user_daily_count,
        per_user_daily_amount=cents_to_decimal(payload.exposure_override.per_user_daily_amount_cents),
        financial_total_amount_cents=payload.exposure_override.financial_total_amount_cents,
    )


def _extract_simulation_amount(payload: SimulationRequest) -> Decimal:
    if payload.action_type == "refund":
        if payload.refund is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="refund payload is required")
        return cents_to_decimal(payload.refund.refund_amount_cents)
    if payload.action_type == "credit_adjustment":
        if payload.credit is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="credit payload is required")
        return cents_to_decimal(payload.credit.credit_amount_cents)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported action_type")
