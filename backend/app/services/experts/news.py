from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.profile import UserProfile
from app.services.rag.retrieval import retrieve_documents, to_evidence
from app.services.sentiment.sentiment import score_text


def run(db: Session, stock: Stock, profile: UserProfile) -> dict:
    docs = retrieve_documents(db, stock.symbol, ["news", "announcement"], days_back=30, limit=6)
    if docs:
        sentiment = sum(score_text(doc.content) for doc in docs) / len(docs)
        score = max(0.0, min(1.0, 0.5 + sentiment / 8))
        confidence = min(0.85, 0.5 + 0.08 * len(docs))
        key_factors = ["Recent news sentiment"]
    else:
        score = 0.5
        confidence = 0.2
        key_factors = ["No recent news"]

    signal = "bullish" if score >= 0.6 else "bearish" if score <= 0.4 else "neutral"

    return {
        "expert_name": "news",
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "horizon": "1-30d",
        "key_factors": key_factors,
        "risk_flags": ["headline_risk"] if docs else [],
        "evidence": to_evidence(docs),
    }
