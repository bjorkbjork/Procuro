from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    source_url = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    specs = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @hybrid_property
    def source_slug(self) -> str:
        return self.source_url.rstrip("/").split("/")[-1]
