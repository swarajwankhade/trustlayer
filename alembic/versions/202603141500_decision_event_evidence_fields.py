"""add decision_event evidence metadata fields

Revision ID: 202603141500
Revises: 202603141200
Create Date: 2026-03-14 15:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "202603141500"
down_revision = "202603141200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_events", sa.Column("event_schema_version", sa.String(length=32), nullable=True))
    op.add_column(
        "decision_events",
        sa.Column("normalized_input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(sa.text("UPDATE decision_events SET event_schema_version = '1' WHERE event_schema_version IS NULL"))


def downgrade() -> None:
    op.drop_column("decision_events", "normalized_input_json")
    op.drop_column("decision_events", "event_schema_version")
