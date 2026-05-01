from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db.database import Base
from app.db.models.enums import Platform

_platform_check = f"platform IN ({', '.join(repr(p.value) for p in Platform)})"


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    __table_args__ = (
        CheckConstraint(_platform_check, name="check_supplier_product_platform"),
    )

    id = Column(Integer, primary_key=True)
    source_product_id = Column(Integer, ForeignKey("source_products.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    platform = Column(String, nullable=False)
    product_url = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    specs = Column(JSONB, nullable=True)
    price = Column(String, nullable=True)
    moq = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source_product = relationship("SourceProduct")
    supplier = relationship("Supplier")
