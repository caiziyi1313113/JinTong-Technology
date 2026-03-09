from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.document import Document
from app.schemas.document import DocumentCreate, DocumentOut

'''
存储股票相关文档
可以存储：
财报
新闻
研究报告
公告
'''

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentOut)
def ingest_document(payload: DocumentCreate, db: Session = Depends(get_db)) -> DocumentOut:
    raw = payload.model_dump()
    metadata = raw.pop("metadata", {})
    doc = Document(**raw, doc_metadata=metadata)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    stock_symbol: str | None = None,
    doc_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[DocumentOut]:
    query = db.query(Document)
    if stock_symbol:
        query = query.filter(Document.stock_symbol == stock_symbol)
    if doc_type:
        query = query.filter(Document.doc_type == doc_type)
    return query.order_by(Document.created_at.desc()).limit(100).all()
