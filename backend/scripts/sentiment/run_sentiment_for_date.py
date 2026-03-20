import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute stock sentiment for a specified date")
    parser.add_argument("--symbol", required=True, help="stock symbol, e.g. 000056")
    parser.add_argument("--date", required=True, help="target date, format YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--max-news", type=int, default=80)
    parser.add_argument("--max-guba", type=int, default=120)
    parser.add_argument("--no-persist", action="store_true", help="compute only, do not write DB")
    parser.add_argument("--item-limit", type=int, default=5, help="print top N items for each source")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    try:
        from app.core.db import SessionLocal
        from app.sentiment.service import stock_sentiment_service
    except Exception as exc:
        print(f"import failed: {exc}")
        return

    db = SessionLocal()
    try:
        result = stock_sentiment_service.compute_for_date(
            db,
            symbol=args.symbol,
            trade_date=target_date,
            max_pages=args.max_pages,
            max_news=args.max_news,
            max_guba=args.max_guba,
            persist=not args.no_persist,
            detail_limit=max(1, args.item_limit),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
