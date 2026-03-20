from datetime import datetime

from pydantic import BaseModel


class PortfolioTradeCreate(BaseModel):
    stock_symbol: str
    side: str
    quantity: float
    price: float
    trade_time: datetime | None = None
    note: str | None = None


class PortfolioTradeOut(BaseModel):
    id: int
    stock_symbol: str
    side: str
    quantity: float
    price: float
    trade_time: datetime
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PortfolioTrackingClearOut(BaseModel):
    deleted_trades: int
    deleted_positions: int
    deleted_holdings: int
    deleted_trade_plans: int
    deleted_trade_signals: int
