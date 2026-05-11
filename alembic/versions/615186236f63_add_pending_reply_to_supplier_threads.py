"""add pending reply to supplier threads

Revision ID: 615186236f63
Revises: fd096fda7f8f
Create Date: 2026-05-11 16:58:27.900053

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "615186236f63"
down_revision: Union[str, Sequence[str], None] = "fd096fda7f8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supplier_threads",
        sa.Column("pending_reply", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("supplier_threads", "pending_reply")
