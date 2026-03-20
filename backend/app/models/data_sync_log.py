from sqlalchemy import Column, Integer, String, DateTime, JSON, func

from app.core.db import Base


class DataSyncLog(Base):
    __tablename__ = "data_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String(64), nullable=False, index=True)
    scope = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, index=True)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True)
    detail = Column(JSON, default=dict, nullable=False)
    error_message = Column(String, nullable=True)
