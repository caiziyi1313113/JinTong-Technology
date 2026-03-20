from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.market_scope import is_target_symbol, normalize_symbol
from app.models.stock import Stock
from app.models.trade_plan import TradePlan
from app.models.trade_signal import TradeSignal
from app.models.user import User
from app.schemas.trade import TradePlanCreate, TradePlanOut, TradeSignalCreate, TradeSignalOut
from app.services.data_ingest import akshare_service
from app.services.trades.planner import create_trade_plan, create_trade_signal

router = APIRouter(prefix="/trades", tags=["trades"])

'''
负责 生成交易策略。
交易计划
TradePlan
内容：
买入区间
止损
止盈
仓位
持有天数
交易信号
TradeSignal

实时触发：

BUY
SELL
REDUCE
'''
def _plan_to_out(plan: TradePlan, stock_symbol: str) -> TradePlanOut:
    return TradePlanOut(
        id=plan.id,
        stock_symbol=stock_symbol,
        side=plan.side,
        entry_low=plan.entry_low,
        entry_high=plan.entry_high,
        ladder_prices=plan.ladder_prices,
        stop_loss_price=plan.stop_loss_price,
        take_profit_price=plan.take_profit_price,
        trailing_stop_pct=plan.trailing_stop_pct,
        reduce_ratio=plan.reduce_ratio,
        suggested_shares=plan.suggested_shares,
        hold_days=plan.hold_days,
        status=plan.status,
        rationale=plan.rationale,
        created_at=plan.created_at,
    )


def _signal_to_out(signal: TradeSignal, stock_symbol: str) -> TradeSignalOut:
    return TradeSignalOut(
        id=signal.id,
        stock_symbol=stock_symbol,
        side=signal.side,
        signal_type=signal.signal_type,
        trigger_price=signal.trigger_price,
        suggested_shares=signal.suggested_shares,
        confidence=signal.confidence,
        reason=signal.reason,
        created_at=signal.created_at,
    )


@router.post("/plans", response_model=TradePlanOut)
def generate_trade_plan(
    payload: TradePlanCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TradePlanOut:
    code = normalize_symbol(payload.stock_symbol)
    if not is_target_symbol(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Shenzhen main-board A shares are supported",
        )
    try:
        akshare_service.sync_symbol_hot_data(
            db,
            symbol=code,
            as_of_date=date.today(),
            history_days=120,
            force=False,
        )
    except Exception:
        # Allow plan generation with existing cached data if refresh fails.
        pass
    plan, _fused = create_trade_plan(db, current_user, payload.stock_symbol)
    stock = db.get(Stock, plan.stock_id)
    return _plan_to_out(plan, stock.symbol if stock else payload.stock_symbol.upper())


@router.get("/plans", response_model=list[TradePlanOut])
def list_trade_plans(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TradePlanOut]:
    rows = (
        db.query(TradePlan)
        .filter(TradePlan.user_id == current_user.id)
        .order_by(TradePlan.created_at.desc())
        .limit(100)
        .all()
    )
    out = []
    for row in rows:
        stock = db.get(Stock, row.stock_id)
        out.append(_plan_to_out(row, stock.symbol if stock else ""))
    return out


@router.post("/signals", response_model=TradeSignalOut)
def generate_trade_signal(
    payload: TradeSignalCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TradeSignalOut:
    plan = db.query(TradePlan).filter(TradePlan.id == payload.trade_plan_id, TradePlan.user_id == current_user.id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade plan not found")
    signal = create_trade_signal(db, current_user, plan, current_price=payload.current_price)
    stock = db.get(Stock, signal.stock_id)
    return _signal_to_out(signal, stock.symbol if stock else "")


@router.get("/signals", response_model=list[TradeSignalOut])
def list_trade_signals(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TradeSignalOut]:
    rows = (
        db.query(TradeSignal)
        .filter(TradeSignal.user_id == current_user.id)
        .order_by(TradeSignal.created_at.desc())
        .limit(200)
        .all()
    )
    out = []
    for row in rows:
        stock = db.get(Stock, row.stock_id)
        out.append(_signal_to_out(row, stock.symbol if stock else ""))
    return out
