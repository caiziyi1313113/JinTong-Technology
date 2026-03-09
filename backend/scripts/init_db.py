import sys
from pathlib import Path

# 项目的“数据库初始化脚本” 创建整个系统需要的所有数据库表

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import Base, engine
from app.models import (
    analysis,
    candidate_pool,
    daily_recap,
    document,
    expert_signal,
    market,
    position,
    profile,
    scan_result,
    stock,
    trade_plan,
    trade_signal,
    user,
)


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("DB initialized")
