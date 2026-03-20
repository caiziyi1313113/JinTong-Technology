from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.market_scope import market_from_symbol, normalize_symbol
from app.models.market import MarketData
from app.models.stock import Stock
from app.models.stock_quote import StockQuote
from app.models.stock_sentiment_daily import StockSentimentDaily
from app.models.stock_sentiment_item import StockSentimentItem
from app.sentiment.cleaning import repair_mojibake_text
from app.sentiment.domain import DailySentimentResult, SentimentScoredItem

logger = logging.getLogger(__name__)


def _item_rank(item: SentimentScoredItem) -> tuple[int, float, int, int, int, int]:
    """
    Ranking used when duplicate text hashes appear in one batch.
    Prefer item with newer publish time, richer text/title, and stronger source identifiers.
    """
    has_time = 1 if item.published_at else 0
    time_score = item.published_at.timestamp() if item.published_at else 0.0
    text_len = len((item.text or "").strip())
    title_len = len((item.title or "").strip()) if item.title else 0
    has_external_id = 1 if item.external_id else 0
    has_source_url = 1 if item.source_url else 0
    return has_time, time_score, text_len, title_len, has_external_id, has_source_url


def _identity_key(item: SentimentScoredItem, normalized_text: str) -> str:
    """
    Identity key for deduping:
    1) external_id (best for guba post_id)
    2) source_url
    3) published_at + title
    4) fallback to source_type + text
    """
    source_type = str(item.source_type or "").strip().lower()
    external_id = str(item.external_id or "").strip()
    if external_id:
        return f"{source_type}|eid:{external_id}"

    source_url = str(item.source_url or "").strip()
    if source_url:
        return f"{source_type}|url:{source_url}"

    published_at = item.published_at.isoformat() if item.published_at else ""
    title = str(item.title or "").strip()
    if published_at or title:
        return f"{source_type}|pt:{published_at}|title:{title}"

    return f"{source_type}|text:{normalized_text}"


def ensure_stock(db: Session, symbol: str) -> Stock:
    code = normalize_symbol(symbol)
    if not code:
        raise ValueError("Invalid symbol")
    stock = db.query(Stock).filter(Stock.symbol == code).first()
    if stock:
        return stock
    stock = Stock(symbol=code, name=code, market=market_from_symbol(code))
    db.add(stock)
    db.flush()
    return stock


def market_snapshot(db: Session, *, stock_id: int, trade_date: date) -> MarketData | None:
    return (
        db.query(MarketData)
        .filter(MarketData.stock_id == stock_id, MarketData.date == trade_date)
        .first()
    )


def recent_closes(db: Session, *, stock_id: int, trade_date: date, limit: int = 60) -> list[float]:
    rows = (
        db.query(MarketData.close)
        .filter(MarketData.stock_id == stock_id, MarketData.date <= trade_date)
        .order_by(MarketData.date.desc())
        .limit(max(1, limit))
        .all()
    )
    return [float(row[0]) for row in rows if row and row[0] is not None]


def recent_quote_metrics(
    db: Session,
    *,
    stock_id: int,
    trade_date: date,
    limit: int = 120,
) -> tuple[float | None, list[float], float | None, list[float]]:
    end_dt = datetime.combine(trade_date + timedelta(days=1), datetime.min.time())
    rows = (
        db.query(StockQuote.pe_dynamic, StockQuote.pb)
        .filter(StockQuote.stock_id == stock_id, StockQuote.quote_time < end_dt)
        .order_by(StockQuote.quote_time.desc())
        .limit(max(1, limit))
        .all()
    )
    pe_values = [float(row[0]) for row in rows if row and row[0] is not None and row[0] > 0]
    pb_values = [float(row[1]) for row in rows if row and row[1] is not None and row[1] > 0]
    latest_pe = pe_values[0] if pe_values else None
    latest_pb = pb_values[0] if pb_values else None
    return latest_pe, pe_values, latest_pb, pb_values


def list_recent_daily(
    db: Session,
    *,
    stock_id: int,
    days: int,
    end_date: date | None = None,
) -> list[StockSentimentDaily]:
    query = db.query(StockSentimentDaily).filter(StockSentimentDaily.stock_id == stock_id)
    if end_date is not None:
        query = query.filter(StockSentimentDaily.trade_date <= end_date)
    return (
        query.order_by(StockSentimentDaily.trade_date.desc())
        .limit(max(1, days))
        .all()
    )


def upsert_daily(db: Session, *, stock: Stock, payload: DailySentimentResult) -> StockSentimentDaily:
    row = (
        db.query(StockSentimentDaily)
        .filter(StockSentimentDaily.stock_id == stock.id, StockSentimentDaily.trade_date == payload.trade_date)
        .first()
    )
    if row is None:
        row = StockSentimentDaily(stock_id=stock.id, stock_symbol=stock.symbol, trade_date=payload.trade_date)
        db.add(row)

    row.stock_symbol = stock.symbol
    row.news_count = int(payload.news_count)
    row.guba_count = int(payload.guba_count)
    row.news_score_raw = float(payload.news_score_raw)
    row.news_score_norm = float(payload.news_score_norm)
    row.guba_score_raw = float(payload.guba_score_raw)
    row.guba_score_norm = float(payload.guba_score_norm)
    row.combined_score_raw = float(payload.combined_score_raw)
    row.combined_score_norm = float(payload.combined_score_norm)
    row.sentiment_label = payload.sentiment_label
    row.trend_deltas = [float(v) for v in payload.trend_deltas]
    row.trend_5d = float(payload.trend_5d) if payload.trend_5d is not None else None
    row.trend_signal = payload.trend_signal
    row.trend_conclusion = payload.trend_conclusion
    row.valuation_level = payload.valuation_level
    row.valuation_reason = payload.valuation_reason
    row.strategy_matrix_advice = payload.strategy_matrix_advice
    row.strategy_summary = payload.strategy_summary
    row.corr_with_next_return = (
        float(payload.corr_with_next_return) if payload.corr_with_next_return is not None else None
    )
    row.corr_sample_size = int(payload.corr_sample_size)
    row.reliability_level = payload.reliability_level
    row.open = float(payload.open) if payload.open is not None else None
    row.high = float(payload.high) if payload.high is not None else None
    row.low = float(payload.low) if payload.low is not None else None
    row.close = float(payload.close) if payload.close is not None else None
    row.volume = float(payload.volume) if payload.volume is not None else None
    row.extra = dict(payload.extra or {})

    db.flush()
    return row


def replace_items(
    db: Session,
    *,
    stock: Stock,
    daily: StockSentimentDaily | None,
    trade_date: date,
    items: Iterable[SentimentScoredItem],
) -> None:
    db.query(StockSentimentItem).filter(
        StockSentimentItem.stock_id == stock.id,
        StockSentimentItem.trade_date == trade_date,
    ).delete(synchronize_session=False)

    deduped: dict[str, tuple[SentimentScoredItem, str]] = {}
    total_items = 0

    for item in items:
        total_items += 1
        normalized_text = (item.text or "").strip()
        if not normalized_text:
            continue
        key = _identity_key(item, normalized_text)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = (item, normalized_text)
            continue
        if _item_rank(item) > _item_rank(existing[0]):
            deduped[key] = (item, normalized_text)

    dropped = total_items - len(deduped)
    if dropped > 0:
        logger.info(
            "sentiment item deduplicated | symbol=%s trade_date=%s total=%s kept=%s dropped=%s",
            stock.symbol,
            trade_date.isoformat(),
            total_items,
            len(deduped),
            dropped,
        )

    for identity_key, (item, normalized_text) in deduped.items():
        source_type = str(item.source_type)
        # Keep same-text different-post records by including identity in hash.
        text_hash = hashlib.sha256(f"{identity_key}|{normalized_text}".encode("utf-8")).hexdigest()
        row = StockSentimentItem(
            stock_id=stock.id,
            daily_id=daily.id if daily else None,
            stock_symbol=stock.symbol,
            trade_date=trade_date,
            source_type=source_type,
            external_id=item.external_id,
            source_url=item.source_url,
            title=item.title,
            text=normalized_text,
            text_hash=text_hash,
            published_at=item.published_at,
            label=item.label,
            positive_prob=float(item.positive_prob),
            neutral_prob=float(item.neutral_prob),
            negative_prob=float(item.negative_prob),
            score_raw=float(item.score_raw),
            score_norm=float(item.score_norm),
            extra=dict(item.extra or {}),
        )
        db.add(row)

    db.flush()


def list_items_by_source(
    db: Session,
    *,
    stock_id: int,
    trade_date: date,
    source_type: str,
    limit: int = 20,
) -> list[StockSentimentItem]:
    return (
        db.query(StockSentimentItem)
        .filter(
            StockSentimentItem.stock_id == stock_id,
            StockSentimentItem.trade_date == trade_date,
            StockSentimentItem.source_type == source_type,
        )
        .order_by(StockSentimentItem.score_norm.desc(), StockSentimentItem.created_at.desc())
        .limit(max(1, limit))
        .all()
    )


def daily_to_dict(row: StockSentimentDaily) -> dict:
    return {
        "trade_date": row.trade_date,
        "news_count": row.news_count,
        "guba_count": row.guba_count,
        "news_score_raw": row.news_score_raw,
        "news_score_norm": row.news_score_norm,
        "guba_score_raw": row.guba_score_raw,
        "guba_score_norm": row.guba_score_norm,
        "combined_score_raw": row.combined_score_raw,
        "combined_score_norm": row.combined_score_norm,
        "sentiment_label": row.sentiment_label,
        "trend_deltas": list(row.trend_deltas or []),
        "trend_5d": row.trend_5d,
        "trend_signal": row.trend_signal,
        "trend_conclusion": row.trend_conclusion,
        "valuation_level": row.valuation_level,
        "valuation_reason": row.valuation_reason,
        "strategy_matrix_advice": row.strategy_matrix_advice,
        "strategy_summary": row.strategy_summary,
        "corr_with_next_return": row.corr_with_next_return,
        "corr_sample_size": row.corr_sample_size,
        "reliability_level": row.reliability_level,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "extra": dict(row.extra or {}),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def item_to_dict(row: StockSentimentItem) -> dict:
    return {
        "source_type": row.source_type,
        "external_id": row.external_id,
        "source_url": row.source_url,
        "title": repair_mojibake_text(row.title) if row.title else None,
        "text": repair_mojibake_text(row.text),
        "label": row.label,
        "positive_prob": row.positive_prob,
        "neutral_prob": row.neutral_prob,
        "negative_prob": row.negative_prob,
        "score_raw": row.score_raw,
        "score_norm": row.score_norm,
        "published_at": row.published_at,
        "extra": dict(row.extra or {}),
    }
