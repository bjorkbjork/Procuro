from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import relationship

from app.db.database import Base


class Quote(Base):
    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True)
    thread_id = Column(Integer, ForeignKey("supplier_threads.id"), nullable=False)
    round_number = Column(Integer, nullable=False)
    price_usd = Column(Numeric(10, 2), nullable=False)
    moq = Column(Integer, nullable=True)
    lead_time = Column(String, nullable=True)
    received_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thread = relationship("SupplierThread", back_populates="quotes")
