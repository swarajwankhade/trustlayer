from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import require_api_key
from app.api.schemas import RefundActionRequest, RefundActionResponse
from app.db.session import get_db_session
from app.models import DecisionEvent

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

    decision_event = DecisionEvent(
        action_type="refund",
        request_id=payload.request_id,
        decision="ALLOW",
        reason_codes=["PLACEHOLDER_ALLOW"],
        model_version=payload.model_version,
        policy_version=None,
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
