from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, UniqueConstraint, func

from app.db.database import Base
from app.db.models.enums import Platform

_platform_check = f"platform IN ({', '.join(repr(p.value) for p in Platform)})"


class Supplier(Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        CheckConstraint(_platform_check, name="check_platform"),
        UniqueConstraint("profile_url", name="uq_supplier_profile_url"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    profile_url = Column(String, nullable=False)
    business_type = Column(String, nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    contact_address = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
