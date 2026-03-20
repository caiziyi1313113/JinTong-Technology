from sqlalchemy import Column, Integer, String, Date, DateTime, JSON, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "snapshot_type", name="uq_ranking_snapshot_date_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    snapshot_type = Column(String(32), nullable=False, index=True)  # post_close/pre_open/realtime
    status = Column(String(32), default="completed", nullable=False)
    summary = Column(JSON, default=dict, nullable=False)
    generated_at = Column(DateTime, server_default=func.now(), nullable=False)

    items = relationship("RankingItem", back_populates="snapshot", cascade="all, delete-orphan")
