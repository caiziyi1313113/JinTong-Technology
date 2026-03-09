from sqlalchemy import Column, Integer, Date, DateTime, String, JSON, func

from app.core.db import Base


class DailyRecap(Base):
    __tablename__ = "daily_recap"

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, unique=True, index=True, nullable=False)
    market_summary = Column(String, nullable=False)
    macro_summary = Column(String, nullable=False)
    top_movers = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
