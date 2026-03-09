from sqlalchemy import Column, Integer, String, Float, Date, DateTime, JSON, func

from app.core.db import Base


class ScanResult(Base):
    __tablename__ = "scan_result"

    id = Column(Integer, primary_key=True, index=True)
    scan_date = Column(Date, index=True, nullable=False)
    stock_symbol = Column(String(32), index=True, nullable=False)
    rank = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)
    action = Column(String(32), nullable=False)
    notes = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
