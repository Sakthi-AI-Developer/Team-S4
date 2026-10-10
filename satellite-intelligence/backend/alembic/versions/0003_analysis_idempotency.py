"""Add a nullable per-owner analysis idempotency key.

Revision ID: 0003_analysis_idempotency
Revises: 0002_artifact_ownership
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_analysis_idempotency"
down_revision = "0002_artifact_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("idempotency_scope", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "analyses",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "uq_analyses_idempotency_scope_key",
        "analyses",
        ["idempotency_scope", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration intentionally has no destructive downgrade. "
        "Back up and migrate production data explicitly before schema removal."
    )
