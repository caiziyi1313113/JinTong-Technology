from datetime import datetime, timedelta
import random
import sys
from pathlib import Path

# “演示数据生成脚本”,生成一批测试数据（Demo Data）

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import SessionLocal
from app import models  # noqa: F401
from app.models.document import Document
from app.models.market import MarketData
from app.models.stock import Stock


NEWS_SNIPPETS = [
    "Company reports strong growth and record revenue.",
    "Management highlights innovation and expansion plans.",
    "Supply chain headwinds and volatility remain.",
    "Regulatory review creates short-term uncertainty.",
]

MACRO_SNIPPETS = [
    "Policy stance remains supportive with gradual easing.",
    "Inflation pressures show signs of stabilizing.",
]


if __name__ == "__main__":
    db = SessionLocal()
    symbol = "APL"
    stock = db.query(Stock).filter(Stock.symbol == symbol).first()
    if not stock:
        stock = Stock(symbol=symbol, name="Apple Inc.", market="NASDAQ", sector="Technology")
        db.add(stock)
        db.commit()
        db.refresh(stock)

    # Seed market data
    today = datetime.utcnow().date()
    price = 180.0
    for i in range(90):
        date = today - timedelta(days=90 - i)
        change = random.uniform(-1.5, 1.5)
        open_p = price + random.uniform(-0.5, 0.5)
        close_p = max(1.0, price + change)
        high = max(open_p, close_p) + random.uniform(0, 0.8)
        low = min(open_p, close_p) - random.uniform(0, 0.8)
        volume = random.uniform(50_000_000, 120_000_000)
        price = close_p
        db.add(
            MarketData(
                stock_id=stock.id,
                date=date,
                open=open_p,
                high=high,
                low=low,
                close=close_p,
                volume=volume,
            )
        )

    # Seed documents
    for idx, text in enumerate(NEWS_SNIPPETS):
        db.add(
            Document(
                stock_symbol=symbol,
                doc_type="news",
                title=f"News headline {idx+1}",
                content=text,
                source="seed",
                published_at=datetime.utcnow() - timedelta(days=idx + 1),
                doc_metadata={},
            )
        )

    db.add(
        Document(
            stock_symbol=symbol,
            doc_type="fundamental",
            title="Company overview",
            content="Leading brand with resilient demand and innovation.",
            source="seed",
            published_at=datetime.utcnow() - timedelta(days=30),
            doc_metadata={},
        )
    )

    db.add(
        Document(
            stock_symbol=symbol,
            doc_type="financial",
            title="Quarterly report",
            content="Reported profit growth with strong margins.",
            source="seed",
            published_at=datetime.utcnow() - timedelta(days=45),
            doc_metadata={},
        )
    )

    for idx, text in enumerate(MACRO_SNIPPETS):
        db.add(
            Document(
                stock_symbol=None,
                doc_type="macro",
                title=f"Macro update {idx+1}",
                content=text,
                source="seed",
                published_at=datetime.utcnow() - timedelta(days=20 + idx),
                doc_metadata={},
            )
        )

    db.commit()
    db.close()
    print("Seed data inserted")
