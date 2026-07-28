"""add users.goal_weight_kg

Target body weight. Anchors the dynamically-generated target lines on the
/weight_model chart, replacing the hardcoded 100/95/90/86 set. Nullable —
a user without a goal simply gets a chart with no target lines.

Revision ID: b3d7c1a94f2e
Revises: 9a1c4f7b2e08
Create Date: 2026-07-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d7c1a94f2e'
down_revision: Union[str, Sequence[str], None] = '9a1c4f7b2e08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('goal_weight_kg', sa.Numeric(precision=5, scale=2), nullable=True),
        schema='app',
    )


def downgrade() -> None:
    op.drop_column('users', 'goal_weight_kg', schema='app')
