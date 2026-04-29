from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="check_direction",
        ),
    )

    id = Column(Integer, primary_key=True)
    thread_id = Column(Integer, ForeignKey("supplier_threads.id"), nullable=False)
    gmail_message_id = Column(String, nullable=True, unique=True)
    direction = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    thread = relationship("SupplierThread", back_populates="messages")
