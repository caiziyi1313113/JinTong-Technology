from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.market_scope import is_target_symbol, market_from_symbol, normalize_symbol
from app.models.portfolio_trade import PortfolioTrade
from app.models.position import Position
from app.models.stock import Stock
from app.models.trade_plan import TradePlan
from app.models.trade_signal import TradeSignal
from app.models.user_stock_holding import UserStockHolding
from app.models.user import User
from app.schemas.portfolio_trade import PortfolioTradeCreate, PortfolioTradeOut, PortfolioTrackingClearOut
from app.schemas.position import PositionClose, PositionCreate, PositionOut

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _resolve_stock(db: Session, symbol: str) -> Stock:
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


def _find_stock(db: Session, symbol: str) -> Stock | None:
    code = normalize_symbol(symbol)
    if not code:
        return None
    return db.query(Stock).filter(Stock.symbol == code).first()


def _position_to_out(position: Position, stock_symbol: str) -> PositionOut:
    return PositionOut(
        id=position.id,
        stock_symbol=stock_symbol,
        quantity=position.quantity,
        avg_price=position.avg_price,
        status=position.status,
        opened_at=position.opened_at,
        updated_at=position.updated_at,
        closed_at=position.closed_at,
    )


def _trade_to_out(trade: PortfolioTrade, stock_symbol: str) -> PortfolioTradeOut:
    return PortfolioTradeOut(
        id=trade.id,
        stock_symbol=stock_symbol,
        side=trade.side,
        quantity=trade.quantity,
        price=trade.price,
        trade_time=trade.trade_time,
        note=trade.note,
        created_at=trade.created_at,
    )


def _sync_user_stock_holding(db: Session, *, user_id: int, stock: Stock) -> None:
    open_position = (
        db.query(Position)
        .filter(Position.user_id == user_id, Position.stock_id == stock.id, Position.status == "open")
        .order_by(Position.updated_at.desc())
        .first()
    )
    total_buy_amount = (
        db.query(func.sum(PortfolioTrade.quantity * PortfolioTrade.price))
        .filter(
            PortfolioTrade.user_id == user_id,
            PortfolioTrade.stock_id == stock.id,
            PortfolioTrade.side == "buy",
        )
        .scalar()
        or 0.0
    )
    total_sell_amount = (
        db.query(func.sum(PortfolioTrade.quantity * PortfolioTrade.price))
        .filter(
            PortfolioTrade.user_id == user_id,
            PortfolioTrade.stock_id == stock.id,
            PortfolioTrade.side == "sell",
        )
        .scalar()
        or 0.0
    )

    row = (
        db.query(UserStockHolding)
        .filter(UserStockHolding.user_id == user_id, UserStockHolding.stock_id == stock.id)
        .first()
    )
    if not row:
        row = UserStockHolding(user_id=user_id, stock_id=stock.id, stock_symbol=stock.symbol)
    row.stock_symbol = stock.symbol
    row.quantity = float(open_position.quantity) if open_position else 0.0
    row.avg_price = float(open_position.avg_price) if open_position else 0.0
    row.total_buy_amount = float(total_buy_amount or 0.0)
    row.total_sell_amount = float(total_sell_amount or 0.0)
    row.updated_at = datetime.utcnow()
    db.add(row)


def _rebuild_user_stock_holdings(db: Session, *, user_id: int) -> None:
    tracked_stock_ids = {
        stock_id
        for (stock_id,) in db.query(Position.stock_id)
        .filter(Position.user_id == user_id)
        .distinct()
        .all()
    }
    tracked_stock_ids.update(
        stock_id
        for (stock_id,) in db.query(PortfolioTrade.stock_id)
        .filter(PortfolioTrade.user_id == user_id)
        .distinct()
        .all()
    )

    for stock_id in tracked_stock_ids:
        stock = db.get(Stock, stock_id)
        if stock:
            _sync_user_stock_holding(db, user_id=user_id, stock=stock)

    stale_query = db.query(UserStockHolding).filter(UserStockHolding.user_id == user_id)
    if tracked_stock_ids:
        stale_query = stale_query.filter(~UserStockHolding.stock_id.in_(tracked_stock_ids))
    deleted_stale = stale_query.delete(synchronize_session=False)
    if deleted_stale:
        db.flush()


def _ensure_holdings_summary_exists(db: Session, *, user_id: int) -> None:
    holding_count = (
        db.query(func.count(UserStockHolding.id))
        .filter(UserStockHolding.user_id == user_id)
        .scalar()
        or 0
    )
    if holding_count > 0:
        return

    trade_count = (
        db.query(func.count(PortfolioTrade.id))
        .filter(PortfolioTrade.user_id == user_id)
        .scalar()
        or 0
    )
    position_count = (
        db.query(func.count(Position.id))
        .filter(Position.user_id == user_id)
        .scalar()
        or 0
    )
    if trade_count <= 0 and position_count <= 0:
        return

    _rebuild_user_stock_holdings(db, user_id=user_id)
    db.commit()


@router.get("/positions", response_model=list[PositionOut])
def list_positions(
    include_closed: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PositionOut]:
    _ensure_holdings_summary_exists(db, user_id=current_user.id)
    query = db.query(Position).filter(Position.user_id == current_user.id)
    if not include_closed:
        query = query.filter(Position.status == "open")
    rows = query.order_by(Position.updated_at.desc()).all()
    out = []
    for row in rows:
        stock = db.get(Stock, row.stock_id)
        out.append(_position_to_out(row, stock.symbol if stock else ""))
    return out


@router.post("/positions", response_model=PositionOut)
def upsert_position(
    payload: PositionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PositionOut:
    if payload.quantity <= 0 or payload.avg_price <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quantity and avg_price must be positive")

    stock = _resolve_stock(db, payload.stock_symbol)
    position = (
        db.query(Position)
        .filter(Position.user_id == current_user.id, Position.stock_id == stock.id, Position.status == "open")
        .first()
    )
    if not position:
        position = Position(
            user_id=current_user.id,
            stock_id=stock.id,
            quantity=payload.quantity,
            avg_price=payload.avg_price,
            status="open",
        )
        db.add(position)
    else:
        total_qty = position.quantity + payload.quantity
        position.avg_price = ((position.avg_price * position.quantity) + (payload.avg_price * payload.quantity)) / max(1e-6, total_qty)
        position.quantity = total_qty
        position.updated_at = datetime.utcnow()

    db.flush()
    db.add(
        PortfolioTrade(
            user_id=current_user.id,
            stock_id=stock.id,
            side="buy",
            quantity=payload.quantity,
            price=payload.avg_price,
            trade_time=datetime.utcnow(),
            note="position_upsert",
        )
    )
    _sync_user_stock_holding(db, user_id=current_user.id, stock=stock)

    db.commit()
    db.refresh(position)
    return _position_to_out(position, stock.symbol)


@router.post("/positions/{position_id}/close", response_model=PositionOut)
def close_position(
    position_id: int,
    payload: PositionClose,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PositionOut:
    position = db.query(Position).filter(Position.id == position_id, Position.user_id == current_user.id).first()
    if not position:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")
    stock = db.get(Stock, position.stock_id)

    if position.status != "open":
        return _position_to_out(position, stock.symbol if stock else "")

    close_qty = payload.quantity if payload.quantity is not None else position.quantity
    if close_qty <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="close quantity must be positive")
    if close_qty > position.quantity:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="close quantity exceeds current position")

    close_price = payload.price if payload.price and payload.price > 0 else position.avg_price
    if close_qty == position.quantity:
        sold_qty = position.quantity
        position.quantity = 0
        position.status = "closed"
        position.closed_at = datetime.utcnow()
    else:
        sold_qty = close_qty
        position.quantity -= close_qty
        position.updated_at = datetime.utcnow()

    db.add(
        PortfolioTrade(
            user_id=current_user.id,
            stock_id=position.stock_id,
            side="sell",
            quantity=sold_qty,
            price=close_price,
            trade_time=datetime.utcnow(),
            note=payload.note,
        )
    )
    if stock:
        _sync_user_stock_holding(db, user_id=current_user.id, stock=stock)

    db.commit()
    db.refresh(position)
    return _position_to_out(position, stock.symbol if stock else "")


@router.get("/trades", response_model=list[PortfolioTradeOut])
def list_portfolio_trades(
    stock_symbol: str | None = None,
    limit: int = 200,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PortfolioTradeOut]:
    _ensure_holdings_summary_exists(db, user_id=current_user.id)
    query = db.query(PortfolioTrade).filter(PortfolioTrade.user_id == current_user.id)
    if stock_symbol:
        stock = _resolve_stock(db, stock_symbol)
        query = query.filter(PortfolioTrade.stock_id == stock.id)
    rows = query.order_by(PortfolioTrade.trade_time.desc()).limit(max(1, min(limit, 500))).all()

    out = []
    for row in rows:
        stock = db.get(Stock, row.stock_id)
        out.append(_trade_to_out(row, stock.symbol if stock else ""))
    return out


@router.post("/trades", response_model=PortfolioTradeOut)
def create_portfolio_trade(
    payload: PortfolioTradeCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PortfolioTradeOut:
    side = payload.side.lower()
    if side not in {"buy", "sell"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="side must be buy or sell")
    if payload.quantity <= 0 or payload.price <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quantity and price must be positive")

    stock = _resolve_stock(db, payload.stock_symbol)
    trade_time = payload.trade_time or datetime.utcnow()

    trade = PortfolioTrade(
        user_id=current_user.id,
        stock_id=stock.id,
        side=side,
        quantity=payload.quantity,
        price=payload.price,
        trade_time=trade_time,
        note=payload.note,
    )
    db.add(trade)

    position = (
        db.query(Position)
        .filter(Position.user_id == current_user.id, Position.stock_id == stock.id, Position.status == "open")
        .first()
    )

    if side == "buy":
        if not position:
            position = Position(
                user_id=current_user.id,
                stock_id=stock.id,
                quantity=payload.quantity,
                avg_price=payload.price,
                status="open",
            )
            db.add(position)
        else:
            total_qty = position.quantity + payload.quantity
            position.avg_price = ((position.avg_price * position.quantity) + (payload.price * payload.quantity)) / max(1e-6, total_qty)
            position.quantity = total_qty
            position.updated_at = datetime.utcnow()
    else:
        if not position:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No open position to sell")
        if payload.quantity > position.quantity:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="sell quantity exceeds current position")
        if payload.quantity == position.quantity:
            position.quantity = 0
            position.status = "closed"
            position.closed_at = datetime.utcnow()
        else:
            position.quantity -= payload.quantity
            position.updated_at = datetime.utcnow()

    _sync_user_stock_holding(db, user_id=current_user.id, stock=stock)

    db.commit()
    db.refresh(trade)
    return _trade_to_out(trade, stock.symbol)


@router.delete("/trades/all", response_model=PortfolioTrackingClearOut)
def clear_all_portfolio_tracking(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PortfolioTrackingClearOut:
    deleted_trade_signals = (
        db.query(TradeSignal)
        .filter(TradeSignal.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    deleted_trade_plans = (
        db.query(TradePlan)
        .filter(TradePlan.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    deleted_trades = (
        db.query(PortfolioTrade)
        .filter(PortfolioTrade.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    deleted_positions = (
        db.query(Position)
        .filter(Position.user_id == current_user.id)
        .delete(synchronize_session=False)
    )
    deleted_holdings = (
        db.query(UserStockHolding)
        .filter(UserStockHolding.user_id == current_user.id)
        .delete(synchronize_session=False)
    )

    db.commit()
    return PortfolioTrackingClearOut(
        deleted_trades=int(deleted_trades or 0),
        deleted_positions=int(deleted_positions or 0),
        deleted_holdings=int(deleted_holdings or 0),
        deleted_trade_plans=int(deleted_trade_plans or 0),
        deleted_trade_signals=int(deleted_trade_signals or 0),
    )


@router.delete("/symbol/{stock_symbol}", response_model=PortfolioTrackingClearOut)
def clear_symbol_portfolio_tracking(
    stock_symbol: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PortfolioTrackingClearOut:
    """
    Clear tracking data for one symbol only.

    This endpoint removes the symbol-scoped rows from:
    - trade_signals
    - trade_plans
    - portfolio_trades
    - positions
    - user_stock_holdings
    """
    stock = _find_stock(db, stock_symbol)
    if not stock:
        return PortfolioTrackingClearOut(
            deleted_trades=0,
            deleted_positions=0,
            deleted_holdings=0,
            deleted_trade_plans=0,
            deleted_trade_signals=0,
        )

    deleted_trade_signals = (
        db.query(TradeSignal)
        .filter(TradeSignal.user_id == current_user.id, TradeSignal.stock_id == stock.id)
        .delete(synchronize_session=False)
    )
    deleted_trade_plans = (
        db.query(TradePlan)
        .filter(TradePlan.user_id == current_user.id, TradePlan.stock_id == stock.id)
        .delete(synchronize_session=False)
    )
    deleted_trades = (
        db.query(PortfolioTrade)
        .filter(PortfolioTrade.user_id == current_user.id, PortfolioTrade.stock_id == stock.id)
        .delete(synchronize_session=False)
    )
    deleted_positions = (
        db.query(Position)
        .filter(Position.user_id == current_user.id, Position.stock_id == stock.id)
        .delete(synchronize_session=False)
    )
    deleted_holdings = (
        db.query(UserStockHolding)
        .filter(UserStockHolding.user_id == current_user.id, UserStockHolding.stock_id == stock.id)
        .delete(synchronize_session=False)
    )

    db.commit()
    return PortfolioTrackingClearOut(
        deleted_trades=int(deleted_trades or 0),
        deleted_positions=int(deleted_positions or 0),
        deleted_holdings=int(deleted_holdings or 0),
        deleted_trade_plans=int(deleted_trade_plans or 0),
        deleted_trade_signals=int(deleted_trade_signals or 0),
    )
