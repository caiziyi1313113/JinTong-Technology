from sqlalchemy import Column, Integer, String, DateTime, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    analyses = relationship("Analysis", back_populates="user")
    positions = relationship("Position", back_populates="user")
    trade_plans = relationship("TradePlan", back_populates="user")
    trade_signals = relationship("TradeSignal", back_populates="user")
    portfolio_trades = relationship("PortfolioTrade", back_populates="user")
