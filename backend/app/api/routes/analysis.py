from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.analysis import Analysis
from app.models.expert_signal import ExpertSignal
from app.models.market import MarketData
from app.models.position import Position
from app.models.profile import UserProfile
from app.models.stock import Stock
from app.models.user import User
from app.schemas.analysis import AnalysisOut, AnalysisRequest
from app.services.decision.engine import fuse_signals
from app.services.experts import fundamental, financial, macro, news, technical

# FastAPI 后端的 API 路由模块
# 每个文件负责一个 业务模块（功能）。
# 整体是一个 股票分析 / 交易系统的后端 API。
router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("", response_model=AnalysisOut)
def create_analysis(
    payload: AnalysisRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User mismatch")

    stock = db.query(Stock).filter(Stock.symbol == payload.stock_symbol).first()
    if not stock:
        stock = Stock(symbol=payload.stock_symbol, name=payload.stock_symbol, market="Unknown")
        db.add(stock)
        db.commit()
        db.refresh(stock)

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)

    signals = [
        fundamental.run(db, stock, profile),
        financial.run(db, stock, profile),
        technical.run(db, stock, profile),
        news.run(db, stock, profile),
        macro.run(db, stock, profile),
    ]

    latest_market = (
        db.query(MarketData)
        .filter(MarketData.stock_id == stock.id)
        .order_by(MarketData.date.desc())
        .first()
    )
    current_price = float(latest_market.close) if latest_market else 1.0
    current_position = (
        db.query(Position)
        .filter(Position.user_id == current_user.id, Position.stock_id == stock.id, Position.status == "open")
        .order_by(Position.updated_at.desc())
        .first()
    )
    position_payload = (
        {"quantity": current_position.quantity, "avg_price": current_position.avg_price}
        if current_position
        else None
    )

    fused = fuse_signals(profile, signals, current_price=current_price, position=position_payload)

    analysis = Analysis(
        user_id=current_user.id,
        stock_id=stock.id,
        final_action=fused["action"],
        position_size=fused["position_size"],
        rationale=fused["rationale"],
        risk_notes=fused["risk_notes"],
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    for signal in signals:
        db.add(
            ExpertSignal(
                analysis_id=analysis.id,
                expert_name=signal["expert_name"],
                signal=signal["signal"],
                score=signal["score"],
                confidence=signal["confidence"],
                horizon=signal["horizon"],
                key_factors=signal["key_factors"],
                risk_flags=signal["risk_flags"],
                evidence=signal["evidence"],
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
        expert_signals=signals,
    )


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
