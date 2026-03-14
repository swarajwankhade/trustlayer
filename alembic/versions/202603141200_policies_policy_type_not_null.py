"""make policies.policy_type non-null

Revision ID: 202603141200
Revises: 202603131030
Create Date: 2026-03-14 12:00:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202603141200"
down_revision = "202603131030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE policies SET policy_type = 'refund_credit_v1' WHERE policy_type IS NULL"))
    op.alter_column("policies", "policy_type", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    op.alter_column("policies", "policy_type", existing_type=sa.String(length=64), nullable=True)
