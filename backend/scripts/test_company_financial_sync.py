import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import func, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.market_scope import is_target_symbol, normalize_symbol
from app.models.ak_data_snapshot import AkDataSnapshot
from app.models.company_financial import CompanyFinancial
from app.models.company_financial_event import CompanyFinancialEvent
from app.models.stock import Stock
from app.services.data_ingest import akshare_service
from app.services.data_ingest.cninfo_service import cninfo_client


def _mask(text: str, left: int = 8, right: int = 6) -> str:
    value = str(text or "")
    if len(value) <= left + right:
        return "*" * len(value)
    return f"{value[:left]}...{value[-right:]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test company_financials sync for one symbol and inspect DB write results."
    )
    parser.add_argument("--symbol", default="000001", help="A-share symbol, e.g. 000001")
    parser.add_argument("--limit", type=int, default=24, help="sync_company_financial limit parameter")
    parser.add_argument("--show-rows", type=int, default=12, help="print latest N rows from company_financials")
    parser.add_argument(
        "--truncate-symbol",
        action="store_true",
        help="delete existing company_financials rows for this symbol before sync",
    )
    parser.add_argument(
        "--probe-cninfo",
        action="store_true",
        help="also probe CNInfo endpoint /api/stock/p_stock2399 for this symbol",
    )
    parser.add_argument(
        "--auto-bootstrap",
        action="store_true",
        help="enable runtime auto bootstrap (Playwright) when CNInfo headers are missing/expired",
    )
    parser.add_argument(
        "--bootstrap-headless",
        action="store_true",
        help="when --auto-bootstrap is used, run Playwright in headless mode",
    )
    parser.add_argument(
        "--assert-write",
        action="store_true",
        help="exit with non-zero code if no insert/delete/update change is observed",
    )
    return parser.parse_args()


def print_env_diagnostics(symbol: str) -> None:
    print("=== ENV Diagnostics ===")
    print(f"database_url={settings.database_url}")
    print(f"symbol={symbol}")
    print(f"cninfo_enabled={settings.cninfo_enabled}")
    print(f"cninfo_financial_strict={settings.cninfo_financial_strict}")
    print(f"cninfo_client.enabled={cninfo_client.enabled}")
    print(f"cninfo_base_url={settings.cninfo_base_url}")
    print(f"cninfo_accept_enckey={_mask(settings.cninfo_accept_enckey)}")
    print(f"cninfo_cookie={_mask(settings.cninfo_cookie, left=12, right=10)}")
    print(f"cninfo_auto_bootstrap={settings.cninfo_auto_bootstrap}")
    print(f"cninfo_bootstrap_headless={settings.cninfo_bootstrap_headless}")
    print(f"cninfo_header_max_age_seconds={settings.cninfo_header_max_age_seconds}")
    print("")


def probe_cninfo_latest_report(symbol: str) -> None:
    print("=== CNInfo Probe: /api/stock/p_stock2399 ===")
    try:
        response = cninfo_client.request(
            "/api/stock/p_stock2399",
            params={"scode": symbol, "format": "json"},
            method="GET",
        )
        print(f"probe records={len(response.records)} object_id_max={response.object_id_max}")
        if response.records:
            print("first_record=", json.dumps(response.records[0], ensure_ascii=False, default=str))
    except Exception as exc:
        print(f"probe failed: {exc}")
    print("")


def print_db_diagnostics(db) -> None:
    print("=== DB Diagnostics ===")
    try:
        row = db.execute(
            text(
                "select current_database() as db, "
                "inet_server_addr()::text as host, "
                "inet_server_port() as port, "
                "current_user as usr"
            )
        ).mappings().first()
        if row:
            print(f"db={row.get('db')} host={row.get('host')} port={row.get('port')} user={row.get('usr')}")
    except Exception as exc:
        print(f"db diagnostics failed: {exc}")

    try:
        data_dir = db.execute(text("show data_directory")).scalar()
        print(f"data_directory={data_dir}")
    except Exception as exc:
        print(f"show data_directory failed: {exc}")

    try:
        version = db.execute(text("select version()")).scalar()
        print(f"server_version={version}")
    except Exception as exc:
        print(f"select version() failed: {exc}")

    try:
        total = db.query(func.count(CompanyFinancial.id)).scalar() or 0
        print(f"company_financials_total={int(total)}")
    except Exception as exc:
        print(f"total count failed: {exc}")
    try:
        event_total = db.query(func.count(CompanyFinancialEvent.id)).scalar() or 0
        print(f"company_financial_events_total={int(event_total)}")
    except Exception as exc:
        print(f"event total count failed: {exc}")
    print("")


def main() -> None:
    args = parse_args()
    symbol = normalize_symbol(args.symbol)

    if args.auto_bootstrap:
        settings.cninfo_auto_bootstrap = True
        settings.cninfo_bootstrap_headless = bool(args.bootstrap_headless)
        if not settings.cninfo_enabled:
            settings.cninfo_enabled = True
        print("auto-bootstrap requested: ensuring CNInfo headers (missing/stale -> refresh) ...")
        ok = cninfo_client.ensure_headers()
        print(f"auto-bootstrap ensure_headers result={ok}")

    if not is_target_symbol(symbol):
        raise SystemExit(
            f"Unsupported symbol={symbol}. Current project scope only supports SZ_MAIN_A (000/001/002/003...)."
        )

    print_env_diagnostics(symbol)
    if args.probe_cninfo:
        probe_cninfo_latest_report(symbol)

    db = SessionLocal()
    try:
        print_db_diagnostics(db)
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        stock_id = stock.id if stock else None
        before_count = (
            db.query(func.count(CompanyFinancial.id))
            .filter(CompanyFinancial.stock_id == stock_id)
            .scalar()
            if stock_id
            else 0
        )
        before_max_updated = (
            db.query(func.max(CompanyFinancial.updated_at))
            .filter(CompanyFinancial.stock_id == stock_id)
            .scalar()
            if stock_id
            else None
        )
        print(f"before_count={int(before_count or 0)} stock_exists={bool(stock)}")
        print(f"before_max_updated={before_max_updated}")

        if args.truncate_symbol and stock_id:
            deleted = (
                db.query(CompanyFinancial)
                .filter(CompanyFinancial.stock_id == stock_id)
                .delete(synchronize_session=False)
            )
            event_deleted = (
                db.query(CompanyFinancialEvent)
                .filter(CompanyFinancialEvent.stock_id == stock_id)
                .delete(synchronize_session=False)
            )
            db.commit()
            print(f"truncate_symbol deleted_financials={deleted} deleted_events={event_deleted}")

        print("\n=== Running sync_company_financial ===")
        result = akshare_service.sync_company_financial(db, symbol=symbol, limit=max(1, int(args.limit)))
        print("sync_result=", json.dumps(result, ensure_ascii=False, default=str, indent=2))

        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        stock_id = stock.id if stock else None
        after_count = (
            db.query(func.count(CompanyFinancial.id))
            .filter(CompanyFinancial.stock_id == stock_id)
            .scalar()
            if stock_id
            else 0
        )
        after_max_updated = (
            db.query(func.max(CompanyFinancial.updated_at))
            .filter(CompanyFinancial.stock_id == stock_id)
            .scalar()
            if stock_id
            else None
        )
        delta = int(after_count or 0) - int(before_count or 0)
        print(f"\nafter_count={int(after_count or 0)} delta={delta}")
        print(f"after_max_updated={after_max_updated}")

        inserted = int(result.get("inserted") or 0)
        deleted = int(result.get("deleted") or 0)
        has_update_observation = (
            after_max_updated is not None and before_max_updated is not None and after_max_updated != before_max_updated
        )
        print(
            "write_observation=",
            json.dumps(
                {
                    "inserted": inserted,
                    "deleted": deleted,
                    "count_delta": delta,
                    "updated_at_changed": has_update_observation,
                },
                ensure_ascii=False,
            ),
        )

        if args.assert_write:
            changed = inserted > 0 or deleted > 0 or delta != 0 or has_update_observation
            if not changed:
                raise SystemExit(
                    "ASSERT_WRITE failed: no insert/delete/count/update-timestamp change observed for this symbol."
                )

        if stock_id:
            print("\n=== dataset_counts ===")
            grouped = (
                db.query(CompanyFinancial.dataset, func.count(CompanyFinancial.id))
                .filter(CompanyFinancial.stock_id == stock_id)
                .group_by(CompanyFinancial.dataset)
                .order_by(func.count(CompanyFinancial.id).desc())
                .all()
            )
            for dataset, cnt in grouped:
                print(f"{dataset or '-'}: {int(cnt)}")

            print("\n=== event_dataset_counts ===")
            event_grouped = (
                db.query(CompanyFinancialEvent.dataset, func.count(CompanyFinancialEvent.id))
                .filter(CompanyFinancialEvent.stock_id == stock_id)
                .group_by(CompanyFinancialEvent.dataset)
                .order_by(func.count(CompanyFinancialEvent.id).desc())
                .all()
            )
            for dataset, cnt in event_grouped:
                print(f"{dataset or '-'}: {int(cnt)}")

            print(f"\n=== latest_rows (top {max(1, args.show_rows)}) ===")
            rows = (
                db.query(CompanyFinancial)
                .filter(CompanyFinancial.stock_id == stock_id)
                .order_by(CompanyFinancial.report_date.desc(), CompanyFinancial.id.desc())
                .limit(max(1, int(args.show_rows)))
                .all()
            )
            for row in rows:
                print(
                    json.dumps(
                        {
                            "id": row.id,
                            "report_date": row.report_date.isoformat() if row.report_date else None,
                            "report_name": row.report_name,
                            "report_type": row.report_type,
                            "dataset": row.dataset,
                            "source": row.source,
                            "row_key": row.row_key,
                            "object_id": row.object_id,
                            "declare_date": row.declare_date.isoformat() if row.declare_date else None,
                            "eps": row.eps,
                            "revenue": row.revenue,
                            "net_profit": row.net_profit,
                            "roe": row.roe,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )

            print(f"\n=== latest_events (top {max(1, args.show_rows)}) ===")
            event_rows = (
                db.query(CompanyFinancialEvent)
                .filter(CompanyFinancialEvent.stock_id == stock_id)
                .order_by(CompanyFinancialEvent.event_date.desc(), CompanyFinancialEvent.id.desc())
                .limit(max(1, int(args.show_rows)))
                .all()
            )
            for row in event_rows:
                print(
                    json.dumps(
                        {
                            "id": row.id,
                            "event_date": row.event_date.isoformat() if row.event_date else None,
                            "event_name": row.event_name,
                            "event_type": row.event_type,
                            "dataset": row.dataset,
                            "source": row.source,
                            "row_key": row.row_key,
                            "object_id": row.object_id,
                            "declare_date": row.declare_date.isoformat() if row.declare_date else None,
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                )

        print("\n=== cninfo_increment_state_snapshots ===")
        state_rows = (
            db.query(AkDataSnapshot)
            .filter(
                AkDataSnapshot.snapshot_key.like("cninfo_state_%"),
                AkDataSnapshot.layer == "state",
            )
            .order_by(AkDataSnapshot.snapshot_key.asc(), AkDataSnapshot.id.desc())
            .all()
        )
        seen: set[str] = set()
        for row in state_rows:
            if row.snapshot_key in seen:
                continue
            seen.add(row.snapshot_key)
            payload = row.payload if isinstance(row.payload, dict) else {}
            print(
                json.dumps(
                    {
                        "snapshot_key": row.snapshot_key,
                        "snapshot_date": row.snapshot_date.isoformat() if row.snapshot_date else None,
                        "objectid": payload.get("objectid"),
                        "id": row.id,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )

    except Exception as exc:
        db.rollback()
        print(f"\nFAILED: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
