from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class RankingItem(Base):
    __tablename__ = "ranking_items"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "stock_id", name="uq_ranking_item_snapshot_stock"),
    )

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("ranking_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_symbol = Column(String(32), nullable=False, index=True)
    rank = Column(Integer, nullable=False, index=True)
    total_score = Column(Float, nullable=False)
    news_score = Column(Float, nullable=False)
    stock_score = Column(Float, nullable=False)
    macro_score = Column(Float, nullable=False)
    financial_score = Column(Float, nullable=False)
    fundamental_score = Column(Float, nullable=False)
    data_drive_score = Column(Float, nullable=False)
    emotion_drive_score = Column(Float, nullable=False)
    conflict_signal = Column(Boolean, default=False, nullable=False)
    recommendation_action = Column(String(16), nullable=False)
    recommendation_confidence = Column(Float, nullable=False)
    recommendation_summary = Column(String, nullable=True)
    expert_payload = Column(JSON, default=dict, nullable=False)
    investment_payload = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    snapshot = relationship("RankingSnapshot", back_populates="items")
    stock = relationship("Stock", back_populates="ranking_items")
