from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class StockSentimentDaily(Base):
    __tablename__ = "stock_sentiment_daily"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_stock_sentiment_daily_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_symbol = Column(String(32), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    news_count = Column(Integer, nullable=False, default=0)
    guba_count = Column(Integer, nullable=False, default=0)

    news_score_raw = Column(Float, nullable=False, default=0.0)
    news_score_norm = Column(Float, nullable=False, default=0.5)
    guba_score_raw = Column(Float, nullable=False, default=0.0)
    guba_score_norm = Column(Float, nullable=False, default=0.5)
    combined_score_raw = Column(Float, nullable=False, default=0.0)
    combined_score_norm = Column(Float, nullable=False, default=0.5)
    sentiment_label = Column(String(16), nullable=False, default="中性")

    trend_deltas = Column(JSON, default=list, nullable=False)
    trend_5d = Column(Float, nullable=True)
    trend_signal = Column(String(32), nullable=False, default="none")
    trend_conclusion = Column(String, nullable=True)

    valuation_level = Column(String(8), nullable=False, default="低")
    valuation_reason = Column(String, nullable=True)
    strategy_matrix_advice = Column(String, nullable=True)
    strategy_summary = Column(String, nullable=True)

    corr_with_next_return = Column(Float, nullable=True)
    corr_sample_size = Column(Integer, nullable=False, default=0)
    reliability_level = Column(String(16), nullable=False, default="数据不足")

    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)

    extra = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="sentiment_daily")
    items = relationship("StockSentimentItem", back_populates="daily", cascade="all, delete-orphan")
