"""Initial schema — replace with your real tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20

Pattern: every migration MUST implement a working downgrade() so prod rollbacks
are mechanical, not improvised. The rollback runbook depends on this.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        "INSERT INTO app_meta (key, value) VALUES ('schema_version', '0001_initial')"
    )


def downgrade() -> None:
    op.drop_table("app_meta")
