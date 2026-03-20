import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.db import SessionLocal
from app.core.market_scope import TARGET_MARKET
from app.models.stock import Stock
from app.services.data_ingest import akshare_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually run static sync for all Shenzhen main-board A shares"
    )
    parser.add_argument("--refresh-universe", action="store_true", default=False)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--sleep-seconds", type=float, default=0.8)
    parser.add_argument("--start", type=int, default=0, help="start index in symbol list")
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit")
    return parser.parse_args()


def _chunked(items: list[str], size: int):
    for idx in range(0, len(items), size):
        yield idx // size + 1, items[idx : idx + size]


def _count_ok(payload: dict) -> int:
    ok = 0
    for item in payload.values():
        if isinstance(item, dict) and "error" in item:
            continue
        ok += 1
    return ok


def main() -> None:
    args = parse_args()
    batch_size = max(1, args.batch_size)

    db = SessionLocal()
    try:
        if args.refresh_universe:
            universe_result = akshare_service.sync_stock_universe(db)
            print("[sync_stock_universe]", universe_result)

        query = db.query(Stock.symbol).filter(Stock.market == TARGET_MARKET).order_by(Stock.symbol.asc())
        symbols = [row[0] for row in query.all()]
        if args.start > 0:
            symbols = symbols[args.start :]
        if args.limit > 0:
            symbols = symbols[: args.limit]

        total = len(symbols)
        if total == 0:
            print(f"[static_all] no symbols found in market scope={TARGET_MARKET}")
            return

        print(
            f"[static_all] scope={TARGET_MARKET} total_symbols={total} "
            f"batch_size={batch_size} sleep={args.sleep_seconds}s"
        )

        done = 0
        profile_ok = 0
        financial_ok = 0
        started_at = datetime.now()

        for batch_no, batch_symbols in _chunked(symbols, batch_size):
            t0 = time.time()
            result = akshare_service.static_sync(db, symbols=batch_symbols)
            took = time.time() - t0

            this_profile_ok = _count_ok(result.get("profiles", {}))
            this_financial_ok = _count_ok(result.get("financials", {}))
            done += len(batch_symbols)
            profile_ok += this_profile_ok
            financial_ok += this_financial_ok

            print(
                f"[batch {batch_no}] symbols={len(batch_symbols)} "
                f"done={done}/{total} profiles_ok={this_profile_ok} "
                f"financials_ok={this_financial_ok} took={took:.1f}s"
            )
            if args.sleep_seconds > 0 and done < total:
                time.sleep(args.sleep_seconds)

        elapsed = (datetime.now() - started_at).total_seconds()
        print(
            f"[static_all_done] total={total} profiles_ok={profile_ok} "
            f"financials_ok={financial_ok} elapsed={elapsed:.1f}s"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()

