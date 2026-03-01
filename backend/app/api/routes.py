from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.api.schemas import RefundActionRequest, RefundActionResponse
from app.db.session import get_db_session
from app.models import DecisionEvent
from app.policies.engine import evaluate_refund
from app.policies.schemas import ExposureContext
from app.policies.service import load_active_policy

router = APIRouter()
v1_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


@router.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@v1_router.post("/actions/refund", response_model=RefundActionResponse)
def create_refund_action(
    payload: RefundActionRequest,
    db: Session = Depends(get_db_session),
) -> RefundActionResponse:
    existing_event = db.scalar(select(DecisionEvent).where(DecisionEvent.request_id == payload.request_id))
    if existing_event is not None:
        return _build_refund_response(existing_event)

    active_policy = load_active_policy(db)
    decision, reason_codes, _risk_metrics = evaluate_refund(
        action=payload,
        exposure_context=ExposureContext(),
        policy=active_policy.rules,
    )

    decision_event = DecisionEvent(
        action_type="refund",
        request_id=payload.request_id,
        decision=decision,
        reason_codes=active_policy.base_reason_codes + reason_codes,
        model_version=payload.model_version,
        policy_id=active_policy.policy_id,
        policy_version=active_policy.policy_version,
        exposure_snapshot_json={},
        action_payload_json=_serialize_payload(payload),
    )
    db.add(decision_event)
    db.commit()
    db.refresh(decision_event)

    return _build_refund_response(decision_event)


def _build_refund_response(event: DecisionEvent) -> RefundActionResponse:
    return RefundActionResponse(
        request_id=event.request_id,
        decision=event.decision,
        reason_codes=event.reason_codes,
        policy_version=event.policy_version,
        model_version=event.model_version,
    )


def _serialize_payload(payload: RefundActionRequest) -> dict[str, Any]:
    return payload.model_dump(mode="json")
