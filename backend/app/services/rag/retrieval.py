from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.document import Document

'''
retrieval.py：从数据库里“捞文档”+ 生成证据片段（RAG 检索层）

它干两件事：

retrieval

retrieve_documents(...)
按：

stock_symbol（可选）

doc_types（news/macro/financial…）

days_back（只取最近 N 天）

limit
从 Document 表里取最新文档。

to_evidence(docs)
把文档转成给前端/LLM可读的证据列表：source/date/title/snippet/doc_type（截取前 280 字）。

retrieval
'''
def retrieve_documents(
    db: Session,
    stock_symbol: str | None,
    doc_types: Iterable[str] | None = None,
    days_back: int | None = None,
    limit: int = 5,
) -> list[Document]:
    query = db.query(Document)
    if stock_symbol:
        query = query.filter(Document.stock_symbol == stock_symbol)
    if doc_types:
        query = query.filter(Document.doc_type.in_(list(doc_types)))
    if days_back:
        since = datetime.utcnow() - timedelta(days=days_back)
        query = query.filter(Document.published_at.isnot(None)).filter(Document.published_at >= since)
    return query.order_by(Document.published_at.desc().nullslast(), Document.created_at.desc()).limit(limit).all()


def to_evidence(documents: list[Document]) -> list[dict]:
    evidence = []
    for doc in documents:
        snippet = doc.content[:280]
        evidence.append(
            {
                "source": doc.source or "internal",
                "date": doc.published_at.isoformat() if doc.published_at else None,
                "title": doc.title,
                "snippet": snippet,
                "doc_type": doc.doc_type,
            }
        )
    return evidence
