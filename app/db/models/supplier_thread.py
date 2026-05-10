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
    "UNPROCESSABLE",
)


class SupplierThread(Base):
    __tablename__ = "supplier_threads"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({', '.join(repr(s) for s in VALID_STATES)})",
            name="check_state",
        ),
        UniqueConstraint(
            "source_product_id",
            "supplier_product_id",
            name="uq_source_supplier_product",
        ),
    )

    id = Column(Integer, primary_key=True)
    source_product_id = Column(
        Integer, ForeignKey("source_products.id"), nullable=False
    )
    supplier_product_id = Column(
        Integer, ForeignKey("supplier_products.id"), nullable=False
    )
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    state = Column(String, nullable=False, default="NEW")
    channel = Column(String, nullable=False, default="email", server_default="email")
    gmail_thread_id = Column(String, nullable=True)
    platform_thread_url = Column(String, nullable=True)
    respond_after = Column(DateTime(timezone=True), nullable=True)
    negotiation_rounds = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    negotiation_failures = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_updated = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    source_product = relationship("SourceProduct")
    supplier_product = relationship("SupplierProduct")
    supplier = relationship("Supplier")
    quotes = relationship(
        "Quote", back_populates="thread", order_by="Quote.round_number"
    )
    messages = relationship(
        "Message", back_populates="thread", order_by="Message.sent_at"
    )
