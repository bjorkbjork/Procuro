"""Merge heads

Revision ID: 06e7fb7357a1
Revises: 412740f85c98, c7e2f1a3b5d8
Create Date: 2026-05-04 13:43:49.245278

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "06e7fb7357a1"
down_revision: Union[str, Sequence[str], None] = ("412740f85c98", "c7e2f1a3b5d8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
