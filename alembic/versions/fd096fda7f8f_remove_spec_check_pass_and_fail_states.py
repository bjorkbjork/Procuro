"""remove spec check pass and fail states

Revision ID: fd096fda7f8f
Revises: d1a2b3c4e5f6
Create Date: 2026-05-11 14:08:05.638739

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "fd096fda7f8f"
down_revision: Union[str, Sequence[str], None] = "d1a2b3c4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Move stuck threads before tightening the constraint
    op.execute(
        "UPDATE supplier_threads SET state = 'AWAITING_REPLY' WHERE state = 'SPEC_CHECK_PASS'"
    )
    op.execute(
        "UPDATE supplier_threads SET state = 'CLOSED' WHERE state = 'SPEC_CHECK_FAIL'"
    )

    op.drop_constraint("check_state", "supplier_threads", type_="check")
    op.create_check_constraint(
        "check_state",
        "supplier_threads",
        "state IN ('NEW', 'OUTREACH_SENT', 'AWAITING_REPLY', "
        "'NEGOTIATING', 'FINAL_PRICE_LOGGED', 'CLOSED', 'UNPROCESSABLE')",
    )


def downgrade() -> None:
    op.drop_constraint("check_state", "supplier_threads", type_="check")
    op.create_check_constraint(
        "check_state",
        "supplier_threads",
        "state IN ('NEW', 'OUTREACH_SENT', 'AWAITING_REPLY', 'SPEC_CHECK_PASS', "
        "'SPEC_CHECK_FAIL', 'NEGOTIATING', 'FINAL_PRICE_LOGGED', 'CLOSED', 'UNPROCESSABLE')",
    )
