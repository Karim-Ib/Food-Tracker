"""rename food_source value

Revision ID: 5725f5534780
Revises: ee42c9dc1eb6
Create Date: 2026-06-13 14:26:11.528355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5725f5534780'
down_revision: Union[str, Sequence[str], None] = 'ee42c9dc1eb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename_enum_value(old: str, new: str) -> None:
    """Rename an app.food_source label, but only if `old` is actually present.

    The initial migration first created this label as a typo ('oystem') and was
    later corrected in place (commit 5d7441f). A database built before that fix
    therefore has 'oystem', while one built after already has 'system' — and
    both report the same alembic revision, so the revision alone can't tell them
    apart. Renaming unconditionally fails on the second kind with
    '"oystem" is not an existing enum label'. Guarding on the label makes this
    migration correct against either history.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'app'
                  AND t.typname = 'food_source'
                  AND e.enumlabel = '{old}'
            ) THEN
                ALTER TYPE app.food_source RENAME VALUE '{old}' TO '{new}';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    _rename_enum_value("oystem", "system")


def downgrade() -> None:
    _rename_enum_value("system", "oystem")