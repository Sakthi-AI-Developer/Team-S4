"""Create analysis, processing job, status history and artifact tables.

Revision ID: 0001_persistent_analysis
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_persistent_analysis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("analysis", sa.String(length=80), nullable=False),
        sa.Column("input_parameters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("analysis_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_parameters", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_processing_jobs_analysis_id", "processing_jobs", ["analysis_id"])
    op.create_table(
        "job_status_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_status_history_job_id", "job_status_history", ["job_id"])
    op.create_table(
        "analysis_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("analysis_id", sa.String(length=32), nullable=False),
        sa.Column("artifact_name", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("analysis_id", "artifact_name"),
    )
    op.create_index("ix_analysis_artifacts_analysis_id", "analysis_artifacts", ["analysis_id"])


def downgrade() -> None:
    raise RuntimeError(
        "This migration intentionally has no destructive downgrade. "
        "Back up and migrate production data explicitly before schema removal."
    )
