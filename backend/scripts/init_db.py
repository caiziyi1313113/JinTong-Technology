import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import Base, engine
from app.core.schema_compat import apply_schema_compat_migrations
from app.models import (
    analysis,
    block_trade,
    candidate_pool,
    company_financial,
    company_financial_event,
    company_fundamental,
    daily_recap,
    data_sync_log,
    document,
    expert_signal,
    market,
    portfolio_trade,
    position,
    profile,
    ranking_item,
    ranking_snapshot,
    scan_result,
    stock,
    stock_kline,
    stock_quote,
    trade_plan,
    trade_signal,
    user,
    user_stock_holding,
)


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    migration_result = apply_schema_compat_migrations(engine)
    print("DB initialized")
    print("Schema compatibility migrations:", migration_result)
