from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr


class RegisterProfileInput(BaseModel):
    assets: float | None = None
    disposable_funds: float | None = None
    income: float | None = None
    risk_level: str | None = None
    investment_horizon: str | None = None
    style: str | None = None
    persona: str | None = None
    questionnaire_answers: dict[str, Any] | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    profile: RegisterProfileInput | None = None


class UserOut(BaseModel):
    id: int
    email: EmailStr
    created_at: datetime

    model_config = {"from_attributes": True}
