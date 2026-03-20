from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import SessionLocal, get_db
from app.core.market_scope import is_target_symbol, market_from_symbol, normalize_symbol
from app.core.task_manager import task_manager
from app.models.analysis import Analysis
from app.models.company_financial import CompanyFinancial
from app.models.company_financial_event import CompanyFinancialEvent
from app.models.company_fundamental import CompanyFundamental
from app.models.document import Document
from app.models.expert_signal import ExpertSignal
from app.models.macro_news import MacroNews
from app.models.market import MarketData
from app.models.portfolio_trade import PortfolioTrade
from app.models.position import Position
from app.models.profile import UserProfile
from app.models.stock import Stock
from app.models.user import User
from app.schemas.analysis import AnalysisOut, AnalysisRequest, AnalysisTaskOut
from app.services.data_ingest import akshare_service
from app.services.experts_v2 import expert_orchestrator
from app.services.experts_v2.postprocess import sanitize_report_for_storage
from app.schemas.analysis import MacroStandaloneOut
from app.services.macro_analysis import macro_standalone_service

router = APIRouter(prefix="/analysis", tags=["analysis"])

logger = logging.getLogger(__name__)

TOTAL_STEPS = 7
MAX_TASKS = 300
_tasks_lock = threading.Lock()
_analysis_tasks: dict[str, dict] = {}
_task_seq = 0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_profile(db: Session, user_id: int) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile:
        return profile
    profile = UserProfile(user_id=user_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _ensure_stock(db: Session, symbol: str) -> Stock:
    code = normalize_symbol(symbol)
    if not is_target_symbol(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Shenzhen main-board A shares are supported",
        )
    stock = db.query(Stock).filter(Stock.symbol == code).first()
    if stock:
        return stock
    stock = Stock(symbol=code, name=code, market=market_from_symbol(code))
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _expert_to_signal_item(expert_name: str, payload: dict) -> dict:
    return {
        "expert_name": expert_name,
        "signal": payload.get("signal", "hold"),
        "score": float(payload.get("score", 50.0)),
        "confidence": float(payload.get("confidence", 0.5)),
        "fallback": bool(payload.get("fallback", False)),
        "horizon": "multi",
        "key_factors": payload.get("key_points", []),
        "risk_flags": payload.get("risks", []),
        "evidence": payload.get("evidence", []),
    }


def _stable_signature(payload: dict) -> str:
    """
    Create deterministic hash for cache invalidation checks.
    """
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _profile_signature(profile: UserProfile) -> str:
    payload = {
        "risk_level": profile.risk_level,
        "investment_horizon": profile.investment_horizon,
        "income": float(profile.income or 0.0),
        "assets": float(profile.assets or 0.0),
        "disposable_funds": float(profile.disposable_funds or 0.0),
        "experience_years": float(profile.experience_years or 0.0),
        "max_drawdown": float(profile.max_drawdown or 0.0),
        "risk_budget": float(profile.risk_budget or 0.0),
        "target_return": float(profile.target_return or 0.0),
        "max_single_position": float(profile.max_single_position or 0.0),
        "style": profile.style,
        "persona": profile.persona,
        "questionnaire_answers": profile.questionnaire_answers or {},
        "preferences": profile.preferences or {},
    }
    return _stable_signature(payload)


def _trade_signature(db: Session, *, user_id: int, stock_id: int) -> str:
    trade_count = (
        db.query(func.count(PortfolioTrade.id))
        .filter(PortfolioTrade.user_id == user_id, PortfolioTrade.stock_id == stock_id)
        .scalar()
        or 0
    )
    latest_trade_time = (
        db.query(func.max(PortfolioTrade.trade_time))
        .filter(PortfolioTrade.user_id == user_id, PortfolioTrade.stock_id == stock_id)
        .scalar()
    )
    payload = {
        "count": int(trade_count),
        "latest_trade_time": latest_trade_time.isoformat() if latest_trade_time else None,
    }
    return _stable_signature(payload)


def _recompute_queue_positions_locked() -> None:
    queued = sorted(
        (
            (task_id, task)
            for task_id, task in _analysis_tasks.items()
            if task.get("status") == "queued"
        ),
        key=lambda item: (item[1].get("sequence", 0), item[1].get("created_at")),
    )
    for index, (_, task) in enumerate(queued, start=1):
        task["queue_position"] = index
    for task in _analysis_tasks.values():
        if task.get("status") != "queued":
            task["queue_position"] = None


def _task_cleanup() -> None:
    with _tasks_lock:
        if len(_analysis_tasks) <= MAX_TASKS:
            return

        removable = sorted(
            (
                (task_id, task)
                for task_id, task in _analysis_tasks.items()
                if task.get("status") in {"completed", "failed"}
            ),
            key=lambda item: item[1].get("updated_at", datetime.min.replace(tzinfo=timezone.utc)),
        )
        overflow = len(_analysis_tasks) - MAX_TASKS
        for task_id, _ in removable[:overflow]:
            _analysis_tasks.pop(task_id, None)

        _recompute_queue_positions_locked()


def _task_create(*, user_id: int, stock_symbol: str) -> dict:
    global _task_seq
    now = _now_utc()
    task_id = uuid.uuid4().hex
    with _tasks_lock:
        _task_seq += 1
        seq = _task_seq
    task = {
        "task_id": task_id,
        "user_id": user_id,
        "stock_symbol": stock_symbol,
        "sequence": seq,
        "status": "queued",
        "queue_position": None,
        "current_step": 0,
        "total_steps": TOTAL_STEPS,
        "stage": "queued",
        "message": "Task created and waiting in queue",
        "error": None,
        "analysis_id": None,
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    with _tasks_lock:
        _analysis_tasks[task_id] = task
        _recompute_queue_positions_locked()
    _task_cleanup()
    return task


def _task_update(task_id: str, **kwargs) -> None:
    with _tasks_lock:
        task = _analysis_tasks.get(task_id)
        if not task:
            return
        task.update(kwargs)
        task["updated_at"] = _now_utc()
        _recompute_queue_positions_locked()
        logger.info(
            "analysis task update | task_id=%s status=%s step=%s/%s stage=%s",
            task_id,
            task.get("status"),
            task.get("current_step"),
            task.get("total_steps"),
            task.get("stage"),
        )


def _task_get(task_id: str) -> dict | None:
    with _tasks_lock:
        task = _analysis_tasks.get(task_id)
        if not task:
            return None
        return dict(task)


def _task_to_out(task: dict) -> AnalysisTaskOut:
    return AnalysisTaskOut(
        task_id=task["task_id"],
        stock_symbol=task["stock_symbol"],
        status=task["status"],
        current_step=task["current_step"],
        total_steps=task["total_steps"],
        queue_position=task.get("queue_position"),
        stage=task["stage"],
        message=task.get("message"),
        error=task.get("error"),
        analysis_id=task.get("analysis_id"),
        result=task.get("result"),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
    )


def _persist_analysis(
    db: Session,
    *,
    current_user: User,
    stock: Stock,
    report: dict,
    refresh_detail: dict,
) -> AnalysisOut:
    investment = report.get("investment", {})
    aggregate = report.get("aggregate", {})
    final_action = investment.get("final_signal") or aggregate.get("recommendation_action") or "hold"

    position_size = 0.0
    position_payload = investment.get("position_management", {})
    if isinstance(position_payload, dict):
        try:
            position_size = float(position_payload.get("position_ratio", 0.0))
        except Exception:
            position_size = 0.0

    rationale = {
        "aggregate": aggregate,
        "investment": investment,
        "context": report.get("context", {}),
        "experts": report.get("experts", {}),
        "domain_fingerprints": report.get("domain_fingerprints", {}),
        "cache_meta": report.get("cache_meta", {}),
        "refresh": refresh_detail,
    }

    risk_notes = []
    for expert in report.get("experts", {}).values():
        risk_notes.extend(expert.get("risks", []))
    risk_notes.extend(investment.get("risk_warnings", []))
    risk_notes = sorted({str(item) for item in risk_notes if item})

    analysis = Analysis(
        user_id=current_user.id,
        stock_id=stock.id,
        final_action=final_action,
        position_size=position_size,
        rationale=rationale,
        risk_notes=risk_notes,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    expert_signals_payload = []
    for key, value in report.get("experts", {}).items():
        item = _expert_to_signal_item(key, value)
        expert_signals_payload.append(item)
        db.add(
            ExpertSignal(
                analysis_id=analysis.id,
                expert_name=item["expert_name"],
                signal=item["signal"],
                score=item["score"],
                confidence=item["confidence"],
                fallback=item["fallback"],
                horizon=item["horizon"],
                key_factors=item["key_factors"],
                risk_flags=item["risk_flags"],
                evidence=item["evidence"],
            )
        )

    investment_signal = _expert_to_signal_item(
        "investment",
        {
            "signal": final_action,
            "score": aggregate.get("total_score", 50),
            "confidence": investment.get("confidence", aggregate.get("recommendation_confidence", 0.5)),
            "fallback": investment.get("fallback", False),
            "key_points": [investment.get("summary", "")],
            "risks": investment.get("risk_warnings", []),
            "evidence": [
                {
                    "type": "aggregate",
                    "detail": f"total_score={aggregate.get('total_score')}, conflict={aggregate.get('conflict_signal')}",
                }
            ],
        },
    )
    expert_signals_payload.append(investment_signal)
    db.add(
        ExpertSignal(
            analysis_id=analysis.id,
            expert_name=investment_signal["expert_name"],
            signal=investment_signal["signal"],
            score=investment_signal["score"],
            confidence=investment_signal["confidence"],
            fallback=investment_signal["fallback"],
            horizon=investment_signal["horizon"],
            key_factors=investment_signal["key_factors"],
            risk_flags=investment_signal["risk_flags"],
            evidence=investment_signal["evidence"],
        )
    )
    db.commit()

    return AnalysisOut(
        id=analysis.id,
        user_id=analysis.user_id,
        stock_symbol=stock.symbol,
        created_at=analysis.created_at,
        final_action=analysis.final_action,
        position_size=analysis.position_size,
        rationale=analysis.rationale,
        risk_notes=analysis.risk_notes,
        expert_signals=expert_signals_payload,
    )


def _get_data_coverage(db: Session, stock: Stock, *, target_date: date) -> dict[str, int | bool]:
    """
    Coverage snapshot used to guarantee data-link readiness before expert calls.
    """
    news_cutoff = datetime.combine(target_date, datetime.min.time()) - timedelta(days=7)

    news_count = (
        db.query(func.count(Document.id))
        .filter(
            or_(Document.stock_id == stock.id, Document.stock_symbol == stock.symbol),
            Document.doc_type.in_(
                [
                    "news",
                    "announcement",
                    "research_report",
                    "market_sentiment",
                    "peer_comparison",
                    "company_profile",
                    "financial_snapshot",
                    "business_composition",
                    "pledge_risk",
                ]
            ),
            or_(Document.published_at.is_(None), Document.published_at >= news_cutoff),
        )
        .scalar()
        or 0
    )
    macro_count = (
        db.query(func.count(MacroNews.id))
        .filter(or_(MacroNews.published_at.is_(None), MacroNews.published_at >= news_cutoff))
        .scalar()
        or 0
    )
    market_data_count = (
        db.query(func.count(MarketData.id))
        .filter(
            MarketData.stock_id == stock.id,
            MarketData.date >= target_date - timedelta(days=180),
        )
        .scalar()
        or 0
    )
    financial_count = (
        db.query(func.count(CompanyFinancial.id))
        .filter(CompanyFinancial.stock_id == stock.id)
        .scalar()
        or 0
    )
    financial_event_count = (
        db.query(func.count(CompanyFinancialEvent.id))
        .filter(CompanyFinancialEvent.stock_id == stock.id)
        .scalar()
        or 0
    )
    has_fundamental = (
        db.query(CompanyFundamental.id)
        .filter(CompanyFundamental.stock_id == stock.id)
        .order_by(CompanyFundamental.snapshot_date.desc())
        .first()
        is not None
    )
    return {
        "news_count": int(news_count),
        "macro_count": int(macro_count),
        "market_data_count": int(market_data_count),
        "financial_count": int(financial_count),
        "financial_event_count": int(financial_event_count),
        "has_fundamental": bool(has_fundamental),
    }


def _ensure_analysis_data_ready(db: Session, stock: Stock, *, target_date: date) -> dict:
    """
    Targeted repair pass:
    - run lightweight coverage checks
    - only refill missing domains
    """
    before = _get_data_coverage(db, stock, target_date=target_date)
    repairs: dict[str, dict] = {}
    errors: dict[str, str] = {}

    if int(before["news_count"]) <= 0:
        try:
            repairs["company_news"] = akshare_service.sync_company_news_from_global(db, stock.symbol, limit=200)
        except Exception as exc:
            errors["company_news"] = str(exc)
    if int(before["macro_count"]) <= 0:
        try:
            repairs["macro_news"] = akshare_service.sync_global_news(db, limit=200)
        except Exception as exc:
            errors["macro_news"] = str(exc)
    if int(before["market_data_count"]) <= 0:
        try:
            repairs["history"] = akshare_service.sync_history(
                db,
                symbol=stock.symbol,
                start_date=target_date - timedelta(days=120),
                end_date=target_date,
                periods=("daily", "weekly", "monthly"),
                adjust="qfq",
            )
        except Exception as exc:
            errors["history"] = str(exc)
    if int(before["financial_count"]) <= 0 and int(before["financial_event_count"]) <= 0:
        try:
            repairs["financials"] = akshare_service.sync_company_financial(db, stock.symbol)
        except Exception as exc:
            errors["financials"] = str(exc)
    if not bool(before["has_fundamental"]):
        try:
            repairs["fundamental"] = akshare_service.sync_company_profile(db, stock.symbol)
        except Exception as exc:
            errors["fundamental"] = str(exc)

    after = _get_data_coverage(db, stock, target_date=target_date)
    return {"before": before, "after": after, "repairs": repairs, "errors": errors}


def _run_analysis(
    db: Session,
    *,
    payload: AnalysisRequest,
    current_user: User,
    task_id: str | None = None,
) -> AnalysisOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User mismatch")

    profile = _ensure_profile(db, current_user.id)
    stock = _ensure_stock(db, payload.stock_symbol)
    stock_symbol = stock.symbol

    logger.info("analysis request received | user_id=%s symbol=%s task_id=%s", current_user.id, stock_symbol, task_id)

    if task_id:
        _task_update(
            task_id,
            status="running",
            current_step=1,
            stage="sync_data",
            message="Checking and refreshing market/company data",
        )

    refresh_detail: dict = {}
    try:
        refresh_detail = akshare_service.sync_symbol_hot_data(
            db,
            symbol=stock_symbol,
            as_of_date=date.today(),
            history_days=120,
            force=False,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("analysis data refresh failed | symbol=%s error=%s", stock_symbol, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Data refresh failed for {stock_symbol}: {exc}",
        ) from exc

    # Data-link repair: before experts run, backfill missing critical domains if needed.
    coverage_detail = _ensure_analysis_data_ready(db, stock, target_date=date.today())
    refresh_detail["coverage"] = coverage_detail
    logger.info(
        "analysis data coverage | symbol=%s before=%s after=%s repair_keys=%s errors=%s",
        stock.symbol,
        coverage_detail.get("before"),
        coverage_detail.get("after"),
        sorted((coverage_detail.get("repairs") or {}).keys()),
        coverage_detail.get("errors"),
    )

    refreshed_flags = (refresh_detail or {}).get("refreshed", {}) if isinstance(refresh_detail, dict) else {}
    repair_keys = sorted((coverage_detail.get("repairs") or {}).keys())
    has_new_data = bool(any(bool(v) for v in refreshed_flags.values())) if isinstance(refreshed_flags, dict) else True
    has_new_data = has_new_data or bool(repair_keys)
    after_coverage = coverage_detail.get("after") if isinstance(coverage_detail.get("after"), dict) else {}
    critical_missing: list[str] = []
    if int(after_coverage.get("market_data_count", 0) or 0) <= 0:
        critical_missing.append("stock_data")
    if (
        int(after_coverage.get("financial_count", 0) or 0) <= 0
        and int(after_coverage.get("financial_event_count", 0) or 0) <= 0
    ):
        critical_missing.append("financial")
    if critical_missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Critical data not ready for {stock.symbol}: {','.join(critical_missing)}",
        )

    latest = (
        db.query(Analysis)
        .filter(Analysis.user_id == current_user.id, Analysis.stock_id == stock.id)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    current_position = (
        db.query(Position)
        .filter(
            Position.user_id == current_user.id,
            Position.stock_id == stock.id,
            Position.status == "open",
        )
        .order_by(Position.updated_at.desc())
        .first()
    )
    current_position_sig = (
        {
            "quantity": float(current_position.quantity),
            "avg_price": float(current_position.avg_price),
            "status": str(current_position.status),
        }
        if current_position
        else None
    )
    # Signatures drive safe cache reuse:
    # - profile/trades/position changes must invalidate investment advice.
    current_profile_signature = _profile_signature(profile)
    current_trade_signature = _trade_signature(db, user_id=current_user.id, stock_id=stock.id)
    current_position_signature = _stable_signature(
        current_position_sig or {"quantity": 0.0, "avg_price": 0.0, "status": "none"}
    )
    investment_scope_signature = _stable_signature(
        {
            "profile_signature": current_profile_signature,
            "trade_signature": current_trade_signature,
            "position_signature": current_position_signature,
            "run_context": "query",
        }
    )

    latest_cache_meta: dict = {}
    if latest and isinstance(latest.rationale, dict) and isinstance(latest.rationale.get("cache_meta"), dict):
        latest_cache_meta = latest.rationale.get("cache_meta") or {}

    latest_position_signature = latest_cache_meta.get("position_signature")
    if not isinstance(latest_position_signature, str) or not latest_position_signature:
        # Backward compatibility: old rows may only store position in context.
        latest_position_sig = None
        if latest and isinstance(latest.rationale, dict):
            latest_ctx = latest.rationale.get("context") if isinstance(latest.rationale.get("context"), dict) else {}
            latest_pos = latest_ctx.get("position")
            if isinstance(latest_pos, dict):
                latest_position_sig = {
                    "quantity": float(latest_pos.get("quantity") or 0.0),
                    "avg_price": float(latest_pos.get("avg_price") or 0.0),
                    "status": str(latest_pos.get("status") or "open"),
                }
        latest_position_signature = _stable_signature(
            latest_position_sig or {"quantity": 0.0, "avg_price": 0.0, "status": "none"}
        )

    latest_profile_signature = latest_cache_meta.get("profile_signature")
    latest_trade_signature = latest_cache_meta.get("trade_signature")
    # For records created before signature support, force one recompute to seed signatures.
    position_changed = current_position_signature != str(latest_position_signature or "")
    profile_changed = bool(latest and (not latest_profile_signature or current_profile_signature != latest_profile_signature))
    trade_changed = bool(latest and (not latest_trade_signature or current_trade_signature != latest_trade_signature))
    domain_changed = False
    if latest and not has_new_data and not position_changed and not profile_changed and not trade_changed:
        latest_domain_fingerprints = (
            latest.rationale.get("domain_fingerprints")
            if isinstance(latest.rationale, dict) and isinstance(latest.rationale.get("domain_fingerprints"), dict)
            else {}
        )
        if not latest_domain_fingerprints:
            # Old rows without domain fingerprints should be recomputed once.
            domain_changed = True
        else:
            current_context = expert_orchestrator.build_context(
                db,
                stock=stock,
                as_of_date=date.today(),
                user_id=current_user.id,
            )
            current_domain_fingerprints = expert_orchestrator._build_domain_fingerprints(current_context)
            normalized_latest_fingerprints = {
                str(k): str(v) for k, v in latest_domain_fingerprints.items() if isinstance(k, str)
            }
            domain_changed = current_domain_fingerprints != normalized_latest_fingerprints

    if not has_new_data and not position_changed and not profile_changed and not trade_changed and not domain_changed:
        if latest:
            logger.info(
                "analysis reuse cached result | user_id=%s symbol=%s analysis_id=%s",
                current_user.id,
                stock.symbol,
                latest.id,
            )
            if task_id:
                _task_update(
                    task_id,
                    status="completed",
                    current_step=TOTAL_STEPS,
                    stage="completed",
                    message="Data unchanged, reused latest analysis result",
                    analysis_id=latest.id,
                )
            stock_row = db.get(Stock, latest.stock_id)
            signals = db.query(ExpertSignal).filter(ExpertSignal.analysis_id == latest.id).all()
            return AnalysisOut(
                id=latest.id,
                user_id=latest.user_id,
                stock_symbol=stock_row.symbol if stock_row else stock.symbol,
                created_at=latest.created_at,
                final_action=latest.final_action,
                position_size=latest.position_size,
                rationale=latest.rationale,
                risk_notes=latest.risk_notes,
                expert_signals=signals,
            )

    def _progress(event: dict) -> None:
        if not task_id:
            return
        phase = event.get("phase")
        if phase == "expert_done":
            idx = int(event.get("expert_index", 0))
            step = min(TOTAL_STEPS, idx + 1)  # sync is step1, experts are step2..6
            _task_update(
                task_id,
                current_step=step,
                stage=f"expert:{event.get('expert_key', '')}",
                message=f"Expert completed {idx}/{event.get('expert_total', 5)}",
            )
        elif phase == "investment_start":
            _task_update(
                task_id,
                current_step=7,
                stage="investment_summary",
                message="Generating investment advice and execution plan",
            )

    report = expert_orchestrator.analyze_stock(
        db,
        stock_symbol=stock.symbol,
        profile=profile,
        run_context="query",
        user_id=current_user.id,
        reuse_cache=latest.rationale if latest and isinstance(latest.rationale, dict) else None,
        investment_scope_signature=investment_scope_signature,
        progress_callback=_progress,
    )
    report = sanitize_report_for_storage(report)
    cache_meta = report.get("cache_meta") if isinstance(report.get("cache_meta"), dict) else {}
    cache_meta.update(
        {
            "profile_signature": current_profile_signature,
            "trade_signature": current_trade_signature,
            "position_signature": current_position_signature,
            "investment_scope_signature": investment_scope_signature,
            "has_new_data": has_new_data,
            "position_changed": position_changed,
            "profile_changed": profile_changed,
            "trade_changed": trade_changed,
            "domain_changed": domain_changed,
        }
    )
    report["cache_meta"] = cache_meta

    return _persist_analysis(
        db,
        current_user=current_user,
        stock=stock,
        report=report,
        refresh_detail=refresh_detail,
    )


def _analysis_task_worker(task_id: str, *, user_id: int, stock_symbol: str) -> None:
    db = SessionLocal()
    try:
        logger.info("analysis task worker start | task_id=%s user_id=%s symbol=%s", task_id, user_id, stock_symbol)
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise RuntimeError("User not found")

        payload = AnalysisRequest(user_id=user_id, stock_symbol=stock_symbol)
        result = _run_analysis(db, payload=payload, current_user=user, task_id=task_id)

        _task_update(
            task_id,
            status="completed",
            current_step=TOTAL_STEPS,
            stage="completed",
            message="Analysis completed",
            error=None,
            analysis_id=result.id,
            result=result.model_dump(),
        )
        logger.info("analysis task completed | task_id=%s analysis_id=%s", task_id, result.id)
    except Exception as exc:
        logger.exception("analysis task failed | task_id=%s error=%s", task_id, exc)
        _task_update(
            task_id,
            status="failed",
            stage="failed",
            message="Analysis failed",
            error=str(exc),
        )
    finally:
        db.close()


@router.post("", response_model=AnalysisOut)
def create_analysis(
    payload: AnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    return _run_analysis(db, payload=payload, current_user=current_user, task_id=None)


@router.post("/tasks", response_model=AnalysisTaskOut)
def create_analysis_task(
    payload: AnalysisRequest,
    current_user: User = Depends(get_current_user),
) -> AnalysisTaskOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User mismatch")

    code = normalize_symbol(payload.stock_symbol)
    if not is_target_symbol(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Shenzhen main-board A shares are supported",
        )

    task = _task_create(user_id=current_user.id, stock_symbol=code)
    try:
        task_manager.submit(
            pool="analysis",
            tracking_id=task["task_id"],
            fn=_analysis_task_worker,
            task_id=task["task_id"],
            user_id=current_user.id,
            stock_symbol=code,
        )
    except RuntimeError as exc:
        _task_update(
            task["task_id"],
            status="failed",
            stage="queue_rejected",
            message="Background queue is full, please retry",
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    return _task_to_out(task)


@router.get("/tasks/{task_id}", response_model=AnalysisTaskOut)
def get_analysis_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
) -> AnalysisTaskOut:
    task = _task_get(task_id)
    if not task or task.get("user_id") != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis task not found")
    return _task_to_out(task)


@router.post("/macro/standalone", response_model=MacroStandaloneOut)
def generate_macro_standalone_report(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MacroStandaloneOut:
    _ = current_user  # auth gate
    report = macro_standalone_service.generate_report(db)
    return MacroStandaloneOut(
        generated_at=datetime.utcnow(),
        report=report,
    )


def _latest_analysis_by_symbol(
    stock_symbol: str,
    current_user: User,
    db: Session,
) -> AnalysisOut:
    """
    Return current user's latest analysis for a symbol.
    """
    code = normalize_symbol(stock_symbol)
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    stock = db.query(Stock).filter(Stock.symbol == code).first()
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == current_user.id, Analysis.stock_id == stock.id)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    if not analysis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    signals = db.query(ExpertSignal).filter(ExpertSignal.analysis_id == analysis.id).all()
    return AnalysisOut(
        id=analysis.id,
        user_id=analysis.user_id,
        stock_symbol=stock.symbol,
        created_at=analysis.created_at,
        final_action=analysis.final_action,
        position_size=analysis.position_size,
        rationale=analysis.rationale,
        risk_notes=analysis.risk_notes,
        expert_signals=signals,
    )


@router.get("/symbol/{stock_symbol}/latest", response_model=AnalysisOut)
def get_latest_analysis_by_symbol_v2(
    stock_symbol: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    # Static-prefix route avoids conflict with "/{analysis_id}" and is safe for frontend polling/load.
    return _latest_analysis_by_symbol(stock_symbol=stock_symbol, current_user=current_user, db=db)


@router.get("/latest/{stock_symbol}", response_model=AnalysisOut)
def get_latest_analysis_by_symbol_compat(
    stock_symbol: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    # Keep backward compatibility for existing clients.
    return _latest_analysis_by_symbol(stock_symbol=stock_symbol, current_user=current_user, db=db)


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(
    analysis_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not analysis or analysis.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")

    stock = db.get(Stock, analysis.stock_id)
    signals = db.query(ExpertSignal).filter(ExpertSignal.analysis_id == analysis.id).all()
    return AnalysisOut(
        id=analysis.id,
        user_id=analysis.user_id,
        stock_symbol=stock.symbol if stock else "",
        created_at=analysis.created_at,
        final_action=analysis.final_action,
        position_size=analysis.position_size,
        rationale=analysis.rationale,
        risk_notes=analysis.risk_notes,
        expert_signals=signals,
    )
