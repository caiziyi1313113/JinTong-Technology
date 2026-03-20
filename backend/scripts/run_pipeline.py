import argparse
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.market_scope import TARGET_MARKET, filter_target_symbols, normalize_symbol
from app.core.db import SessionLocal
from app.services.data_ingest import akshare_service
from app.services.workflow.review_pipeline import run_ranking_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stock intelligence data and ranking pipeline")
    parser.add_argument("--mode", choices=["daily", "static", "ranking", "all"], default="all")
    parser.add_argument("--date", dest="target_date", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--symbols", default="", help="comma separated symbols, e.g. 000001,600519")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--snapshot-type", default="post_close", choices=["post_close", "pre_open", "realtime"])
    parser.add_argument("--history-days", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_date = datetime.strptime(args.target_date, "%Y-%m-%d").date()
    raw_symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    symbols = filter_target_symbols(raw_symbols)
    skipped_symbols = sorted(
        {code for code in (normalize_symbol(s) for s in raw_symbols) if code and code not in symbols}
    )
    if skipped_symbols:
        print(f"[scope={TARGET_MARKET}] skip unsupported symbols: {','.join(skipped_symbols)}")

    db = SessionLocal()
    try:
        if args.mode in {"daily", "all"}:
            daily_result = akshare_service.daily_sync(
                db,
                trade_date=target_date,
                symbols=symbols or None,
                history_days=args.history_days,
                include_block_trade=True,
                include_news=True,
                include_macro=True,
            )
            print("[daily_sync]", daily_result)

        if args.mode in {"static", "all"}:
            if not symbols:
                raise ValueError(
                    f"static mode requires --symbols in {TARGET_MARKET} scope, e.g. --symbols 000001,002594"
                )
            static_result = akshare_service.static_sync(db, symbols=symbols)
            print("[static_sync]", static_result)

        if args.mode in {"ranking", "all"}:
            snapshot = run_ranking_snapshot(
                db,
                snapshot_date=target_date,
                snapshot_type=args.snapshot_type,
                top_n=args.top_n,
                symbols=symbols or None,
            )
            print(
                "[ranking]",
                {
                    "snapshot_id": snapshot.id,
                    "date": snapshot.snapshot_date.isoformat(),
                    "type": snapshot.snapshot_type,
                    "summary": snapshot.summary,
                },
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
