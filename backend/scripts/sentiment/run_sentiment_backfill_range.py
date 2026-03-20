import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stock sentiment for date range [start, end]")
    parser.add_argument("--symbol", required=True, help="stock symbol, e.g. 000056")
    parser.add_argument("--start-date", required=True, help="start date, format YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="end date, format YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=5, help="max guba pages per day")
    parser.add_argument("--max-news", type=int, default=80, help="max news rows per day")
    parser.add_argument("--max-guba", type=int, default=120, help="max guba rows per day")
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="sleep between dates")
    parser.add_argument("--no-persist", action="store_true", help="compute only, do not write DB")
    parser.add_argument("--continue-on-error", action="store_true", help="continue if one day fails")
    parser.add_argument("--print-each", action="store_true", help="print each day result")
    return parser.parse_args()


def date_range_inclusive(start_dt, end_dt):
    current = start_dt
    while current <= end_dt:
        yield current
        current += timedelta(days=1)


def main() -> None:
    args = parse_args()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if end_date < start_date:
        print("end-date must be >= start-date")
        return

    try:
        from app.core.db import SessionLocal
        from app.sentiment.service import stock_sentiment_service
    except Exception as exc:
        print(f"import failed: {exc}")
        return

    db = SessionLocal()
    summary = {
        "symbol": args.symbol,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_days": (end_date - start_date).days + 1,
        "ok_days": 0,
        "failed_days": 0,
        "details": [],
    }
    try:
        for current_date in date_range_inclusive(start_date, end_date):
            one = {"trade_date": current_date.isoformat(), "ok": False}
            try:
                result = stock_sentiment_service.compute_for_date(
                    db,
                    symbol=args.symbol,
                    trade_date=current_date,
                    max_pages=args.max_pages,
                    max_news=args.max_news,
                    max_guba=args.max_guba,
                    persist=not args.no_persist,
                )
                latest = result.get("latest", {}) if isinstance(result, dict) else {}
                one.update(
                    {
                        "ok": True,
                        "combined_score_norm": latest.get("combined_score_norm"),
                        "news_count": latest.get("news_count"),
                        "guba_count": latest.get("guba_count"),
                        "trend_signal": latest.get("trend_signal"),
                    }
                )
                summary["ok_days"] += 1
                if args.print_each:
                    print(json.dumps(one, ensure_ascii=False))
            except Exception as exc:
                one.update({"ok": False, "error": str(exc)})
                summary["failed_days"] += 1
                print(json.dumps(one, ensure_ascii=False))
                if not args.continue_on_error:
                    summary["details"].append(one)
                    break
            summary["details"].append(one)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    finally:
        db.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
