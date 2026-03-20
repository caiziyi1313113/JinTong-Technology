from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    stock_id: Optional[int] = None
    stock_symbol: Optional[str] = None
    doc_type: str
    title: str
    content: str
    source: Optional[str] = None
    published_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DocumentOut(BaseModel):
    id: int
    stock_id: Optional[int]
    stock_symbol: Optional[str]
    doc_type: str
    title: str
    content: str
    source: Optional[str]
    published_at: Optional[datetime]
    metadata: Dict[str, Any] = Field(default_factory=dict, alias="doc_metadata")

    model_config = {"from_attributes": True, "populate_by_name": True}
