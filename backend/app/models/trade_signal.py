from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class TradeSignal(Base):
    __tablename__ = "trade_signals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    trade_plan_id = Column(Integer, ForeignKey("trade_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    side = Column(String(16), nullable=False)
    signal_type = Column(String(32), nullable=False)
    trigger_price = Column(Float, nullable=True)
    suggested_shares = Column(Integer, default=0, nullable=False)
    confidence = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="trade_signals")
    stock = relationship("Stock", back_populates="trade_signals")
    trade_plan = relationship("TradePlan", back_populates="trade_signals")
