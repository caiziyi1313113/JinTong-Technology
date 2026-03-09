from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class TradePlanCreate(BaseModel):
    stock_symbol: str


class TradePlanOut(BaseModel):
    id: int
    stock_symbol: str
    side: str
    entry_low: float | None
    entry_high: float | None
    ladder_prices: List[float] = Field(default_factory=list)
    stop_loss_price: float | None
    take_profit_price: float | None
    trailing_stop_pct: float | None
    reduce_ratio: float
    suggested_shares: int
    hold_days: str | None
    status: str
    rationale: Dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeSignalCreate(BaseModel):
    trade_plan_id: int
    current_price: float | None = None


class TradeSignalOut(BaseModel):
    id: int
    stock_symbol: str
    side: str
    signal_type: str
    trigger_price: float | None
    suggested_shares: int
    confidence: float
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}
