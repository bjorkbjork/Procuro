from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)

from app.db.database import Base

VALID_STAGES = ("s3_outreach", "s4_inbox", "s5_negotiation")
VALID_ACTIONS = ("send_inquiry", "read_messages", "send_reply")
VALID_OUTCOMES = ("deterministic", "agent_fallback", "failed")


class AutomationEvent(Base):
    __tablename__ = "automation_events"
    __table_args__ = (
        CheckConstraint(
            f"stage IN ({', '.join(repr(s) for s in VALID_STAGES)})",
            name="check_automation_stage",
        ),
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in VALID_ACTIONS)})",
            name="check_automation_action",
        ),
        CheckConstraint(
            f"outcome IN ({', '.join(repr(o) for o in VALID_OUTCOMES)})",
            name="check_automation_outcome",
        ),
    )

    id = Column(Integer, primary_key=True)
    stage = Column(String, nullable=False)
    action = Column(String, nullable=False)
    outcome = Column(String, nullable=False)
    supplier_thread_id = Column(
        Integer, ForeignKey("supplier_threads.id"), nullable=True
    )
    detail = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
