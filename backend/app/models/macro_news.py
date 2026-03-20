from sqlalchemy import Column, DateTime, Integer, JSON, String, func

from app.core.db import Base


class MacroNews(Base):
    __tablename__ = "macro_news"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    content = Column(String, nullable=False)
    source = Column(String(255), nullable=True)
    published_at = Column(DateTime, nullable=True, index=True)
    news_metadata = Column("metadata", JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
