from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.core.db import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    risk_level = Column(String(32), default="medium", nullable=False)
    investment_horizon = Column(String(32), default="long", nullable=False)
    income = Column(Float, default=0.0, nullable=False)
    assets = Column(Float, default=0.0, nullable=False)
    disposable_funds = Column(Float, default=0.0, nullable=False)
    experience_years = Column(Float, default=0.0, nullable=False)
    max_drawdown = Column(Float, default=0.2, nullable=False)
    risk_budget = Column(Float, default=0.02, nullable=False)
    target_return = Column(Float, default=0.12, nullable=False)
    max_single_position = Column(Float, default=0.15, nullable=False)
    style = Column(String(32), default="balanced", nullable=False)
    persona = Column(String(64), default="balanced_growth", nullable=False)
    questionnaire_answers = Column(JSON, default=dict, nullable=False)
    preferences = Column(JSON, default=dict, nullable=False)

    user = relationship("User", back_populates="profile")
