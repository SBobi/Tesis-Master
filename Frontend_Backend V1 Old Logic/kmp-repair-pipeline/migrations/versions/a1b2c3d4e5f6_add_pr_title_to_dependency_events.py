"""add pr_title to dependency_events

Revision ID: a1b2c3d4e5f6
Revises: c4beede9862d
Create Date: 2026-04-05 10:00:00.000000

Adds a nullable `pr_title` column to `dependency_events` to store the
Dependabot (or manual) PR title.  This is used in repair context prompts
so the RepairAgent sees the human-readable summary of what was updated
(e.g. "Bump ktor from 3.1.3 to 3.4.1").
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c4beede9862d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'dependency_events',
        sa.Column('pr_title', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('dependency_events', 'pr_title')
