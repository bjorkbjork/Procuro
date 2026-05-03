"""Add UNPROCESSABLE thread state

Revision ID: 412740f85c98
Revises: 775631f2aaa9
Create Date: 2026-05-02 15:46:12.027643

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "412740f85c98"
down_revision: Union[str, Sequence[str], None] = "775631f2aaa9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("check_state", "supplier_threads", type_="check")
    op.create_check_constraint(
        "check_state",
        "supplier_threads",
        "state IN ('NEW', 'OUTREACH_SENT', 'AWAITING_REPLY', 'SPEC_CHECK_PASS', "
        "'SPEC_CHECK_FAIL', 'NEGOTIATING', 'FINAL_PRICE_LOGGED', 'CLOSED', 'UNPROCESSABLE')",
    )


def downgrade() -> None:
    op.drop_constraint("check_state", "supplier_threads", type_="check")
    op.create_check_constraint(
        "check_state",
        "supplier_threads",
        "state IN ('NEW', 'OUTREACH_SENT', 'AWAITING_REPLY', 'SPEC_CHECK_PASS', "
        "'SPEC_CHECK_FAIL', 'NEGOTIATING', 'FINAL_PRICE_LOGGED', 'CLOSED')",
    )
