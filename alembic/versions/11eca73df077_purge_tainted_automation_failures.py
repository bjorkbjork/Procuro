"""purge tainted automation failures

Old tracking logic recorded intermediate retry attempts (login_required,
re-auth loops) as "failed" events even when the operation eventually
succeeded. Delete only the false failures — where the thread proves
the operation eventually went through.

Revision ID: 11eca73df077
Revises: 6739745bdf57
Create Date: 2026-05-11 00:57:39.371071

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "11eca73df077"
down_revision: Union[str, Sequence[str], None] = "6739745bdf57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # s3 outreach failures where the thread moved past NEW — outreach eventually succeeded
    op.execute("""
        DELETE FROM automation_events
        WHERE outcome = 'failed'
          AND stage = 's3_outreach'
          AND supplier_thread_id IN (
              SELECT id FROM supplier_threads WHERE state != 'NEW'
          )
    """)

    # s4 inbox failures where the thread moved past triage — messages eventually read
    op.execute("""
        DELETE FROM automation_events
        WHERE outcome = 'failed'
          AND stage = 's4_inbox'
          AND supplier_thread_id IN (
              SELECT id FROM supplier_threads
              WHERE state NOT IN ('NEW', 'OUTREACH_SENT', 'AWAITING_REPLY')
          )
    """)

    # s5 negotiation failures where the thread reached FINAL_PRICE_LOGGED or beyond
    op.execute("""
        DELETE FROM automation_events
        WHERE outcome = 'failed'
          AND stage = 's5_negotiation'
          AND supplier_thread_id IN (
              SELECT id FROM supplier_threads
              WHERE state IN ('FINAL_PRICE_LOGGED', 'CLOSED')
          )
    """)


def downgrade() -> None:
    # Data migration — deleted rows cannot be restored.
    pass
