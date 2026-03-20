from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, Field


class SentimentComputeRequest(BaseModel):
    trade_date: date | None = None
    max_pages: int = Field(default=5, ge=1, le=30)
    max_news: int = Field(default=80, ge=1, le=300)
    max_guba: int = Field(default=120, ge=1, le=600)
    persist: bool = True


class SentimentItemOut(BaseModel):
    source_type: str
    external_id: str | None = None
    source_url: str | None = None
    title: str | None = None
    text: str
    label: str
    positive_prob: float
    neutral_prob: float
    negative_prob: float
    score_raw: float
    score_norm: float
    published_at: datetime | None = None
    extra: dict = Field(default_factory=dict)


class SentimentDailyOut(BaseModel):
    trade_date: date
    news_count: int
    guba_count: int
    news_score_raw: float
    news_score_norm: float
    guba_score_raw: float
    guba_score_norm: float
    combined_score_raw: float
    combined_score_norm: float
    sentiment_label: str
    trend_deltas: list[float]
    trend_5d: float | None = None
    trend_signal: str
    trend_conclusion: str | None = None
    valuation_level: str
    valuation_reason: str | None = None
    strategy_matrix_advice: str | None = None
    strategy_summary: str | None = None
    corr_with_next_return: float | None = None
    corr_sample_size: int
    reliability_level: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    extra: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SentimentResultOut(BaseModel):
    symbol: str
    trade_date: date
    latest: SentimentDailyOut
    recent_series: list[SentimentDailyOut]
    news_items: list[SentimentItemOut]
    guba_items: list[SentimentItemOut]
