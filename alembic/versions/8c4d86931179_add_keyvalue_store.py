"""Add keyvalue store

Revision ID: 8c4d86931179
Revises: dd64606e590c
Create Date: 2026-04-30 20:04:54.353865

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8c4d86931179"
down_revision: Union[str, Sequence[str], None] = "dd64606e590c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "keyvalue",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("keyvalue")
