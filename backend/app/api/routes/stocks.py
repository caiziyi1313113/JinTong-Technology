from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.market_scope import TARGET_MARKET, is_target_symbol, market_from_symbol, normalize_symbol
from app.models.market import MarketData
from app.models.stock import Stock
from app.models.stock_kline import StockKline
from app.schemas.stock import StockCreate, StockKlineOut, StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])

'''
管理 股票基础数据
'''

@router.get("", response_model=list[StockOut])
def list_stocks(db: Session = Depends(get_db)) -> list[StockOut]:
    return db.query(Stock).filter(Stock.market == TARGET_MARKET).order_by(Stock.symbol).all()


@router.post("", response_model=StockOut)
def create_stock(payload: StockCreate, db: Session = Depends(get_db)) -> StockOut:
    code = normalize_symbol(payload.symbol)
    if not is_target_symbol(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only Shenzhen main-board A shares are supported",
        )
    existing = db.query(Stock).filter(Stock.symbol == code).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stock already exists")
    data = payload.model_dump()
    data["symbol"] = code
    data["market"] = market_from_symbol(code)
    stock = Stock(**data)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


@router.get("/{symbol}", response_model=StockOut)
def get_stock(symbol: str, db: Session = Depends(get_db)) -> StockOut:
    code = normalize_symbol(symbol)
    stock = db.query(Stock).filter(Stock.symbol == code, Stock.market == TARGET_MARKET).first()
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock not found")
    return stock


@router.get("/{symbol}/kline", response_model=StockKlineOut)
def get_stock_kline(
    symbol: str,
    period: str = "daily",
    limit: int = 240,
    db: Session = Depends(get_db),
) -> StockKlineOut:
    code = normalize_symbol(symbol)
    if period not in {"daily", "weekly", "monthly"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="period must be daily/weekly/monthly")
    stock = db.query(Stock).filter(Stock.symbol == code, Stock.market == TARGET_MARKET).first()
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock not found")

    capped_limit = max(20, min(limit, 1000))
    if period == "daily":
        rows = (
            db.query(MarketData)
            .filter(MarketData.stock_id == stock.id)
            .order_by(MarketData.date.desc())
            .limit(capped_limit)
            .all()
        )
        items = [
            {
                "trade_date": row.date.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume or 0.0),
            }
            for row in reversed(rows)
        ]
    else:
        rows = (
            db.query(StockKline)
            .filter(StockKline.stock_id == stock.id, StockKline.period == period)
            .order_by(StockKline.trade_date.desc())
            .limit(capped_limit)
            .all()
        )
        items = [
            {
                "trade_date": row.trade_date.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume or 0.0),
            }
            for row in reversed(rows)
        ]

    return StockKlineOut(symbol=stock.symbol, period=period, items=items)
