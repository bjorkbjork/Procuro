from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.db.database import Base

VALID_STATES = (
    "NEW",
    "OUTREACH_SENT",
    "AWAITING_REPLY",
    "SPEC_CHECK_PASS",
    "SPEC_CHECK_FAIL",
    "NEGOTIATING",
    "FINAL_PRICE_LOGGED",
    "CLOSED",
)


class SupplierThread(Base):
    __tablename__ = "supplier_threads"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({', '.join(repr(s) for s in VALID_STATES)})",
            name="check_state",
        ),
        UniqueConstraint("product_id", "supplier_id", name="uq_product_supplier"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    state = Column(String, nullable=False, default="NEW")
    gmail_thread_id = Column(String, nullable=True)
    respond_after = Column(DateTime(timezone=True), nullable=True)
    negotiation_rounds = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    product = relationship("Product")
    supplier = relationship("Supplier")
    quotes = relationship(
        "Quote", back_populates="thread", order_by="Quote.round_number"
    )
    messages = relationship(
        "Message", back_populates="thread", order_by="Message.sent_at"
    )
