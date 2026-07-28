"""add body_metrics.is_seed

Marks a weight row as remembered/estimated rather than measured, so the
weight-trend model can exclude it from fits. Backfills false for every
existing row — nothing is retroactively treated as a seed.

Revision ID: 9a1c4f7b2e08
Revises: 5725f5534780
Create Date: 2026-07-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c4f7b2e08'
down_revision: Union[str, Sequence[str], None] = '5725f5534780'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'body_metrics',
        sa.Column(
            'is_seed',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
        schema='app',
    )


def downgrade() -> None:
    op.drop_column('body_metrics', 'is_seed', schema='app')
