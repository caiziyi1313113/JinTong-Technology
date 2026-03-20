from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class StockSentimentItem(Base):
    __tablename__ = "stock_sentiment_item"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", "source_type", "text_hash", name="uq_stock_sentiment_item_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    daily_id = Column(Integer, ForeignKey("stock_sentiment_daily.id", ondelete="CASCADE"), nullable=True, index=True)
    stock_symbol = Column(String(32), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    source_type = Column(String(16), nullable=False, index=True)  # news | guba
    external_id = Column(String(64), nullable=True, index=True)
    source_url = Column(String(512), nullable=True)
    title = Column(String(512), nullable=True)
    text = Column(String, nullable=False)
    text_hash = Column(String(64), nullable=False, index=True)
    published_at = Column(DateTime, nullable=True)

    label = Column(String(32), nullable=False)
    positive_prob = Column(Float, nullable=False, default=0.0)
    neutral_prob = Column(Float, nullable=False, default=0.0)
    negative_prob = Column(Float, nullable=False, default=0.0)
    score_raw = Column(Float, nullable=False, default=0.0)
    score_norm = Column(Float, nullable=False, default=0.5)

    extra = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="sentiment_items")
    daily = relationship("StockSentimentDaily", back_populates="items")
