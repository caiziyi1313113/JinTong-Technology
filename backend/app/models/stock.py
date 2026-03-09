from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from app.core.db import Base


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    market = Column(String(64), nullable=False)
    sector = Column(String(128), nullable=True)

    market_data = relationship("MarketData", back_populates="stock", cascade="all, delete-orphan")
    analyses = relationship("Analysis", back_populates="stock")
    positions = relationship("Position", back_populates="stock")
    trade_plans = relationship("TradePlan", back_populates="stock")
    trade_signals = relationship("TradeSignal", back_populates="stock")
