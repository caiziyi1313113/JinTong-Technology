from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="SET NULL"), index=True, nullable=True)
    stock_symbol = Column(String(32), index=True, nullable=True)
    doc_type = Column(String(64), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(String, nullable=False)
    source = Column(String(255), nullable=True)
    published_at = Column(DateTime, nullable=True)
    doc_metadata = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock")
