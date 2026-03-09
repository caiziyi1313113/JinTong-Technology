from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.profile import UserProfile
from app.services.rag.retrieval import retrieve_documents, to_evidence
from app.services.sentiment.sentiment import score_text


def run(db: Session, stock: Stock, profile: UserProfile) -> dict:
    docs = retrieve_documents(db, stock.symbol, ["company", "fundamental", "research"], days_back=365, limit=5)
    if docs:
        sentiment = sum(score_text(doc.content) for doc in docs) / len(docs)
        score = max(0.0, min(1.0, 0.5 + sentiment / 10))
        confidence = min(0.9, 0.4 + 0.1 * len(docs))
        key_factors = ["Long-term positioning from company/industry documents"]
    else:
        score = 0.5
        confidence = 0.2
        key_factors = ["Insufficient company/fundamental documents"]

    if score >= 0.6:
        signal = "bullish"
    elif score <= 0.4:
        signal = "bearish"
    else:
        signal = "neutral"

    return {
        "expert_name": "fundamental",
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "horizon": "6m+",
        "key_factors": key_factors,
        "risk_flags": ["low_coverage"] if not docs else [],
        "evidence": to_evidence(docs),
    }
