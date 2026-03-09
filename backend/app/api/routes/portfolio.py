from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.position import Position
from app.models.stock import Stock
from app.models.user import User
from app.schemas.position import PositionClose, PositionCreate, PositionOut

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

'''
管理 用户持仓
查看持仓
添加持仓
平仓
'''

def _resolve_stock(db: Session, symbol: str) -> Stock:
    stock = db.query(Stock).filter(Stock.symbol == symbol.upper()).first()
    if stock:
        return stock
    stock = Stock(symbol=symbol.upper(), name=symbol.upper(), market="Unknown")
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


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


@router.get("/positions", response_model=list[PositionOut])
def list_positions(
    include_closed: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PositionOut]:
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
    if position.status != "open":
        stock = db.get(Stock, position.stock_id)
        return _position_to_out(position, stock.symbol if stock else "")

    close_qty = payload.quantity if payload.quantity is not None else position.quantity
    if close_qty <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="close quantity must be positive")
    if close_qty >= position.quantity:
        position.quantity = 0
        position.status = "closed"
        position.closed_at = datetime.utcnow()
    else:
        position.quantity -= close_qty
        position.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(position)
    stock = db.get(Stock, position.stock_id)
    return _position_to_out(position, stock.symbol if stock else "")
