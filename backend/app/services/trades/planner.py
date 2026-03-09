from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.market import MarketData
from app.models.position import Position
from app.models.profile import UserProfile
from app.models.stock import Stock
from app.models.trade_plan import TradePlan
from app.models.trade_signal import TradeSignal
from app.models.user import User
from app.services.decision.engine import fuse_signals
from app.services.experts import fundamental, financial, macro, news, technical

'''
把“融合决策”落成可执行的交易计划 + 交易信号,它是 trades 模块背后的核心：

5.1 create_trade_plan(db, user, stock_symbol)

流程：
确保 Stock 存在（不存在就创建）
确保 UserProfile 存在（不存在就创建）
取最新价格 _latest_price()（从 MarketData 最新 close）
查用户是否已有 open 持仓（Position）
跑五个专家 _build_signals() 
调用 fuse_signals() 得到 trade_advice（new_entry 或 manage_position）
把建议写成 TradePlan 入库，包括 entry_range/止损止盈/建议股数/valid_until/rationale 等。
输出：(TradePlan, fused_result)

5.2 create_trade_signal(db, user, plan, current_price)
根据 plan.side：
buy → 触发价用 entry_low，signal_type=entry
sell → 触发价用 stop_loss_price，signal_type=exit
hold → signal_type=monitor
并用 fused_score 作为置信度（夹在 0.3~0.95）写入 TradeSignal。

'''

def _latest_price(db: Session, stock_id: int) -> float:
    row = (
        db.query(MarketData)
        .filter(MarketData.stock_id == stock_id)
        .order_by(MarketData.date.desc())
        .first()
    )
    return float(row.close) if row else 1.0


def _resolve_stock(db: Session, symbol: str) -> Stock:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock:
        return stock
    stock = Stock(symbol=symbol.upper(), name=symbol.upper(), market="Unknown")
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


def _build_signals(db: Session, stock: Stock, profile: UserProfile) -> list[dict]:
    return [
        fundamental.run(db, stock, profile),
        financial.run(db, stock, profile),
        technical.run(db, stock, profile),
        news.run(db, stock, profile),
        macro.run(db, stock, profile),
    ]


def create_trade_plan(db: Session, user: User, stock_symbol: str) -> tuple[TradePlan, dict]:
    stock = _resolve_stock(db, stock_symbol)
    profile = _ensure_profile(db, user)
    current_price = _latest_price(db, stock.id)
    position = (
        db.query(Position)
        .filter(Position.user_id == user.id, Position.stock_id == stock.id, Position.status == "open")
        .order_by(Position.updated_at.desc())
        .first()
    )
    position_payload = {"quantity": position.quantity, "avg_price": position.avg_price} if position else None
    signals = _build_signals(db, stock, profile)
    fused = fuse_signals(profile, signals, current_price=current_price, position=position_payload)
    advice = fused["rationale"]["trade_advice"]

    side = "sell" if advice["mode"] == "manage_position" and fused["action"] == "sell" else "buy" if advice["mode"] == "new_entry" else "hold"
    entry_range = advice.get("entry_range") or [None, None]
    suggested_shares = advice.get("suggested_buy_shares", 0) if advice["mode"] == "new_entry" else advice.get("suggested_sell_shares", 0)

    valid_until = datetime.utcnow() + timedelta(days=1 if profile.investment_horizon == "short" else 3)
    plan = TradePlan(
        user_id=user.id,
        stock_id=stock.id,
        position_id=position.id if position else None,
        side=side,
        entry_low=entry_range[0],
        entry_high=entry_range[1],
        ladder_prices=advice.get("ladder_buy_prices", []),
        stop_loss_price=advice.get("stop_loss_price"),
        take_profit_price=advice.get("take_profit_price"),
        trailing_stop_pct=advice.get("trailing_stop_pct"),
        reduce_ratio=advice.get("reduce_ratio", 0.0),
        suggested_shares=int(suggested_shares or 0),
        hold_days=advice.get("hold_days"),
        valid_until=valid_until,
        rationale={
            "decision": fused["rationale"],
            "experts": signals,
            "action": fused["action"],
            "position_size": fused["position_size"],
        },
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan, fused


def create_trade_signal(db: Session, user: User, plan: TradePlan, current_price: float | None = None) -> TradeSignal:
    stock = db.get(Stock, plan.stock_id)
    px = current_price if current_price and current_price > 0 else _latest_price(db, plan.stock_id)

    if plan.side == "buy":
        trigger_price = plan.entry_low or px
        signal_type = "entry"
        reason = "Entry plan generated from pre-open decision pipeline."
    elif plan.side == "sell":
        trigger_price = plan.stop_loss_price or px
        signal_type = "exit"
        reason = "Exit/risk-control signal generated from position management rules."
    else:
        trigger_price = px
        signal_type = "monitor"
        reason = "No immediate trade. Keep monitoring thresholds."

    fused_score = float(plan.rationale.get("decision", {}).get("fused_score", 0.5))
    confidence = max(0.3, min(0.95, fused_score))
    signal = TradeSignal(
        user_id=user.id,
        stock_id=plan.stock_id,
        trade_plan_id=plan.id,
        side=plan.side,
        signal_type=signal_type,
        trigger_price=trigger_price,
        suggested_shares=plan.suggested_shares,
        confidence=confidence,
        reason=reason if stock else reason,
    )
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal
