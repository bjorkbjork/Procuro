"""Add negotiation_failures column to supplier_threads

Revision ID: d1a2b3c4e5f6
Revises: b4f8a2c91d03
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d1a2b3c4e5f6"
down_revision: Union[str, Sequence[str], None] = "b4f8a2c91d03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supplier_threads",
        sa.Column(
            "negotiation_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("supplier_threads", "negotiation_failures")
