from datetime import date, datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class DailySyncRequest(BaseModel):
    trade_date: date | None = None
    symbols: List[str] | None = None
    history_days: int = 90
    include_block_trade: bool = True
    include_news: bool = True
    include_macro: bool = True


class StaticSyncRequest(BaseModel):
    symbols: List[str]


class SyncLogOut(BaseModel):
    id: int
    job_type: str
    scope: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    detail: Dict[str, Any] = Field(default_factory=dict)
    error_message: str | None

    model_config = {"from_attributes": True}
