"""add attachments column to messages

Store PDF attachment metadata (filename, mime_type, size, attachment_id,
gmail_message_id) as JSONB so the negotiation agent can fetch and read
supplier-attached price lists.

Revision ID: b4f8a2c91d03
Revises: 11eca73df077
Create Date: 2026-05-11 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "b4f8a2c91d03"
down_revision: Union[str, Sequence[str], None] = "11eca73df077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("attachments", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "attachments")
