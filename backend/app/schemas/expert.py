from typing import List, Dict, Any
from pydantic import BaseModel


class ExpertSignalOut(BaseModel):
    expert_name: str
    signal: str
    score: float
    confidence: float
    horizon: str
    key_factors: List[str]
    risk_flags: List[str]
    evidence: List[Dict[str, Any]]

    model_config = {"from_attributes": True}
