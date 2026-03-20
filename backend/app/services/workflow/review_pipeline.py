from __future__ import annotations

from datetime import date
import logging
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.market_scope import TARGET_MARKET, filter_target_symbols, normalize_symbol
from app.models.ranking_item import RankingItem
from app.models.ranking_snapshot import RankingSnapshot
from app.models.stock import Stock
from app.models.stock_quote import StockQuote
from app.services.data_ingest import akshare_service
from app.services.experts_v2 import expert_orchestrator
from app.services.experts_v2.postprocess import sanitize_report_for_storage


class RankingPipelineError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    return normalize_symbol(symbol)


def _resolve_universe(db: Session, symbols: Iterable[str] | None, top_n: int) -> list[Stock]:
    limit_size = max(top_n * 3, settings.max_ranking_symbols)

    if symbols:
        normalized = set(filter_target_symbols(symbols))
        rows = (
            db.query(Stock)
            .filter(Stock.symbol.in_(list(normalized)), Stock.market == TARGET_MARKET)
            .order_by(Stock.symbol)
            .all()
        )
        return rows

    # Best-effort: if stock universe is too small, refresh it once.
    stock_count = db.query(func.count(Stock.id)).filter(Stock.market == TARGET_MARKET).scalar() or 0
    if stock_count < max(top_n, 50):
        try:
            akshare_service.sync_stock_universe(db)
        except Exception:
            pass

    latest_quote_time = db.query(func.max(StockQuote.quote_time)).scalar()
    if latest_quote_time:
        quote_rows = (
            db.query(StockQuote)
            .join(Stock, Stock.id == StockQuote.stock_id)
            .filter(Stock.market == TARGET_MARKET)
            .filter(StockQuote.quote_time == latest_quote_time)
            .order_by(StockQuote.amount.desc().nullslast())
            .limit(limit_size)
            .all()
        )
        stock_ids = [row.stock_id for row in quote_rows]
        # If this timestamp only contains sparse symbols, fallback to stock universe.
        if len(stock_ids) >= top_n:
            stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
            stock_map = {stock.id: stock for stock in stocks}
            ordered = [stock_map[sid] for sid in stock_ids if sid in stock_map]
            return ordered

    return (
        db.query(Stock)
        .filter(Stock.market == TARGET_MARKET)
        .order_by(Stock.symbol)
        .limit(limit_size)
        .all()
    )


def _snapshot_summary(rows: list[dict]) -> dict:
    buy = sum(1 for row in rows if row["aggregate"]["recommendation_action"] == "buy")
    hold = sum(1 for row in rows if row["aggregate"]["recommendation_action"] == "hold")
    sell = sum(1 for row in rows if row["aggregate"]["recommendation_action"] == "sell")
    conflict = sum(1 for row in rows if row["aggregate"]["conflict_signal"])
    return {
        "total_symbols": len(rows),
        "buy": buy,
        "hold": hold,
        "sell": sell,
        "conflict_count": conflict,
    }


def run_ranking_snapshot(
    db: Session,
    *,
    snapshot_date: date,
    snapshot_type: str,
    top_n: int,
    symbols: list[str] | None = None,
) -> RankingSnapshot:
    universe = _resolve_universe(db, symbols, top_n)
    if not universe:
        raise RankingPipelineError("No stocks available. Run data sync first.")
    universe_size = len(universe)

    # Ranking is global data output, so analysis context must stay user-agnostic.
    user_id = None

    # Reuse source: latest snapshot of the same type.
    # We carry forward domain fingerprints + investment signatures to skip unchanged expert/plan recomputation.
    previous_snapshot = get_latest_snapshot(db, snapshot_type=snapshot_type)
    previous_item_by_symbol: dict[str, RankingItem] = {}
    previous_domain_fingerprints_by_symbol: dict[str, dict[str, str]] = {}
    previous_investment_signatures_by_symbol: dict[str, str] = {}
    if previous_snapshot:
        previous_item_by_symbol = {
            item.stock_symbol: item
            for item in (previous_snapshot.items or [])
            if isinstance(getattr(item, "stock_symbol", None), str)
        }
        previous_summary = previous_snapshot.summary if isinstance(previous_snapshot.summary, dict) else {}
        previous_domain_map_raw = previous_summary.get("domain_fingerprints_by_symbol")
        if isinstance(previous_domain_map_raw, dict):
            for symbol, payload in previous_domain_map_raw.items():
                if isinstance(symbol, str) and isinstance(payload, dict):
                    previous_domain_fingerprints_by_symbol[symbol] = {
                        str(k): str(v) for k, v in payload.items() if isinstance(k, str)
                    }
        previous_investment_map_raw = previous_summary.get("investment_signatures_by_symbol")
        if isinstance(previous_investment_map_raw, dict):
            for symbol, sig in previous_investment_map_raw.items():
                if isinstance(symbol, str) and isinstance(sig, str) and sig.strip():
                    previous_investment_signatures_by_symbol[symbol] = sig.strip()

    reports: list[dict] = []
    refresh_errors: dict[str, str] = {}

    # Phase 1: refresh/check all symbols first.
    for stock in universe:
        try:
            akshare_service.sync_symbol_hot_data(
                db,
                symbol=stock.symbol,
                as_of_date=snapshot_date,
                history_days=120,
                force=False,
            )
        except Exception as exc:
            refresh_errors[stock.symbol] = str(exc)
            logger.warning("ranking pre-analysis refresh failed | symbol=%s error=%s", stock.symbol, exc)

    domain_fingerprints_by_symbol: dict[str, dict[str, str]] = {}
    investment_signatures_by_symbol: dict[str, str] = {}
    # Phase 2: run per-stock experts with reuse cache.
    for stock in universe:
        reuse_cache: dict[str, Any] | None = None
        previous_item = previous_item_by_symbol.get(stock.symbol)
        if previous_item:
            reuse_cache = {
                "experts": previous_item.expert_payload if isinstance(previous_item.expert_payload, dict) else {},
                "investment": previous_item.investment_payload
                if isinstance(previous_item.investment_payload, dict)
                else {},
                "domain_fingerprints": previous_domain_fingerprints_by_symbol.get(stock.symbol, {}),
                "cache_meta": {
                    "investment_signature": previous_investment_signatures_by_symbol.get(stock.symbol),
                },
            }

        report = expert_orchestrator.analyze_stock(
            db,
            stock_symbol=stock.symbol,
            profile=None,
            run_context=snapshot_type,
            as_of_date=snapshot_date,
            user_id=user_id,
            reuse_cache=reuse_cache,
        )
        report = sanitize_report_for_storage(report)
        if isinstance(report.get("domain_fingerprints"), dict):
            domain_fingerprints_by_symbol[stock.symbol] = {
                str(k): str(v) for k, v in report.get("domain_fingerprints", {}).items() if isinstance(k, str)
            }
        cache_meta = report.get("cache_meta") if isinstance(report.get("cache_meta"), dict) else {}
        inv_sig = cache_meta.get("investment_signature")
        if isinstance(inv_sig, str) and inv_sig.strip():
            investment_signatures_by_symbol[stock.symbol] = inv_sig.strip()
        reports.append(report)

    reports.sort(key=lambda item: item["aggregate"]["total_score"], reverse=True)
    reports = reports[: max(1, top_n)]

    snapshot = (
        db.query(RankingSnapshot)
        .options(joinedload(RankingSnapshot.items))
        .filter(
            RankingSnapshot.snapshot_date == snapshot_date,
            RankingSnapshot.snapshot_type == snapshot_type,
        )
        .first()
    )
    if snapshot:
        db.query(RankingItem).filter(RankingItem.snapshot_id == snapshot.id).delete(synchronize_session=False)
    else:
        snapshot = RankingSnapshot(snapshot_date=snapshot_date, snapshot_type=snapshot_type)

    snapshot.status = "completed"
    snapshot.summary = _snapshot_summary(reports)
    snapshot.summary["universe_size"] = universe_size
    snapshot.summary["top_n"] = top_n
    snapshot.summary["refresh_error_count"] = len(refresh_errors)
    snapshot.summary["domain_fingerprints_by_symbol"] = domain_fingerprints_by_symbol
    snapshot.summary["investment_signatures_by_symbol"] = investment_signatures_by_symbol
    if previous_snapshot:
        snapshot.summary["reuse_source_snapshot_id"] = previous_snapshot.id
    if refresh_errors:
        snapshot.summary["refresh_error_symbols"] = sorted(refresh_errors.keys())[:20]
    db.add(snapshot)
    db.flush()

    for index, report in enumerate(reports, start=1):
        stock = db.query(Stock).filter(Stock.symbol == report["stock_symbol"]).first()
        if not stock:
            continue
        experts = report["experts"]
        aggregate = report["aggregate"]
        investment = report["investment"]

        db.add(
            RankingItem(
                snapshot_id=snapshot.id,
                stock_id=stock.id,
                stock_symbol=stock.symbol,
                rank=index,
                total_score=aggregate["total_score"],
                news_score=experts["news"]["score"],
                stock_score=experts["stock_data"]["score"],
                macro_score=experts["macro"]["score"],
                financial_score=experts["financial"]["score"],
                fundamental_score=experts["fundamental"]["score"],
                data_drive_score=aggregate["data_drive_score"],
                emotion_drive_score=aggregate["emotion_drive_score"],
                conflict_signal=aggregate["conflict_signal"],
                recommendation_action=aggregate["recommendation_action"],
                recommendation_confidence=aggregate["recommendation_confidence"],
                recommendation_summary=investment.get("summary"),
                expert_payload=experts,
                investment_payload=investment,
            )
        )

    db.commit()
    db.refresh(snapshot)

    # reload with items
    snapshot = (
        db.query(RankingSnapshot)
        .options(joinedload(RankingSnapshot.items))
        .filter(RankingSnapshot.id == snapshot.id)
        .first()
    )
    if not snapshot:
        raise RankingPipelineError("Failed to load ranking snapshot")
    return snapshot


def get_latest_snapshot(db: Session, snapshot_type: str | None = None) -> RankingSnapshot | None:
    query = db.query(RankingSnapshot).options(joinedload(RankingSnapshot.items))
    if snapshot_type:
        query = query.filter(RankingSnapshot.snapshot_type == snapshot_type)
    snapshot = query.order_by(RankingSnapshot.snapshot_date.desc(), RankingSnapshot.generated_at.desc()).first()
    return snapshot


def get_snapshot(db: Session, snapshot_id: int) -> RankingSnapshot | None:
    return (
        db.query(RankingSnapshot)
        .options(joinedload(RankingSnapshot.items))
        .filter(RankingSnapshot.id == snapshot_id)
        .first()
    )
