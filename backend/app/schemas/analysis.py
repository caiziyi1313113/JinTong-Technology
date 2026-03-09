from datetime import datetime
from typing import Dict, Any, List
from pydantic import BaseModel

from app.schemas.expert import ExpertSignalOut

# 这个文件定义了分析相关的 Pydantic 模型，主要用于接口请求和响应的数据验证和序列化。
# 比如前端发送的请求的格式
# 比如返回给前端的数据格式
class AnalysisRequest(BaseModel):
    user_id: int
    stock_symbol: str


class AnalysisOut(BaseModel):
    id: int
    user_id: int
    stock_symbol: str
    created_at: datetime
    final_action: str
    position_size: float
    rationale: Dict[str, Any]
    risk_notes: List[str]
    expert_signals: List[ExpertSignalOut]

    model_config = {"from_attributes": True}
