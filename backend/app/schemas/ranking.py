from datetime import date, datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class RankingRunRequest(BaseModel):
    snapshot_date: date | None = None
    snapshot_type: str = "post_close"
    top_n: int = 30
    symbols: List[str] | None = None


class RankingItemOut(BaseModel):
    id: int
    stock_symbol: str
    rank: int
    total_score: float
    news_score: float
    stock_score: float
    macro_score: float
    financial_score: float
    fundamental_score: float
    data_drive_score: float
    emotion_drive_score: float
    conflict_signal: bool
    recommendation_action: str
    recommendation_confidence: float
    recommendation_summary: str | None
    expert_payload: Dict[str, Any] = Field(default_factory=dict)
    investment_payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class RankingSnapshotOut(BaseModel):
    id: int
    snapshot_date: date
    snapshot_type: str
    status: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    items: List[RankingItemOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RankingTaskOut(BaseModel):
    task_id: str
    snapshot_type: str
    snapshot_date: date
    top_n: int
    status: str
    stage: str
    message: str | None = None
    error: str | None = None
    snapshot_id: int | None = None
    created_at: datetime
    updated_at: datetime
