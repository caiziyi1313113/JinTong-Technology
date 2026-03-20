from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.core.db import Base


class CompanyFinancial(Base):
    __tablename__ = "company_financials"
    __table_args__ = (
        UniqueConstraint("stock_id", "report_date", "report_name", name="uq_company_financial_report"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    report_name = Column(String(64), nullable=False)
    report_type = Column(String(64), nullable=True)
    eps = Column(Float, nullable=True)
    revenue = Column(Float, nullable=True)
    net_profit = Column(Float, nullable=True)
    gross_margin = Column(Float, nullable=True)
    roe = Column(Float, nullable=True)
    asset_liability_ratio = Column(Float, nullable=True)
    operating_cashflow = Column(Float, nullable=True)
    yoy_revenue = Column(Float, nullable=True)
    yoy_net_profit = Column(Float, nullable=True)
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

    stock = relationship("Stock", back_populates="financials")
