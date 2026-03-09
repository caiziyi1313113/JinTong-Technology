from sqlalchemy import Column, Integer, String, Float, Date, DateTime, JSON, func

from app.core.db import Base


class CandidatePool(Base):
    __tablename__ = "candidate_pool"

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, index=True, nullable=False)
    stock_symbol = Column(String(32), index=True, nullable=False)
    sentiment_score = Column(Float, nullable=False)
    data_score = Column(Float, nullable=False)
    total_score = Column(Float, nullable=False)
    reasons = Column(JSON, default=list, nullable=False)
    evidence = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
