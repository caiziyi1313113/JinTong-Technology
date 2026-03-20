from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ProfileBase(BaseModel):
    risk_level: str = "medium"
    investment_horizon: str = "long"
    income: float = 0.0
    assets: float = 0.0
    disposable_funds: float = 0.0
    experience_years: float = 0.0
    max_drawdown: float = 0.2
    risk_budget: float = 0.02
    target_return: float = 0.12
    max_single_position: float = 0.15
    style: str = "balanced"
    persona: str = "balanced_growth"
    questionnaire_answers: Dict[str, Any] = Field(default_factory=dict)
    preferences: Dict[str, Any] = Field(default_factory=dict)


class ProfileUpdate(BaseModel):
    risk_level: Optional[str] = None
    investment_horizon: Optional[str] = None
    income: Optional[float] = None
    assets: Optional[float] = None
    disposable_funds: Optional[float] = None
    experience_years: Optional[float] = None
    max_drawdown: Optional[float] = None
    risk_budget: Optional[float] = None
    target_return: Optional[float] = None
    max_single_position: Optional[float] = None
    style: Optional[str] = None
    persona: Optional[str] = None
    questionnaire_answers: Optional[Dict[str, Any]] = None
    preferences: Optional[Dict[str, Any]] = None
    current_password: Optional[str] = None


class ProfileOut(ProfileBase):
    id: int
    user_id: int

    model_config = {"from_attributes": True}
