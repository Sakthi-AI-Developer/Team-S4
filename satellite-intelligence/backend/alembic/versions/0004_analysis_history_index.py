"""Index owner/status/time for paginated analysis history.

Revision ID: 0004_analysis_history_index
Revises: 0003_analysis_idempotency
"""
from alembic import op

revision = "0004_analysis_history_index"
down_revision = "0003_analysis_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_analyses_owner_status_created",
        "analyses",
        ["owner_id", "status", "created_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration intentionally has no destructive downgrade. "
        "Back up and migrate production data explicitly before schema removal."
    )
