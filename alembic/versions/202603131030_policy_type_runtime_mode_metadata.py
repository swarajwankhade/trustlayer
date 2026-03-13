"""add policy_type and runtime_mode metadata

Revision ID: 202603131030
Revises: 202603071900
Create Date: 2026-03-13 10:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202603131030"
down_revision = "202603071900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("policies", sa.Column("policy_type", sa.String(length=64), nullable=True))
    op.add_column("decision_events", sa.Column("policy_type", sa.String(length=64), nullable=True))
    op.add_column("decision_events", sa.Column("runtime_mode", sa.String(length=32), nullable=True))

    op.execute(sa.text("UPDATE policies SET policy_type = 'refund_credit_v1' WHERE policy_type IS NULL"))
    op.execute(
        sa.text("UPDATE decision_events SET policy_type = 'refund_credit_v1' WHERE policy_type IS NULL")
    )
    op.execute(
        sa.text(
            """
            UPDATE decision_events
            SET runtime_mode = CASE
                WHEN would_decision IS NOT NULL THEN 'observe_only'
                WHEN 'KILL_SWITCH_ENABLED' = ANY(reason_codes) THEN 'kill_switch'
                ELSE 'enforce'
            END
            WHERE runtime_mode IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("decision_events", "runtime_mode")
    op.drop_column("decision_events", "policy_type")
    op.drop_column("policies", "policy_type")
