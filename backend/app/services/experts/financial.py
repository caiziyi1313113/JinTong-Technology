from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.profile import UserProfile
from app.services.rag.retrieval import retrieve_documents, to_evidence
from app.services.sentiment.sentiment import score_text


def run(db: Session, stock: Stock, profile: UserProfile) -> dict:
    docs = retrieve_documents(db, stock.symbol, ["financial", "earnings", "report"], days_back=365, limit=5)
    if docs:
        sentiment = sum(score_text(doc.content) for doc in docs) / len(docs)
        score = max(0.0, min(1.0, 0.5 + sentiment / 12))
        confidence = min(0.85, 0.4 + 0.1 * len(docs))
        key_factors = ["Recent financial report tone"]
    else:
        score = 0.5
        confidence = 0.2
        key_factors = ["No recent financial report text"]

    signal = "bullish" if score >= 0.6 else "bearish" if score <= 0.4 else "neutral"

    return {
        "expert_name": "financial",
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "horizon": "3-12m",
        "key_factors": key_factors,
        "risk_flags": ["low_financial_coverage"] if not docs else [],
        "evidence": to_evidence(docs),
    }
