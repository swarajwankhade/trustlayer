"""observe-only mode fields

Revision ID: 202603071900
Revises: 202602281700
Create Date: 2026-03-07 19:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "202603071900"
down_revision = "202602281700"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kill_switch",
        sa.Column("observe_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.alter_column("kill_switch", "observe_only", server_default=None)

    op.add_column("decision_events", sa.Column("would_decision", sa.String(length=32), nullable=True))
    op.add_column(
        "decision_events",
        sa.Column("would_reason_codes", postgresql.ARRAY(sa.String(length=64)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("decision_events", "would_reason_codes")
    op.drop_column("decision_events", "would_decision")
    op.drop_column("kill_switch", "observe_only")
