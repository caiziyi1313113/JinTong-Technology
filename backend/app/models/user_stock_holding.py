from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class UserStockHolding(Base):
    __tablename__ = "user_stock_holdings"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_id", name="uq_user_stock_holding_user_stock"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_symbol = Column(String(32), nullable=False, index=True)
    quantity = Column(Float, nullable=False, default=0.0)
    avg_price = Column(Float, nullable=False, default=0.0)
    total_buy_amount = Column(Float, nullable=False, default=0.0)
    total_sell_amount = Column(Float, nullable=False, default=0.0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    user = relationship("User")
    stock = relationship("Stock")
