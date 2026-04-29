from sqlalchemy import CheckConstraint, Column, DateTime, Integer, String, func

from app.db.database import Base


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('alibaba', 'globalsources')",
            name="check_platform",
        ),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    profile_url = Column(String, nullable=False)
    contact_address = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
