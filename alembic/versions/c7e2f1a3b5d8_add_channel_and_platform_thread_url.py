"""Add channel and platform_thread_url columns

Revision ID: c7e2f1a3b5d8
Revises: a3a4a63a0c35
Create Date: 2026-05-04 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7e2f1a3b5d8"
down_revision: Union[str, Sequence[str], None] = "a3a4a63a0c35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supplier_threads",
        sa.Column("channel", sa.String(), server_default="email", nullable=False),
    )
    op.add_column(
        "supplier_threads",
        sa.Column("platform_thread_url", sa.String(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("channel", sa.String(), server_default="email", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("messages", "channel")
    op.drop_column("supplier_threads", "platform_thread_url")
    op.drop_column("supplier_threads", "channel")
