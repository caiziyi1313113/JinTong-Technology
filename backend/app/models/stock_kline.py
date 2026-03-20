from sqlalchemy import Column, Integer, Float, Date, String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class StockKline(Base):
    __tablename__ = "stock_klines"
    __table_args__ = (
        UniqueConstraint("stock_id", "period", "trade_date", name="uq_stock_kline_period_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    period = Column(String(16), nullable=False, index=True)  # daily/weekly/monthly
    trade_date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    amplitude = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)
    change_amount = Column(Float, nullable=True)
    turnover_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="klines")
