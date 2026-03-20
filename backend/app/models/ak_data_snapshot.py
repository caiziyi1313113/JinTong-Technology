from sqlalchemy import Column, Date, DateTime, Integer, JSON, String, UniqueConstraint, func

from app.core.db import Base


class AkDataSnapshot(Base):
    """
    Layered AKShare storage:
    - raw: original API rows (trimmed)
    - normalized: normalized key metrics used by downstream modules
    """

    __tablename__ = "ak_data_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_key",
            "snapshot_date",
            "layer",
            "stock_symbol",
            name="uq_ak_snapshot_key_date_layer_symbol",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    snapshot_key = Column(String(64), nullable=False, index=True)
    snapshot_date = Column(Date, nullable=False, index=True)
    layer = Column(String(16), nullable=False, index=True)  # raw | normalized
    stock_symbol = Column(String(32), nullable=True, index=True)
    source = Column(String(64), nullable=True)
    summary = Column(String(512), nullable=True)
    payload = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

