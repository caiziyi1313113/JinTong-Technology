from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_FACTOR_NAMES = [
    "ROE",
    "ROA",
    "毛利率",
    "净利率",
    "营收同比",
    "净利润同比",
    "资产负债率",
    "流动比率",
    "PE",
    "PB",
    "PD",
    "市值",
    "近5日成交量变化",
    "当日换手率",
    "近5日换手率均值",
    "Beta(近100周)",
    "Sharpe Ratio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quick test: compute quant factors from existing DB financial/quote/kline data."
    )
    parser.add_argument("--symbol", default="002150", help="A-share symbol, e.g. 002150")
    parser.add_argument("--daily-limit", type=int, default=320, help="Daily market rows used for returns/sharpe")
    parser.add_argument("--weekly-limit", type=int, default=140, help="Weekly rows used for beta")
    parser.add_argument("--financial-limit", type=int, default=80, help="Financial rows used for factor extraction")
    parser.add_argument(
        "--assert-required",
        action="store_true",
        help="Exit non-zero when required factor names are missing in output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        from app.core.db import SessionLocal
        from app.core.market_scope import market_from_symbol
        from app.models.company_financial import CompanyFinancial
        from app.models.company_fundamental import CompanyFundamental
        from app.models.market import MarketData
        from app.models.stock import Stock
        from app.models.stock_kline import StockKline
        from app.models.stock_quote import StockQuote
        from app.services.financial_analysis import quant_factor_engine
    except Exception as exc:  # pragma: no cover - runtime env dependency guard
        raise SystemExit(
            "Import failed. Please install backend dependencies (notably SQLAlchemy>=2) first. "
            f"Original error: {exc}"
        ) from exc

    raw_symbol = str(args.symbol or "").strip().upper()
    if "." in raw_symbol:
        left, right = raw_symbol.split(".", 1)
        symbol = left.zfill(6) if left.isdigit() and right in {"SH", "SZ", "BJ"} else raw_symbol
    elif raw_symbol.startswith(("SH", "SZ", "BJ")) and raw_symbol[2:].isdigit():
        symbol = raw_symbol[2:].zfill(6)
    else:
        digits = "".join(ch for ch in raw_symbol if ch.isdigit())
        symbol = digits[-6:] if len(digits) >= 6 else raw_symbol
    if market_from_symbol(symbol) == "UNKNOWN":
        raise SystemExit(f"Unsupported/invalid symbol={args.symbol} -> normalized={symbol}")

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if not stock:
            raise SystemExit(f"Stock {symbol} not found in DB. Please run data sync first.")

        market_rows_desc = (
            db.query(MarketData)
            .filter(MarketData.stock_id == stock.id)
            .order_by(MarketData.date.desc())
            .limit(max(30, int(args.daily_limit)))
            .all()
        )
        market_rows = list(reversed(market_rows_desc))

        weekly_rows = (
            db.query(StockKline)
            .filter(StockKline.stock_id == stock.id, StockKline.period == "weekly")
            .order_by(StockKline.trade_date.desc())
            .limit(max(30, int(args.weekly_limit)))
            .all()
        )
        financial_rows = (
            db.query(CompanyFinancial)
            .filter(CompanyFinancial.stock_id == stock.id)
            .order_by(CompanyFinancial.report_date.desc(), CompanyFinancial.updated_at.desc())
            .limit(max(12, int(args.financial_limit)))
            .all()
        )
        latest_quote = (
            db.query(StockQuote)
            .filter(StockQuote.stock_id == stock.id)
            .order_by(StockQuote.quote_time.desc())
            .first()
        )
        latest_fundamental = (
            db.query(CompanyFundamental)
            .filter(CompanyFundamental.stock_id == stock.id)
            .order_by(CompanyFundamental.snapshot_date.desc())
            .first()
        )

        payload = quant_factor_engine.compute(
            db=db,
            stock_id=stock.id,
            symbol=symbol,
            latest_price=_latest_price(latest_quote, market_rows),
            market_rows=market_rows,
            weekly_rows=weekly_rows,
            latest_quote=latest_quote,
            financial_rows=financial_rows,
            fundamental=latest_fundamental,
        )

        factors = payload.get("factors") if isinstance(payload.get("factors"), list) else []
        metric_snapshot = payload.get("metric_snapshot") if isinstance(payload.get("metric_snapshot"), dict) else {}
        annual_series = payload.get("annual_series_3y") if isinstance(payload.get("annual_series_3y"), list) else []

        print("factor_formula=", payload.get("factor_formula"))
        print("factor_weights=", json.dumps(payload.get("factor_weights"), ensure_ascii=False))
        print("metric_snapshot=", json.dumps(metric_snapshot, ensure_ascii=False, default=str))
        print("annual_series_3y=", json.dumps(annual_series, ensure_ascii=False, default=str))
        print(f"\ncomputed_factor_count={len(factors)}")
        print("factors:")
        for row in factors:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "-"))
            value = row.get("value")
            unit = str(row.get("unit", "")).strip()
            category = str(row.get("category", "")).strip()
            formula = str(row.get("formula", "")).strip()
            print(f"- {name}: {value}{(' ' + unit) if unit else ''} | category={category} | formula={formula}")

        if args.assert_required:
            computed_names = {str(item.get("name")) for item in factors if isinstance(item, dict)}
            missing = [name for name in REQUIRED_FACTOR_NAMES if name not in computed_names]
            if missing:
                raise SystemExit(f"ASSERT_REQUIRED failed: missing factors => {missing}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
