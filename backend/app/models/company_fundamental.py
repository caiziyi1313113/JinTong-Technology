from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, JSON, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.db import Base


class CompanyFundamental(Base):
    __tablename__ = "company_fundamentals"
    __table_args__ = (
        UniqueConstraint("stock_id", "snapshot_date", name="uq_company_fundamental_snapshot"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    industry = Column(String(255), nullable=True)
    listed_date = Column(Date, nullable=True)
    legal_representative = Column(String(128), nullable=True)
    chairman = Column(String(128), nullable=True)
    general_manager = Column(String(128), nullable=True)
    staff_num = Column(Integer, nullable=True)
    main_business = Column(String, nullable=True)
    business_scope = Column(String, nullable=True)
    company_intro = Column(String, nullable=True)
    management_info = Column(JSON, default=dict, nullable=False)
    raw = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    stock = relationship("Stock", back_populates="fundamentals")
