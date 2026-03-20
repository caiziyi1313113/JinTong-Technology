from datetime import datetime

from pydantic import BaseModel


class PositionCreate(BaseModel):
    stock_symbol: str
    quantity: float
    avg_price: float


class PositionClose(BaseModel):
    quantity: float | None = None
    price: float | None = None
    note: str | None = None


class PositionOut(BaseModel):
    id: int
    stock_symbol: str
    quantity: float
    avg_price: float
    status: str
    opened_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}
