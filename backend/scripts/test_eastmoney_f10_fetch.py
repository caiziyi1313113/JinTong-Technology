import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args_1() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quick test: crawl Eastmoney F10 financial datasets and write cleaned rows into DB."
    )
    parser.add_argument("--symbol", default="002150", help="A-share symbol, e.g. 002150")
    parser.add_argument("--limit", type=int, default=24, help="Max rows per dataset to keep")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.35,
        help="Request interval seconds between F10 datasets (default: 0.35)",
    )
    parser.add_argument("--show-rows", type=int, default=8, help="Show latest N stored eastmoney_f10 rows")
    parser.add_argument(
        "--assert-records",
        action="store_true",
        help="Exit with non-zero status when no Eastmoney records were fetched/written",
    )
    parser.add_argument(
        "--datasets",
        default="",
        help="Optional dataset keys split by comma, e.g. GBALANCE,GINCOME,GCASHFLOW,GRATIO",
    )
    parser.add_argument(
        "--max-report-dates",
        type=int,
        default=12,
        help="Max report dates discovered for each dataset (dynamic, no hard-coded dates)",
    )
    parser.add_argument("--show-events", type=int, default=5, help="Show latest N stored eastmoney event rows")
    return parser.parse_args()


def _normalize_any_symbol(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        left, right = text.split(".", 1)
        if left.isdigit() and right in {"SH", "SZ", "BJ"}:
            return left.zfill(6)
    if text.startswith(("SH", "SZ", "BJ")) and text[2:].isdigit():
        return text[2:].zfill(6)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return text


def _parse_dataset_keys(text: str) -> Set[str]:
    values: Iterable[str] = str(text or "").replace(";", ",").split(",")
    return {item.strip().upper() for item in values if item and item.strip()}


def main() -> None:
    args = parse_args()
    try:
        from sqlalchemy import func
        from app.core.db import SessionLocal
        from app.core.market_scope import market_from_symbol
        from app.models.company_financial import CompanyFinancial
        from app.models.company_financial_event import CompanyFinancialEvent
        from app.models.stock import Stock
        from app.services.data_ingest import akshare_service
    except Exception as exc:  # pragma: no cover - runtime env dependency guard
        raise SystemExit(
            "Import failed. Please install backend dependencies (notably SQLAlchemy>=2) first. "
            f"Original error: {exc}"
        ) from exc

    symbol = _normalize_any_symbol(args.symbol)
    market = market_from_symbol(symbol)
    if market == "UNKNOWN":
        raise SystemExit(f"Unsupported/invalid symbol={args.symbol} -> normalized={symbol}")
    dataset_keys = _parse_dataset_keys(args.datasets)

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            stock = Stock(symbol=symbol, name=symbol, market=market, sector=None)
            db.add(stock)
            db.flush()

        before_count = (
            db.query(func.count(CompanyFinancial.id))
            .filter(
                CompanyFinancial.stock_id == stock.id,
                CompanyFinancial.source == "eastmoney_f10",
            )
            .scalar()
            or 0
        )
        before_event_count = (
            db.query(func.count(CompanyFinancialEvent.id))
            .filter(
                CompanyFinancialEvent.stock_id == stock.id,
                CompanyFinancialEvent.source == "eastmoney_f10",
            )
            .scalar()
            or 0
        )
        print(
            f"symbol={symbol} market={market} stock_id={stock.id} "
            f"before_eastmoney_rows={int(before_count)} before_event_rows={int(before_event_count)}"
        )
        if dataset_keys:
            print(f"dataset_keys={sorted(dataset_keys)}")

        result = akshare_service._sync_company_financial_eastmoney_f10(
            db,
            stock=stock,
            code=symbol,
            limit=max(1, int(args.limit)),
            request_interval_seconds=max(0.05, float(args.interval)),
            dataset_keys=dataset_keys or None,
            max_report_dates=max(1, int(args.max_report_dates)),
        )
        print("sync_result=")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

        after_count = (
            db.query(func.count(CompanyFinancial.id))
            .filter(
                CompanyFinancial.stock_id == stock.id,
                CompanyFinancial.source == "eastmoney_f10",
            )
            .scalar()
            or 0
        )
        after_event_count = (
            db.query(func.count(CompanyFinancialEvent.id))
            .filter(
                CompanyFinancialEvent.stock_id == stock.id,
                CompanyFinancialEvent.source == "eastmoney_f10",
            )
            .scalar()
            or 0
        )
        print(
            f"after_eastmoney_rows={int(after_count)} delta={int(after_count) - int(before_count)} "
            f"after_event_rows={int(after_event_count)} event_delta={int(after_event_count) - int(before_event_count)}"
        )

        rows = (
            db.query(CompanyFinancial)
            .filter(
                CompanyFinancial.stock_id == stock.id,
                CompanyFinancial.source == "eastmoney_f10",
            )
            .order_by(CompanyFinancial.report_date.desc(), CompanyFinancial.updated_at.desc())
            .limit(max(1, int(args.show_rows)))
            .all()
        )
        if rows:
            print("\nlatest_rows:")
            for row in rows:
                print(
                    json.dumps(
                        {
                            "report_date": row.report_date.isoformat(),
                            "report_name": row.report_name,
                            "dataset": row.dataset,
                            "eps": row.eps,
                            "revenue": row.revenue,
                            "net_profit": row.net_profit,
                            "roe": row.roe,
                            "asset_liability_ratio": row.asset_liability_ratio,
                        },
                        ensure_ascii=False,
                    )
                )
        else:
            print("\nlatest_rows: none")

        event_rows = (
            db.query(CompanyFinancialEvent)
            .filter(
                CompanyFinancialEvent.stock_id == stock.id,
                CompanyFinancialEvent.source == "eastmoney_f10",
            )
            .order_by(CompanyFinancialEvent.event_date.desc(), CompanyFinancialEvent.updated_at.desc())
            .limit(max(1, int(args.show_events)))
            .all()
        )
        if event_rows:
            print("\nlatest_event_rows:")
            for row in event_rows:
                print(
                    json.dumps(
                        {
                            "event_date": row.event_date.isoformat(),
                            "event_name": row.event_name,
                            "event_type": row.event_type,
                            "dataset": row.dataset,
                        },
                        ensure_ascii=False,
                    )
                )
        else:
            print("\nlatest_event_rows: none")

        if args.assert_records and int(result.get("records") or 0) <= 0:
            raise SystemExit("ASSERT_RECORDS failed: Eastmoney F10 returned no records.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
