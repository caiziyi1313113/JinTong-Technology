from statistics import mean, pstdev

from sqlalchemy.orm import Session

from app.models.market import MarketData
from app.models.stock import Stock
from app.models.profile import UserProfile


def run(db: Session, stock: Stock, profile: UserProfile) -> dict:
    rows = (
        db.query(MarketData)
        .filter(MarketData.stock_id == stock.id)
        .order_by(MarketData.date.desc())
        .limit(60)
        .all()
    )
    if len(rows) < 20:
        return {
            "expert_name": "technical",
            "signal": "neutral",
            "score": 0.5,
            "confidence": 0.2,
            "horizon": "5-20d",
            "key_factors": ["Insufficient market data"],
            "risk_flags": ["low_liquidity_data"],
            "evidence": [],
        }

    closes = [row.close for row in reversed(rows)]
    sma5 = mean(closes[-5:])
    sma20 = mean(closes[-20:])
    momentum = (closes[-1] - closes[-5]) / max(1e-6, closes[-5])
    volatility = pstdev(closes[-20:]) / max(1e-6, mean(closes[-20:]))

    score = 0.5 + (0.2 if sma5 > sma20 else -0.2) + max(-0.1, min(0.1, momentum))
    score = max(0.0, min(1.0, score))
    confidence = min(0.9, 0.4 + 0.01 * len(rows))
    signal = "bullish" if score >= 0.6 else "bearish" if score <= 0.4 else "neutral"

    key_factors = [
        f"SMA5={sma5:.2f} vs SMA20={sma20:.2f}",
        f"Momentum={momentum:.2%}",
        f"Volatility={volatility:.2%}",
    ]
    risk_flags = ["high_volatility"] if volatility > 0.05 else []

    return {
        "expert_name": "technical",
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "horizon": "5-20d",
        "key_factors": key_factors,
        "risk_flags": risk_flags,
        "evidence": [],
    }
