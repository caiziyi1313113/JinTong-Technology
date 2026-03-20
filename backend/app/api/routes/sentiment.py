from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.market_scope import normalize_symbol
from app.schemas.sentiment import SentimentComputeRequest, SentimentResultOut
from app.sentiment.service import stock_sentiment_service

router = APIRouter(prefix="/sentiment", tags=["sentiment"])


@router.get("/{symbol}/latest", response_model=SentimentResultOut)
def get_latest_sentiment(
    symbol: str,
    days: int = Query(default=30, ge=1, le=180),
    item_limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SentimentResultOut:
    code = normalize_symbol(symbol)
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    result = stock_sentiment_service.get_latest(
        db,
        symbol=code,
        recent_days=days,
        detail_limit=item_limit,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sentiment data not found")
    return SentimentResultOut(**result)


@router.post("/{symbol}/compute", response_model=SentimentResultOut)
def compute_sentiment_for_date(
    symbol: str,
    payload: SentimentComputeRequest,
    db: Session = Depends(get_db),
) -> SentimentResultOut:
    code = normalize_symbol(symbol)
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid symbol")
    target_date = payload.trade_date or date.today()
    try:
        result = stock_sentiment_service.compute_for_date(
            db,
            symbol=code,
            trade_date=target_date,
            max_pages=payload.max_pages,
            max_news=payload.max_news,
            max_guba=payload.max_guba,
            persist=payload.persist,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"sentiment compute failed: {exc}") from exc
    return SentimentResultOut(**result)
