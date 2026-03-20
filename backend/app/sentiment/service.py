from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta
import logging

from sqlalchemy.orm import Session

from app.core.market_scope import normalize_symbol
from app.models.stock import Stock
from app.sentiment.analytics import (
    combine_channel_scores,
    compute_trend,
    corr_with_next_day_return,
    mean_score,
    raw_to_norm,
    sentiment_label,
    strategy_matrix_advice,
    strategy_summary,
    valuation_level_from_signals,
)
from app.sentiment.domain import DailySentimentResult, SentimentScoredItem
from app.sentiment.guba_crawler import fetch_guba_posts
from app.sentiment.inference import DualModelSentimentScorer
from app.sentiment.news_fetcher import fetch_akshare_company_news
from app.sentiment.repository import (
    daily_to_dict,
    ensure_stock,
    item_to_dict,
    list_items_by_source,
    list_recent_daily,
    market_snapshot,
    recent_closes,
    recent_quote_metrics,
    replace_items,
    upsert_daily,
)

logger = logging.getLogger(__name__)


class StockSentimentService:
    def __init__(self) -> None:
        self._scorer = DualModelSentimentScorer()

    def compute_for_date(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        max_pages: int = 5,
        max_news: int = 80,
        max_guba: int = 120,
        persist: bool = True,
        recent_days: int = 30,
        detail_limit: int = 20,
    ) -> dict:
        code = normalize_symbol(symbol)
        if not code:
            raise ValueError("Invalid symbol")

        stock = ensure_stock(db, code)
        logger.info(
            "sentiment compute start | symbol=%s trade_date=%s max_pages=%s max_news=%s max_guba=%s persist=%s",
            code,
            trade_date.isoformat(),
            max_pages,
            max_news,
            max_guba,
            persist,
        )

        news_items = fetch_akshare_company_news(code, target_date=trade_date, max_items=max_news)
        guba_items = fetch_guba_posts(code, target_date=trade_date, max_pages=max_pages, max_items=max_guba)

        scored_news = self._scorer.score_news(news_items) if news_items else []
        scored_guba = self._scorer.score_guba(guba_items) if guba_items else []
        all_items: list[SentimentScoredItem] = [*scored_news, *scored_guba]

        news_raw = mean_score([item.score_raw for item in scored_news])
        guba_raw = mean_score([item.score_raw for item in scored_guba])
        news_norm = raw_to_norm(news_raw)
        guba_norm = raw_to_norm(guba_raw)

        combined_raw = combine_channel_scores(
            news_raw=news_raw,
            guba_raw=guba_raw,
            news_count=len(scored_news),
            guba_count=len(scored_guba),
        )
        combined_norm = raw_to_norm(combined_raw)
        sentiment_state = sentiment_label(combined_norm)

        history_before = list_recent_daily(
            db,
            stock_id=stock.id,
            days=120,
            end_date=trade_date - timedelta(days=1),
        )
        score_series_newest = [combined_norm] + [float(row.combined_score_norm) for row in history_before]
        trend = compute_trend(score_series_newest)

        market_row = market_snapshot(db, stock_id=stock.id, trade_date=trade_date)
        close_values = recent_closes(db, stock_id=stock.id, trade_date=trade_date, limit=60)
        latest_pe, pe_values, latest_pb, pb_values = recent_quote_metrics(
            db,
            stock_id=stock.id,
            trade_date=trade_date,
            limit=120,
        )
        valuation_level, valuation_reason = valuation_level_from_signals(
            latest_close=float(market_row.close) if market_row else None,
            recent_closes=close_values,
            latest_pb=latest_pb,
            recent_pbs=pb_values,
            latest_pe=latest_pe,
            recent_pes=pe_values,
        )
        matrix_advice = strategy_matrix_advice(valuation_level, sentiment_state)
        summary_text = strategy_summary(valuation_level, sentiment_state, matrix_advice, trend.conclusion)

        corr_rows = list_recent_daily(
            db,
            stock_id=stock.id,
            days=240,
            end_date=trade_date,
        )
        corr_map = {
            row.trade_date: (float(row.combined_score_norm), float(row.close) if row.close is not None else None)
            for row in corr_rows
        }
        corr_map[trade_date] = (
            float(combined_norm),
            float(market_row.close) if market_row and market_row.close is not None else None,
        )
        corr_series = [
            (d, score, close)
            for d, (score, close) in sorted(corr_map.items(), key=lambda pair: pair[0])
        ]
        corr_value, corr_sample, corr_meta = corr_with_next_day_return(corr_series)
        agreement_rate = corr_meta.get("agreement_rate")
        if isinstance(agreement_rate, (int, float)):
            corr_level = f"{float(agreement_rate) * 100:.2f}%"
        else:
            corr_level = "-"

        daily_result = DailySentimentResult(
            symbol=code,
            trade_date=trade_date,
            news_count=len(scored_news),
            guba_count=len(scored_guba),
            news_score_raw=float(news_raw),
            news_score_norm=float(news_norm),
            guba_score_raw=float(guba_raw),
            guba_score_norm=float(guba_norm),
            combined_score_raw=float(combined_raw),
            combined_score_norm=float(combined_norm),
            sentiment_label=sentiment_state,
            trend_deltas=[float(v) for v in trend.deltas],
            trend_5d=float(trend.trend_5d) if trend.trend_5d is not None else None,
            trend_signal=trend.signal,
            trend_conclusion=trend.conclusion,
            valuation_level=valuation_level,
            valuation_reason=valuation_reason,
            strategy_matrix_advice=matrix_advice,
            strategy_summary=summary_text,
            corr_with_next_return=float(corr_value) if corr_value is not None else None,
            corr_sample_size=int(corr_sample),
            reliability_level=corr_level,
            open=float(market_row.open) if market_row else None,
            high=float(market_row.high) if market_row else None,
            low=float(market_row.low) if market_row else None,
            close=float(market_row.close) if market_row else None,
            volume=float(market_row.volume) if market_row else None,
            extra={
                "compute_time": datetime.utcnow().isoformat(),
                "news_model": self._scorer._news_model_name,
                "guba_model": self._scorer._guba_model_name,
                "corr_metric": corr_meta,
                "inputs": {
                    "news": len(news_items),
                    "guba": len(guba_items),
                },
            },
        )

        if persist:
            daily_row = upsert_daily(db, stock=stock, payload=daily_result)
            replace_items(db, stock=stock, daily=daily_row, trade_date=trade_date, items=all_items)
            db.commit()
            db.refresh(daily_row)
            latest_dict = daily_to_dict(daily_row)
            recent_rows = list_recent_daily(db, stock_id=stock.id, days=recent_days)
            series = [daily_to_dict(row) for row in recent_rows]
            news_rows = list_items_by_source(
                db,
                stock_id=stock.id,
                trade_date=trade_date,
                source_type="news",
                limit=detail_limit,
            )
            guba_rows = list_items_by_source(
                db,
                stock_id=stock.id,
                trade_date=trade_date,
                source_type="guba",
                limit=detail_limit,
            )
            news_out = [item_to_dict(row) for row in news_rows]
            guba_out = [item_to_dict(row) for row in guba_rows]
        else:
            latest_dict = asdict(daily_result)
            latest_dict["created_at"] = None
            latest_dict["updated_at"] = None

            series = [latest_dict]
            for row in history_before[: max(0, recent_days - 1)]:
                series.append(daily_to_dict(row))
            news_out = [self._item_to_response(item) for item in scored_news[:detail_limit]]
            guba_out = [self._item_to_response(item) for item in scored_guba[:detail_limit]]

        return {
            "symbol": code,
            "trade_date": trade_date,
            "latest": latest_dict,
            "recent_series": series,
            "news_items": news_out,
            "guba_items": guba_out,
        }

    def get_latest(
        self,
        db: Session,
        *,
        symbol: str,
        recent_days: int = 30,
        detail_limit: int = 20,
    ) -> dict | None:
        code = normalize_symbol(symbol)
        if not code:
            return None
        stock = db.query(Stock).filter(Stock.symbol == code).first()
        if not stock:
            return None
        rows = list_recent_daily(db, stock_id=stock.id, days=recent_days)
        if not rows:
            return None
        latest = rows[0]
        news_rows = list_items_by_source(
            db,
            stock_id=stock.id,
            trade_date=latest.trade_date,
            source_type="news",
            limit=detail_limit,
        )
        guba_rows = list_items_by_source(
            db,
            stock_id=stock.id,
            trade_date=latest.trade_date,
            source_type="guba",
            limit=detail_limit,
        )
        return {
            "symbol": code,
            "trade_date": latest.trade_date,
            "latest": daily_to_dict(latest),
            "recent_series": [daily_to_dict(row) for row in rows],
            "news_items": [item_to_dict(row) for row in news_rows],
            "guba_items": [item_to_dict(row) for row in guba_rows],
        }

    @staticmethod
    def _item_to_response(item: SentimentScoredItem) -> dict:
        data = asdict(item)
        return {
            "source_type": data["source_type"],
            "external_id": data.get("external_id"),
            "source_url": data.get("source_url"),
            "title": data.get("title"),
            "text": data["text"],
            "label": data["label"],
            "positive_prob": data["positive_prob"],
            "neutral_prob": data["neutral_prob"],
            "negative_prob": data["negative_prob"],
            "score_raw": data["score_raw"],
            "score_norm": data["score_norm"],
            "published_at": data.get("published_at"),
            "extra": data.get("extra") or {},
        }


stock_sentiment_service = StockSentimentService()
