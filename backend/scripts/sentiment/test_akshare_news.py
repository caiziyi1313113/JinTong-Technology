import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sentiment.news_fetcher import fetch_akshare_company_news


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate AKShare company news fetch")
    parser.add_argument("--symbol", required=True, help="stock symbol, e.g. 000056")
    parser.add_argument("--date", required=True, help="target date, format YYYY-MM-DD")
    parser.add_argument("--max-items", type=int, default=60)
    parser.add_argument("--show", type=int, default=10, help="print first N records")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    try:
        rows = fetch_akshare_company_news(
            args.symbol,
            target_date=target_date,
            max_items=args.max_items,
        )
    except Exception as exc:
        print(f"news fetch failed: {exc}")
        return
    print(f"symbol={args.symbol} date={target_date} rows={len(rows)}")
    for item in rows[: max(1, args.show)]:
        print(
            json.dumps(
                {
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "title": item.title,
                    "text": item.text,
                    "source_url": item.source_url,
                    "extra": item.extra,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
