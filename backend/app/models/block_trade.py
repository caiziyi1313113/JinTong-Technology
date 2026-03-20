from sqlalchemy import Column, Integer, String, Float, Date, DateTime, JSON, UniqueConstraint, func

from app.core.db import Base


class BlockTradeRecord(Base):
    __tablename__ = "block_trade_records"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "stock_symbol",
            "deal_price",
            "volume",
            "buyer_branch",
            "seller_branch",
            name="uq_block_trade_row",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_symbol = Column(String(32), nullable=False, index=True)
    stock_name = Column(String(255), nullable=True)
    change_pct = Column(Float, nullable=True)
    close_price = Column(Float, nullable=True)
    deal_price = Column(Float, nullable=True)
    premium_discount = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    amount_to_float_mkt = Column(Float, nullable=True)
    buyer_branch = Column(String(255), nullable=True)
    seller_branch = Column(String(255), nullable=True)
    source = Column(String(64), default="akshare", nullable=False)
    raw = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
