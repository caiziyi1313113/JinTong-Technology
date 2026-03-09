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
