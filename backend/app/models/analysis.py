from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship

from app.core.db import Base

# 数据库中的结构
class Analysis(Base):
    __tablename__ = "analyses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    final_action = Column(String(32), nullable=False)
    position_size = Column(Float, nullable=False)
    rationale = Column(JSON, default=dict, nullable=False)
    risk_notes = Column(JSON, default=list, nullable=False)

    user = relationship("User", back_populates="analyses")
    stock = relationship("Stock", back_populates="analyses")
    expert_signals = relationship("ExpertSignal", back_populates="analysis", cascade="all, delete-orphan")
