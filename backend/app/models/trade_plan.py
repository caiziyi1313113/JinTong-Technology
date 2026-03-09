from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class TradePlan(Base):
    __tablename__ = "trade_plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    position_id = Column(Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True)
    side = Column(String(16), nullable=False)
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    ladder_prices = Column(JSON, default=list, nullable=False)
    stop_loss_price = Column(Float, nullable=True)
    take_profit_price = Column(Float, nullable=True)
    trailing_stop_pct = Column(Float, nullable=True)
    reduce_ratio = Column(Float, default=0.0, nullable=False)
    suggested_shares = Column(Integer, default=0, nullable=False)
    hold_days = Column(String(32), nullable=True)
    valid_until = Column(DateTime, nullable=True)
    status = Column(String(32), default="active", nullable=False)
    rationale = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="trade_plans")
    stock = relationship("Stock", back_populates="trade_plans")
    position = relationship("Position", back_populates="trade_plans")
    trade_signals = relationship("TradeSignal", back_populates="trade_plan")
