import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionEvent(Base):
    __tablename__ = "decision_events"
    __table_args__ = (
        Index("ix_decision_events_timestamp", "timestamp"),
        Index("ix_decision_events_policy_version", "policy_id", "policy_version"),
        Index("uq_decision_events_request_id", "request_id", unique=True),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    would_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    would_reason_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, server_default="1")
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exposure_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    normalized_input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    normalized_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
