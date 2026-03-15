"""harden decision_event schema version and add normalized input hash

Revision ID: 202603151100
Revises: 202603141500
Create Date: 2026-03-15 11:00:00
"""

import hashlib
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202603151100"
down_revision = "202603141500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_events", sa.Column("normalized_input_hash", sa.String(length=64), nullable=True))
    op.execute(sa.text("UPDATE decision_events SET event_schema_version = '1' WHERE event_schema_version IS NULL"))
    op.alter_column(
        "decision_events",
        "event_schema_version",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="1",
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT event_id, normalized_input_json
            FROM decision_events
            WHERE normalized_input_json IS NOT NULL
              AND normalized_input_hash IS NULL
            """
        )
    ).mappings()
    for row in rows:
        normalized_hash = _stable_json_sha256(row["normalized_input_json"])
        connection.execute(
            sa.text(
                """
                UPDATE decision_events
                SET normalized_input_hash = :normalized_input_hash
                WHERE event_id = :event_id
                """
            ),
            {"event_id": row["event_id"], "normalized_input_hash": normalized_hash},
        )


def downgrade() -> None:
    op.alter_column(
        "decision_events",
        "event_schema_version",
        existing_type=sa.String(length=32),
        nullable=True,
        server_default=None,
    )
    op.drop_column("decision_events", "normalized_input_hash")


def _stable_json_sha256(payload: object) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
