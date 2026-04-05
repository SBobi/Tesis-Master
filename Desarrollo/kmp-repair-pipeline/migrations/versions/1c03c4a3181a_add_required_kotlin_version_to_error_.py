"""add_required_kotlin_version_to_error_observations

Revision ID: 1c03c4a3181a
Revises: a1b2c3d4e5f6
Create Date: 2026-04-05 07:02:50.339074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c03c4a3181a'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add required_kotlin_version to error_observations.

    Populated for KLIB_ABI_ERROR rows when the compiler w: warning line
    contains "produced by 'X.Y.Z' compiler" — the exact Kotlin version the
    repair agent must bump to.  Nullable; None for all other error types.
    """
    op.add_column(
        "error_observations",
        sa.Column("required_kotlin_version", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("error_observations", "required_kotlin_version")
