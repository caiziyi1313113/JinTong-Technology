from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.core.db import Base


class CompanyFinancialEvent(Base):
    __tablename__ = "company_financial_events"
    __table_args__ = (
        UniqueConstraint("stock_id", "event_date", "event_name", name="uq_company_financial_event"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_date = Column(Date, nullable=False, index=True)
    event_name = Column(String(96), nullable=False)
    event_type = Column(String(64), nullable=True)
    source = Column(String(64), nullable=True, index=True)
    dataset = Column(String(64), nullable=True, index=True)
    row_key = Column(String(128), nullable=True, index=True)
    object_id = Column(BigInteger, nullable=True, index=True)
    change_code = Column(Integer, nullable=True)
    declare_date = Column(Date, nullable=True, index=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    raw = Column(JSON, default=dict, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="financial_events")
