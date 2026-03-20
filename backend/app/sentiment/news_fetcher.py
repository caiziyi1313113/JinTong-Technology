from __future__ import annotations

from datetime import date, datetime
import logging
from typing import Any

from app.core.market_scope import normalize_symbol
from app.sentiment.cleaning import clean_sentiment_text
from app.sentiment.domain import SentimentSourceItem

logger = logging.getLogger(__name__)


def _parse_datetime(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def fetch_akshare_company_news(
    symbol: str,
    *,
    target_date: date | None,
    max_items: int = 80,
) -> list[SentimentSourceItem]:
    """
    Fetch company news from akshare.stock_news_em and normalize records.
    """
    code = normalize_symbol(symbol)
    if not code:
        return []

    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        raise RuntimeError("akshare is required for news sentiment collection") from exc

    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as exc:
        logger.warning("akshare stock_news_em failed | symbol=%s error=%s", code, exc)
        return []

    if df is None or df.empty:
        return []

    seen: set[str] = set()
    results: list[SentimentSourceItem] = []
    for _, row in df.head(max(1, max_items * 3)).iterrows():
        title = str(row.get("新闻标题", "")).strip()
        content = str(row.get("新闻内容", "")).strip()
        published_at = _parse_datetime(row.get("发布时间"))
        if target_date and (published_at is None or published_at.date() != target_date):
            continue

        merged = clean_sentiment_text(f"{title}。{content}", max_chars=320, min_chars=5)
        if not merged:
            continue

        dedupe_key = f"{title}|{published_at.isoformat() if published_at else ''}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append(
            SentimentSourceItem(
                source_type="news",
                external_id=None,
                source_url=str(row.get("新闻链接", "")).strip() or None,
                title=title or None,
                text=merged,
                published_at=published_at,
                extra={
                    "source": str(row.get("文章来源", "")).strip() or "akshare-stock-news",
                    "keyword": row.get("关键词"),
                },
            )
        )

        if len(results) >= max(1, max_items):
            break

    return results
