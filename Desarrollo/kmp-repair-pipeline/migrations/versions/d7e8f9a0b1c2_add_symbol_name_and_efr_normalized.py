"""add_symbol_name_to_error_observations_and_efr_normalized_to_evaluation_metrics

Revision ID: d7e8f9a0b1c2
Revises: 1c03c4a3181a
Create Date: 2026-04-05 12:00:00.000000

Changes:
  - error_observations.symbol_name VARCHAR(255) NULLABLE
      Populated for API_BREAK_ERROR: the unresolved symbol extracted from
      "Unresolved reference: Foo" compiler messages.
  - evaluation_metrics.efr_normalized FLOAT NULLABLE
      Message-normalized EFR (dedup key omits line number) to avoid
      counting line-number shifts as fixes.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'd9f4e6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'error_observations',
        sa.Column('symbol_name', sa.String(255), nullable=True),
    )
    op.add_column(
        'evaluation_metrics',
        sa.Column('efr_normalized', sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('evaluation_metrics', 'efr_normalized')
    op.drop_column('error_observations', 'symbol_name')
