from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.stock import Stock
from app.schemas.stock import StockCreate, StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])

'''
管理 股票基础数据
'''

@router.get("", response_model=list[StockOut])
def list_stocks(db: Session = Depends(get_db)) -> list[StockOut]:
    return db.query(Stock).order_by(Stock.symbol).all()


@router.post("", response_model=StockOut)
def create_stock(payload: StockCreate, db: Session = Depends(get_db)) -> StockOut:
    existing = db.query(Stock).filter(Stock.symbol == payload.symbol).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stock already exists")
    stock = Stock(**payload.model_dump())
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


@router.get("/{symbol}", response_model=StockOut)
def get_stock(symbol: str, db: Session = Depends(get_db)) -> StockOut:
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stock not found")
    return stock
