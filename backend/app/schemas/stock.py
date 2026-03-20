from pydantic import BaseModel


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: str
    sector: str | None = None


class StockOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    sector: str | None

    model_config = {"from_attributes": True}


class StockKlinePointOut(BaseModel):
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class StockKlineOut(BaseModel):
    symbol: str
    period: str
    items: list[StockKlinePointOut]
