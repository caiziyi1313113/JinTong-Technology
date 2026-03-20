from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship

from app.core.db import Base


class ExpertSignal(Base):
    __tablename__ = "expert_signals"

    id = Column(Integer, primary_key=True, index=True)
    analysis_id = Column(Integer, ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    expert_name = Column(String(64), nullable=False)
    signal = Column(String(32), nullable=False)
    score = Column(Float, nullable=False)
    confidence = Column(Float, nullable=False)
    fallback = Column(Boolean, default=False, nullable=False)
    horizon = Column(String(32), nullable=False)
    key_factors = Column(JSON, default=list, nullable=False)
    risk_flags = Column(JSON, default=list, nullable=False)
    evidence = Column(JSON, default=list, nullable=False)

    analysis = relationship("Analysis", back_populates="expert_signals")
