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
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exposure_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
