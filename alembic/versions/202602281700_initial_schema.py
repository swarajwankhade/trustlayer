"""initial schema

Revision ID: 202602281700
Revises:
Create Date: 2026-02-28 17:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "202602281700"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rules_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_policies_name_version"),
    )
    op.create_index("ix_policies_status", "policies", ["status"], unique=False)
    op.create_index("ix_policies_created_at", "policies", ["created_at"], unique=False)

    op.create_table(
        "decision_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("model_version", sa.String(length=255), nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("exposure_snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_decision_events_request_id", "decision_events", ["request_id"], unique=False)
    op.create_index("ix_decision_events_timestamp", "decision_events", ["timestamp"], unique=False)
    op.create_index("ix_decision_events_decision", "decision_events", ["decision"], unique=False)
    op.create_index("ix_decision_events_action_type", "decision_events", ["action_type"], unique=False)
    op.create_index(
        "ix_decision_events_policy_version",
        "decision_events",
        ["policy_id", "policy_version"],
        unique=False,
    )

    op.create_table(
        "kill_switch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_kill_switch_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO kill_switch (id, enabled, reason, updated_by)
            VALUES (1, false, 'initial state', 'system')
            """
        )
    )


def downgrade() -> None:
    op.drop_table("kill_switch")
    op.drop_index("ix_decision_events_policy_version", table_name="decision_events")
    op.drop_index("ix_decision_events_action_type", table_name="decision_events")
    op.drop_index("ix_decision_events_decision", table_name="decision_events")
    op.drop_index("ix_decision_events_timestamp", table_name="decision_events")
    op.drop_index("ix_decision_events_request_id", table_name="decision_events")
    op.drop_table("decision_events")
    op.drop_index("ix_policies_created_at", table_name="policies")
    op.drop_index("ix_policies_status", table_name="policies")
    op.drop_table("policies")
