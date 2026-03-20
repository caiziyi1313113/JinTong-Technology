from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class StockQuote(Base):
    __tablename__ = "stock_quotes"
    __table_args__ = (
        UniqueConstraint("stock_id", "quote_time", name="uq_stock_quote_time"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    quote_time = Column(DateTime, nullable=False, index=True)
    latest_price = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=True)
    change_amount = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    turnover_rate = Column(Float, nullable=True)
    pe_dynamic = Column(Float, nullable=True)
    pb = Column(Float, nullable=True)
    raw = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="quotes")
