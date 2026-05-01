from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.db.database import Base


class SourceProduct(Base):
    __tablename__ = "source_products"

    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False, unique=True)
    slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    specs = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
