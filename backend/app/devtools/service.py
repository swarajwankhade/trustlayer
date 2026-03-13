from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.actions.service import ActionAuthorizationInput, authorize_action, get_or_init_kill_switch
from app.exposure.store import ExposureStore
from app.models import DecisionEvent, KillSwitch, Policy

DEMO_POLICY_RULES = {
    "per_action_max_amount": 10_000,
    "daily_total_cap_amount": 20_000,
    "per_user_daily_count_cap": 10,
    "per_user_daily_amount_cap": 20_000,
    "near_cap_escalation_ratio": 0.9,
}


@dataclass(frozen=True)
class BootstrapResult:
    created_kill_switch: bool
    created_policy: bool
    activated_policy: bool
    policy_id: str | None
    policy_version: int | None


@dataclass(frozen=True)
class ResetResult:
    decision_events_deleted: int
    policies_deleted: int
    redis_keys_deleted: int
    kill_switch_enabled: bool


@dataclass(frozen=True)
class GenerateDemoResult:
    generated_count: int
    request_ids: list[str]
    decisions: list[str]


def bootstrap_demo_data(
    db: Session,
    *,
    activate_policy: bool = True,
    policy_name: str = "demo-default-policy",
    policy_version: int = 1,
    created_by: str = "dev-bootstrap",
) -> BootstrapResult:
    created_kill_switch = db.get(KillSwitch, 1) is None
    kill_switch = get_or_init_kill_switch(db)
    if kill_switch.enabled:
        kill_switch.enabled = False
        kill_switch.observe_only = False
        kill_switch.reason = "bootstrap reset to safe default"
        kill_switch.updated_by = created_by
        db.add(kill_switch)
        db.commit()

    policy = db.scalar(
        select(Policy)
        .where(Policy.name == policy_name, Policy.version == policy_version)
        .order_by(Policy.created_at.desc())
        .limit(1)
    )
    created_policy = False

    if policy is None:
        policy = Policy(
            name=policy_name,
            version=policy_version,
            status="INACTIVE",
            rules_json=DEMO_POLICY_RULES,
            created_by=created_by,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        created_policy = True

    activated_policy = False
    if activate_policy and policy is not None and policy.status != "ACTIVE":
        db.execute(update(Policy).values(status="INACTIVE"))
        db.execute(update(Policy).where(Policy.id == policy.id).values(status="ACTIVE"))
        db.commit()
        db.refresh(policy)
        activated_policy = True

    return BootstrapResult(
        created_kill_switch=created_kill_switch,
        created_policy=created_policy,
        activated_policy=activated_policy,
        policy_id=str(policy.id) if policy is not None else None,
        policy_version=policy.version if policy is not None else None,
    )


def reset_dev_data(
    db: Session,
    *,
    redis_url: str,
    updated_by: str = "dev-reset",
) -> ResetResult:
    decision_events_deleted = db.execute(delete(DecisionEvent)).rowcount or 0
    policies_deleted = db.execute(delete(Policy)).rowcount or 0

    kill_switch = get_or_init_kill_switch(db)
    kill_switch.enabled = False
    kill_switch.observe_only = False
    kill_switch.reason = "reset-dev-data"
    kill_switch.updated_by = updated_by
    db.add(kill_switch)
    db.commit()

    redis_keys_deleted = _clear_redis_exposure(redis_url)

    return ResetResult(
        decision_events_deleted=decision_events_deleted,
        policies_deleted=policies_deleted,
        redis_keys_deleted=redis_keys_deleted,
        kill_switch_enabled=kill_switch.enabled,
    )


def _clear_redis_exposure(redis_url: str) -> int:
    try:
        client = Redis.from_url(redis_url, decode_responses=True)
        deleted_total = 0
        batch: list[str] = []
        for key in client.scan_iter(match="exposure:*"):
            batch.append(key)
            if len(batch) >= 500:
                deleted_total += client.delete(*batch)
                batch.clear()
        if batch:
            deleted_total += client.delete(*batch)
        return int(deleted_total)
    except RedisError:
        return 0


def generate_demo_decisions(
    db: Session,
    exposure_store: ExposureStore,
    *,
    model_version: str = "demo-v1",
) -> GenerateDemoResult:
    bootstrap_demo_data(db, activate_policy=True, created_by="demo-generate")
    kill_switch = get_or_init_kill_switch(db)
    if kill_switch.enabled or kill_switch.observe_only:
        kill_switch.enabled = False
        kill_switch.observe_only = False
        kill_switch.reason = "demo-generate reset controls"
        kill_switch.updated_by = "demo-generate"
        db.add(kill_switch)
        db.commit()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    actions = [
        {
            "action_type": "refund",
            "request_id": f"demo-{run_id}-refund-allow",
            "user_id": "demo-user-1",
            "amount_cents": 5_000,
            "payload_json": {
                "request_id": f"demo-{run_id}-refund-allow",
                "user_id": "demo-user-1",
                "ticket_id": "ticket-demo-1",
                "refund_amount_cents": 5_000,
                "currency": "USD",
                "model_version": model_version,
                "metadata": {"source": "demo-helper"},
            },
        },
        {
            "action_type": "credit_adjustment",
            "request_id": f"demo-{run_id}-credit-allow",
            "user_id": "demo-user-2",
            "amount_cents": 3_000,
            "payload_json": {
                "request_id": f"demo-{run_id}-credit-allow",
                "user_id": "demo-user-2",
                "ticket_id": "ticket-demo-2",
                "credit_amount_cents": 3_000,
                "currency": "USD",
                "credit_type": "courtesy",
                "model_version": model_version,
                "metadata": {"source": "demo-helper"},
            },
        },
        {
            "action_type": "refund",
            "request_id": f"demo-{run_id}-refund-block",
            "user_id": "demo-user-3",
            "amount_cents": 15_000,
            "payload_json": {
                "request_id": f"demo-{run_id}-refund-block",
                "user_id": "demo-user-3",
                "ticket_id": "ticket-demo-3",
                "refund_amount_cents": 15_000,
                "currency": "USD",
                "model_version": model_version,
                "metadata": {"source": "demo-helper"},
            },
        },
    ]

    decisions: list[str] = []
    request_ids: list[str] = []
    for action in actions:
        event = authorize_action(
            ActionAuthorizationInput(
                action_type=action["action_type"],
                request_id=action["request_id"],
                user_id=action["user_id"],
                amount=(Decimal(action["amount_cents"]) / Decimal("100")).quantize(Decimal("0.01")),
                model_version=model_version,
                payload_json=action["payload_json"],
            ),
            db=db,
            exposure_store=exposure_store,
        )
        decisions.append(event.decision)
        request_ids.append(action["request_id"])

    return GenerateDemoResult(
        generated_count=len(request_ids),
        request_ids=request_ids,
        decisions=decisions,
    )
