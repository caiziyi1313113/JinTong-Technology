from datetime import date, datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class PostCloseReviewRequest(BaseModel):
    trade_date: date | None = None
    top_n: int = 20


class DailyRecapOut(BaseModel):
    id: int
    trade_date: date
    market_summary: str
    macro_summary: str
    top_movers: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class CandidateOut(BaseModel):
    id: int
    trade_date: date
    stock_symbol: str
    sentiment_score: float
    data_score: float
    total_score: float
    reasons: List[str] = Field(default_factory=list)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class PostCloseReviewOut(BaseModel):
    recap: DailyRecapOut
    candidates: List[CandidateOut]


class PreOpenScanRequest(BaseModel):
    scan_date: date | None = None
    top_n: int = 10


class ScanResultOut(BaseModel):
    id: int
    scan_date: date
    stock_symbol: str
    rank: int
    score: float
    action: str
    notes: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}
