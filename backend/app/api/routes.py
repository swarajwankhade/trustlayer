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
    KillSwitchResponse,
    KillSwitchUpdateRequest,
    PolicyResponse,
    RefundActionRequest,
    cents_to_decimal,
)
from app.db.session import get_db_session
from app.exposure.store import ExposureStore, get_exposure_store
from app.models import DecisionEvent, KillSwitch, Policy
from app.policies.schemas import PolicyRules

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
