"""Add artifact ownership and storage metadata without removing existing rows.

Revision ID: 0002_artifact_ownership
Revises: 0001_persistent_analysis
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_artifact_ownership"
down_revision = "0001_persistent_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("owner_id", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_analyses_owner_id", "analyses", ["owner_id"])

    op.add_column(
        "analysis_artifacts",
        sa.Column("bucket_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "analysis_artifacts",
        sa.Column(
            "artifact_type",
            sa.String(length=40),
            nullable=False,
            server_default="file",
        ),
    )
    op.add_column(
        "analysis_artifacts",
        sa.Column("job_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "analysis_artifacts",
        sa.Column(
            "artifact_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    with op.batch_alter_table("analysis_artifacts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch_op.create_foreign_key(
            "fk_analysis_artifacts_job_id_processing_jobs",
            "processing_jobs",
            ["job_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_analysis_artifacts_job_id", "analysis_artifacts", ["job_id"])
    op.create_index("ix_analysis_artifacts_created_at", "analysis_artifacts", ["created_at"])
    op.create_index(
        "uq_analysis_artifacts_bucket_object_key",
        "analysis_artifacts",
        ["bucket_name", "object_key"],
        unique=True,
    )
def downgrade() -> None:
    raise RuntimeError(
        "This migration intentionally has no destructive downgrade. "
        "Back up and migrate production data explicitly before schema removal."
    )
