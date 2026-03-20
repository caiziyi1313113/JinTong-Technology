from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import re
import time
from typing import Any, Iterable

import requests
from sqlalchemy import and_, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.market_scope import (
    exchange_from_symbol,
    market_from_symbol,
    normalize_symbol,
)
from app.models.ak_data_snapshot import AkDataSnapshot
from app.models.block_trade import BlockTradeRecord
#目前是空的
from app.models.company_financial import CompanyFinancial
from app.models.company_financial_event import CompanyFinancialEvent
#目前company_fundamental里是空的
from app.models.company_fundamental import CompanyFundamental
# 公司基本情况，里面的公司董事行、员工等是空的
from app.models.data_sync_log import DataSyncLog
# 目前是空的，这个是日志但是不太清楚洋有什么用
from app.models.document import Document
from app.models.macro_news import MacroNews
# 文件，包括新闻、宏观面等，但是目前新闻不能和公司的代码结合起来
from app.models.market import MarketData
# 交易数据，开盘价格、闭盘价格、最高价格、最低价格、成交量，是有的4个月
from app.models.stock import Stock
# 股票代码、公司名称、证券交易所
from app.models.stock_kline import StockKline
# 股票的K线数据，包含日期、开盘价格、闭盘价格、最高价格、最低价格、成交量等，然后有daily、weekly、monthly三种
from app.models.stock_quote import StockQuote
from app.services.financial_analysis.eastmoney_financial import extract_core_metrics
from app.services.data_ingest.cninfo_service import CninfoClientError, cninfo_client


MACRO_KEYWORDS = (
    "央行",
    "国债",
    "GDP",
    "PMI",
    "CPI",
    "PPI",
    "财政",
    "货币",
    "利率",
    "inflation",
    "economy",
)


logger = logging.getLogger(__name__)
SYNC_ALLOWED_MARKETS = {"SZ_MAIN_A", "SZ_CHINEXT_A", "SH_MAIN_A", "SH_STAR_A"}
SYNC_MARKET_SCOPE = "CN_A_SH_SZ"

CNINFO_FINANCIAL_DATASETS = {
    "p_stock2300",
    "p_stock2301",
    "p_stock2302",
    "p_stock2303",
    "p_stock2387",
    "p_stock2328",
    "p_ods3302",
    "p_stock2238",
}

CNINFO_EVENT_DATASETS = {
    "p_stock2237_inc",
    "p_stock2237",
    "p_stock2239",
    "p_stock2399",
}


class AkshareServiceError(RuntimeError):
    pass


class AkshareService:
    def __init__(self) -> None:
        # Avoid probing CNInfo latest-report endpoint on every frontend request.
        # Cache key: symbol -> (fetched_at_utc, latest_report_date_or_none)
        self._cninfo_latest_report_cache: dict[str, tuple[datetime, date | None]] = {}

    def _ak(self):
        try:
            import akshare as ak  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise AkshareServiceError(
                "akshare is not installed. Run `pip install -r backend/requirements.txt`."
            ) from exc
        return ak

    @staticmethod
    def _call_with_retry(
        fn,
        *args,
        retries: int = 3,
        retry_delay: float = 0.6,
        retry_name: str | None = None,
        **kwargs,
    ):
        last_error: Exception | None = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= max(1, retries):
                    break
                logger.warning(
                    "external call failed, retrying | call=%s attempt=%s/%s error=%s",
                    retry_name or getattr(fn, "__name__", "unknown"),
                    attempt,
                    retries,
                    exc,
                )
                time.sleep(retry_delay * attempt)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip().replace(",", "")
            if value in {"", "-", "--", "None", "nan", "NaN"}:
                return None
        try:
            number = float(value)
        except Exception:
            return None
        if number != number:  # NaN
            return None
        return number

    @staticmethod
    def _safe_float_with_unit(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return AkshareService._safe_float(value)
        text = str(value).strip().replace(",", "")
        if text in {"", "-", "--", "None", "nan", "NaN"}:
            return None
        multiplier = 1.0
        if text.endswith("亿"):
            multiplier = 1e8
            text = text[:-1]
        elif text.endswith("万"):
            multiplier = 1e4
            text = text[:-1]
        elif text.endswith("%"):
            text = text[:-1]
        number = AkshareService._safe_float(text)
        if number is None:
            return None
        return number * multiplier

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        f = AkshareService._safe_float(value)
        if f is None:
            return None
        return int(f)

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            if value != value:
                return None
            return value
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): AkshareService._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [AkshareService._to_jsonable(v) for v in value]
        try:
            if hasattr(value, "item") and callable(value.item):
                extracted = value.item()
                if extracted is not value:
                    return AkshareService._to_jsonable(extracted)
        except Exception:
            pass
        text = str(value)
        if text.lower() in {"nan", "<na>", "nat", "none"}:
            return None
        return text

    @classmethod
    def _df_to_records(cls, df: Any, limit: int = 200) -> list[dict[str, Any]]:
        if df is None or getattr(df, "empty", True):
            return []
        rows: list[dict[str, Any]] = []
        for _, row in df.head(max(1, limit)).iterrows():
            item = {}
            for key, value in row.to_dict().items():
                item[str(key)] = cls._to_jsonable(value)
            rows.append(item)
        return rows

    def _upsert_ak_snapshot(
        self,
        db: Session,
        *,
        snapshot_key: str,
        snapshot_date: date,
        layer: str,
        source: str,
        payload: dict[str, Any] | list[Any],
        stock_symbol: str | None = None,
        summary: str | None = None,
    ) -> bool:
        query = db.query(AkDataSnapshot).filter(
            AkDataSnapshot.snapshot_key == snapshot_key,
            AkDataSnapshot.snapshot_date == snapshot_date,
            AkDataSnapshot.layer == layer,
        )
        if stock_symbol is None:
            query = query.filter(AkDataSnapshot.stock_symbol.is_(None))
        else:
            query = query.filter(AkDataSnapshot.stock_symbol == stock_symbol)

        row = query.first()
        inserted = False
        if not row:
            row = AkDataSnapshot(
                snapshot_key=snapshot_key,
                snapshot_date=snapshot_date,
                layer=layer,
                stock_symbol=stock_symbol,
            )
            inserted = True
        row.source = source
        row.summary = summary
        json_payload = self._to_jsonable(payload)
        row.payload = json_payload if isinstance(json_payload, (dict, list)) else {"value": json_payload}
        db.add(row)
        return inserted

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()

        text = str(value).strip()
        if not text or text.lower() in {"none", "nat", "nan"}:
            return None
        text = text.replace("/", "-")
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
            "%Y-%m-%d",
            "%Y%m%d",
            "%Y-%m",
            "%Y%m",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.date()
            except ValueError:
                continue

        # epoch ms in some xq fields
        if text.isdigit() and len(text) >= 10:
            try:
                ts = int(text)
                if len(text) >= 13:
                    ts = ts // 1000
                return datetime.utcfromtimestamp(ts).date()
            except Exception:
                return None
        return None

    @staticmethod
    def _parse_datetime(value: Any, *, reference_date: date | None = None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())

        text = str(value).strip()
        if not text or text.lower() in {"none", "nat", "nan"}:
            return None
        text = text.replace("/", "-")

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
            "%Y-%m-%d",
            "%Y%m%d",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                ref = reference_date or date.today()
                return parsed.replace(year=ref.year)
            except ValueError:
                continue

        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed_time = datetime.strptime(text, fmt).time()
                ref = reference_date or date.today()
                return datetime.combine(ref, parsed_time)
            except ValueError:
                continue

        if text.isdigit() and len(text) >= 10:
            try:
                ts = int(text)
                if len(text) >= 13:
                    ts = ts // 1000
                return datetime.utcfromtimestamp(ts)
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_datetime_from_text(text: Any) -> datetime | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", raw)
        if not match:
            return None
        try:
            year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return datetime(year, month, day)
        except Exception:
            return None

    @staticmethod
    def _normalize_code_token(value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return ""
        digits = "".join(ch for ch in text if ch.isdigit())
        if not digits:
            return ""
        if len(digits) >= 6:
            return digits[-6:]
        return digits.zfill(6)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return normalize_symbol(symbol)

    @staticmethod
    def _market_from_code(code: str) -> str:
        return market_from_symbol(code)

    @staticmethod
    def _is_sync_symbol(symbol: str) -> bool:
        return market_from_symbol(symbol) in SYNC_ALLOWED_MARKETS

    @classmethod
    def _filter_sync_symbols(cls, symbols: Iterable[str]) -> list[str]:
        out: list[str] = []
        for symbol in symbols:
            code = normalize_symbol(symbol)
            if code and cls._is_sync_symbol(code):
                out.append(code)
        return out

    def _ensure_stock(self, db: Session, symbol: str, name: str | None = None) -> Stock:
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = db.query(Stock).filter(Stock.symbol == code).first()
        if stock:
            if name and stock.name != name:
                stock.name = name
            expected_market = self._market_from_code(code)
            if stock.market != expected_market:
                stock.market = expected_market
            if not stock.market:
                stock.market = self._market_from_code(code)
            db.add(stock)
            return stock

        stock = Stock(
            symbol=code,
            name=name or code,
            market=self._market_from_code(code),
            sector=None,
        )
        db.add(stock)
        db.flush()
        return stock

    def _doc_exists(
        self,
        db: Session,
        *,
        stock_id: int | None = None,
        stock_symbol: str | None,
        doc_type: str,
        title: str,
        published_at: datetime | None,
    ) -> bool:
        query = db.query(Document).filter(Document.doc_type == doc_type, Document.title == title)
        if stock_id is not None:
            query = query.filter(Document.stock_id == stock_id)
        else:
            query = query.filter(Document.stock_symbol == stock_symbol)
        if published_at:
            query = query.filter(Document.published_at == published_at)
        return db.query(query.exists()).scalar()  # type: ignore[arg-type]

    def _macro_exists(self, db: Session, *, title: str, published_at: datetime | None) -> bool:
        query = db.query(MacroNews).filter(MacroNews.title == title)
        if published_at:
            query = query.filter(MacroNews.published_at == published_at)
        return db.query(query.exists()).scalar()  # type: ignore[arg-type]

    def _upsert_sync_log(
        self,
        db: Session,
        *,
        log: DataSyncLog,
        status: str,
        detail: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> DataSyncLog:
        log.status = status
        log.finished_at = datetime.utcnow()
        log.detail = detail or {}
        log.error_message = error_message
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def start_sync_log(self, db: Session, job_type: str, scope: str | None = None) -> DataSyncLog:
        log = DataSyncLog(job_type=job_type, scope=scope, status="running")
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    def finish_sync_log(
        self,
        db: Session,
        *,
        log: DataSyncLog,
        status: str,
        detail: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> DataSyncLog:
        return self._upsert_sync_log(
            db,
            log=log,
            status=status,
            detail=detail,
            error_message=error_message,
        )

    def sync_stock_universe(self, db: Session, limit: int | None = None) -> dict[str, Any]:
        ak = self._ak()
        source_dfs: list[Any] = []
        spot_sources = [
            ("sz", getattr(ak, "stock_sz_a_spot_em", None)),
            ("sh", getattr(ak, "stock_sh_a_spot_em", None)),
        ]
        for tag, fn in spot_sources:
            if not callable(fn):
                continue
            try:
                df = self._call_with_retry(
                    fn,
                    retries=3,
                    retry_name=f"{fn.__name__}:universe:{tag}",
                )
            except Exception as exc:
                logger.warning("sync_stock_universe source failed | source=%s error=%s", tag, exc)
                continue
            if df is not None and not df.empty:
                source_dfs.append(df)

        if not source_dfs:
            raise AkshareServiceError("sync_stock_universe failed: no available quote source for SH/SZ.")

        inserted = 0
        updated = 0
        skipped = 0
        seen_codes: set[str] = set()
        source_rows = 0
        for df in source_dfs:
            for _, row in df.iterrows():
                if limit and source_rows >= int(limit):
                    break
                source_rows += 1
                code = str(row.get("代码", "")).zfill(6)
                if not code or code == "000000" or code in seen_codes:
                    continue
                seen_codes.add(code)
                if not self._is_sync_symbol(code):
                    skipped += 1
                    continue
                name = str(row.get("名称", code))
                stock = db.query(Stock).filter(Stock.symbol == code).first()
                if stock:
                    changed = False
                    if stock.name != name:
                        stock.name = name
                        changed = True
                    market = self._market_from_code(code)
                    if stock.market != market:
                        stock.market = market
                        changed = True
                    if changed:
                        db.add(stock)
                        updated += 1
                else:
                    stock = Stock(symbol=code, name=name, market=self._market_from_code(code), sector=None)
                    db.add(stock)
                    inserted += 1
            if limit and source_rows >= int(limit):
                break

        db.commit()
        return {
            "market_scope": SYNC_MARKET_SCOPE,
            "inserted": inserted,
            "updated": updated,
            "skipped_non_target": skipped,
            "total": int(inserted + updated),
        }

    def sync_realtime_quotes(self, db: Session, symbols: Iterable[str] | None = None) -> dict[str, Any]:
        ak = self._ak()
        symbols_provided = symbols is not None
        symbol_set = set(self._filter_sync_symbols(symbols or [])) if symbols_provided else None
        source_dfs: list[Any] = []
        spot_sources = [
            ("sz", getattr(ak, "stock_sz_a_spot_em", None)),
            ("sh", getattr(ak, "stock_sh_a_spot_em", None)),
        ]
        for tag, fn in spot_sources:
            if not callable(fn):
                continue
            try:
                df = self._call_with_retry(
                    fn,
                    retries=3,
                    retry_name=f"{fn.__name__}:quotes:{tag}",
                )
            except Exception as exc:
                logger.warning("sync_realtime_quotes source failed | source=%s error=%s", tag, exc)
                continue
            if df is not None and not df.empty:
                source_dfs.append(df)

        if not source_dfs:
            raise AkshareServiceError("sync_realtime_quotes failed: no available quote source for SH/SZ.")
        now = datetime.utcnow().replace(microsecond=0)

        inserted = 0
        skipped = 0
        seen_codes: set[str] = set()
        for df in source_dfs:
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).zfill(6)
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                if not self._is_sync_symbol(code):
                    skipped += 1
                    continue
                if symbol_set is not None and code not in symbol_set:
                    continue

                stock = self._ensure_stock(db, code, str(row.get("名称", code)))
                quote = StockQuote(
                    stock_id=stock.id,
                    quote_time=now,
                    latest_price=self._safe_float(row.get("最新价")) or 0.0,
                    change_pct=self._safe_float(row.get("涨跌幅")),
                    change_amount=self._safe_float(row.get("涨跌额")),
                    volume=self._safe_float(row.get("成交量")),
                    amount=self._safe_float(row.get("成交额")),
                    turnover_rate=self._safe_float(row.get("换手率")),
                    pe_dynamic=self._safe_float(row.get("市盈率-动态")),
                    pb=self._safe_float(row.get("市净率")),
                    raw={str(k): self._to_jsonable(v) for k, v in row.to_dict().items()},
                )
                db.add(quote)
                inserted += 1

        db.commit()
        return {
            "market_scope": SYNC_MARKET_SCOPE,
            "inserted": inserted,
            "skipped_non_target": skipped,
            "quote_time": now.isoformat(),
        }

    def sync_realtime_quote_single(self, db: Session, symbol: str) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )

        stock = self._ensure_stock(db, code)
        df = self._call_with_retry(
            ak.stock_bid_ask_em,
            symbol=code,
            retries=3,
            retry_name=f"stock_bid_ask_em:{code}",
        )
        if df is None or df.empty:
            return {"symbol": code, "inserted": 0}

        quote_map = {str(row.get("item")): row.get("value") for _, row in df.iterrows()}
        now = datetime.utcnow()

        quote = StockQuote(
            stock_id=stock.id,
            quote_time=now,
            latest_price=self._safe_float(quote_map.get("最新")) or 0.0,
            change_pct=self._safe_float(quote_map.get("涨幅")),
            change_amount=self._safe_float(quote_map.get("涨跌")),
            volume=self._safe_float(quote_map.get("总手")),
            amount=self._safe_float(quote_map.get("金额")),
            turnover_rate=self._safe_float(quote_map.get("换手")),
            pe_dynamic=self._safe_float(quote_map.get("市盈率")),
            pb=self._safe_float(quote_map.get("市净率")),
            raw={str(k): self._to_jsonable(v) for k, v in quote_map.items()},
        )
        db.add(quote)
        db.commit()
        return {"symbol": code, "inserted": 1, "quote_time": now.isoformat()}

    def sync_history(
        self,
        db: Session,
        symbol: str,
        start_date: date,
        end_date: date,
        periods: Iterable[str] = ("daily", "weekly", "monthly"),
        adjust: str = "qfq",
    ) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)

        period_counts: dict[str, int] = {}
        for period in periods:
            df = self._call_with_retry(
                ak.stock_zh_a_hist,
                symbol=code,
                period=period,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust=adjust,
                retries=3,
                retry_name=f"stock_zh_a_hist:{code}:{period}",
            )
            if df is None or df.empty:
                period_counts[period] = 0
                continue

            db.query(StockKline).filter(
                StockKline.stock_id == stock.id,
                StockKline.period == period,
                and_(StockKline.trade_date >= start_date, StockKline.trade_date <= end_date),
            ).delete(synchronize_session=False)

            if period == "daily":
                db.query(MarketData).filter(
                    MarketData.stock_id == stock.id,
                    and_(MarketData.date >= start_date, MarketData.date <= end_date),
                ).delete(synchronize_session=False)

            count = 0
            for _, row in df.iterrows():
                trade_date = self._parse_date(row.get("日期"))
                if trade_date is None:
                    continue
                open_price = self._safe_float(row.get("开盘"))
                close_price = self._safe_float(row.get("收盘"))
                high_price = self._safe_float(row.get("最高"))
                low_price = self._safe_float(row.get("最低"))
                if None in {open_price, close_price, high_price, low_price}:
                    continue

                kline = StockKline(
                    stock_id=stock.id,
                    period=period,
                    trade_date=trade_date,
                    open=open_price or 0.0,
                    high=high_price or 0.0,
                    low=low_price or 0.0,
                    close=close_price or 0.0,
                    volume=self._safe_float(row.get("成交量")),
                    amount=self._safe_float(row.get("成交额")),
                    amplitude=self._safe_float(row.get("振幅")),
                    pct_change=self._safe_float(row.get("涨跌幅")),
                    change_amount=self._safe_float(row.get("涨跌额")),
                    turnover_rate=self._safe_float(row.get("换手率")),
                )
                db.add(kline)

                if period == "daily":
                    db.add(
                        MarketData(
                            stock_id=stock.id,
                            date=trade_date,
                            open=open_price,
                            high=high_price,
                            low=low_price,
                            close=close_price,
                            volume=self._safe_float(row.get("成交量")) or 0.0,
                        )
                    )
                count += 1

            period_counts[period] = count

        db.commit()
        return {"symbol": code, "period_counts": period_counts}

    def sync_block_trade(self, db: Session, start_date: date, end_date: date) -> dict[str, Any]:
        ak = self._ak()
        try:
            df = self._call_with_retry(
                ak.stock_dzjy_mrmx,
                symbol="A股",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                retries=2,
                retry_delay=0.8,
                retry_name=f"stock_dzjy_mrmx:{start_date.isoformat()}:{end_date.isoformat()}",
            )
        except Exception as exc:
            # Upstream may intermittently return invalid payload (e.g. NoneType in akshare internals).
            # Degrade gracefully: keep existing block-trade data and continue whole analysis pipeline.
            logger.warning(
                "block trade sync skipped due to upstream error | start=%s end=%s error=%s",
                start_date,
                end_date,
                exc,
            )
            return {
                "market_scope": SYNC_MARKET_SCOPE,
                "inserted": 0,
                "skipped_non_target": 0,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "skipped": True,
                "error": str(exc),
            }
        if df is None:
            logger.warning(
                "block trade sync skipped due to empty upstream payload | start=%s end=%s",
                start_date,
                end_date,
            )
            return {
                "market_scope": SYNC_MARKET_SCOPE,
                "inserted": 0,
                "skipped_non_target": 0,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "skipped": True,
                "error": "empty payload",
            }

        db.query(BlockTradeRecord).filter(
            and_(BlockTradeRecord.trade_date >= start_date, BlockTradeRecord.trade_date <= end_date)
        ).delete(synchronize_session=False)

        inserted = 0
        skipped = 0
        payload_duplicates = 0
        rows_to_insert: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, ...]] = set()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                trade_date = self._parse_date(row.get("交易日期"))
                symbol = str(row.get("证券代码", "")).zfill(6)
                if not trade_date or not symbol:
                    continue
                if not self._is_sync_symbol(symbol):
                    skipped += 1
                    continue
                deal_price = self._safe_float(row.get("成交价"))
                volume = self._safe_float(row.get("成交量"))
                buyer_branch = str(row.get("买方营业部", "") or "").strip() or None
                seller_branch = str(row.get("卖方营业部", "") or "").strip() or None
                dedupe_key = (
                    trade_date,
                    symbol,
                    deal_price,
                    volume,
                    buyer_branch or "",
                    seller_branch or "",
                )
                if dedupe_key in seen_keys:
                    payload_duplicates += 1
                    continue
                seen_keys.add(dedupe_key)
                rows_to_insert.append(
                    {
                        "trade_date": trade_date,
                        "stock_symbol": symbol,
                        "stock_name": str(row.get("证券简称", "")) or None,
                        "change_pct": self._safe_float(row.get("涨跌幅")),
                        "close_price": self._safe_float(row.get("收盘价")),
                        "deal_price": deal_price,
                        "premium_discount": self._safe_float(row.get("折溢率")),
                        "volume": volume,
                        "amount": self._safe_float(row.get("成交额")),
                        "amount_to_float_mkt": self._safe_float(row.get("成交额/流通市值")),
                        "buyer_branch": buyer_branch,
                        "seller_branch": seller_branch,
                        "source": "akshare",
                        "raw": {str(k): self._to_jsonable(v) for k, v in row.to_dict().items()},
                    }
                )

        if rows_to_insert:
            stmt = pg_insert(BlockTradeRecord).values(rows_to_insert)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[
                    BlockTradeRecord.trade_date,
                    BlockTradeRecord.stock_symbol,
                    BlockTradeRecord.deal_price,
                    BlockTradeRecord.volume,
                    BlockTradeRecord.buyer_branch,
                    BlockTradeRecord.seller_branch,
                ]
            )
            result = db.execute(stmt)
            inserted = int(result.rowcount or 0)

        db.commit()
        return {
            "market_scope": SYNC_MARKET_SCOPE,
            "inserted": inserted,
            "rows_prepared": int(len(rows_to_insert)),
            "duplicates_in_payload": int(payload_duplicates),
            "skipped_non_target": skipped,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "skipped": False,
        }

    def sync_company_profile(self, db: Session, symbol: str) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)

        info_map: dict[str, Any] = {}
        profile_row: dict[str, Any] = {}
        info_error: str | None = None
        profile_error: str | None = None

        try:
            info_df = self._call_with_retry(
                ak.stock_individual_info_em,
                symbol=code,
                retries=3,
                retry_name=f"stock_individual_info_em:{code}",
            )
            if info_df is not None and not info_df.empty:
                info_map = {str(row["item"]): row["value"] for _, row in info_df.iterrows()}
        except Exception as exc:
            info_error = str(exc)
            logger.warning("sync_company_profile individual info failed | symbol=%s error=%s", code, exc)

        try:
            profile_df = self._call_with_retry(
                ak.stock_zyjs_ths,
                symbol=code,
                retries=3,
                retry_name=f"stock_zyjs_ths:{code}",
            )
            if profile_df is not None and not profile_df.empty:
                profile_row = profile_df.iloc[0].to_dict()
        except Exception as exc:
            profile_error = str(exc)
            logger.warning("sync_company_profile ths profile failed | symbol=%s error=%s", code, exc)

        if not info_map and not profile_row:
            latest_snapshot = (
                db.query(func.max(CompanyFundamental.snapshot_date))
                .filter(CompanyFundamental.stock_id == stock.id)
                .scalar()
            )
            logger.warning(
                "sync_company_profile no data fetched, skip refresh | symbol=%s latest_snapshot=%s info_error=%s profile_error=%s",
                code,
                latest_snapshot,
                info_error,
                profile_error,
            )
            return {
                "symbol": code,
                "snapshot_date": latest_snapshot.isoformat() if latest_snapshot else None,
                "industry": None,
                "skipped": True,
                "error": info_error or profile_error or "upstream returned empty data",
            }

        snapshot_date = date.today()
        record = (
            db.query(CompanyFundamental)
            .filter(CompanyFundamental.stock_id == stock.id, CompanyFundamental.snapshot_date == snapshot_date)
            .first()
        )
        if not record:
            record = CompanyFundamental(stock_id=stock.id, snapshot_date=snapshot_date)

        listed_date = self._parse_date(info_map.get("上市时间"))
        industry = str(info_map.get("行业", "")) or None

        record.industry = industry
        record.listed_date = listed_date
        record.legal_representative = str(info_map.get("法人代表", "")) or None
        record.chairman = str(info_map.get("董事长", "")) or None
        record.general_manager = str(info_map.get("总经理", "")) or None
        record.staff_num = self._safe_int(info_map.get("员工人数"))
        record.main_business = str(profile_row.get("主营业务", "")) or None
        record.business_scope = str(profile_row.get("经营范围", "")) or None
        record.company_intro = str(profile_row.get("产品名称", "")) or None
        record.management_info = {
            "legal_representative": info_map.get("法人代表"),
            "chairman": info_map.get("董事长"),
            "general_manager": info_map.get("总经理"),
        }
        record.raw = {
            "individual_info": info_map,
            "ths_profile": profile_row,
        }
        db.add(record)

        summary_parts = [
            f"股票代码: {code}",
            f"行业: {industry or '未知'}",
            f"主营业务: {record.main_business or '暂无'}",
            f"经营范围: {record.business_scope or '暂无'}",
        ]
        doc_title = f"{code} 公司基本面快照"
        published_at = datetime.combine(snapshot_date, datetime.min.time())
        if not self._doc_exists(
            db,
            stock_id=stock.id,
            stock_symbol=code,
            doc_type="company_profile",
            title=doc_title,
            published_at=published_at,
        ):
            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="company_profile",
                    title=doc_title,
                    content="\n".join(summary_parts),
                    source="akshare",
                    published_at=published_at,
                    doc_metadata={"snapshot_date": snapshot_date.isoformat()},
                )
            )

        db.commit()
        return {
            "symbol": code,
            "snapshot_date": snapshot_date.isoformat(),
            "industry": industry,
        }

    def sync_company_business_composition(
        self,
        db: Session,
        symbol: str,
        limit: int = 120,
    ) -> dict[str, Any]:
        """
        Pull主营构成数据(ak.stock_zygc_em), store layered snapshot + company document.
        """
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)

        em_symbol = f"{exchange_from_symbol(code)}{code}"
        df = None
        used_symbol = None
        for candidate in (em_symbol, code):
            try:
                candidate_df = ak.stock_zygc_em(symbol=candidate)
            except Exception as exc:
                logger.warning(
                    "business composition fetch failed | symbol=%s candidate=%s error=%s",
                    code,
                    candidate,
                    exc,
                )
                continue
            if candidate_df is not None and not candidate_df.empty:
                df = candidate_df
                used_symbol = candidate
                break

        if df is None or df.empty:
            return {"symbol": code, "inserted": 0, "records": 0, "source": "none"}

        raw_records = self._df_to_records(df, limit=max(200, limit))
        enriched_rows: list[dict[str, Any]] = []
        for row in raw_records:
            report_date = self._parse_date(row.get("报告日期"))
            if not report_date:
                continue
            enriched_rows.append(
                {
                    "report_date": report_date,
                    "classification_type": str(row.get("分类类型", "")).strip() or None,
                    "component": str(row.get("主营构成", "")).strip() or None,
                    "revenue": self._safe_float_with_unit(row.get("主营收入")),
                    "revenue_ratio": self._safe_float(row.get("收入比例")),
                    "cost": self._safe_float_with_unit(row.get("主营成本")),
                    "cost_ratio": self._safe_float(row.get("成本比例")),
                    "profit": self._safe_float_with_unit(row.get("主营利润")),
                    "profit_ratio": self._safe_float(row.get("利润比例")),
                    "gross_margin": self._safe_float(row.get("毛利率")),
                    "raw": row,
                }
            )

        if not enriched_rows:
            return {"symbol": code, "inserted": 0, "records": 0, "source": "none"}

        enriched_rows.sort(key=lambda item: item["report_date"], reverse=True)
        latest_report_date = enriched_rows[0]["report_date"]
        latest_rows = [row for row in enriched_rows if row["report_date"] == latest_report_date]
        latest_rows.sort(
            key=lambda item: item.get("revenue") if item.get("revenue") is not None else -1.0,
            reverse=True,
        )

        top_segments = []
        for row in latest_rows[:8]:
            top_segments.append(
                {
                    "classification_type": row.get("classification_type"),
                    "component": row.get("component"),
                    "revenue": row.get("revenue"),
                    "revenue_ratio": row.get("revenue_ratio"),
                    "gross_margin": row.get("gross_margin"),
                }
            )

        normalized_payload = {
            "symbol": code,
            "report_date": latest_report_date.isoformat(),
            "source_symbol": used_symbol,
            "segment_count": len(latest_rows),
            "classification_types": sorted(
                {str(row.get("classification_type")) for row in latest_rows if row.get("classification_type")}
            ),
            "top_segments": top_segments,
        }

        summary = (
            f"主营构成最新报告期={latest_report_date.isoformat()}, "
            f"分类数={len(normalized_payload['classification_types'])}, 分项数={len(latest_rows)}"
        )

        inserted = 0
        inserted += int(
            self._upsert_ak_snapshot(
                db,
                snapshot_key="company_business_composition",
                snapshot_date=latest_report_date,
                layer="raw",
                source="ak.stock_zygc_em",
                payload=raw_records,
                stock_symbol=code,
                summary=summary,
            )
        )
        inserted += int(
            self._upsert_ak_snapshot(
                db,
                snapshot_key="company_business_composition",
                snapshot_date=latest_report_date,
                layer="normalized",
                source="ak.stock_zygc_em",
                payload=normalized_payload,
                stock_symbol=code,
                summary=summary,
            )
        )

        doc_title = f"{code} 主营构成快照 {latest_report_date.isoformat()}"
        published_at = datetime.combine(latest_report_date, datetime.min.time())
        if not self._doc_exists(
            db,
            stock_id=stock.id,
            stock_symbol=code,
            doc_type="business_composition",
            title=doc_title,
            published_at=published_at,
        ):
            lines = [summary]
            for item in top_segments[:5]:
                lines.append(
                    f"{item.get('classification_type') or '-'} | {item.get('component') or '-'} | "
                    f"收入占比={item.get('revenue_ratio')} | 毛利率={item.get('gross_margin')}"
                )
            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="business_composition",
                    title=doc_title,
                    content="\n".join(lines),
                    source="ak.stock_zygc_em",
                    published_at=published_at,
                    doc_metadata={
                        "report_date": latest_report_date.isoformat(),
                        "source_symbol": used_symbol,
                    },
                )
            )

        db.commit()
        return {
            "symbol": code,
            "inserted": inserted,
            "records": len(raw_records),
            "report_date": latest_report_date.isoformat(),
            "source": "stock_zygc_em",
        }

    def sync_market_pledge_ratio(
        self,
        db: Session,
        *,
        as_of_date: date | None = None,
        lookback_days: int = 30,
        raw_limit: int = 4000,
        focus_symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Pull上市公司质押比例(ak.stock_gpzy_pledge_ratio_em), store market snapshot.
        Optionally emit stock-level pledge risk docs for focus symbols.
        """
        ak = self._ak()
        target_date = as_of_date or date.today()

        df = None
        used_date: date | None = None
        for offset in range(max(1, lookback_days)):
            candidate = target_date - timedelta(days=offset)
            try:
                candidate_df = ak.stock_gpzy_pledge_ratio_em(date=candidate.strftime("%Y%m%d"))
            except Exception as exc:
                message = str(exc)
                if "NoneType" in message and "subscriptable" in message:
                    logger.info(
                        "pledge ratio source returned empty payload | candidate_date=%s detail=%s",
                        candidate.isoformat(),
                        message,
                    )
                else:
                    logger.warning(
                        "pledge ratio fetch failed | candidate_date=%s error=%s",
                        candidate.isoformat(),
                        exc,
                    )
                continue
            if candidate_df is not None and not candidate_df.empty:
                df = candidate_df
                used_date = candidate
                break

        if df is None or df.empty or used_date is None:
            return {"snapshot_date": target_date.isoformat(), "inserted": 0, "records": 0, "source": "none"}

        raw_records = self._df_to_records(df, limit=raw_limit)
        normalized_rows: list[dict[str, Any]] = []
        for row in raw_records:
            code = str(row.get("股票代码", "")).strip()
            code = "".join(ch for ch in code if ch.isdigit()).zfill(6) if code else ""
            if not code:
                continue
            normalized_rows.append(
                {
                    "symbol": code,
                    "name": str(row.get("股票简称", "")).strip() or None,
                    "trade_date": row.get("交易日期"),
                    "industry": str(row.get("所属行业", "")).strip() or None,
                    "pledge_ratio_pct": self._safe_float(row.get("质押比例")),
                    "pledged_shares_10k": self._safe_float(row.get("质押股数")),
                    "pledged_market_value_10k_cny": self._safe_float(row.get("质押市值")),
                    "pledge_count": self._safe_int(row.get("质押笔数")),
                    "one_year_change_pct": self._safe_float(row.get("近一年涨跌幅")),
                    "raw": row,
                }
            )

        normalized_rows.sort(
            key=lambda item: item.get("pledge_ratio_pct") if item.get("pledge_ratio_pct") is not None else -1.0,
            reverse=True,
        )
        market_top = normalized_rows[:50]
        target_market_rows = [row for row in normalized_rows if self._is_sync_symbol(row["symbol"])]
        target_market_rows.sort(
            key=lambda item: item.get("pledge_ratio_pct") if item.get("pledge_ratio_pct") is not None else -1.0,
            reverse=True,
        )
        target_top = target_market_rows[:20]

        target_ratios = [row["pledge_ratio_pct"] for row in target_market_rows if row.get("pledge_ratio_pct") is not None]
        avg_target_ratio = round(sum(target_ratios) / len(target_ratios), 4) if target_ratios else None

        normalized_payload = {
            "snapshot_date": used_date.isoformat(),
            "market_top50": market_top,
            "target_market_top20": target_top,
            "target_market_count": len(target_market_rows),
            "target_market_avg_pledge_ratio_pct": avg_target_ratio,
        }
        summary = (
            f"上市公司质押比例快照={used_date.isoformat()}, "
            f"全市场样本={len(normalized_rows)}, 目标市场样本={len(target_market_rows)}"
        )

        inserted = 0
        inserted += int(
            self._upsert_ak_snapshot(
                db,
                snapshot_key="market_pledge_ratio_em",
                snapshot_date=used_date,
                layer="raw",
                source="ak.stock_gpzy_pledge_ratio_em",
                payload=raw_records,
                stock_symbol=None,
                summary=summary,
            )
        )
        inserted += int(
            self._upsert_ak_snapshot(
                db,
                snapshot_key="market_pledge_ratio_em",
                snapshot_date=used_date,
                layer="normalized",
                source="ak.stock_gpzy_pledge_ratio_em",
                payload=normalized_payload,
                stock_symbol=None,
                summary=summary,
            )
        )

        market_doc_title = f"{used_date.isoformat()} 上市公司质押比例快照"
        published_at = datetime.combine(used_date, datetime.min.time())
        if not self._doc_exists(
            db,
            stock_symbol=None,
            doc_type="market_risk",
            title=market_doc_title,
            published_at=published_at,
        ):
            db.add(
                Document(
                    stock_symbol=None,
                    doc_type="market_risk",
                    title=market_doc_title,
                    content=summary,
                    source="ak.stock_gpzy_pledge_ratio_em",
                    published_at=published_at,
                    doc_metadata={"snapshot_key": "market_pledge_ratio_em"},
                )
            )
        if not self._macro_exists(db, title=market_doc_title, published_at=published_at):
            db.add(
                MacroNews(
                    title=market_doc_title,
                    content=summary,
                    source="ak.stock_gpzy_pledge_ratio_em",
                    published_at=published_at,
                    news_metadata={"snapshot_key": "market_pledge_ratio_em"},
                )
            )

        focus_result: dict[str, Any] = {}
        for code in self._filter_sync_symbols(focus_symbols or []):
            row = next((item for item in normalized_rows if item["symbol"] == code), None)
            focus_result[code] = row
            if not row:
                continue
            stock = self._ensure_stock(db, code)
            title = f"{code} 股权质押比例快照 {used_date.isoformat()}"
            if self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="pledge_risk",
                title=title,
                published_at=published_at,
            ):
                continue
            content = (
                f"质押比例={row.get('pledge_ratio_pct')}%, 质押股数(万股)={row.get('pledged_shares_10k')}, "
                f"质押市值(万元)={row.get('pledged_market_value_10k_cny')}, 近一年涨跌幅={row.get('one_year_change_pct')}%"
            )
            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="pledge_risk",
                    title=title,
                    content=content,
                    source="ak.stock_gpzy_pledge_ratio_em",
                    published_at=published_at,
                    doc_metadata={"trade_date": row.get("trade_date"), "industry": row.get("industry")},
                )
            )

        db.commit()
        return {
            "snapshot_date": used_date.isoformat(),
            "inserted": inserted,
            "records": len(raw_records),
            "focus": focus_result,
            "source": "stock_gpzy_pledge_ratio_em",
        }

    def sync_company_pledge_detail_batch(
        self,
        db: Session,
        *,
        symbols: list[str],
        limit_per_symbol: int = 80,
    ) -> dict[str, Any]:
        """
        Pull重要股东股权质押明细(ak.stock_gpzy_pledge_ratio_detail_em) once, then split by symbol.
        """
        codes = self._filter_sync_symbols(symbols)
        if not codes:
            return {}

        ak = self._ak()
        try:
            df = ak.stock_gpzy_pledge_ratio_detail_em()
        except Exception as exc:
            logger.warning("pledge detail fetch failed | error=%s", exc)
            return {code: {"symbol": code, "inserted": 0, "records": 0, "error": str(exc)} for code in codes}

        if df is None or df.empty:
            return {code: {"symbol": code, "inserted": 0, "records": 0} for code in codes}

        rows_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
        for _, row in df.iterrows():
            raw_code = str(row.get("股票代码", "")).strip()
            norm_code = "".join(ch for ch in raw_code if ch.isdigit()).zfill(6) if raw_code else ""
            if norm_code not in rows_by_code:
                continue
            payload = {str(k): self._to_jsonable(v) for k, v in row.to_dict().items()}
            announce_date = self._parse_date(payload.get("公告日期"))
            pledge_start_date = self._parse_date(payload.get("质押开始日期"))
            payload["_announce_date"] = announce_date.isoformat() if announce_date else None
            payload["_pledge_start_date"] = pledge_start_date.isoformat() if pledge_start_date else None
            rows_by_code[norm_code].append(payload)

        result: dict[str, Any] = {}
        for code in codes:
            rows = rows_by_code.get(code) or []
            if not rows:
                result[code] = {"symbol": code, "inserted": 0, "records": 0}
                continue

            rows.sort(
                key=lambda item: (
                    item.get("_announce_date") or "",
                    item.get("_pledge_start_date") or "",
                ),
                reverse=True,
            )
            trimmed_rows = rows[: max(1, limit_per_symbol)]
            latest_announce_date = self._parse_date(trimmed_rows[0].get("_announce_date")) or date.today()

            ratios = [self._safe_float(row.get("占总股本比例")) for row in trimmed_rows]
            ratio_values = [v for v in ratios if v is not None]
            max_ratio = max(ratio_values) if ratio_values else None
            avg_ratio = round(sum(ratio_values) / len(ratio_values), 4) if ratio_values else None

            holder_sorted = sorted(
                trimmed_rows,
                key=lambda item: self._safe_float(item.get("占总股本比例")) or -1.0,
                reverse=True,
            )
            top_holders = []
            for row in holder_sorted[:5]:
                top_holders.append(
                    {
                        "holder": row.get("股东名称"),
                        "pledged_shares": self._safe_float_with_unit(row.get("质押股份数量")),
                        "ratio_to_total_pct": self._safe_float(row.get("占总股本比例")),
                        "ratio_to_holder_pct": self._safe_float(row.get("占所持股份比例")),
                        "institution": row.get("质押机构"),
                        "announcement_date": row.get("公告日期"),
                    }
                )

            normalized_payload = {
                "symbol": code,
                "latest_announcement_date": latest_announce_date.isoformat(),
                "record_count": len(trimmed_rows),
                "max_ratio_to_total_pct": max_ratio,
                "avg_ratio_to_total_pct": avg_ratio,
                "top_holders": top_holders,
            }
            summary = (
                f"质押明细最新公告日={latest_announce_date.isoformat()}, "
                f"样本数={len(trimmed_rows)}, 最大占总股本比例={max_ratio}"
            )

            inserted = 0
            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key="company_pledge_detail",
                    snapshot_date=latest_announce_date,
                    layer="raw",
                    source="ak.stock_gpzy_pledge_ratio_detail_em",
                    payload=trimmed_rows,
                    stock_symbol=code,
                    summary=summary,
                )
            )
            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key="company_pledge_detail",
                    snapshot_date=latest_announce_date,
                    layer="normalized",
                    source="ak.stock_gpzy_pledge_ratio_detail_em",
                    payload=normalized_payload,
                    stock_symbol=code,
                    summary=summary,
                )
            )

            stock = self._ensure_stock(db, code)
            doc_title = f"{code} 重要股东质押明细快照 {latest_announce_date.isoformat()}"
            published_at = datetime.combine(latest_announce_date, datetime.min.time())
            if not self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="pledge_risk",
                title=doc_title,
                published_at=published_at,
            ):
                lines = [summary]
                for item in top_holders[:5]:
                    lines.append(
                        f"{item.get('holder') or '-'} | 占总股本比例={item.get('ratio_to_total_pct')} | "
                        f"占所持股份比例={item.get('ratio_to_holder_pct')} | 机构={item.get('institution') or '-'}"
                    )
                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code,
                        doc_type="pledge_risk",
                        title=doc_title,
                        content="\n".join(lines),
                        source="ak.stock_gpzy_pledge_ratio_detail_em",
                        published_at=published_at,
                        doc_metadata={"latest_announcement_date": latest_announce_date.isoformat()},
                    )
                )

            result[code] = {
                "symbol": code,
                "inserted": inserted,
                "records": len(trimmed_rows),
                "latest_announcement_date": latest_announce_date.isoformat(),
                "source": "stock_gpzy_pledge_ratio_detail_em",
            }

        db.commit()
        return result

    def sync_company_pledge_detail(
        self,
        db: Session,
        *,
        symbol: str,
        limit: int = 80,
    ) -> dict[str, Any]:
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        result = self.sync_company_pledge_detail_batch(
            db,
            symbols=[code],
            limit_per_symbol=limit,
        )
        return result.get(code, {"symbol": code, "inserted": 0, "records": 0})

    def _sync_company_financial_from_abstract(
        self,
        db: Session,
        *,
        stock: Stock,
        code: str,
        limit: int,
    ) -> dict[str, Any] | None:
        ak = self._ak()
        try:
            df = ak.stock_financial_abstract(symbol=code)
        except Exception as exc:
            logger.warning("financial abstract fallback failed | symbol=%s error=%s", code, exc)
            return None
        if df is None or df.empty:
            return None

        report_cols = [col for col in df.columns if str(col) not in {"选项", "指标"}]
        parsed_reports: list[tuple[date, str]] = []
        for col in report_cols:
            report_date = self._parse_date(col)
            if report_date:
                parsed_reports.append((report_date, str(col)))
        parsed_reports.sort(key=lambda item: item[0], reverse=True)
        if not parsed_reports:
            return None

        metric_map: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            metric_name = str(row.get("指标", "")).strip()
            if not metric_name:
                continue
            metric_map[metric_name] = {str(k): self._to_jsonable(v) for k, v in row.to_dict().items()}

        def _metric_value(col_name: str, *metric_names: str) -> float | None:
            for metric_name in metric_names:
                row = metric_map.get(metric_name)
                if not row:
                    continue
                return self._safe_float_with_unit(row.get(col_name))
            return None

        inserted = 0
        for report_date, col_name in parsed_reports[: max(1, limit)]:
            report_name = col_name
            item = (
                db.query(CompanyFinancial)
                .filter(
                    CompanyFinancial.stock_id == stock.id,
                    CompanyFinancial.report_date == report_date,
                    CompanyFinancial.report_name == report_name,
                )
                .first()
            )
            if not item:
                item = CompanyFinancial(stock_id=stock.id, report_date=report_date, report_name=report_name)
                inserted += 1

            item.report_type = "sina_abstract"
            item.source = "akshare"
            item.dataset = "stock_financial_abstract"
            item.row_key = f"sina:{report_name}:{report_date.isoformat()}"
            item.eps = _metric_value(col_name, "摊薄每股收益(元)", "加权每股收益(元)", "每股收益", "每股收益_调整后(元)")
            item.revenue = _metric_value(col_name, "营业总收入")
            item.net_profit = _metric_value(col_name, "归母净利润", "净利润")
            item.gross_margin = _metric_value(col_name, "销售毛利率(%)")
            item.roe = _metric_value(col_name, "净资产收益率(%)", "加权净资产收益率(%)")
            item.asset_liability_ratio = _metric_value(col_name, "资产负债率(%)")
            item.operating_cashflow = _metric_value(col_name, "每股经营性现金流(元)")
            item.yoy_revenue = _metric_value(col_name, "主营业务收入增长率(%)")
            item.yoy_net_profit = _metric_value(col_name, "净利润增长率(%)")
            item.raw = {
                metric_name: row.get(col_name)
                for metric_name, row in metric_map.items()
                if row.get(col_name) is not None
            }
            db.add(item)

        latest_date, latest_col = parsed_reports[0]
        doc_title = f"{code} 财务摘要快照 {latest_date.isoformat()}"
        if not self._doc_exists(
            db,
            stock_id=stock.id,
            stock_symbol=code,
            doc_type="financial_snapshot",
            title=doc_title,
            published_at=datetime.combine(latest_date, datetime.min.time()),
        ):
            content = (
                f"EPS={_metric_value(latest_col, '摊薄每股收益(元)', '加权每股收益(元)', '每股收益')}, "
                f"营收={_metric_value(latest_col, '营业总收入')}, "
                f"归母净利润={_metric_value(latest_col, '归母净利润', '净利润')}, "
                f"毛利率={_metric_value(latest_col, '销售毛利率(%)')}, "
                f"ROE={_metric_value(latest_col, '净资产收益率(%)', '加权净资产收益率(%)')}"
            )
            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="financial_snapshot",
                    title=doc_title,
                    content=content,
                    source="sina-financial-abstract",
                    published_at=datetime.combine(latest_date, datetime.min.time()),
                    doc_metadata={"report_name": latest_col},
                )
            )

        db.commit()
        logger.info(
            "sync_company_financial done | symbol=%s inserted=%s records=%s source=sina_abstract",
            code,
            inserted,
            min(len(parsed_reports), max(1, limit)),
        )
        return {
            "symbol": code,
            "inserted": inserted,
            "records": min(len(parsed_reports), max(1, limit)),
            "source": "sina_abstract",
        }

    def sync_market_overview_layers(self, db: Session, as_of_date: date | None = None) -> dict[str, Any]:
        """
        Extended AKShare coverage with layered storage:
        - raw layer: trimmed raw rows from source interfaces
        - normalized layer: compact fields for downstream experts/retrieval
        """
        ak = self._ak()
        target_date = as_of_date or date.today()
        published_at = datetime.combine(target_date, datetime.min.time())

        inserted = 0
        datasets: dict[str, Any] = {}
        errors: dict[str, str] = {}

        def _store_layered(
            *,
            snapshot_key: str,
            source: str,
            raw_payload: dict[str, Any] | list[Any],
            normalized_payload: dict[str, Any] | list[Any],
            summary: str,
            doc_type: str,
            doc_title: str,
            doc_content: str,
        ) -> None:
            nonlocal inserted
            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key=snapshot_key,
                    snapshot_date=target_date,
                    layer="raw",
                    source=source,
                    payload=raw_payload,
                    summary=summary,
                )
            )
            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key=snapshot_key,
                    snapshot_date=target_date,
                    layer="normalized",
                    source=source,
                    payload=normalized_payload,
                    summary=summary,
                )
            )
            datasets[snapshot_key] = {
                "summary": summary,
                "raw_size": len(raw_payload) if isinstance(raw_payload, list) else 1,
            }
            if not self._doc_exists(
                db,
                stock_symbol=None,
                doc_type=doc_type,
                title=doc_title,
                published_at=published_at,
            ):
                db.add(
                    Document(
                        stock_symbol=None,
                        doc_type=doc_type,
                        title=doc_title,
                        content=doc_content,
                        source=source,
                        published_at=published_at,
                        doc_metadata={
                            "snapshot_key": snapshot_key,
                            "snapshot_date": target_date.isoformat(),
                        },
                    )
                )
            if not self._macro_exists(db, title=doc_title, published_at=published_at):
                db.add(
                    MacroNews(
                        title=doc_title,
                        content=doc_content,
                        source=source,
                        published_at=published_at,
                        news_metadata={
                            "snapshot_key": snapshot_key,
                            "snapshot_date": target_date.isoformat(),
                            "doc_type": doc_type,
                        },
                    )
                )

        try:
            sse_df = ak.stock_sse_summary()
            sse_raw = self._df_to_records(sse_df)
            sse_norm = {}
            for row in sse_raw:
                item = str(row.get("项目", "")).strip()
                if not item:
                    continue
                sse_norm[item] = {k: v for k, v in row.items() if k != "项目" and v is not None}
            total_mv = (sse_norm.get("总市值", {}) or {}).get("股票")
            sse_summary = f"上交所总市值={total_mv}"
            _store_layered(
                snapshot_key="market_sse_summary",
                source="ak.stock_sse_summary",
                raw_payload=sse_raw,
                normalized_payload=sse_norm,
                summary=sse_summary,
                doc_type="market_overview",
                doc_title=f"{target_date.isoformat()} 上交所市场总貌",
                doc_content=sse_summary,
            )
        except Exception as exc:
            errors["market_sse_summary"] = str(exc)

        try:
            szse_df = None
            used_date = target_date
            for offset in range(0, 7):
                candidate = target_date - timedelta(days=offset)
                try:
                    fetched = ak.stock_szse_summary(date=candidate.strftime("%Y%m%d"))
                except Exception:
                    continue
                if fetched is not None and not fetched.empty:
                    szse_df = fetched
                    used_date = candidate
                    break
            if szse_df is not None and not szse_df.empty:
                szse_raw = self._df_to_records(szse_df)
                stock_row = next((r for r in szse_raw if str(r.get("证券类别", "")) == "股票"), {})
                szse_norm = {
                    "snapshot_date": used_date.isoformat(),
                    "stock_count": self._safe_int(stock_row.get("数量")),
                    "stock_turnover": self._safe_float(stock_row.get("成交金额")),
                    "stock_total_mv": self._safe_float(stock_row.get("总市值")),
                    "stock_float_mv": self._safe_float(stock_row.get("流通市值")),
                }
                szse_summary = (
                    f"深交所股票数量={szse_norm.get('stock_count')}, "
                    f"成交金额={szse_norm.get('stock_turnover')}"
                )
                _store_layered(
                    snapshot_key="market_szse_summary",
                    source="ak.stock_szse_summary",
                    raw_payload=szse_raw,
                    normalized_payload=szse_norm,
                    summary=szse_summary,
                    doc_type="market_overview",
                    doc_title=f"{target_date.isoformat()} 深交所市场总貌",
                    doc_content=szse_summary,
                )
        except Exception as exc:
            errors["market_szse_summary"] = str(exc)

        try:
            sse_daily_df = None
            used_date = target_date
            for offset in range(0, 7):
                candidate = target_date - timedelta(days=offset)
                try:
                    fetched = ak.stock_sse_deal_daily(date=candidate.strftime("%Y%m%d"))
                except Exception:
                    continue
                if fetched is not None and not fetched.empty:
                    sse_daily_df = fetched
                    used_date = candidate
                    break
            if sse_daily_df is not None and not sse_daily_df.empty:
                sse_daily_raw = self._df_to_records(sse_daily_df)
                sse_daily_norm = {}
                for row in sse_daily_raw:
                    item = str(row.get("单日情况", "")).strip()
                    if not item:
                        continue
                    sse_daily_norm[item] = {
                        k: v for k, v in row.items() if k != "单日情况" and v is not None
                    }
                turnover = (sse_daily_norm.get("成交金额", {}) or {}).get("股票")
                summary = f"上交所单日成交金额(股票)={turnover}, 统计日={used_date.isoformat()}"
                _store_layered(
                    snapshot_key="market_sse_deal_daily",
                    source="ak.stock_sse_deal_daily",
                    raw_payload=sse_daily_raw,
                    normalized_payload=sse_daily_norm,
                    summary=summary,
                    doc_type="market_overview",
                    doc_title=f"{target_date.isoformat()} 上交所单日成交概况",
                    doc_content=summary,
                )
        except Exception as exc:
            errors["market_sse_deal_daily"] = str(exc)

        try:
            activity_df = ak.stock_market_activity_legu()
            activity_raw = self._df_to_records(activity_df)
            activity_norm = {str(row.get("item")): row.get("value") for row in activity_raw if row.get("item")}
            summary = (
                f"上涨={activity_norm.get('上涨')}, 下跌={activity_norm.get('下跌')}, "
                f"真实涨停={activity_norm.get('真实涨停')}, 真实跌停={activity_norm.get('真实跌停')}"
            )
            _store_layered(
                snapshot_key="market_activity_legu",
                source="ak.stock_market_activity_legu",
                raw_payload=activity_raw,
                normalized_payload=activity_norm,
                summary=summary,
                doc_type="market_heat",
                doc_title=f"{target_date.isoformat()} 市场赚钱效应",
                doc_content=summary,
            )
        except Exception as exc:
            errors["market_activity_legu"] = str(exc)

        try:
            hot_rank_df = ak.stock_hot_rank_em()
            hot_rank_raw = self._df_to_records(hot_rank_df, limit=100)
            hot_rank_norm = {
                "top10": hot_rank_raw[:10],
                "count": len(hot_rank_raw),
            }
            top_name = hot_rank_raw[0].get("股票名称") if hot_rank_raw else None
            summary = f"A股人气榜Top1={top_name}, 样本数={len(hot_rank_raw)}"
            _store_layered(
                snapshot_key="market_hot_rank_em",
                source="ak.stock_hot_rank_em",
                raw_payload=hot_rank_raw,
                normalized_payload=hot_rank_norm,
                summary=summary,
                doc_type="market_heat",
                doc_title=f"{target_date.isoformat()} 个股人气榜",
                doc_content=summary,
            )
        except Exception as exc:
            errors["market_hot_rank_em"] = str(exc)

        try:
            hot_up_df = ak.stock_hot_up_em()
            hot_up_raw = self._df_to_records(hot_up_df, limit=100)
            hot_up_norm = {"top10": hot_up_raw[:10], "count": len(hot_up_raw)}
            summary = f"飙升榜记录数={len(hot_up_raw)}"
            _store_layered(
                snapshot_key="market_hot_up_em",
                source="ak.stock_hot_up_em",
                raw_payload=hot_up_raw,
                normalized_payload=hot_up_norm,
                summary=summary,
                doc_type="market_heat",
                doc_title=f"{target_date.isoformat()} 个股飙升榜",
                doc_content=summary,
            )
        except Exception as exc:
            errors["market_hot_up_em"] = str(exc)

        try:
            margin_df = ak.stock_margin_account_info()
            margin_raw = self._df_to_records(margin_df, limit=3)
            latest = margin_raw[0] if margin_raw else {}
            margin_norm = {
                "date": latest.get("日期"),
                "financing_balance_billion": self._safe_float(latest.get("融资余额")),
                "securities_balance_billion": self._safe_float(latest.get("融券余额")),
                "avg_maintenance_ratio": self._safe_float(latest.get("平均维持担保比例")),
            }
            summary = (
                f"两融余额={margin_norm.get('financing_balance_billion')}亿/"
                f"{margin_norm.get('securities_balance_billion')}亿"
            )
            _store_layered(
                snapshot_key="market_margin_account_info",
                source="ak.stock_margin_account_info",
                raw_payload=margin_raw,
                normalized_payload=margin_norm,
                summary=summary,
                doc_type="market_overview",
                doc_title=f"{target_date.isoformat()} 两融账户统计",
                doc_content=summary,
            )
        except Exception as exc:
            errors["market_margin_account_info"] = str(exc)

        db.commit()
        return {
            "snapshot_date": target_date.isoformat(),
            "inserted": inserted,
            "datasets": datasets,
            "errors": errors,
        }

    def sync_company_peer_comparison(self, db: Session, symbol: str, limit: int = 30) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)
        target_date = date.today()
        published_at = datetime.combine(target_date, datetime.min.time())
        em_symbol = f"{exchange_from_symbol(code)}{code}"

        inserted = 0
        datasets: dict[str, Any] = {}
        errors: dict[str, str] = {}
        summary_lines: list[str] = []

        interfaces = [
            ("peer_growth", "ak.stock_zh_growth_comparison_em", ak.stock_zh_growth_comparison_em),
            ("peer_valuation", "ak.stock_zh_valuation_comparison_em", ak.stock_zh_valuation_comparison_em),
            ("peer_dupont", "ak.stock_zh_dupont_comparison_em", ak.stock_zh_dupont_comparison_em),
            ("peer_scale", "ak.stock_zh_scale_comparison_em", ak.stock_zh_scale_comparison_em),
        ]

        for snapshot_key, source_name, fn in interfaces:
            try:
                df = fn(symbol=em_symbol)
            except Exception as exc:
                errors[snapshot_key] = str(exc)
                continue
            if df is None or df.empty:
                continue

            records = self._df_to_records(df, limit=limit)
            own_rows = [row for row in records if str(row.get("代码", "")).zfill(6) == code][:1]
            industry_rows = [
                row
                for row in records
                if str(row.get("代码", "")).strip() in {"行业平均", "行业中值"}
            ][:2]
            normalized = {
                "symbol": code,
                "self_row": own_rows[0] if own_rows else {},
                "industry_rows": industry_rows,
                "sample_rows": records[: min(10, len(records))],
            }

            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key=snapshot_key,
                    snapshot_date=target_date,
                    layer="raw",
                    source=source_name,
                    payload=records,
                    stock_symbol=code,
                    summary=f"{snapshot_key} rows={len(records)}",
                )
            )
            inserted += int(
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key=snapshot_key,
                    snapshot_date=target_date,
                    layer="normalized",
                    source=source_name,
                    payload=normalized,
                    stock_symbol=code,
                    summary=f"{snapshot_key} normalized",
                )
            )
            datasets[snapshot_key] = {"rows": len(records)}
            if own_rows:
                summary_lines.append(f"{snapshot_key}: 已提取公司自身同行比较数据")

        if summary_lines:
            title = f"{code} 同行比较快照 {target_date.isoformat()}"
            if not self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="peer_comparison",
                title=title,
                published_at=published_at,
            ):
                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code,
                        doc_type="peer_comparison",
                        title=title,
                        content="; ".join(summary_lines),
                        source="akshare-peer-comparison",
                        published_at=published_at,
                        doc_metadata={"datasets": datasets},
                    )
                )

        db.commit()
        return {
            "symbol": code,
            "inserted": inserted,
            "datasets": datasets,
            "errors": errors,
        }

    @staticmethod
    def _first_numeric(row: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            if key not in row:
                continue
            value = AkshareService._safe_float_with_unit(row.get(key))
            if value is not None:
                return value
        return None

    @staticmethod
    def _first_text(row: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            if key not in row:
                continue
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text in {"", "-", "--", "None", "nan", "NaN"}:
                continue
            return text
        return None

    def _cninfo_report_type(self, dataset: str, row: dict[str, Any]) -> str | None:
        dataset_keys: dict[str, list[str]] = {
            "p_stock2238": ["F003V", "F002V"],
            "p_stock2239": ["F007V", "F006V", "F002C"],
            "p_stock2300": ["F003V", "F005V", "F002V"],
            "p_stock2301": ["F003V", "F005V", "F002V"],
            "p_stock2302": ["F003V", "F005V", "F002V"],
            "p_stock2303": ["F071V", "F002V", "F001V"],
            "p_stock2328": ["F002V", "F001V"],
            "p_stock2387": ["F004V", "F002C"],
            "p_ods3302": ["F003V", "F005V"],
        }
        default_keys = ["REPORT_TYPE", "F003V", "F002V", "F004V", "F005V", "F071V"]
        text = self._first_text(row, dataset_keys.get(dataset, default_keys))
        return text[:64] if text else None

    def _cninfo_report_name_seed(self, dataset: str, row: dict[str, Any], report_date: date) -> str:
        dataset_keys: dict[str, list[str]] = {
            "p_stock2237_inc": ["ROWKEY", "F001D", "F006D", "F002D"],
            "p_stock2237": ["F001D", "F006D", "F002D"],
            "p_ods3302": ["F003V", "F005V", "DECLAREDATE", "ENDDATE"],
            "p_stock2238": ["F001D", "F003V", "DECLAREDATE"],
            "p_stock2239": ["F001D", "F007V", "DECLAREDATE"],
            "p_stock2300": ["F001D", "F003V", "DECLAREDATE", "ENDDATE"],
            "p_stock2301": ["F001D", "F003V", "DECLAREDATE", "ENDDATE"],
            "p_stock2302": ["F001D", "F003V", "DECLAREDATE", "ENDDATE"],
            "p_stock2303": ["F069D", "F071V", "ENDDATE"],
            "p_stock2328": ["ENDDATE", "F002V", "DECLAREDATE"],
            "p_stock2387": ["F001D", "F004V", "ENDDATE"],
            "p_stock2399": ["RPTDATE", "SECCODE"],
        }
        default_keys = ["REPORT_DATE_NAME", "REPORT_NAME", "REPORT", "F003V", "F002V", "ROWKEY"]
        text = self._first_text(row, dataset_keys.get(dataset, default_keys))
        return text or report_date.isoformat()

    def _map_cninfo_core_metrics(self, dataset: str, row: dict[str, Any]) -> dict[str, float | None]:
        metrics: dict[str, float | None] = {
            "eps": None,
            "revenue": None,
            "net_profit": None,
            "gross_margin": None,
            "roe": None,
            "asset_liability_ratio": None,
            "operating_cashflow": None,
            "yoy_revenue": None,
            "yoy_net_profit": None,
        }

        if dataset == "p_stock2300":
            metrics["asset_liability_ratio"] = self._first_numeric(row, ["F041N", "ZCFZL"])
            return metrics

        if dataset == "p_stock2301":
            revenue = self._first_numeric(row, ["F035N", "F006N", "F089N"])
            cost = self._first_numeric(row, ["F036N", "F007N", "F090N"])
            metrics["eps"] = self._first_numeric(row, ["F031N", "F032N"])
            metrics["revenue"] = revenue
            metrics["net_profit"] = self._first_numeric(row, ["F028N", "F027N", "F102N", "F101N"])
            metrics["gross_margin"] = self._first_numeric(row, ["F078N"])
            if metrics["gross_margin"] is None and revenue not in (None, 0) and cost is not None:
                metrics["gross_margin"] = (revenue - cost) / revenue * 100
            metrics["yoy_revenue"] = self._first_numeric(row, ["F052N", "TOTALOPERATEREVETZ"])
            metrics["yoy_net_profit"] = self._first_numeric(row, ["F142N", "F053N", "PARENTNETPROFITTZ"])
            return metrics

        if dataset == "p_stock2302":
            metrics["operating_cashflow"] = self._first_numeric(row, ["F015N", "F060N", "F105N"])
            metrics["net_profit"] = self._first_numeric(row, ["F044N"])
            return metrics

        if dataset == "p_stock2303":
            metrics["eps"] = self._first_numeric(row, ["F003N", "F004N", "F033N"])
            metrics["revenue"] = self._first_numeric(row, ["F089N"])
            metrics["net_profit"] = self._first_numeric(row, ["F102N", "F101N"])
            metrics["gross_margin"] = self._first_numeric(row, ["F078N"])
            metrics["roe"] = self._first_numeric(row, ["F081N", "F067N", "F014N", "F035N"])
            metrics["asset_liability_ratio"] = self._first_numeric(row, ["F041N"])
            metrics["operating_cashflow"] = self._first_numeric(row, ["F105N", "F060N"])
            metrics["yoy_revenue"] = self._first_numeric(row, ["F052N", "F006N"])
            metrics["yoy_net_profit"] = self._first_numeric(row, ["F142N", "F053N", "F012N"])
            return metrics

        if dataset == "p_stock2328":
            metrics["eps"] = self._first_numeric(row, ["F006N"])
            metrics["net_profit"] = self._first_numeric(row, ["F003N"])
            metrics["roe"] = self._first_numeric(row, ["F007N", "F008N"])
            return metrics

        if dataset == "p_stock2387":
            metrics["eps"] = self._first_numeric(row, ["F033N"])
            metrics["revenue"] = self._first_numeric(row, ["F005N"])
            metrics["net_profit"] = self._first_numeric(row, ["F011N", "F013N"])
            metrics["gross_margin"] = self._first_numeric(row, ["F015N"])
            metrics["roe"] = self._first_numeric(row, ["F035N"])
            metrics["asset_liability_ratio"] = self._first_numeric(row, ["F041N"])
            metrics["operating_cashflow"] = self._first_numeric(row, ["F051N"])
            metrics["yoy_revenue"] = self._first_numeric(row, ["F006N"])
            metrics["yoy_net_profit"] = self._first_numeric(row, ["F012N", "F014N"])
            return metrics

        if dataset == "p_ods3302":
            revenue = self._first_numeric(row, ["F006N"])
            cost = self._first_numeric(row, ["F007N"])
            metrics["revenue"] = revenue
            if revenue not in (None, 0) and cost is not None:
                metrics["gross_margin"] = (revenue - cost) / revenue * 100
            return metrics

        if dataset == "p_stock2238":
            low = self._first_numeric(row, ["F007N"])
            high = self._first_numeric(row, ["F008N"])
            if low is not None and high is not None:
                metrics["net_profit"] = (low + high) / 2
            else:
                metrics["net_profit"] = low if low is not None else high

            y_low = self._first_numeric(row, ["F009N"])
            y_high = self._first_numeric(row, ["F010N"])
            if y_low is not None and y_high is not None:
                metrics["yoy_net_profit"] = (y_low + y_high) / 2
            else:
                metrics["yoy_net_profit"] = y_low if y_low is not None else y_high
            return metrics

        # p_stock2237_inc / p_stock2237 / p_stock2239 / p_stock2399:
        # mostly schedule/meta datasets; keep canonical numeric metrics empty.
        return metrics

    def _first_date(self, row: dict[str, Any], keys: list[str]) -> date | None:
        for key in keys:
            if key not in row:
                continue
            parsed = self._parse_date(row.get(key))
            if parsed:
                return parsed
        return None

    def _get_cninfo_increment_state(self, db: Session, dataset: str) -> int:
        snapshot = (
            db.query(AkDataSnapshot)
            .filter(
                AkDataSnapshot.snapshot_key == f"cninfo_state_{dataset}",
                AkDataSnapshot.layer == "state",
                AkDataSnapshot.stock_symbol.is_(None),
            )
            .order_by(AkDataSnapshot.snapshot_date.desc(), AkDataSnapshot.id.desc())
            .first()
        )
        if not snapshot or not isinstance(snapshot.payload, dict):
            return 0
        try:
            return int(snapshot.payload.get("objectid") or 0)
        except Exception:
            return 0

    def _set_cninfo_increment_state(self, db: Session, dataset: str, objectid: int, *, snapshot_date: date) -> None:
        self._upsert_ak_snapshot(
            db,
            snapshot_key=f"cninfo_state_{dataset}",
            snapshot_date=snapshot_date,
            layer="state",
            source="cninfo",
            payload={"objectid": int(objectid)},
            stock_symbol=None,
            summary=f"max_objectid={int(objectid)}",
        )

    @staticmethod
    def _find_pending_financial(
        db: Session,
        *,
        stock_id: int,
        dataset: str,
        row_key: str | None,
        report_date: date,
        report_name: str,
    ) -> CompanyFinancial | None:
        # SessionLocal is configured with autoflush=False, so duplicates in the same
        # batch can exist in db.new and be invisible to DB queries before commit.
        for obj in db.new:
            if not isinstance(obj, CompanyFinancial):
                continue
            if obj.stock_id != stock_id:
                continue
            if row_key:
                if obj.dataset == dataset and obj.row_key == row_key:
                    return obj
            else:
                if obj.report_date == report_date and obj.report_name == report_name:
                    return obj
        return None

    @staticmethod
    def _find_pending_financial_event(
        db: Session,
        *,
        stock_id: int,
        dataset: str,
        row_key: str | None,
        event_date: date,
        event_name: str,
    ) -> CompanyFinancialEvent | None:
        for obj in db.new:
            if not isinstance(obj, CompanyFinancialEvent):
                continue
            if obj.stock_id != stock_id:
                continue
            if row_key:
                if obj.dataset == dataset and obj.row_key == row_key:
                    return obj
            else:
                if obj.event_date == event_date and obj.event_name == event_name:
                    return obj
        return None

    def _upsert_cninfo_financial_event_row(
        self,
        db: Session,
        *,
        stock: Stock,
        dataset: str,
        row: dict[str, Any],
        fallback_event_date: date,
    ) -> tuple[int, int]:
        object_id = self._safe_int(row.get("OBJECTID"))
        change_code = self._safe_int(row.get("CHANGE_CODE"))
        row_key = str(row.get("ROWKEY") or "").strip() or None

        event_date = self._first_date(
            row,
            ["REPORT_DATE", "RPTDATE", "F001D", "ENDDATE", "F069D", "DECLAREDATE", "STARTDATE"],
        ) or fallback_event_date
        declare_date = self._first_date(row, ["DECLAREDATE", "F002D", "F006D"])
        start_date = self._first_date(row, ["STARTDATE"])
        end_date = self._first_date(row, ["ENDDATE", "RPTDATE", "F001D"])

        event_seed = self._cninfo_report_name_seed(dataset, row, event_date)
        event_name_base = f"{dataset}:{str(event_seed)}"
        if row_key:
            event_name = f"{event_name_base}:{row_key[-12:]}"[:96]
        elif object_id is not None:
            event_name = f"{event_name_base}:{int(object_id)}"[:96]
        else:
            signature_src = "|".join(
                [
                    str(row.get("SECCODE") or ""),
                    str(row.get("DECLAREDATE") or ""),
                    str(row.get("F001D") or ""),
                    str(row.get("ENDDATE") or ""),
                    str(row.get("F003V") or ""),
                    str(row.get("F005V") or ""),
                ]
            )
            signature = hashlib.md5(signature_src.encode("utf-8")).hexdigest()[:8]
            event_name = f"{event_name_base}:{signature}"[:96]

        item = self._find_pending_financial_event(
            db,
            stock_id=stock.id,
            dataset=dataset,
            row_key=row_key,
            event_date=event_date,
            event_name=event_name,
        )
        if item is None:
            item_query = db.query(CompanyFinancialEvent).filter(
                CompanyFinancialEvent.stock_id == stock.id,
                CompanyFinancialEvent.dataset == dataset,
            )
            if row_key:
                item = item_query.filter(CompanyFinancialEvent.row_key == row_key).first()
            else:
                item = (
                    db.query(CompanyFinancialEvent)
                    .filter(
                        CompanyFinancialEvent.stock_id == stock.id,
                        CompanyFinancialEvent.event_date == event_date,
                        CompanyFinancialEvent.event_name == event_name,
                    )
                    .first()
                )

        if change_code == 2:
            if item:
                if item in db.new:
                    db.expunge(item)
                else:
                    db.delete(item)
                return (0, 1)
            return (0, 0)

        inserted = 0
        if not item:
            item = CompanyFinancialEvent(
                stock_id=stock.id,
                event_date=event_date,
                event_name=event_name,
            )
            inserted = 1

        item.event_type = self._cninfo_report_type(dataset, row)
        item.source = "cninfo"
        item.dataset = dataset
        item.row_key = row_key
        item.object_id = object_id
        item.change_code = change_code
        item.declare_date = declare_date
        item.start_date = start_date
        item.end_date = end_date
        item.raw = {str(k): self._to_jsonable(v) for k, v in row.items()}
        db.add(item)
        return (inserted, 0)

    def _migrate_legacy_event_rows(
        self,
        db: Session,
        *,
        stock: Stock,
    ) -> int:
        legacy_rows = (
            db.query(CompanyFinancial)
            .filter(
                CompanyFinancial.stock_id == stock.id,
                CompanyFinancial.dataset.in_(list(CNINFO_EVENT_DATASETS)),
            )
            .all()
        )
        migrated = 0
        for legacy in legacy_rows:
            event_date = legacy.report_date or date.today()
            event_name = str(legacy.report_name or f"{legacy.dataset}:{event_date.isoformat()}")[:96]
            item = (
                db.query(CompanyFinancialEvent)
                .filter(
                    CompanyFinancialEvent.stock_id == stock.id,
                    CompanyFinancialEvent.event_date == event_date,
                    CompanyFinancialEvent.event_name == event_name,
                )
                .first()
            )
            if item is None:
                item = CompanyFinancialEvent(
                    stock_id=stock.id,
                    event_date=event_date,
                    event_name=event_name,
                )
                migrated += 1
            item.event_type = legacy.report_type
            item.source = legacy.source
            item.dataset = legacy.dataset
            item.row_key = legacy.row_key
            item.object_id = legacy.object_id
            item.change_code = legacy.change_code
            item.declare_date = legacy.declare_date
            item.start_date = legacy.start_date
            item.end_date = legacy.end_date
            item.raw = legacy.raw if isinstance(legacy.raw, dict) else {}
            db.add(item)
            db.delete(legacy)
        return migrated

    def _upsert_cninfo_financial_row(
        self,
        db: Session,
        *,
        stock: Stock,
        dataset: str,
        row: dict[str, Any],
        fallback_report_date: date,
    ) -> tuple[int, int]:
        object_id = self._safe_int(row.get("OBJECTID"))
        change_code = self._safe_int(row.get("CHANGE_CODE"))
        row_key = str(row.get("ROWKEY") or "").strip() or None

        report_date = self._first_date(
            row,
            ["REPORT_DATE", "RPTDATE", "F001D", "ENDDATE", "F069D", "DECLAREDATE", "STARTDATE"],
        ) or fallback_report_date
        declare_date = self._first_date(row, ["DECLAREDATE", "F002D", "F006D"])
        start_date = self._first_date(row, ["STARTDATE"])
        end_date = self._first_date(row, ["ENDDATE", "RPTDATE", "F001D"])

        raw_report_name = self._cninfo_report_name_seed(dataset, row, report_date)
        report_name_base = f"{dataset}:{str(raw_report_name)}"
        if row_key:
            report_name = f"{report_name_base}:{row_key[-12:]}"[:64]
        elif object_id is not None:
            report_name = f"{report_name_base}:{int(object_id)}"[:64]
        else:
            signature_src = "|".join(
                [
                    str(row.get("SECCODE") or ""),
                    str(row.get("DECLAREDATE") or ""),
                    str(row.get("F001D") or ""),
                    str(row.get("ENDDATE") or ""),
                    str(row.get("F003V") or ""),
                    str(row.get("F005V") or ""),
                ]
            )
            signature = hashlib.md5(signature_src.encode("utf-8")).hexdigest()[:8]
            report_name = f"{report_name_base}:{signature}"[:64]

        # First check pending objects in current transaction to avoid duplicate inserts.
        item = self._find_pending_financial(
            db,
            stock_id=stock.id,
            dataset=dataset,
            row_key=row_key,
            report_date=report_date,
            report_name=report_name,
        )
        if item is None:
            item_query = db.query(CompanyFinancial).filter(
                CompanyFinancial.stock_id == stock.id,
                CompanyFinancial.dataset == dataset,
            )
            if row_key:
                item = item_query.filter(CompanyFinancial.row_key == row_key).first()
            else:
                item = (
                    db.query(CompanyFinancial)
                    .filter(
                        CompanyFinancial.stock_id == stock.id,
                        CompanyFinancial.report_date == report_date,
                        CompanyFinancial.report_name == report_name,
                    )
                    .first()
                )

        # Incremental delete instruction from CNInfo.
        if change_code == 2:
            if item:
                if item in db.new:
                    db.expunge(item)
                else:
                    db.delete(item)
                return (0, 1)
            return (0, 0)

        inserted = 0
        if not item:
            item = CompanyFinancial(
                stock_id=stock.id,
                report_date=report_date,
                report_name=report_name,
            )
            inserted = 1

        item.report_type = self._cninfo_report_type(dataset, row)
        item.source = "cninfo"
        item.dataset = dataset
        item.row_key = row_key
        item.object_id = object_id
        item.change_code = change_code
        item.declare_date = declare_date
        item.start_date = start_date
        item.end_date = end_date

        core = self._map_cninfo_core_metrics(dataset, row)
        item.eps = core["eps"]
        item.revenue = core["revenue"]
        item.net_profit = core["net_profit"]
        item.gross_margin = core["gross_margin"]
        item.roe = core["roe"]
        item.asset_liability_ratio = core["asset_liability_ratio"]
        item.operating_cashflow = core["operating_cashflow"]
        item.yoy_revenue = core["yoy_revenue"]
        item.yoy_net_profit = core["yoy_net_profit"]
        item.raw = {str(k): self._to_jsonable(v) for k, v in row.items()}
        db.add(item)
        return (inserted, 0)

    def _sync_company_financial_cninfo(
        self,
        db: Session,
        *,
        stock: Stock,
        code: str,
        limit: int,
    ) -> dict[str, Any]:
        if not cninfo_client.enabled:
            logger.info("cninfo financial sync skipped | symbol=%s reason=cninfo_not_enabled", code)
            return {"symbol": code, "enabled": False, "source": "cninfo", "inserted": 0, "deleted": 0, "records": 0}

        target_date = date.today()
        window_start = (target_date - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
        window_end = target_date.strftime("%Y-%m-%d")
        max_rows = max(200, min(2000, limit * 80))
        dataset_specs = [
            {"dataset": "p_stock2237_inc", "path": "/api/load/p_stock2237_inc", "incremental": True},
            {"dataset": "p_stock2237", "path": "/api/stock/p_stock2237"},
            {"dataset": "p_ods3302", "path": "/api/stock/p_ods3302"},
            {"dataset": "p_stock2238", "path": "/api/stock/p_stock2238"},
            {"dataset": "p_stock2239", "path": "/api/stock/p_stock2239"},
            {"dataset": "p_stock2300", "path": "/api/stock/p_stock2300"},
            {"dataset": "p_stock2301", "path": "/api/stock/p_stock2301"},
            {"dataset": "p_stock2302", "path": "/api/stock/p_stock2302"},
            {"dataset": "p_stock2303", "path": "/api/stock/p_stock2303"},
            {"dataset": "p_stock2328", "path": "/api/stock/p_stock2328"},
            {"dataset": "p_stock2387", "path": "/api/stock/p_stock2387"},
            {"dataset": "p_stock2399", "path": "/api/stock/p_stock2399"},
        ]

        inserted_total = 0
        deleted_total = 0
        event_inserted_total = 0
        event_deleted_total = 0
        financial_record_total = 0
        event_record_total = 0
        errors: list[str] = []
        dataset_rows: dict[str, int] = {}
        migrated_legacy_events = self._migrate_legacy_event_rows(db, stock=stock)
        if migrated_legacy_events > 0:
            logger.info(
                "migrated legacy event-like rows to company_financial_events | symbol=%s migrated=%s",
                code,
                migrated_legacy_events,
            )

        for spec in dataset_specs:
            dataset = spec["dataset"]
            params: dict[str, Any] = {"format": "json"}
            if spec.get("incremental"):
                objectid = self._get_cninfo_increment_state(db, dataset)
                params["objectid"] = max(0, objectid)
                params["rowcount"] = max(1, min(2000, int(settings.cninfo_increment_rowcount)))
            else:
                params["scode"] = code
                params["@limit"] = max_rows
                if dataset == "p_stock2237":
                    params["sdate"] = window_start
                    params["edate"] = window_end
                if dataset in {"p_stock2238", "p_stock2239", "p_stock2328"}:
                    params["sdate"] = window_start
                    params["edate"] = window_end
                if dataset in {"p_stock2300", "p_stock2301", "p_stock2302", "p_stock2303", "p_stock2387"}:
                    params["sdate"] = window_start
                    params["edate"] = window_end

            try:
                response = cninfo_client.request(spec["path"], params=params)
            except CninfoClientError as exc:
                error_text = str(exc)
                errors.append(f"{dataset}: {error_text}")
                logger.warning(
                    "cninfo dataset sync failed | symbol=%s dataset=%s path=%s params=%s error=%s",
                    code,
                    dataset,
                    spec["path"],
                    params,
                    exc,
                )
                lowered = error_text.lower()
                is_endpoint_permission_denied = (
                    "code=451" in lowered
                    or "apifilter" in lowered
                    or "尚未授权" in error_text
                    or "未经授权" in error_text
                )
                is_token_null = "token null" in lowered
                is_auth_like = any(
                    marker in lowered
                    for marker in (
                        "auth",
                        "login",
                        "unauthorized",
                        "forbidden",
                        "token",
                        "enckey",
                        "cookie",
                        "未登录",
                        "请先登录",
                        "无权限",
                        "过期",
                    )
                )
                # 451 can be endpoint-level permission issue, but `token null`
                # indicates session/auth is globally invalid and should abort early.
                if is_auth_like and (is_token_null or not is_endpoint_permission_denied):
                    # Auth/session failures are global; stop further per-dataset calls to avoid noisy logs.
                    logger.error(
                        "cninfo auth/session failure, abort remaining datasets | symbol=%s dataset=%s error=%s",
                        code,
                        dataset,
                        exc,
                    )
                    break
                continue

            rows = response.records or []
            if spec.get("incremental"):
                old_objectid = int(params.get("objectid") or 0)
                max_objectid = response.object_id_max if response.object_id_max is not None else old_objectid
                if max_objectid > old_objectid:
                    self._set_cninfo_increment_state(db, dataset, max_objectid, snapshot_date=target_date)
                rows = [
                    row
                    for row in rows
                    if self._normalize_code_token(row.get("SECCODE")) == code
                ]
            else:
                filtered_rows: list[dict[str, Any]] = []
                for row in rows:
                    row_code = self._normalize_code_token(
                        row.get("SECCODE") or row.get("SECURITY_CODE") or row.get("SCODE")
                    )
                    if row_code and row_code != code:
                        continue
                    filtered_rows.append(row)
                rows = filtered_rows

            rows = rows[:max_rows]
            dataset_rows[dataset] = len(rows)
            if not rows:
                raw_keys = sorted(response.raw.keys())[:12] if isinstance(response.raw, dict) else []
                raw_result_code = response.raw.get("resultcode") if isinstance(response.raw, dict) else None
                raw_result_msg = response.raw.get("resultmsg") if isinstance(response.raw, dict) else None
                logger.info(
                    "cninfo dataset empty | symbol=%s dataset=%s params=%s raw_keys=%s resultcode=%s resultmsg=%s",
                    code,
                    dataset,
                    params,
                    raw_keys,
                    raw_result_code,
                    raw_result_msg,
                )
            if dataset in CNINFO_EVENT_DATASETS:
                event_record_total += len(rows)
            elif dataset in CNINFO_FINANCIAL_DATASETS:
                financial_record_total += len(rows)
            else:
                logger.info("cninfo dataset skipped (unclassified) | symbol=%s dataset=%s", code, dataset)
                continue

            if rows:
                self._upsert_ak_snapshot(
                    db,
                    snapshot_key=f"cninfo_{dataset}",
                    snapshot_date=target_date,
                    layer="raw",
                    source="cninfo",
                    payload=rows,
                    stock_symbol=code,
                    summary=f"{dataset} rows={len(rows)}",
                )

            for row in rows:
                if dataset in CNINFO_EVENT_DATASETS:
                    inserted, deleted = self._upsert_cninfo_financial_event_row(
                        db,
                        stock=stock,
                        dataset=dataset,
                        row=row,
                        fallback_event_date=target_date,
                    )
                    event_inserted_total += inserted
                    event_deleted_total += deleted
                elif dataset in CNINFO_FINANCIAL_DATASETS:
                    inserted, deleted = self._upsert_cninfo_financial_row(
                        db,
                        stock=stock,
                        dataset=dataset,
                        row=row,
                        fallback_report_date=target_date,
                    )
                    inserted_total += inserted
                    deleted_total += deleted

        db.commit()
        logger.info(
            "sync_company_financial cninfo done | symbol=%s inserted=%s deleted=%s records=%s "
            "event_inserted=%s event_deleted=%s event_records=%s datasets=%s",
            code,
            inserted_total,
            deleted_total,
            financial_record_total,
            event_inserted_total,
            event_deleted_total,
            event_record_total,
            dataset_rows,
        )
        return {
            "symbol": code,
            "enabled": True,
            "source": "cninfo",
            "inserted": inserted_total,
            "deleted": deleted_total,
            "records": financial_record_total,
            "event_inserted": event_inserted_total,
            "event_deleted": event_deleted_total,
            "event_records": event_record_total,
            "datasets": dataset_rows,
            "errors": errors,
        }

    @staticmethod
    def _cninfo_errors_look_like_auth_fail(errors: list[str] | None) -> bool:
        if not errors:
            return False
        text = " ".join(str(item or "") for item in errors).lower()
        markers = [
            "token null",
            "unauthorized",
            "forbidden",
            "auth",
            "login",
            "cookie",
            "enckey",
            "未经授权",
            "未登录",
            "请先登录",
        ]
        return any(marker in text for marker in markers)

    def _fetch_eastmoney_f10_dataset(
        self,
        *,
        secucode: str,
        type_name: str | None = None,
        sty: str | None = None,
        page_size: int = 120,
        report_dates: list[str] | None = None,
        extra_filters: list[str] | None = None,
        use_v1: bool = False,
        report_name: str | None = None,
        columns: str = "ALL",
        sort_types: str = "-1,1",
        sort_columns: str = "REPORT_DATE,INTERFACE_TYPE",
        distinct: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Pull one Eastmoney F10 dataset with conservative pacing and multi-endpoint fallback.
        Supports:
        - /securities/api/data/get (type + sty)
        - /securities/api/data/v1/get (reportName + columns)
        """
        endpoints = (
            ["https://datacenter.eastmoney.com/securities/api/data/v1/get"]
            if use_v1
            else ["https://datacenter.eastmoney.com/securities/api/data/get"]
        )
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": f"https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={secucode.replace('.', '')}&color=b#/cwfx",
            "Accept": "application/json, text/plain, */*",
        }
        normalized_dates: list[str] = []
        seen_dates: set[str] = set()
        for raw in report_dates or []:
            parsed = self._parse_date(raw)
            if not parsed:
                continue
            iso = parsed.isoformat()
            if iso in seen_dates:
                continue
            seen_dates.add(iso)
            normalized_dates.append(iso)
        filters = [f'(SECUCODE="{secucode}")']
        if normalized_dates:
            joined = ",".join([f"'{item}'" for item in normalized_dates])
            filters.append(f"(REPORT_DATE in ({joined}))")
        for item in extra_filters or []:
            text = str(item or "").strip()
            if text:
                filters.append(text)
        filter_expr = "".join(filters)

        if use_v1:
            if not report_name:
                return []
            params: dict[str, Any] = {
                "reportName": report_name,
                "columns": columns,
                "quoteColumns": "",
                "filter": filter_expr,
                "sortTypes": sort_types,
                "sortColumns": sort_columns,
                "pageNumber": 1,
                "pageSize": max(20, min(500, int(page_size))),
                "source": "HSF10",
                "client": "PC",
                "v": str(int(time.time() * 1000)),
            }
            if distinct:
                params["distinct"] = distinct
        else:
            if not type_name or not sty:
                return []
            params = {
                "type": type_name,
                "sty": sty,
                "filter": filter_expr,
                "p": 1,
                "ps": max(20, min(500, int(page_size))),
                "sr": -1,
                "st": "REPORT_DATE",
                "source": "HSF10",
                "client": "PC",
                "v": str(int(time.time() * 1000)),
            }

        for url in endpoints:
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=18)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
            except Exception:
                continue

            candidates = []
            if isinstance(payload, dict):
                result = payload.get("result")
                data = payload.get("data")
                if isinstance(result, dict) and isinstance(result.get("data"), list):
                    candidates.append(result.get("data"))
                if isinstance(data, list):
                    candidates.append(data)
                if isinstance(result, list):
                    candidates.append(result)
            for rows in candidates:
                if isinstance(rows, list) and rows:
                    normalized: list[dict[str, Any]] = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        normalized.append(
                            {str(k): self._to_jsonable(v) for k, v in row.items()}
                        )
                    if normalized:
                        return normalized
        return []

    def _extract_report_dates_from_rows(self, rows: list[dict[str, Any]]) -> list[str]:
        found: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("REPORT_DATE", "ENDDATE", "RPTDATE", "NOTICE_DATE", "DECLAREDATE", "UPDATE_DATE"):
                parsed = self._parse_date(row.get(key))
                if parsed:
                    found.add(parsed.isoformat())
                    break
        return sorted(found, reverse=True)

    def _sync_company_financial_eastmoney_f10(
        self,
        db: Session,
        *,
        stock: Stock,
        code: str,
        limit: int,
        request_interval_seconds: float = 0.35,
        dataset_keys: set[str] | None = None,
        max_report_dates: int | None = None,
    ) -> dict[str, Any]:
        """
        Eastmoney F10 direct crawler.
        Uses low-frequency sequential requests to avoid pressure on source servers.
        Dynamically discovers report dates so the crawler does not depend on hard-coded dates.
        """
        raw_code = str(code or "").strip().upper()
        code_token = self._normalize_code_token(raw_code)
        if not code_token:
            raise AkshareServiceError(f"Invalid stock symbol for eastmoney f10 sync: {code}")
        exchange = exchange_from_symbol(code_token)
        if exchange not in {"SH", "SZ", "BJ"}:
            raise AkshareServiceError(f"Unsupported exchange for symbol {code_token}")
        secucode = f"{code_token}.{exchange}"
        wanted = {str(item or "").strip().upper() for item in (dataset_keys or set()) if str(item or "").strip()}
        report_date_cap = max(1, int(max_report_dates or limit or 24))

        dataset_specs: list[dict[str, Any]] = [
            {
                "key": "GBALANCE",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_GBALANCE",
                "detail_sty": ["F10_FINANCE_GBALANCE", "APP_F10_GBALANCE"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "GINCOME",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_GINCOME",
                "detail_sty": ["APP_F10_GINCOME"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "GINCOMEQC",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_GINCOMEQC",
                "detail_sty": ["PC_F10_GINCOMEQC"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "GCASHFLOW",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_GCASHFLOW",
                "detail_sty": ["APP_F10_GCASHFLOW"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "GCASHFLOWQC",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_GCASHFLOWQC",
                "detail_sty": ["PC_F10_GCASHFLOWQC"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "MAINFINADATA",
                "kind": "get",
                "type_name": "RPT_F10_FINANCE_MAINFINADATA",
                "detail_sty": ["APP_F10_MAINFINADATA"],
                "probe_sty": "SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,REPORT_DATE_NAME",
            },
            {
                "key": "GRATIO",
                "kind": "v1",
                "report_name": "RPT_F10_FINANCE_GRATIO",
                "columns": "ALL",
                "sort_types": "-1,1",
                "sort_columns": "REPORT_DATE,INTERFACE_TYPE",
                "distinct": None,
            },
            {
                "key": "INDUSTRY_COMPARED",
                "kind": "v1",
                "report_name": "RPT_F10_INDUSTRY_COMPARED",
                "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,INDUSTRY,REPORT_TYPE,REPORT_TYPE_CODE",
                "sort_types": "-1",
                "sort_columns": "REPORT_DATE",
                "distinct": "REPORT_DATE",
            },
            {
                "key": "ORIG_REPORT",
                "kind": "v1",
                "report_name": "RPT_PCF10_ORIG_REPORT",
                "columns": "YEAR,SECUCODE,SECURITY_CODE,REPORT_DATE,REPORT_TYPE,PUBLISH_SITUATIONS,OPINION_TYPE",
                "sort_types": "-1",
                "sort_columns": "REPORT_DATE",
                "distinct": None,
            },
            {
                "key": "PUBLIC_COMPANYTPYE",
                "kind": "get",
                "type_name": "RPT_F10_PUBLIC_COMPANYTPYE",
                "detail_sty": ["ALL"],
                "probe_sty": None,
            },
        ]
        if wanted:
            dataset_specs = [item for item in dataset_specs if item["key"] in wanted]

        inserted = 0
        records = 0
        event_inserted = 0
        event_records = 0
        dataset_rows: dict[str, int] = {}
        dataset_report_dates: dict[str, list[str]] = {}
        errors: dict[str, str] = {}
        shared_dates: set[str] = set()
        event_by_dataset_rowkey: dict[tuple[str, str], CompanyFinancialEvent] = {}
        event_by_unique: dict[tuple[date, str], CompanyFinancialEvent] = {}
        financial_by_dataset_rowkey: dict[tuple[str, str], CompanyFinancial] = {}
        financial_by_unique: dict[tuple[date, str], CompanyFinancial] = {}

        for spec in dataset_specs:
            key = str(spec["key"])
            rows: list[dict[str, Any]] = []
            selected_dates: list[str] = []
            try:
                if spec["kind"] == "get":
                    probe_sty = spec.get("probe_sty")
                    if probe_sty:
                        probe_rows = self._fetch_eastmoney_f10_dataset(
                            secucode=secucode,
                            type_name=spec["type_name"],
                            sty=str(probe_sty),
                            page_size=200,
                        )
                        selected_dates = self._extract_report_dates_from_rows(probe_rows)[:report_date_cap]
                        for d in selected_dates:
                            shared_dates.add(d)

                    for detail_sty in spec.get("detail_sty") or []:
                        rows = self._fetch_eastmoney_f10_dataset(
                            secucode=secucode,
                            type_name=spec["type_name"],
                            sty=str(detail_sty),
                            page_size=max(40, report_date_cap * 6),
                            report_dates=selected_dates or None,
                        )
                        if rows:
                            break
                        if selected_dates:
                            rows = self._fetch_eastmoney_f10_dataset(
                                secucode=secucode,
                                type_name=spec["type_name"],
                                sty=str(detail_sty),
                                page_size=max(40, report_date_cap * 6),
                            )
                            if rows:
                                break
                else:
                    rows = self._fetch_eastmoney_f10_dataset(
                        secucode=secucode,
                        use_v1=True,
                        report_name=spec["report_name"],
                        columns=spec["columns"],
                        sort_types=spec["sort_types"],
                        sort_columns=spec["sort_columns"],
                        distinct=spec["distinct"],
                        page_size=max(40, report_date_cap * 8),
                        report_dates=sorted(shared_dates, reverse=True)[:report_date_cap] or None,
                    )
                    if not rows:
                        rows = self._fetch_eastmoney_f10_dataset(
                            secucode=secucode,
                            use_v1=True,
                            report_name=spec["report_name"],
                            columns=spec["columns"],
                            sort_types=spec["sort_types"],
                            sort_columns=spec["sort_columns"],
                            distinct=spec["distinct"],
                            page_size=max(40, report_date_cap * 8),
                        )
                    selected_dates = self._extract_report_dates_from_rows(rows)[:report_date_cap]
                    for d in selected_dates:
                        shared_dates.add(d)
            except Exception as exc:
                rows = []
                errors[key] = str(exc)

            if not rows:
                errors.setdefault(key, "empty")

            dataset_report_dates[key] = selected_dates
            rows = sorted(
                rows,
                key=lambda row: (
                    self._parse_date(row.get("REPORT_DATE") or row.get("ENDDATE") or row.get("RPTDATE")) or date.min,
                    str(row.get("REPORT_DATE_NAME") or row.get("REPORT_TYPE") or row.get("ROWKEY") or ""),
                ),
                reverse=True,
            )[: max(1, int(limit))]
            dataset_rows[key] = len(rows)

            for row in rows:
                if not isinstance(row, dict):
                    continue
                report_date = self._parse_date(
                    row.get("REPORT_DATE")
                    or row.get("ENDDATE")
                    or row.get("RPTDATE")
                    or row.get("DECLAREDATE")
                    or row.get("NOTICE_DATE")
                    or row.get("UPDATE_DATE")
                )
                if report_date is None:
                    event_date = (
                        self._parse_date(
                            row.get("REPORT_DATE")
                            or row.get("ENDDATE")
                            or row.get("RPTDATE")
                            or row.get("DECLAREDATE")
                            or row.get("NOTICE_DATE")
                            or row.get("UPDATE_DATE")
                        )
                        or date.today()
                    )
                    event_name = (
                        str(row.get("REPORT_DATE_NAME") or "").strip()
                        or str(row.get("REPORT_TYPE_NAME") or "").strip()
                        or str(row.get("REPORT_NAME") or "").strip()
                        or str(row.get("REPORT_TYPE") or "").strip()
                        or str(row.get("INDUSTRY") or "").strip()
                        or str(row.get("OPINION_TYPE") or "").strip()
                        or key
                    )
                    event_row_key = (
                        str(row.get("ROWKEY") or "").strip()
                    )
                    if not event_row_key:
                        event_oid = str(row.get("OBJECTID") or row.get("ORG_CODE") or "").strip()
                        event_seed = (
                            str(row.get("REPORT_DATE") or "").strip()
                            or str(row.get("ENDDATE") or "").strip()
                            or str(row.get("RPTDATE") or "").strip()
                            or str(row.get("REPORT_TYPE") or "").strip()
                            or str(row.get("REPORT_NAME") or "").strip()
                            or str(row.get("PUBLISH_SITUATIONS") or "").strip()
                        )
                        if event_oid:
                            event_row_key = f"{event_oid}:{event_seed}" if event_seed else event_oid
                    if not event_row_key:
                        event_row_key = hashlib.sha256(
                            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest()[:24]
                    event_dataset = f"eastmoney_f10:{key}"
                    cache_dataset_key = (event_dataset, event_row_key)
                    cache_unique_key = (event_date, event_name)
                    ev = event_by_dataset_rowkey.get(cache_dataset_key) or event_by_unique.get(cache_unique_key)
                    if not ev:
                        ev = (
                            db.query(CompanyFinancialEvent)
                            .filter(
                                CompanyFinancialEvent.stock_id == stock.id,
                                CompanyFinancialEvent.dataset == event_dataset,
                                CompanyFinancialEvent.row_key == event_row_key,
                            )
                            .first()
                        )
                    if not ev:
                        ev = (
                            db.query(CompanyFinancialEvent)
                            .filter(
                                CompanyFinancialEvent.stock_id == stock.id,
                                CompanyFinancialEvent.event_date == event_date,
                                CompanyFinancialEvent.event_name == event_name,
                            )
                            .first()
                        )
                    if not ev:
                        ev = CompanyFinancialEvent(stock_id=stock.id, event_date=event_date, event_name=event_name)
                        event_inserted += 1
                    ev.event_type = str(row.get("REPORT_TYPE") or row.get("PUBLISH_SITUATIONS") or "").strip() or None
                    ev.source = "eastmoney_f10"
                    ev.dataset = event_dataset
                    ev.row_key = event_row_key
                    ev.object_id = self._safe_int(row.get("OBJECTID") or row.get("ORG_CODE"))
                    ev.change_code = self._safe_int(row.get("CHANGE_CODE") or row.get("INTERFACE_TYPE"))
                    ev.declare_date = self._parse_date(row.get("DECLAREDATE") or row.get("NOTICE_DATE"))
                    ev.start_date = self._parse_date(row.get("STARTDATE"))
                    ev.end_date = self._parse_date(row.get("ENDDATE"))
                    ev.raw = row
                    db.add(ev)
                    event_by_dataset_rowkey[cache_dataset_key] = ev
                    event_by_unique[cache_unique_key] = ev
                    event_records += 1
                    continue

                report_seed = (
                    str(row.get("REPORT_DATE_NAME") or "").strip()
                    or str(row.get("REPORT_NAME") or "").strip()
                    or report_date.isoformat()
                )
                report_name = f"{report_seed} [{key}]"
                row_key = str(row.get("ROWKEY") or "").strip()
                if not row_key:
                    oid = str(row.get("OBJECTID") or row.get("ORG_CODE") or "").strip()
                    row_seed = (
                        str(row.get("REPORT_DATE") or "").strip()
                        or str(row.get("ENDDATE") or "").strip()
                        or str(row.get("RPTDATE") or "").strip()
                        or str(row.get("REPORT_DATE_NAME") or "").strip()
                        or str(row.get("REPORT_TYPE") or "").strip()
                        or str(row.get("REPORT_NAME") or "").strip()
                    )
                    if oid:
                        row_key = f"{oid}:{row_seed}" if row_seed else oid
                if not row_key:
                    row_key = hashlib.sha256(
                        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest()[:24]
                dataset_name = f"eastmoney_f10:{key}"
                financial_cache_dataset_key = (dataset_name, row_key)
                financial_cache_unique_key = (report_date, report_name)

                item = (
                    financial_by_dataset_rowkey.get(financial_cache_dataset_key)
                    or financial_by_unique.get(financial_cache_unique_key)
                )
                if not item:
                    item = (
                        db.query(CompanyFinancial)
                        .filter(
                            CompanyFinancial.stock_id == stock.id,
                            CompanyFinancial.dataset == dataset_name,
                            CompanyFinancial.row_key == row_key,
                        )
                        .first()
                    )
                if not item:
                    item = (
                        db.query(CompanyFinancial)
                        .filter(
                            CompanyFinancial.stock_id == stock.id,
                            CompanyFinancial.report_date == report_date,
                            CompanyFinancial.report_name == report_name,
                        )
                        .first()
                    )

                if not item:
                    item = CompanyFinancial(stock_id=stock.id, report_date=report_date, report_name=report_name)
                    inserted += 1

                core = extract_core_metrics(row)
                item.report_type = str(row.get("REPORT_TYPE") or row.get("REPORT_DATE_NAME") or "").strip() or None
                item.source = "eastmoney_f10"
                item.dataset = dataset_name
                item.row_key = row_key
                item.object_id = self._safe_int(row.get("OBJECTID") or row.get("ORG_CODE"))
                item.change_code = self._safe_int(row.get("CHANGE_CODE") or row.get("INTERFACE_TYPE"))
                item.declare_date = self._parse_date(row.get("DECLAREDATE") or row.get("NOTICE_DATE") or row.get("UPDATE_DATE"))
                item.start_date = self._parse_date(row.get("STARTDATE"))
                item.end_date = self._parse_date(row.get("ENDDATE"))
                item.eps = core.get("eps") or self._safe_float(row.get("EPS"))
                item.revenue = core.get("revenue") or self._safe_float(row.get("TOTAL_OPERATE_INCOME"))
                item.net_profit = core.get("net_profit") or self._safe_float(row.get("NETPROFIT"))
                item.gross_margin = core.get("gross_margin")
                item.roe = core.get("roe") or self._safe_float(row.get("ROEJQ"))
                if core.get("total_assets") and core.get("total_liabilities"):
                    total_assets = float(core["total_assets"])
                    total_liabilities = float(core["total_liabilities"])
                    if total_assets != 0:
                        item.asset_liability_ratio = total_liabilities / total_assets
                else:
                    item.asset_liability_ratio = self._safe_float(row.get("ZCFZL"))
                item.operating_cashflow = core.get("operating_cashflow") or self._safe_float(row.get("NETCASH_OPERATE"))
                item.yoy_revenue = self._safe_float(row.get("TOTALOPERATEREVETZ") or row.get("F006N") or row.get("OPERATE_INCOME_YOY"))
                item.yoy_net_profit = self._safe_float(row.get("PARENTNETPROFITTZ") or row.get("F012N") or row.get("NETPROFIT_YOY"))
                item.raw = row
                db.add(item)
                financial_by_dataset_rowkey[financial_cache_dataset_key] = item
                financial_by_unique[financial_cache_unique_key] = item
                records += 1

            time.sleep(max(0.05, request_interval_seconds))

        if records > 0 or event_records > 0:
            title_date = date.today()
            title = f"{code_token} 东方财富F10财务快照 {title_date.isoformat()}"
            if not self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code_token,
                doc_type="financial_snapshot",
                title=title,
                published_at=datetime.combine(title_date, datetime.min.time()),
            ):
                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code_token,
                        doc_type="financial_snapshot",
                        title=title,
                        content=(
                            f"来源: 东方财富F10; 成功数据集={sum(1 for _, cnt in dataset_rows.items() if cnt > 0)}; "
                            f"写入财务记录={records}; 事件记录={event_records}; 数据集明细={dataset_rows}"
                        ),
                        source="eastmoney_f10",
                        published_at=datetime.combine(title_date, datetime.min.time()),
                        doc_metadata={"datasets": dataset_rows, "dataset_report_dates": dataset_report_dates, "errors": errors},
                    )
                )
            db.commit()

        return {
            "symbol": code_token,
            "source": "eastmoney_f10",
            "inserted": inserted,
            "records": records,
            "event_inserted": event_inserted,
            "event_records": event_records,
            "datasets": dataset_rows,
            "dataset_report_dates": dataset_report_dates,
            "errors": errors,
        }

    def sync_company_financial(self, db: Session, symbol: str, limit: int = 24) -> dict[str, Any]:
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)

        suffix = exchange_from_symbol(code)
        if suffix not in {"SH", "SZ"}:
            raise AkshareServiceError(f"Unsupported exchange for symbol {code}")
        logger.info("sync_company_financial start | symbol=%s", code)
        eastmoney_f10_result = self._sync_company_financial_eastmoney_f10(
            db,
            stock=stock,
            code=code,
            limit=limit,
            request_interval_seconds=0.35,
        )
        eastmoney_has_records = int(eastmoney_f10_result.get("records") or 0) > 0
        cninfo_result = self._sync_company_financial_cninfo(db, stock=stock, code=code, limit=limit)
        if cninfo_result.get("enabled") and int(cninfo_result.get("records") or 0) > 0:
            if eastmoney_has_records:
                cninfo_result["eastmoney_f10"] = eastmoney_f10_result
            return cninfo_result

        # Learn from test script flow:
        # if CNInfo auth/session failed (token null etc.), force one bootstrap refresh
        # and retry CNInfo sync once in-process.
        if cninfo_result.get("enabled") and self._cninfo_errors_look_like_auth_fail(cninfo_result.get("errors")):
            logger.warning(
                "sync_company_financial auth-like failure detected, forcing cninfo bootstrap retry | symbol=%s errors=%s",
                code,
                cninfo_result.get("errors"),
            )
            refresh_ok = cninfo_client.refresh_headers()
            if not refresh_ok:
                # fallback: maybe another process updated cache
                refresh_ok = cninfo_client.ensure_headers()
            logger.info(
                "sync_company_financial bootstrap retry result | symbol=%s refresh_ok=%s",
                code,
                refresh_ok,
            )
            if refresh_ok:
                retry_result = self._sync_company_financial_cninfo(db, stock=stock, code=code, limit=limit)
                if retry_result.get("enabled") and int(retry_result.get("records") or 0) > 0:
                    if eastmoney_has_records:
                        retry_result["eastmoney_f10"] = eastmoney_f10_result
                    return retry_result
                cninfo_result = retry_result

        existing_financial_count = (
            db.query(func.count(CompanyFinancial.id))
            .filter(CompanyFinancial.stock_id == stock.id)
            .scalar()
            or 0
        )
        existing_event_count = (
            db.query(func.count(CompanyFinancialEvent.id))
            .filter(CompanyFinancialEvent.stock_id == stock.id)
            .scalar()
            or 0
        )
        has_existing_financial_context = int(existing_financial_count) > 0 or int(existing_event_count) > 0

        if settings.cninfo_financial_strict:
            if eastmoney_has_records and int(cninfo_result.get("records") or 0) <= 0:
                return {
                    **eastmoney_f10_result,
                    "strict_mode": True,
                    "cninfo": {
                        "enabled": bool(cninfo_result.get("enabled")),
                        "records": int(cninfo_result.get("records") or 0),
                        "datasets": cninfo_result.get("datasets"),
                        "errors": cninfo_result.get("errors"),
                    },
                }
            if not cninfo_result.get("enabled"):
                raise AkshareServiceError(
                    f"CNInfo strict mode enabled but client is disabled for {code}. "
                    "Please set cninfo_enabled/cninfo_accept_enckey/cninfo_cookie."
                )
            if has_existing_financial_context:
                logger.warning(
                    "cninfo strict empty, reuse existing financial context | symbol=%s financial_rows=%s event_rows=%s "
                    "datasets=%s errors=%s",
                    code,
                    existing_financial_count,
                    existing_event_count,
                    cninfo_result.get("datasets"),
                    cninfo_result.get("errors"),
                )
                return {
                    "symbol": code,
                    "enabled": True,
                    "source": "cninfo_cached",
                    "inserted": 0,
                    "deleted": 0,
                    "records": int(existing_financial_count),
                    "event_records": int(existing_event_count),
                    "reused_existing": True,
                    "datasets": cninfo_result.get("datasets"),
                    "errors": cninfo_result.get("errors"),
                }
            first_error = (cninfo_result.get("errors") or [None])[0]
            raise AkshareServiceError(
                f"CNInfo strict mode enabled and no financial records returned for {code}. "
                f"datasets={cninfo_result.get('datasets')} errors={cninfo_result.get('errors')}"
                + (f" first_error={first_error}" if first_error else "")
            )

        if eastmoney_has_records:
            return {
                **eastmoney_f10_result,
                "cninfo": {
                    "enabled": bool(cninfo_result.get("enabled")),
                    "records": int(cninfo_result.get("records") or 0),
                    "datasets": cninfo_result.get("datasets"),
                    "errors": cninfo_result.get("errors"),
                },
            }

        if not cninfo_result.get("enabled"):
            logger.info("sync_company_financial fallback | symbol=%s reason=cninfo_disabled", code)
        else:
            logger.warning(
                "sync_company_financial cninfo empty, fallback to akshare | symbol=%s datasets=%s errors=%s",
                code,
                cninfo_result.get("datasets"),
                cninfo_result.get("errors"),
            )

        ak = self._ak()
        df = None
        for em_symbol in (f"{code}.{suffix}", f"{suffix}{code}", code):
            try:
                candidate_df = ak.stock_financial_analysis_indicator_em(symbol=em_symbol, indicator="按报告期")
            except Exception as exc:
                logger.warning(
                    "financial indicator fetch failed | symbol=%s em_symbol=%s error=%s",
                    code,
                    em_symbol,
                    exc,
                )
                continue
            if candidate_df is not None and not candidate_df.empty:
                df = candidate_df
                logger.info("financial indicator fetch hit | symbol=%s em_symbol=%s", code, em_symbol)
                break

        if df is None or df.empty:
            # Fallback to THS key metrics when Eastmoney endpoint returns empty.
            logger.warning("financial primary endpoint empty, fallback to ths | symbol=%s", code)
            ths_df = None
            try:
                ths_df = ak.stock_financial_abstract_new_ths(symbol=code, indicator="按报告期")
            except Exception as exc:
                logger.warning("financial ths fetch failed | symbol=%s error=%s", code, exc)
            if ths_df is None or ths_df.empty:
                logger.warning("financial ths endpoint empty, fallback to yjbb | symbol=%s", code)
                for report_period in [
                    f"{date.today().year}1231",
                    f"{date.today().year}0930",
                    f"{date.today().year}0630",
                    f"{date.today().year}0331",
                    f"{date.today().year - 1}1231",
                    f"{date.today().year - 1}0930",
                    f"{date.today().year - 1}0630",
                    f"{date.today().year - 1}0331",
                ]:
                    try:
                        yjbb_df = ak.stock_yjbb_em(date=report_period)
                    except Exception as exc:
                        logger.warning(
                            "financial yjbb fetch failed | symbol=%s report_period=%s error=%s",
                            code,
                            report_period,
                            exc,
                        )
                        continue
                    if yjbb_df is None or yjbb_df.empty:
                        continue
                    rows = yjbb_df[yjbb_df["股票代码"].astype(str).str.zfill(6) == code]
                    if rows.empty:
                        continue
                    row = rows.iloc[0]
                    report_date = self._parse_date(report_period)
                    if report_date is None:
                        continue
                    report_name = str(row.get("最新公告日期") or report_period)
                    item = (
                        db.query(CompanyFinancial)
                        .filter(
                            CompanyFinancial.stock_id == stock.id,
                            CompanyFinancial.report_date == report_date,
                            CompanyFinancial.report_name == report_name,
                        )
                        .first()
                    )
                    inserted = 0
                    if not item:
                        item = CompanyFinancial(stock_id=stock.id, report_date=report_date, report_name=report_name)
                        inserted = 1
                    item.report_type = "yjbb"
                    item.source = "akshare"
                    item.dataset = "stock_yjbb_em"
                    item.row_key = f"yjbb:{report_period}:{code}"
                    item.eps = self._safe_float(row.get("每股收益"))
                    item.revenue = self._safe_float(row.get("营业总收入-营业总收入"))
                    item.net_profit = self._safe_float(row.get("净利润-净利润"))
                    item.gross_margin = self._safe_float(row.get("销售毛利率"))
                    item.roe = self._safe_float(row.get("净资产收益率"))
                    item.operating_cashflow = self._safe_float(row.get("每股经营现金流量"))
                    item.yoy_revenue = self._safe_float(row.get("营业总收入-同比增长"))
                    item.yoy_net_profit = self._safe_float(row.get("净利润-同比增长"))
                    item.raw = {str(k): self._to_jsonable(v) for k, v in row.to_dict().items()}
                    db.add(item)
                    db.commit()
                    logger.info(
                        "sync_company_financial done | symbol=%s inserted=%s records=1 source=yjbb report_period=%s",
                        code,
                        inserted,
                        report_period,
                    )
                    return {"symbol": code, "inserted": inserted, "records": 1, "source": "yjbb"}
                abstract_result = self._sync_company_financial_from_abstract(
                    db,
                    stock=stock,
                    code=code,
                    limit=limit,
                )
                if abstract_result:
                    return abstract_result
                return {"symbol": code, "inserted": 0, "records": 0, "source": "none"}

            metric_map = {
                "每股收益": "eps",
                "营业总收入": "revenue",
                "归母净利润": "net_profit",
                "销售毛利率": "gross_margin",
                "净资产收益率": "roe",
                "资产负债率": "asset_liability_ratio",
                "每股经营现金流": "operating_cashflow",
            }
            grouped: dict[tuple[date, str], dict[str, Any]] = {}
            for _, row in ths_df.iterrows():
                metric_name = str(row.get("metric_name", ""))
                report_date = self._parse_date(row.get("report_date"))
                report_name = str(row.get("report_name", "")) or "unknown"
                if not report_date:
                    continue
                key = (report_date, report_name)
                if key not in grouped:
                    grouped[key] = {
                        "report_date": report_date,
                        "report_name": report_name,
                        "report_type": str(row.get("quarter_name", "")) or None,
                        "raw": {},
                    }
                grouped[key]["raw"][metric_name] = row.get("value")
                for source_name, target_name in metric_map.items():
                    if source_name in metric_name and grouped[key].get(target_name) is None:
                        grouped[key][target_name] = self._safe_float(row.get("value"))

            inserted = 0
            ordered = sorted(grouped.values(), key=lambda x: x["report_date"], reverse=True)[:limit]
            for item_dict in ordered:
                report_date = item_dict["report_date"]
                report_name = item_dict["report_name"]
                item = (
                    db.query(CompanyFinancial)
                    .filter(
                        CompanyFinancial.stock_id == stock.id,
                        CompanyFinancial.report_date == report_date,
                        CompanyFinancial.report_name == report_name,
                    )
                    .first()
                )
                if not item:
                    item = CompanyFinancial(stock_id=stock.id, report_date=report_date, report_name=report_name)
                    inserted += 1
                item.report_type = item_dict.get("report_type")
                item.source = "akshare"
                item.dataset = "stock_financial_abstract_new_ths"
                item.row_key = f"ths:{report_name}:{report_date.isoformat()}"
                item.eps = item_dict.get("eps")
                item.revenue = item_dict.get("revenue")
                item.net_profit = item_dict.get("net_profit")
                item.gross_margin = item_dict.get("gross_margin")
                item.roe = item_dict.get("roe")
                item.asset_liability_ratio = item_dict.get("asset_liability_ratio")
                item.operating_cashflow = item_dict.get("operating_cashflow")
                item.raw = item_dict.get("raw", {})
                db.add(item)
            db.commit()
            logger.info(
                "sync_company_financial done | symbol=%s inserted=%s records=%s source=ths",
                code,
                inserted,
                len(ordered),
            )
            return {"symbol": code, "inserted": inserted, "records": int(len(ordered)), "source": "ths"}

        df = df.sort_values(by="REPORT_DATE", ascending=False).head(limit)

        inserted = 0
        for _, row in df.iterrows():
            report_date = self._parse_date(row.get("REPORT_DATE"))
            report_name = str(row.get("REPORT_DATE_NAME", "")) or "unknown"
            if not report_date:
                continue

            item = (
                db.query(CompanyFinancial)
                .filter(
                    CompanyFinancial.stock_id == stock.id,
                    CompanyFinancial.report_date == report_date,
                    CompanyFinancial.report_name == report_name,
                )
                .first()
            )
            if not item:
                item = CompanyFinancial(stock_id=stock.id, report_date=report_date, report_name=report_name)
                inserted += 1

            item.report_type = str(row.get("REPORT_TYPE", "")) or None
            item.source = "akshare"
            item.dataset = "stock_financial_analysis_indicator_em"
            item.row_key = f"em:{report_name}:{report_date.isoformat()}"
            item.eps = self._safe_float(row.get("EPSJB"))
            item.revenue = self._safe_float(row.get("TOTALOPERATEREVE"))
            item.net_profit = self._safe_float(row.get("PARENTNETPROFIT"))
            item.gross_margin = self._safe_float(row.get("XSMLL"))
            item.roe = self._safe_float(row.get("ROEJQ"))
            item.asset_liability_ratio = self._safe_float(row.get("ZCFZL"))
            item.operating_cashflow = self._safe_float(row.get("MGJYXJJE"))
            item.yoy_revenue = self._safe_float(row.get("TOTALOPERATEREVETZ"))
            item.yoy_net_profit = self._safe_float(row.get("PARENTNETPROFITTZ"))
            item.raw = {str(k): self._to_jsonable(v) for k, v in row.to_dict().items()}
            db.add(item)

        latest = df.iloc[0]
        latest_date = self._parse_date(latest.get("REPORT_DATE"))
        if latest_date:
            title = f"{code} 财务指标快照 {latest_date.isoformat()}"
            if not self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="financial_snapshot",
                title=title,
                published_at=datetime.combine(latest_date, datetime.min.time()),
            ):
                content = (
                    f"EPS={self._safe_float(latest.get('EPSJB'))}, "
                    f"营收={self._safe_float(latest.get('TOTALOPERATEREVE'))}, "
                    f"归母净利润={self._safe_float(latest.get('PARENTNETPROFIT'))}, "
                    f"毛利率={self._safe_float(latest.get('XSMLL'))}, "
                    f"资产负债率={self._safe_float(latest.get('ZCFZL'))}"
                )
                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code,
                        doc_type="financial_snapshot",
                        title=title,
                        content=content,
                        source="akshare",
                        published_at=datetime.combine(latest_date, datetime.min.time()),
                        doc_metadata={"report_name": str(latest.get("REPORT_DATE_NAME", ""))},
                    )
                )

        db.commit()
        logger.info(
            "sync_company_financial done | symbol=%s inserted=%s records=%s source=eastmoney",
            code,
            inserted,
            len(df),
        )
        return {"symbol": code, "inserted": inserted, "records": int(len(df)), "source": "eastmoney"}

    def sync_global_news(self, db: Session, limit: int = 200) -> dict[str, Any]:
        ak = self._ak()
        now_local = datetime.now()
        seven_days_ago = now_local - timedelta(days=7)
        source_limit = max(50, min(500, int(limit)))
        feed_sources = (
            "eastmoney-breakfast",
            "eastmoney-global",
            "sina-global",
            "futu-global",
            "ths-global",
            "cls-telegraph",
            "caixin-main",
        )

        normalized_items: list[dict[str, Any]] = []
        source_rows: dict[str, int] = {}
        source_errors: dict[str, str] = {}

        def _append_item(
            *,
            source: str,
            title: str,
            content: str,
            published_at: datetime | None,
            link: str | None = None,
            extra: dict[str, Any] | None = None,
        ) -> None:
            title = (title or "").strip()
            content = (content or "").strip()
            if not title:
                return
            if published_at is None:
                return
            if published_at < seven_days_ago:
                return
            normalized_items.append(
                {
                    "source": source,
                    "title": title[:255],
                    "content": (content or title),
                    "published_at": published_at,
                    "link": link,
                    "metadata": extra or {},
                }
            )
            source_rows[source] = source_rows.get(source, 0) + 1

        # 东方财富-财经早餐
        try:
            df = self._call_with_retry(
                ak.stock_info_cjzc_em,
                retries=2,
                retry_name="stock_info_cjzc_em:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    published_at = self._parse_datetime(row.get("发布时间"))
                    _append_item(
                        source="eastmoney-breakfast",
                        title=str(row.get("标题", "")),
                        content=str(row.get("摘要", "")),
                        published_at=published_at,
                        link=str(row.get("链接", "")).strip() or None,
                    )
        except Exception as exc:
            source_errors["eastmoney-breakfast"] = str(exc)

        # 东方财富-全球快讯
        try:
            df = self._call_with_retry(
                ak.stock_info_global_em,
                retries=2,
                retry_name="stock_info_global_em:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    published_at = self._parse_datetime(row.get("发布时间"))
                    _append_item(
                        source="eastmoney-global",
                        title=str(row.get("标题", "")),
                        content=str(row.get("摘要", "")),
                        published_at=published_at,
                        link=str(row.get("链接", "")).strip() or None,
                    )
        except Exception as exc:
            source_errors["eastmoney-global"] = str(exc)

        # 新浪财经-全球快讯
        try:
            df = self._call_with_retry(
                ak.stock_info_global_sina,
                retries=2,
                retry_name="stock_info_global_sina:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    content = str(row.get("内容", "")).strip()
                    title = content[:80]
                    published_at = self._parse_datetime(row.get("时间"), reference_date=now_local.date())
                    _append_item(
                        source="sina-global",
                        title=title,
                        content=content,
                        published_at=published_at,
                    )
        except Exception as exc:
            source_errors["sina-global"] = str(exc)

        # 富途牛牛-快讯
        try:
            df = self._call_with_retry(
                ak.stock_info_global_futu,
                retries=2,
                retry_name="stock_info_global_futu:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    title = str(row.get("标题", "")).strip()
                    content = str(row.get("内容", "")).strip()
                    published_at = self._parse_datetime(row.get("发布时间"))
                    _append_item(
                        source="futu-global",
                        title=title or content[:80],
                        content=content or title,
                        published_at=published_at,
                        link=str(row.get("链接", "")).strip() or None,
                    )
        except Exception as exc:
            source_errors["futu-global"] = str(exc)

        # 同花顺-全球财经直播
        try:
            df = self._call_with_retry(
                ak.stock_info_global_ths,
                retries=2,
                retry_name="stock_info_global_ths:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    title = str(row.get("标题", "")).strip()
                    content = str(row.get("内容", "")).strip()
                    published_at = self._parse_datetime(row.get("发布时间"))
                    _append_item(
                        source="ths-global",
                        title=title or content[:80],
                        content=content or title,
                        published_at=published_at,
                        link=str(row.get("链接", "")).strip() or None,
                    )
        except Exception as exc:
            source_errors["ths-global"] = str(exc)

        # 财联社-电报
        try:
            df = self._call_with_retry(
                ak.stock_info_global_cls,
                symbol="全部",
                retries=2,
                retry_name="stock_info_global_cls:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    publish_date = self._parse_date(row.get("发布日期"))
                    publish_time = str(row.get("发布时间", "")).strip()
                    if publish_date and publish_time:
                        published_at = self._parse_datetime(f"{publish_date.isoformat()} {publish_time}")
                    elif publish_date:
                        published_at = datetime.combine(publish_date, datetime.min.time())
                    else:
                        published_at = self._parse_datetime(publish_time, reference_date=now_local.date())
                    _append_item(
                        source="cls-telegraph",
                        title=str(row.get("标题", "")),
                        content=str(row.get("内容", "")),
                        published_at=published_at,
                        extra={"publish_date": str(row.get("发布日期", "")).strip()},
                    )
        except Exception as exc:
            source_errors["cls-telegraph"] = str(exc)

        # 财新-财经内容精选
        try:
            df = self._call_with_retry(
                ak.stock_news_main_cx,
                retries=2,
                retry_name="stock_news_main_cx:macro_news",
            )
            if df is not None and not df.empty:
                for _, row in df.head(source_limit).iterrows():
                    summary = str(row.get("summary", "")).strip()
                    tag = str(row.get("tag", "")).strip()
                    url = str(row.get("url", "")).strip()
                    published_at = self._extract_datetime_from_text(url) or self._extract_datetime_from_text(summary)
                    title = f"{tag} {summary[:72]}".strip()
                    _append_item(
                        source="caixin-main",
                        title=title,
                        content=summary or title,
                        published_at=published_at,
                        link=url or None,
                        extra={"tag": tag},
                    )
        except Exception as exc:
            source_errors["caixin-main"] = str(exc)

        cleaned_noise_docs = (
            db.query(Document)
            .filter(
                Document.doc_type == "news",
                Document.stock_symbol.is_(None),
                Document.source.in_(list(feed_sources)),
            )
            .delete(synchronize_session=False)
        )
        cleaned_old_macro = (
            db.query(MacroNews)
            .filter(
                MacroNews.source.in_(list(feed_sources)),
                or_(MacroNews.published_at.is_(None), MacroNews.published_at < seven_days_ago),
            )
            .delete(synchronize_session=False)
        )

        inserted = 0
        seen_keys: set[tuple[str, str, str]] = set()
        normalized_items.sort(key=lambda x: x["published_at"], reverse=True)
        for item in normalized_items:
            published_at = item["published_at"]
            dedupe_key = (
                item["source"],
                item["title"],
                published_at.isoformat(),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            if self._macro_exists(db, title=item["title"], published_at=published_at):
                continue
            db.add(
                MacroNews(
                    title=item["title"],
                    content=item["content"],
                    source=item["source"],
                    published_at=published_at,
                    news_metadata={
                        "link": item.get("link"),
                        **(item.get("metadata") or {}),
                    },
                )
            )
            inserted += 1

        db.commit()
        logger.info(
            "sync_global_news done | inserted=%s source_rows=%s cleaned_noise_docs=%s cleaned_old_macro=%s errors=%s",
            inserted,
            source_rows,
            cleaned_noise_docs,
            cleaned_old_macro,
            source_errors,
        )
        return {
            "inserted": inserted,
            "source_rows": source_rows,
            "cleaned_noise_docs": int(cleaned_noise_docs or 0),
            "cleaned_old_macro": int(cleaned_old_macro or 0),
            "errors": source_errors,
            "window_days": 7,
        }

    def sync_company_news_from_global(self, db: Session, symbol: str, limit: int = 200) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)
        df = self._call_with_retry(
            ak.stock_news_em,
            symbol=code,
            retries=3,
            retry_name=f"stock_news_em:company_news:{code}",
        )
        if df is None or df.empty:
            return {"symbol": code, "inserted": 0}

        seven_days_ago = datetime.now() - timedelta(days=7)
        purged_old = (
            db.query(Document)
            .filter(
                Document.stock_symbol == code,
                Document.doc_type == "news",
                or_(Document.published_at.is_(None), Document.published_at < seven_days_ago),
            )
            .delete(synchronize_session=False)
        )

        inserted = 0
        for _, row in df.head(max(1, limit)).iterrows():
            title = str(row.get("新闻标题", "")).strip()
            if not title:
                continue
            content = str(row.get("新闻内容", "")).strip()
            published_at = self._parse_datetime(row.get("发布时间"))
            if published_at is None:
                published_at = self._extract_datetime_from_text(row.get("新闻链接"))
            if published_at is None or published_at < seven_days_ago:
                continue

            if self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="news",
                title=title,
                published_at=published_at,
            ):
                continue

            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="news",
                    title=title,
                    content=content or title,
                    source=str(row.get("文章来源", "")).strip() or "eastmoney-stock-news",
                    published_at=published_at,
                    doc_metadata={
                        "link": row.get("新闻链接"),
                        "keyword": row.get("关键词"),
                    },
                )
            )
            inserted += 1

        db.commit()
        logger.info(
            "sync_company_news done | symbol=%s inserted=%s purged_old=%s records=%s",
            code,
            inserted,
            purged_old,
            len(df),
        )
        return {"symbol": code, "inserted": inserted, "purged_old": int(purged_old or 0), "records": int(len(df))}

    def sync_company_announcements(
        self,
        db: Session,
        symbol: str,
        start_date: date,
        end_date: date,
        category: str = "",
    ) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)
        exchange = exchange_from_symbol(code)
        market_name = "沪市" if exchange == "SH" else "深市"
        try:
            df = self._call_with_retry(
                ak.stock_zh_a_disclosure_report_cninfo,
                symbol=code,
                market=market_name,
                keyword="",
                category=category,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                retries=2,
                retry_name=f"stock_zh_a_disclosure_report_cninfo:{code}",
            )
        except Exception:
            return {"symbol": code, "inserted": 0}

        if df is None or df.empty:
            return {"symbol": code, "inserted": 0}

        inserted = 0
        for _, row in df.iterrows():
            title = str(row.get("公告标题", "")).strip()
            if not title:
                continue
            published = self._parse_date(row.get("公告时间"))
            published_at = datetime.combine(published, datetime.min.time()) if published else None
            if self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="announcement",
                title=title,
                published_at=published_at,
            ):
                continue
            db.add(
                Document(
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="announcement",
                    title=title,
                    content=f"公告标题: {title}",
                    source="cninfo",
                    published_at=published_at,
                    doc_metadata={"link": row.get("公告链接")},
                )
            )
            inserted += 1

        db.commit()
        return {"symbol": code, "inserted": inserted}

    def sync_company_advanced_signals(self, db: Session, symbol: str, limit: int = 30) -> dict[str, Any]:
        ak = self._ak()
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )
        stock = self._ensure_stock(db, code)

        inserted = {"research_report": 0, "market_sentiment": 0}

        try:
            rr_df = ak.stock_research_report_em(symbol=code)
        except Exception as exc:
            message = str(exc)
            if "infoCode" in message:
                logger.info(
                    "sync_company_advanced_signals research report unavailable | symbol=%s detail=%s",
                    code,
                    message,
                )
            else:
                logger.warning("sync_company_advanced_signals research report failed | symbol=%s error=%s", code, exc)
            rr_df = None

        if rr_df is not None and not rr_df.empty:
            for _, row in rr_df.head(limit).iterrows():
                title = str(row.get("报告名称", "")).strip()
                if not title:
                    continue
                report_date = self._parse_date(row.get("日期"))
                published_at = datetime.combine(report_date, datetime.min.time()) if report_date else None
                if self._doc_exists(
                    db,
                    stock_id=stock.id,
                    stock_symbol=code,
                    doc_type="research_report",
                    title=title,
                    published_at=published_at,
                ):
                    continue

                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code,
                        doc_type="research_report",
                        title=title,
                        content=(
                            f"机构={row.get('机构')}, 评级={row.get('东财评级')}, "
                            f"2024E EPS={row.get('2024-盈利预测-收益')}, "
                            f"2025E EPS={row.get('2025-盈利预测-收益')}, "
                            f"2026E EPS={row.get('2026-盈利预测-收益')}"
                        ),
                        source="eastmoney-research",
                        published_at=published_at,
                        doc_metadata={str(k): self._to_jsonable(v) for k, v in row.to_dict().items()},
                    )
                )
                inserted["research_report"] += 1

        try:
            score_df = ak.stock_comment_detail_zhpj_lspf_em(symbol=code)
            focus_df = ak.stock_comment_detail_scrd_focus_em(symbol=code)
            desire_df = ak.stock_comment_detail_scrd_desire_em(symbol=code)
            inst_df = ak.stock_comment_detail_zlkp_jgcyd_em(symbol=code)
        except Exception as exc:
            logger.warning("sync_company_advanced_signals comment detail failed | symbol=%s error=%s", code, exc)
            score_df = None
            focus_df = None
            desire_df = None
            inst_df = None

        latest_score = None
        latest_focus = None
        latest_desire = None
        latest_inst = None
        latest_date: date | None = None
        if score_df is not None and not score_df.empty:
            srow = score_df.sort_values(by=score_df.columns[0], ascending=False).iloc[0]
            latest_score = self._safe_float(srow.get("评分"))
            latest_date = self._parse_date(srow.get("交易日") or srow.get("日期"))
        if focus_df is not None and not focus_df.empty:
            frow = focus_df.sort_values(by=focus_df.columns[0], ascending=False).iloc[0]
            latest_focus = self._safe_float(frow.get("用户关注指数"))
            latest_date = latest_date or self._parse_date(frow.get("交易日"))
        if desire_df is not None and not desire_df.empty:
            drow = desire_df.sort_values(by=desire_df.columns[0], ascending=False).iloc[0]
            latest_desire = self._safe_float(drow.get("参与意愿"))
            latest_date = latest_date or self._parse_date(drow.get("交易日期"))
        if inst_df is not None and not inst_df.empty:
            irow = inst_df.sort_values(by=inst_df.columns[0], ascending=False).iloc[0]
            latest_inst = self._safe_float(irow.get("机构参与度"))
            latest_date = latest_date or self._parse_date(irow.get("交易日"))

        if any(v is not None for v in (latest_score, latest_focus, latest_desire, latest_inst)):
            published_at = datetime.combine(latest_date, datetime.min.time()) if latest_date else None
            title = f"{code} 市场热度与机构参与快照"
            if not self._doc_exists(
                db,
                stock_id=stock.id,
                stock_symbol=code,
                doc_type="market_sentiment",
                title=title,
                published_at=published_at,
            ):
                db.add(
                    Document(
                        stock_id=stock.id,
                        stock_symbol=code,
                        doc_type="market_sentiment",
                        title=title,
                        content=(
                            f"综合评分={latest_score}, 用户关注指数={latest_focus}, "
                            f"市场参与意愿={latest_desire}, 机构参与度={latest_inst}"
                        ),
                        source="eastmoney-comment",
                        published_at=published_at,
                        doc_metadata={
                            "latest_score": latest_score,
                            "latest_focus": latest_focus,
                            "latest_desire": latest_desire,
                            "latest_inst": latest_inst,
                        },
                    )
                )
                inserted["market_sentiment"] += 1

        db.commit()
        return {"symbol": code, "inserted": inserted}

    def _cninfo_latest_report_date(self, code: str) -> date | None:
        if not cninfo_client.enabled:
            return None
        normalized_code = self._normalize_symbol(code)
        now_utc = datetime.utcnow()
        # Reuse header max age as a conservative probe TTL floor to reduce CNInfo pressure.
        cache_ttl_seconds = max(300, int(getattr(settings, "cninfo_header_max_age_seconds", 300) or 300))
        cached = self._cninfo_latest_report_cache.get(normalized_code)
        if cached is not None:
            fetched_at, cached_date = cached
            if (now_utc - fetched_at).total_seconds() < cache_ttl_seconds:
                return cached_date
        try:
            response = cninfo_client.request(
                "/api/stock/p_stock2399",
                params={"scode": normalized_code, "format": "json"},
            )
        except Exception as exc:
            logger.warning("cninfo latest report fetch failed | symbol=%s error=%s", normalized_code, exc)
            self._cninfo_latest_report_cache[normalized_code] = (now_utc, None)
            return None
        latest: date | None = None
        for row in response.records:
            rpt_date = self._parse_date(row.get("RPTDATE"))
            if rpt_date is None:
                continue
            latest = rpt_date if latest is None else max(latest, rpt_date)
        self._cninfo_latest_report_cache[normalized_code] = (now_utc, latest)
        return latest

    def sync_symbol_hot_data(
        self,
        db: Session,
        *,
        symbol: str,
        as_of_date: date | None = None,
        history_days: int = 120,
        force: bool = False,
    ) -> dict[str, Any]:
        code = self._normalize_symbol(symbol)
        if not self._is_sync_symbol(code):
            raise AkshareServiceError(
                f"Unsupported symbol {code}. Current sync scope supports Shanghai/Shenzhen A shares."
            )

        stock = self._ensure_stock(db, code)
        target_date = as_of_date or date.today()
        now_local = datetime.now()

        latest_quote_time = (
            db.query(func.max(StockQuote.quote_time))
            .filter(StockQuote.stock_id == stock.id)
            .scalar()
        )
        latest_market_date = (
            db.query(func.max(MarketData.date))
            .filter(MarketData.stock_id == stock.id)
            .scalar()
        )
        latest_company_news = (
            db.query(func.max(Document.published_at))
            .filter(
                Document.stock_symbol == stock.symbol,
                Document.doc_type.in_(["news", "announcement"]),
            )
            .scalar()
        )
        latest_macro_news = db.query(func.max(MacroNews.published_at)).scalar()
        latest_advanced_doc = (
            db.query(func.max(Document.published_at))
            .filter(
                Document.stock_id == stock.id,
                Document.doc_type.in_(["research_report", "market_sentiment"]),
            )
            .scalar()
        )
        latest_financial_report_date = (
            db.query(func.max(CompanyFinancial.report_date))
            .filter(CompanyFinancial.stock_id == stock.id)
            .scalar()
        )
        latest_fundamental_snapshot = (
            db.query(func.max(CompanyFundamental.snapshot_date))
            .filter(CompanyFundamental.stock_id == stock.id)
            .scalar()
        )
        latest_market_snapshot = (
            db.query(func.max(AkDataSnapshot.snapshot_date))
            .filter(
                AkDataSnapshot.stock_symbol.is_(None),
                AkDataSnapshot.snapshot_key.in_(
                    [
                        "market_sse_summary",
                        "market_szse_summary",
                        "market_sse_deal_daily",
                        "market_activity_legu",
                        "market_hot_rank_em",
                        "market_hot_up_em",
                    ]
                ),
            )
            .scalar()
        )
        latest_peer_snapshot = (
            db.query(func.max(AkDataSnapshot.snapshot_date))
            .filter(
                AkDataSnapshot.stock_symbol == stock.symbol,
                AkDataSnapshot.snapshot_key.in_(
                    ["peer_growth", "peer_valuation", "peer_dupont", "peer_scale"]
                ),
            )
            .scalar()
        )
        latest_business_snapshot = (
            db.query(func.max(AkDataSnapshot.snapshot_date))
            .filter(
                AkDataSnapshot.stock_symbol == stock.symbol,
                AkDataSnapshot.snapshot_key == "company_business_composition",
            )
            .scalar()
        )
        latest_pledge_ratio_snapshot = (
            db.query(func.max(AkDataSnapshot.snapshot_date))
            .filter(
                AkDataSnapshot.stock_symbol.is_(None),
                AkDataSnapshot.snapshot_key == "market_pledge_ratio_em",
            )
            .scalar()
        )
        latest_pledge_detail_snapshot = (
            db.query(func.max(AkDataSnapshot.snapshot_date))
            .filter(
                AkDataSnapshot.stock_symbol == stock.symbol,
                AkDataSnapshot.snapshot_key == "company_pledge_detail",
            )
            .scalar()
        )

        now_utc = datetime.utcnow()
        quote_stale_minutes = 5 if 9 <= now_local.hour <= 15 else 60
        quote_age_seconds = (
            (now_utc - latest_quote_time).total_seconds() if latest_quote_time is not None else None
        )
        should_refresh_quote = (
            force
            or latest_quote_time is None
            or latest_quote_time.date() < target_date
            or (quote_age_seconds is not None and quote_age_seconds >= quote_stale_minutes * 60)
        )
        should_refresh_history = force or latest_market_date is None or (
            now_local.hour >= 16 and latest_market_date < target_date
        )
        should_refresh_block_trade = force or (now_local.hour >= 16 and latest_market_date != target_date)
        should_refresh_company_news = (
            force
            or latest_company_news is None
            or (datetime.now() - latest_company_news) >= timedelta(minutes=30)
        )
        should_refresh_macro = (
            force or latest_macro_news is None or (datetime.now() - latest_macro_news) >= timedelta(minutes=30)
        )
        # Financial refresh policy:
        # 1) no local data => must fetch;
        # 2) local data exists => fetch only when CNInfo latest report date is newer.
        cninfo_latest_report: date | None = None
        if force:
            should_refresh_financial = True
        elif latest_financial_report_date is None:
            should_refresh_financial = True
        else:
            cninfo_latest_report = self._cninfo_latest_report_date(code)
            should_refresh_financial = (
                cninfo_latest_report is not None and cninfo_latest_report > latest_financial_report_date
            )
        should_refresh_fundamental = (
            force
            or latest_fundamental_snapshot is None
            or (target_date - latest_fundamental_snapshot).days >= 7
        )
        should_refresh_advanced = (
            force
            or latest_advanced_doc is None
            or (datetime.now() - latest_advanced_doc) >= timedelta(hours=12)
        )
        should_refresh_market_layers = force or latest_market_snapshot is None or latest_market_snapshot < target_date
        should_refresh_peer = (
            force
            or latest_peer_snapshot is None
            or (target_date - latest_peer_snapshot).days >= 7
        )
        should_refresh_business = (
            force
            or latest_business_snapshot is None
            or (target_date - latest_business_snapshot).days >= 7
        )
        should_refresh_pledge_ratio = (
            force
            or latest_pledge_ratio_snapshot is None
            or (target_date - latest_pledge_ratio_snapshot).days >= 3
        )
        should_refresh_pledge_detail = (
            force
            or latest_pledge_detail_snapshot is None
            or (target_date - latest_pledge_detail_snapshot).days >= 3
        )

        detail: dict[str, Any] = {"symbol": code, "market_scope": SYNC_MARKET_SCOPE}
        refreshed = {
            "quote": False,
            "history": False,
            "block_trade": False,
            "company_news": False,
            "macro_news": False,
            "announcements": False,
            "financials": False,
            "fundamental": False,
            "advanced_signals": False,
            "market_layers": False,
            "peer_comparison": False,
            "business_composition": False,
            "pledge_ratio": False,
            "pledge_detail": False,
        }

        if should_refresh_quote:
            detail["quote"] = self.sync_realtime_quote_single(db, code)
            refreshed["quote"] = True

        if should_refresh_history:
            detail["history"] = self.sync_history(
                db,
                symbol=code,
                start_date=target_date - timedelta(days=max(7, history_days)),
                end_date=target_date,
                periods=("daily", "weekly", "monthly"),
                adjust="qfq",
            )
            refreshed["history"] = True

        if should_refresh_block_trade:
            try:
                detail["block_trade"] = self.sync_block_trade(db, target_date, target_date)
                refreshed["block_trade"] = not bool((detail.get("block_trade") or {}).get("skipped"))
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "sync_symbol_hot_data block trade failed, continue | symbol=%s error=%s",
                    code,
                    exc,
                )
                detail["block_trade"] = {"error": str(exc), "skipped": True}
                refreshed["block_trade"] = False

        if should_refresh_macro:
            detail["macro_news"] = self.sync_global_news(db, limit=200)
            refreshed["macro_news"] = True

        if should_refresh_company_news:
            detail["company_news"] = self.sync_company_news_from_global(db, code, limit=200)
            refreshed["company_news"] = True

        if should_refresh_fundamental:
            try:
                detail["fundamental"] = self.sync_company_profile(db, code)
                refreshed["fundamental"] = not bool((detail.get("fundamental") or {}).get("skipped"))
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "sync_symbol_hot_data fundamental failed, continue | symbol=%s error=%s",
                    code,
                    exc,
                )
                detail["fundamental"] = {"error": str(exc), "skipped": True}
                refreshed["fundamental"] = False

        if should_refresh_financial:
            detail["financials"] = self.sync_company_financial(db, code)
            refreshed["financials"] = True

        if should_refresh_advanced:
            detail["advanced_signals"] = self.sync_company_advanced_signals(db, code, limit=30)
            refreshed["advanced_signals"] = True

        if should_refresh_market_layers:
            detail["market_layers"] = self.sync_market_overview_layers(db, as_of_date=target_date)
            refreshed["market_layers"] = True

        if should_refresh_peer:
            detail["peer_comparison"] = self.sync_company_peer_comparison(db, code, limit=30)
            refreshed["peer_comparison"] = True

        if should_refresh_business:
            detail["business_composition"] = self.sync_company_business_composition(db, code, limit=120)
            refreshed["business_composition"] = True

        if should_refresh_pledge_ratio:
            detail["pledge_ratio"] = self.sync_market_pledge_ratio(
                db,
                as_of_date=target_date,
                lookback_days=30,
                focus_symbols=[code],
            )
            refreshed["pledge_ratio"] = True

        if should_refresh_pledge_detail:
            detail["pledge_detail"] = self.sync_company_pledge_detail(
                db,
                symbol=code,
                limit=80,
            )
            refreshed["pledge_detail"] = True

        should_refresh_announcements = (
            force
            or latest_company_news is None
            or (datetime.now() - latest_company_news) >= timedelta(hours=6)
        )
        if should_refresh_announcements:
            detail["announcements"] = self.sync_company_announcements(
                db,
                symbol=code,
                start_date=target_date - timedelta(days=7),
                end_date=target_date,
            )
            refreshed["announcements"] = True
        else:
            detail["announcements"] = {"skipped": True, "reason": "recent_data_exists"}

        detail["refreshed"] = refreshed
        return detail

    def daily_sync(
        self,
        db: Session,
        *,
        trade_date: date,
        symbols: list[str] | None,
        history_days: int,
        include_block_trade: bool,
        include_news: bool,
        include_macro: bool,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "trade_date": trade_date.isoformat(),
            "history_days": history_days,
            "history": {},
            "market_scope": SYNC_MARKET_SCOPE,
        }

        try:
            universe = self.sync_stock_universe(db)
            summary["universe"] = universe
        except Exception as exc:
            summary["universe"] = {"error": str(exc)}
            logger.exception("daily_sync universe failed | error=%s", exc)

        try:
            quote_result = self.sync_realtime_quotes(db, symbols=symbols)
            summary["quotes"] = quote_result
        except Exception as exc:
            summary["quotes"] = {"error": str(exc)}
            logger.exception("daily_sync quotes failed | error=%s", exc)

        if symbols:
            target_symbols = self._filter_sync_symbols(symbols)
            skipped_symbols = sorted(
                {self._normalize_symbol(s) for s in symbols if self._normalize_symbol(s) and not self._is_sync_symbol(s)}
            )
            if skipped_symbols:
                summary["skipped_symbols"] = skipped_symbols
        else:
            latest_quotes = (
                db.query(StockQuote)
                .join(Stock, Stock.id == StockQuote.stock_id)
                .filter(Stock.market.in_(tuple(SYNC_ALLOWED_MARKETS)))
                .order_by(StockQuote.quote_time.desc(), StockQuote.amount.desc().nullslast())
                .limit(120)
                .all()
            )
            target_symbols = []
            seen = set()
            for quote in latest_quotes:
                stock = db.get(Stock, quote.stock_id)
                if stock and stock.symbol not in seen:
                    target_symbols.append(stock.symbol)
                    seen.add(stock.symbol)
            if not target_symbols:
                target_symbols = [
                    row.symbol
                    for row in (
                        db.query(Stock)
                        .filter(Stock.market.in_(tuple(SYNC_ALLOWED_MARKETS)))
                        .order_by(Stock.symbol.asc())
                        .limit(120)
                        .all()
                    )
                ]

        history_start = trade_date - timedelta(days=max(7, history_days))
        for symbol in target_symbols:
            try:
                summary["history"][symbol] = self.sync_history(
                    db,
                    symbol=symbol,
                    start_date=history_start,
                    end_date=trade_date,
                    periods=("daily", "weekly", "monthly"),
                    adjust="qfq",
                )
            except Exception as exc:
                summary["history"][symbol] = {"error": str(exc)}

        if include_block_trade:
            try:
                summary["block_trade"] = self.sync_block_trade(db, trade_date, trade_date)
            except Exception as exc:
                summary["block_trade"] = {"error": str(exc)}

        if include_news:
            try:
                summary["global_news"] = self.sync_global_news(db, limit=200)
            except Exception as exc:
                summary["global_news"] = {"error": str(exc)}
            summary["company_news"] = {}
            for symbol in target_symbols[:20]:
                try:
                    summary["company_news"][symbol] = self.sync_company_news_from_global(db, symbol=symbol, limit=200)
                except Exception as exc:
                    summary["company_news"][symbol] = {"error": str(exc)}

        if include_macro:
            # Macro docs + broader market snapshots for layered storage
            summary["macro"] = {
                "strategy": "macro documents are aggregated from Eastmoney/Sina/Futu/THS/CLS/Caixin multi-source feeds (rolling 7 days)"
            }
            try:
                summary["market_layers"] = self.sync_market_overview_layers(db, as_of_date=trade_date)
            except Exception as exc:
                summary["market_layers"] = {"error": str(exc)}

        # Keep announcement sync small to avoid excessive API pressure
        announcement_symbols = target_symbols[:20]
        summary["announcements"] = {}
        for symbol in announcement_symbols:
            try:
                summary["announcements"][symbol] = self.sync_company_announcements(
                    db,
                    symbol=symbol,
                    start_date=trade_date - timedelta(days=7),
                    end_date=trade_date,
                )
            except Exception as exc:
                summary["announcements"][symbol] = {"error": str(exc)}

        summary["fundamentals"] = {}
        summary["financials"] = {}
        summary["advanced_signals"] = {}
        summary["peer_comparison"] = {}
        summary["business_composition"] = {}
        for symbol in target_symbols[:20]:
            try:
                summary["fundamentals"][symbol] = self.sync_company_profile(db, symbol)
            except Exception as exc:
                summary["fundamentals"][symbol] = {"error": str(exc)}
            try:
                summary["financials"][symbol] = self.sync_company_financial(db, symbol)
            except Exception as exc:
                summary["financials"][symbol] = {"error": str(exc)}
            try:
                summary["advanced_signals"][symbol] = self.sync_company_advanced_signals(db, symbol, limit=20)
            except Exception as exc:
                summary["advanced_signals"][symbol] = {"error": str(exc)}
            try:
                summary["peer_comparison"][symbol] = self.sync_company_peer_comparison(db, symbol, limit=20)
            except Exception as exc:
                summary["peer_comparison"][symbol] = {"error": str(exc)}
            try:
                summary["business_composition"][symbol] = self.sync_company_business_composition(db, symbol, limit=120)
            except Exception as exc:
                summary["business_composition"][symbol] = {"error": str(exc)}

        try:
            summary["pledge_ratio"] = self.sync_market_pledge_ratio(
                db,
                as_of_date=trade_date,
                lookback_days=30,
                focus_symbols=target_symbols[:20],
            )
        except Exception as exc:
            summary["pledge_ratio"] = {"error": str(exc)}

        try:
            summary["pledge_detail"] = self.sync_company_pledge_detail_batch(
                db,
                symbols=target_symbols[:20],
                limit_per_symbol=80,
            )
        except Exception as exc:
            summary["pledge_detail"] = {"error": str(exc)}

        return summary

    def static_sync(self, db: Session, symbols: list[str]) -> dict[str, Any]:
        target_symbols = self._filter_sync_symbols(symbols)
        skipped = sorted(
            {self._normalize_symbol(s) for s in symbols if self._normalize_symbol(s) and not self._is_sync_symbol(s)}
        )
        result: dict[str, Any] = {
            "profiles": {},
            "financials": {},
            "peer_comparison": {},
            "business_composition": {},
            "market_scope": SYNC_MARKET_SCOPE,
        }
        if skipped:
            result["skipped_symbols"] = skipped
        for symbol in target_symbols:
            code = self._normalize_symbol(symbol)
            try:
                result["profiles"][code] = self.sync_company_profile(db, code)
            except Exception as exc:
                result["profiles"][code] = {"error": str(exc)}
            try:
                result["financials"][code] = self.sync_company_financial(db, code)
            except Exception as exc:
                result["financials"][code] = {"error": str(exc)}
            try:
                result["peer_comparison"][code] = self.sync_company_peer_comparison(db, code, limit=20)
            except Exception as exc:
                result["peer_comparison"][code] = {"error": str(exc)}
            try:
                result["business_composition"][code] = self.sync_company_business_composition(db, code, limit=120)
            except Exception as exc:
                result["business_composition"][code] = {"error": str(exc)}
        try:
            result["market_layers"] = self.sync_market_overview_layers(db, as_of_date=date.today())
        except Exception as exc:
            result["market_layers"] = {"error": str(exc)}
        try:
            result["pledge_ratio"] = self.sync_market_pledge_ratio(
                db,
                as_of_date=date.today(),
                lookback_days=30,
                focus_symbols=target_symbols,
            )
        except Exception as exc:
            result["pledge_ratio"] = {"error": str(exc)}
        try:
            result["pledge_detail"] = self.sync_company_pledge_detail_batch(
                db,
                symbols=target_symbols,
                limit_per_symbol=80,
            )
        except Exception as exc:
            result["pledge_detail"] = {"error": str(exc)}
        return result


akshare_service = AkshareService()

# 目前部分数据是不对的，没有正确接入数据库
# 或者，有一些数据的保存格式不太对，需要完善
