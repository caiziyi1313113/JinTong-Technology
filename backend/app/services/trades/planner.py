from datetime import datetime, timedelta
import hashlib
import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.market_scope import is_target_symbol, market_from_symbol, normalize_symbol
from app.models.analysis import Analysis
from app.models.portfolio_trade import PortfolioTrade
from app.models.position import Position
from app.models.profile import UserProfile
from app.models.stock import Stock
from app.models.trade_plan import TradePlan
from app.models.trade_signal import TradeSignal
from app.models.user import User
from app.services.experts_v2 import expert_orchestrator


def _resolve_stock(db: Session, symbol: str) -> Stock:
    code = normalize_symbol(symbol)
    if not is_target_symbol(code):
        raise ValueError("Only Shenzhen main-board A shares are supported")
    stock = db.query(Stock).filter(Stock.symbol == code).first()
    if stock:
        return stock
    stock = Stock(symbol=code, name=code, market=market_from_symbol(code))
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _ensure_profile(db: Session, user: User) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if profile:
        return profile
    profile = UserProfile(user_id=user.id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _stable_signature(payload: dict) -> str:
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


def create_trade_plan(db: Session, user: User, stock_symbol: str) -> tuple[TradePlan, dict]:
    stock = _resolve_stock(db, stock_symbol)
    profile = _ensure_profile(db, user)

    position = (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.stock_id == stock.id, Position.status == "open")
        .order_by(Position.updated_at.desc())
        .first()
    )

    latest_analysis = (
        db.query(Analysis)
        .filter(Analysis.user_id == user.id, Analysis.stock_id == stock.id)
        .order_by(Analysis.created_at.desc())
        .first()
    )
    current_position_sig = (
        {
            "quantity": float(position.quantity),
            "avg_price": float(position.avg_price),
            "status": str(position.status),
        }
        if position
        else {"quantity": 0.0, "avg_price": 0.0, "status": "none"}
    )
    investment_scope_signature = _stable_signature(
        {
            "profile_signature": _profile_signature(profile),
            "trade_signature": _trade_signature(db, user_id=user.id, stock_id=stock.id),
            "position_signature": _stable_signature(current_position_sig),
            # Trade planner and single-stock query are both user-level scenarios.
            "run_context": "query",
        }
    )

    report = expert_orchestrator.analyze_stock(
        db,
        stock_symbol=stock.symbol,
        profile=profile,
        run_context="query",
        user_id=user.id,
        reuse_cache=latest_analysis.rationale if latest_analysis and isinstance(latest_analysis.rationale, dict) else None,
        investment_scope_signature=investment_scope_signature,
    )

    investment = report.get("investment", {})
    aggregate = report.get("aggregate", {})

    final_signal = investment.get("final_signal") or aggregate.get("recommendation_action") or "hold"
    side = "buy" if final_signal == "buy" else "sell" if final_signal == "sell" else "hold"
    has_open_position = bool(position and (position.quantity or 0) > 0 and position.status == "open")
    if side == "sell" and not has_open_position:
        side = "hold"
        final_signal = "hold"

    buy_strategy = investment.get("buy_strategy", {})
    price_range = buy_strategy.get("price_range") or [None, None]
    if not isinstance(price_range, list) or len(price_range) != 2:
        price_range = [None, None]
    entry_low = _safe_float(price_range[0], None)
    entry_high = _safe_float(price_range[1], None)
    ladder_prices = []
    if entry_low is not None and entry_high is not None:
        ladder_prices = [entry_low, round((entry_low + entry_high) / 2, 4), entry_high]

    stop_loss_plan = investment.get("stop_loss_plan", {})
    take_profit_plan = investment.get("take_profit_plan", [])
    take_profit_price = None
    if isinstance(take_profit_plan, list) and take_profit_plan:
        take_profit_price = take_profit_plan[0].get("target_price")

    position_management = investment.get("position_management", {})
    suggested_shares = int(_safe_float(position_management.get("suggested_shares"), 0))
    if side == "sell" and has_open_position:
        suggested_shares = max(1, min(int(position.quantity), suggested_shares or int(position.quantity)))
    elif side != "buy":
        suggested_shares = 0
    reduce_ratio = _safe_float(investment.get("breakeven_plan", {}).get("sell_ratio"), 0.0)
    if reduce_ratio > 1:
        reduce_ratio = reduce_ratio / 100.0
    reduce_ratio = max(0.0, min(reduce_ratio, 1.0))

    hold_days = profile.investment_horizon
    valid_until = datetime.utcnow() + timedelta(days=1 if profile.investment_horizon == "short" else 3)

    plan = TradePlan(
        user_id=user.id,
        stock_id=stock.id,
        position_id=position.id if position else None,
        side=side,
        entry_low=entry_low,
        entry_high=entry_high,
        ladder_prices=ladder_prices,
        stop_loss_price=_safe_float(stop_loss_plan.get("stop_loss_price"), None),
        take_profit_price=_safe_float(take_profit_price, None),
        trailing_stop_pct=0.0,
        reduce_ratio=reduce_ratio,
        suggested_shares=max(0, suggested_shares),
        hold_days=str(hold_days),
        valid_until=valid_until,
        rationale={
            "report": report,
            "final_signal": final_signal,
        },
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan, report


def create_trade_signal(db: Session, user: User, plan: TradePlan, current_price: float | None = None) -> TradeSignal:
    _ = user
    report = plan.rationale.get("report", {}) if isinstance(plan.rationale, dict) else {}
    context_quote = report.get("context", {}).get("latest_quote", {})
    px = _safe_float(current_price, _safe_float(context_quote.get("latest_price"), 0.0))

    if plan.side == "buy":
        trigger_price = plan.entry_low or px
        signal_type = "entry"
        reason = "Buy setup from investment expert"
    elif plan.side == "sell":
        trigger_price = plan.stop_loss_price or px
        signal_type = "exit"
        reason = "Sell/stop setup from investment expert"
    else:
        trigger_price = px
        signal_type = "monitor"
        reason = "Hold and monitor"

    aggregate = report.get("aggregate", {})
    confidence = max(0.25, min(0.95, _safe_float(aggregate.get("recommendation_confidence"), 0.5)))

    signal = TradeSignal(
        user_id=plan.user_id,
        stock_id=plan.stock_id,
        trade_plan_id=plan.id,
        side=plan.side,
        signal_type=signal_type,
        trigger_price=trigger_price,
        suggested_shares=plan.suggested_shares,
        confidence=confidence,
        reason=reason,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal

