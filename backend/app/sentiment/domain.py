from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing_extensions import Literal  # type: ignore

SourceType = Literal["news", "guba"]


@dataclass
class SentimentSourceItem:
    source_type: SourceType
    text: str
    title: str | None = None
    external_id: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SentimentScoredItem(SentimentSourceItem):
    label: str = "neutral"
    positive_prob: float = 0.0
    neutral_prob: float = 0.0
    negative_prob: float = 0.0
    score_raw: float = 0.0
    score_norm: float = 0.5


@dataclass
class DailySentimentResult:
    symbol: str
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
    trend_5d: float | None
    trend_signal: str
    trend_conclusion: str | None
    valuation_level: str
    valuation_reason: str | None
    strategy_matrix_advice: str | None
    strategy_summary: str | None
    corr_with_next_return: float | None
    corr_sample_size: int
    reliability_level: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    extra: dict[str, Any] = field(default_factory=dict)
