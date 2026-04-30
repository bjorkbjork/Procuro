from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import JSONB

from app.db.database import Base


class KeyValue(Base):
    __tablename__ = "keyvalue"
    # human note here -- yes credentials stored in JSON is dumb
    # however it's credentials to a throwaway google account
    # and there is only outbound network, no inbound, so no risk
    key = Column(String, primary_key=True)
    value = Column(JSONB, nullable=False)
