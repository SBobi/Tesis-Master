"""add pipeline jobs and status transitions

Revision ID: d9f4e6a7b8c9
Revises: 1c03c4a3181a
Create Date: 2026-04-05 13:30:00.000000

Adds:
- pipeline_jobs: async orchestration/audit metadata for web-triggered runs
- case_status_transitions: immutable case status transition history
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d9f4e6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "1c03c4a3181a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repair_case_id", sa.String(length=36), nullable=False),
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("start_from_stage", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rq_job_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("command_preview", sa.Text(), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("effective_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("current_stage", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("log_path", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_job_id"], ["pipeline_jobs.id"]),
        sa.ForeignKeyConstraint(["repair_case_id"], ["repair_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "case_status_transitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repair_case_id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_job_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("from_status", sa.String(length=64), nullable=True),
        sa.Column("to_status", sa.String(length=64), nullable=True),
        sa.Column("transition_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_job_id"], ["pipeline_jobs.id"]),
        sa.ForeignKeyConstraint(["repair_case_id"], ["repair_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("case_status_transitions")
    op.drop_table("pipeline_jobs")
