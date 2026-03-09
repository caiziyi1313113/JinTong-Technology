from sqlalchemy.orm import Session

from app.models.stock import Stock
from app.models.profile import UserProfile
from app.services.rag.retrieval import retrieve_documents, to_evidence
from app.services.sentiment.sentiment import score_text


def run(db: Session, stock: Stock, profile: UserProfile) -> dict:
    docs = retrieve_documents(db, None, ["macro", "policy"], days_back=180, limit=5)
    if docs:
        sentiment = sum(score_text(doc.content) for doc in docs) / len(docs)
        score = max(0.0, min(1.0, 0.5 + sentiment / 10))
        confidence = min(0.8, 0.4 + 0.08 * len(docs))
        key_factors = ["Macro/policy environment"]
    else:
        score = 0.5
        confidence = 0.2
        key_factors = ["No macro documents"]

    signal = "bullish" if score >= 0.6 else "bearish" if score <= 0.4 else "neutral"

    return {
        "expert_name": "macro",
        "signal": signal,
        "score": score,
        "confidence": confidence,
        "horizon": "6-18m",
        "key_factors": key_factors,
        "risk_flags": [],
        "evidence": to_evidence(docs),
    }
