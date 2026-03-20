from __future__ import annotations

from datetime import date, datetime
import math
import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.company_financial import CompanyFinancial
from app.models.company_fundamental import CompanyFundamental
from app.models.stock_quote import StockQuote
from app.services.financial_analysis.eastmoney_financial import extract_core_metrics, to_float

TRUSTED_STATEMENT_DATASETS = {
    "stock_financial_analysis_indicator_em",
    "stock_financial_abstract_new_ths",
    "stock_financial_abstract",
    "p_stock2300",  # 资产负债表相关
    "p_stock2301",  # 利润表相关
    "p_stock2302",  # 现金流量表相关
    "p_stock2303",  # 主要财务指标
}

EXCLUDED_QUANT_DATASETS = {
    "p_stock2237_inc",  # 增量元数据
    "p_stock2237",      # 披露计划
    "p_stock2238",      # 业绩预告
    "p_stock2239",      # 业绩快报/公告类
    "p_stock2328",      # 事件类
    "p_stock2387",      # 事件类
    "p_stock2399",      # 规则类信息
    "p_ods3302",        # 分业务构成，非统一口径主表
}


def _safe_float(value: Any, default: float | None = None) -> float | None:
    parsed = to_float(value)
    if parsed is None:
        return default
    return parsed


def _round(value: float | None, ndigits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), ndigits)


def _normalize_ratio_to_percent(value: float | None) -> float | None:
    """
    Convert ratio-form values (0~5) to percentage points.
    Keep already-percent values untouched.
    """
    if value is None:
        return None
    v = float(value)
    if -5.0 <= v <= 5.0:
        return v * 100.0
    return v


def _sanitize_metric_range(name: str, value: float | None) -> float | None:
    if value is None:
        return None
    v = float(value)
    limits: dict[str, tuple[float, float]] = {
        "roe": (-1000.0, 1000.0),
        "roa": (-200.0, 200.0),
        "gross_margin": (-200.0, 200.0),
        "net_margin": (-200.0, 200.0),
        "debt_ratio": (0.0, 500.0),
        "current_ratio": (0.0, 100.0),
        "yoy_revenue": (-10000.0, 10000.0),
        "yoy_net_profit": (-10000.0, 10000.0),
    }
    if name not in limits:
        return v
    lo, hi = limits[name]
    if v < lo or v > hi:
        return None
    return v


def _returns_from_prices(prices: list[float]) -> list[float]:
    if len(prices) < 2:
        return []
    out: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        cur = prices[i]
        if prev and prev > 0:
            out.append((cur / prev) - 1.0)
    return out


def _covariance(x: list[float], y: list[float]) -> float:
    if len(x) < 2 or len(y) < 2:
        return 0.0
    n = min(len(x), len(y))
    x = x[-n:]
    y = y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    acc = 0.0
    for i in range(n):
        acc += (x[i] - mx) * (y[i] - my)
    return acc / max(1, n - 1)


def _variance(x: list[float]) -> float:
    if len(x) < 2:
        return 0.0
    m = sum(x) / len(x)
    return sum((v - m) ** 2 for v in x) / max(1, len(x) - 1)


class QuantFactorEngine:
    """
    Quant factors are computed on-the-fly and are not persisted to DB.
    """

    def __init__(self, *, annual_rf: float = 0.02) -> None:
        self.annual_rf = annual_rf
        self._benchmark_lock = threading.Lock()
        self._benchmark_cache: dict[str, Any] = {"ts": 0.0, "returns": []}

    @staticmethod
    def _clean_unit_text(value: Any) -> str:
        text = str(value or "").strip().replace(",", "")
        for token in ("股", "元", "人民币"):
            text = text.replace(token, "")
        return text

    @staticmethod
    def _is_row_trusted_for_quant(row: CompanyFinancial) -> bool:
        dataset = str(row.dataset or "").strip()
        source = str(row.source or "").strip().lower()
        if dataset in EXCLUDED_QUANT_DATASETS:
            return False
        if dataset in TRUSTED_STATEMENT_DATASETS:
            return True
        if dataset.startswith("eastmoney_f10:"):
            return True
        # Historical rows may miss dataset but still contain normalized fields from akshare/eastmoney.
        if not dataset and source in {"akshare", "eastmoney_f10"}:
            return any(
                value is not None
                for value in (
                    row.revenue,
                    row.net_profit,
                    row.gross_margin,
                    row.roe,
                    row.asset_liability_ratio,
                    row.operating_cashflow,
                )
            )
        return False

    @staticmethod
    def _allow_raw_metric_extract(row: CompanyFinancial) -> bool:
        dataset = str(row.dataset or "").strip()
        source = str(row.source or "").strip().lower()
        if dataset.startswith("eastmoney_f10:"):
            return True
        if dataset in TRUSTED_STATEMENT_DATASETS:
            return True
        if source in {"akshare", "eastmoney_f10"} and not dataset:
            return True
        return False

    def _extract_total_shares(self, fundamental: CompanyFundamental | None) -> float | None:
        if not fundamental or not isinstance(fundamental.raw, dict):
            return None
        info = fundamental.raw.get("individual_info")
        if not isinstance(info, dict):
            return None

        candidates = [
            "总股本",
            "总股本(股)",
            "总股本(亿股)",
            "总股本(万股)",
            "总股本A股",
            "流通股",
            "流通股本",
        ]
        for key in candidates:
            if key not in info:
                continue
            raw = self._clean_unit_text(info.get(key))
            if "亿" in str(info.get(key)):
                parsed = _safe_float(raw.replace("亿", ""))
                if parsed is not None:
                    return parsed * 1e8
            if "万" in str(info.get(key)):
                parsed = _safe_float(raw.replace("万", ""))
                if parsed is not None:
                    return parsed * 1e4
            parsed = _safe_float(raw)
            if parsed is not None:
                # Some feeds return total shares in 万股; use a conservative heuristic.
                return parsed * 1e4 if parsed < 1e7 else parsed
        return None

    def _build_latest_metrics(self, financial_rows: list[CompanyFinancial]) -> dict[str, float | None]:
        trusted_rows = [row for row in financial_rows if self._is_row_trusted_for_quant(row)]
        ordered_source = trusted_rows if trusted_rows else financial_rows
        ordered = sorted(
            ordered_source,
            key=lambda item: (item.report_date, item.id),
            reverse=True,
        )
        merged: dict[str, float | None] = {
            "eps": None,
            "bps": None,
            "roe": None,
            "revenue": None,
            "net_profit": None,
            "gross_margin": None,
            "asset_liability_ratio": None,
            "operating_cashflow": None,
            "total_assets": None,
            "total_liabilities": None,
            "equity": None,
            "current_assets": None,
            "current_liabilities": None,
            "yoy_revenue": None,
            "yoy_net_profit": None,
        }

        for row in ordered:
            raw = row.raw if isinstance(row.raw, dict) else {}
            from_raw = extract_core_metrics(raw) if self._allow_raw_metric_extract(row) else {}
            candidates = {
                "eps": row.eps if row.eps is not None else from_raw.get("eps"),
                "bps": from_raw.get("bps"),
                "roe": row.roe if row.roe is not None else from_raw.get("roe"),
                "revenue": row.revenue if row.revenue is not None else from_raw.get("revenue"),
                "net_profit": row.net_profit if row.net_profit is not None else from_raw.get("net_profit"),
                "gross_margin": row.gross_margin if row.gross_margin is not None else from_raw.get("gross_margin"),
                "asset_liability_ratio": (
                    row.asset_liability_ratio
                    if row.asset_liability_ratio is not None
                    else (
                        (from_raw.get("total_liabilities") / from_raw.get("total_assets")) * 100
                        if from_raw.get("total_liabilities") is not None
                        and from_raw.get("total_assets") not in (None, 0)
                        else None
                    )
                ),
                "operating_cashflow": (
                    row.operating_cashflow if row.operating_cashflow is not None else from_raw.get("operating_cashflow")
                ),
                "total_assets": from_raw.get("total_assets"),
                "total_liabilities": from_raw.get("total_liabilities"),
                "equity": from_raw.get("equity"),
                "current_assets": from_raw.get("current_assets"),
                "current_liabilities": from_raw.get("current_liabilities"),
                "yoy_revenue": row.yoy_revenue,
                "yoy_net_profit": row.yoy_net_profit,
            }
            for key, value in candidates.items():
                if merged.get(key) is None and value is not None:
                    merged[key] = float(value)
        for key in ("roe", "gross_margin", "asset_liability_ratio", "yoy_revenue", "yoy_net_profit"):
            merged[key] = _sanitize_metric_range(key, merged.get(key))
        return merged

    def _build_annual_series(self, financial_rows: list[CompanyFinancial]) -> list[dict[str, Any]]:
        buckets: dict[int, list[CompanyFinancial]] = {}
        trusted_rows = [row for row in financial_rows if self._is_row_trusted_for_quant(row)]
        source_rows = trusted_rows if trusted_rows else financial_rows
        for row in sorted(source_rows, key=lambda x: (x.report_date, x.id), reverse=True):
            year = int(row.report_date.year)
            if year not in buckets:
                buckets[year] = []
            buckets[year].append(row)
        out: list[dict[str, Any]] = []
        for year in sorted(buckets.keys(), reverse=True)[:3]:
            year_rows = buckets[year]
            # Prefer the row that carries the richest annual metrics, not just the latest inserted row.
            def _score(item: CompanyFinancial) -> tuple[int, date, int]:
                raw_item = item.raw if isinstance(item.raw, dict) else {}
                merged_item = extract_core_metrics(raw_item) if self._allow_raw_metric_extract(item) else {}
                rev = item.revenue if item.revenue is not None else merged_item.get("revenue")
                np = item.net_profit if item.net_profit is not None else merged_item.get("net_profit")
                gm = item.gross_margin if item.gross_margin is not None else merged_item.get("gross_margin")
                r = item.roe if item.roe is not None else merged_item.get("roe")
                score = sum(1 for v in (rev, np, gm, r) if v is not None)
                return score, item.report_date, int(item.id or 0)

            row = max(year_rows, key=_score)
            raw = row.raw if isinstance(row.raw, dict) else {}
            merged = extract_core_metrics(raw) if self._allow_raw_metric_extract(row) else {}
            revenue = row.revenue if row.revenue is not None else merged.get("revenue")
            net_profit = row.net_profit if row.net_profit is not None else merged.get("net_profit")
            gross_margin = _sanitize_metric_range(
                "gross_margin",
                row.gross_margin if row.gross_margin is not None else merged.get("gross_margin"),
            )
            roe = _sanitize_metric_range("roe", row.roe if row.roe is not None else merged.get("roe"))
            net_margin = None
            if revenue and revenue != 0 and net_profit is not None:
                net_margin = _sanitize_metric_range("net_margin", (net_profit / revenue) * 100)
            out.append(
                {
                    "year": year,
                    "report_date": row.report_date.isoformat(),
                    "revenue": _round(revenue, 4),
                    "net_profit": _round(net_profit, 4),
                    "gross_margin": _round(gross_margin, 6),
                    "net_margin": _round(net_margin, 6),
                    "roe": _round(roe, 6),
                }
            )
        return out

    def _benchmark_weekly_returns(self) -> list[float]:
        # Cache for 30 minutes to reduce external pressure.
        with self._benchmark_lock:
            now = time.time()
            if (now - float(self._benchmark_cache.get("ts") or 0.0)) < 1800:
                cached = self._benchmark_cache.get("returns")
                if isinstance(cached, list):
                    return [float(v) for v in cached if isinstance(v, (int, float))]

        try:
            import akshare as ak  # type: ignore
        except Exception:
            return []

        df = None
        last_error: Exception | None = None
        fetchers = [
            lambda: ak.stock_zh_index_daily_em(symbol="sz399001"),
            lambda: ak.stock_zh_index_daily(symbol="sz399001"),
        ]
        for fn in fetchers:
            try:
                candidate = fn()
            except Exception as exc:
                last_error = exc
                continue
            if candidate is not None and not candidate.empty:
                df = candidate
                break
        if df is None or df.empty:
            if last_error:
                # keep silent fallback
                _ = str(last_error)
            return []

        rows: list[tuple[datetime, float]] = []
        for _, row in df.iterrows():
            date_val = row.get("date")
            if date_val is None:
                date_val = row.get("日期")
            close_val = row.get("close")
            if close_val is None:
                close_val = row.get("收盘")
            close = _safe_float(close_val)
            if close is None or close <= 0:
                continue
            try:
                parsed_dt = datetime.strptime(str(date_val)[:10], "%Y-%m-%d")
            except Exception:
                continue
            rows.append((parsed_dt, close))
        rows.sort(key=lambda item: item[0])
        if len(rows) < 20:
            return []

        # Convert daily index close to weekly close (last trading day in week).
        weekly_map: dict[tuple[int, int], tuple[datetime, float]] = {}
        for dt, close in rows:
            key = dt.isocalendar()[:2]
            weekly_map[key] = (dt, close)
        weekly = [value[1] for _, value in sorted(weekly_map.items(), key=lambda item: item[1][0])]
        ret = _returns_from_prices(weekly)[-120:]
        with self._benchmark_lock:
            self._benchmark_cache = {"ts": time.time(), "returns": ret}
        return ret

    @staticmethod
    def _beta(stock_weekly_returns: list[float], benchmark_weekly_returns: list[float], limit: int = 100) -> float | None:
        if not stock_weekly_returns or not benchmark_weekly_returns:
            return None
        n = min(limit, len(stock_weekly_returns), len(benchmark_weekly_returns))
        if n < 20:
            return None
        s = stock_weekly_returns[-n:]
        b = benchmark_weekly_returns[-n:]
        var_b = _variance(b)
        if var_b <= 1e-12:
            return None
        return _covariance(s, b) / var_b

    def compute(
        self,
        *,
        db: Session,
        stock_id: int,
        symbol: str,
        latest_price: float | None,
        market_rows: list[Any],
        weekly_rows: list[Any],
        latest_quote: StockQuote | None,
        financial_rows: list[CompanyFinancial],
        fundamental: CompanyFundamental | None,
    ) -> dict[str, Any]:
        price = _safe_float(latest_price, None)
        metrics = self._build_latest_metrics(financial_rows)
        annual_series = self._build_annual_series(financial_rows)

        shares_total = self._extract_total_shares(fundamental)
        market_cap = None
        if price and shares_total and shares_total > 0:
            market_cap = price * shares_total

        revenue = metrics.get("revenue")
        net_profit = metrics.get("net_profit")
        eps = metrics.get("eps")
        bps = metrics.get("bps")
        roe = metrics.get("roe")
        gross_margin = metrics.get("gross_margin")
        asset_liability_ratio = metrics.get("asset_liability_ratio")
        operating_cashflow = metrics.get("operating_cashflow")
        total_assets = metrics.get("total_assets")
        total_liabilities = metrics.get("total_liabilities")
        equity = metrics.get("equity")
        current_assets = metrics.get("current_assets")
        current_liabilities = metrics.get("current_liabilities")

        roa = (
            _sanitize_metric_range("roa", (net_profit / total_assets) * 100)
            if (net_profit is not None and total_assets and total_assets != 0)
            else None
        )
        net_margin = (
            _sanitize_metric_range("net_margin", (net_profit / revenue) * 100)
            if (net_profit is not None and revenue and revenue != 0)
            else None
        )
        debt_ratio = _sanitize_metric_range("debt_ratio", _normalize_ratio_to_percent(asset_liability_ratio))
        if debt_ratio is None:
            debt_ratio = (
                _sanitize_metric_range("debt_ratio", (total_liabilities / total_assets) * 100)
                if (total_liabilities is not None and total_assets and total_assets != 0)
                else None
            )
        current_ratio = (
            _sanitize_metric_range("current_ratio", (current_assets / current_liabilities))
            if (current_assets is not None and current_liabilities and current_liabilities != 0)
            else None
        )

        pe = None
        if price and eps and eps > 0:
            pe = price / eps
        elif market_cap and net_profit and net_profit > 0:
            pe = market_cap / net_profit

        pb = None
        if price and bps and bps > 0:
            pb = price / bps
        elif market_cap and equity and equity > 0:
            pb = market_cap / equity

        ps = None
        if market_cap and revenue and revenue > 0:
            ps = market_cap / revenue

        market_closes = [
            float(getattr(row, "close"))
            for row in market_rows
            if _safe_float(getattr(row, "close", None), None) is not None
        ]
        market_volumes = [
            float(getattr(row, "volume"))
            for row in market_rows
            if _safe_float(getattr(row, "volume", None), None) is not None
        ]
        daily_returns = _returns_from_prices(market_closes)
        mean_daily = (sum(daily_returns) / len(daily_returns)) if daily_returns else None
        std_daily = None
        if daily_returns and len(daily_returns) > 1:
            m = mean_daily or 0.0
            std_daily = math.sqrt(sum((x - m) ** 2 for x in daily_returns) / (len(daily_returns) - 1))

        sharpe = None
        if mean_daily is not None and std_daily and std_daily > 1e-9:
            rf_daily = self.annual_rf / 252.0
            sharpe = (mean_daily - rf_daily) / std_daily * math.sqrt(252.0)

        volume_5d_change = None
        if len(market_volumes) >= 10:
            last5 = sum(market_volumes[-5:]) / 5.0
            prev5 = sum(market_volumes[-10:-5]) / 5.0
            if prev5 > 0:
                volume_5d_change = (last5 - prev5) / prev5

        quote_rows = (
            db.query(StockQuote)
            .filter(StockQuote.stock_id == stock_id)
            .order_by(StockQuote.quote_time.desc())
            .limit(5)
            .all()
        )
        turnover_5d_avg = None
        if quote_rows:
            turn_values = [float(row.turnover_rate) for row in quote_rows if row.turnover_rate is not None]
            if turn_values:
                turnover_5d_avg = sum(turn_values) / len(turn_values)
        latest_turnover = float(latest_quote.turnover_rate) if latest_quote and latest_quote.turnover_rate is not None else None

        stock_weekly_close = [
            float(getattr(row, "close"))
            for row in reversed(weekly_rows)
            if _safe_float(getattr(row, "close", None), None) is not None
        ]
        stock_weekly_ret = _returns_from_prices(stock_weekly_close)
        benchmark_weekly_ret = self._benchmark_weekly_returns()
        beta_100w = self._beta(stock_weekly_ret, benchmark_weekly_ret, limit=100)

        roe = _sanitize_metric_range("roe", roe)
        gross_margin = _sanitize_metric_range("gross_margin", gross_margin)
        yoy_revenue = _sanitize_metric_range("yoy_revenue", metrics.get("yoy_revenue"))
        yoy_net_profit = _sanitize_metric_range("yoy_net_profit", metrics.get("yoy_net_profit"))

        factors: list[dict[str, Any]] = [
            {"name": "ROE", "value": _round(roe), "unit": "%", "category": "质量", "formula": "净利润 / 净资产"},
            {"name": "ROA", "value": _round(roa), "unit": "%", "category": "质量", "formula": "净利润 / 总资产"},
            {"name": "毛利率", "value": _round(gross_margin), "unit": "%", "category": "质量", "formula": "（营收-营业成本）/营收"},
            {"name": "净利率", "value": _round(net_margin), "unit": "%", "category": "质量", "formula": "净利润 / 营收"},
            {"name": "营收同比", "value": _round(yoy_revenue), "unit": "%", "category": "成长", "formula": "本期营收同比"},
            {"name": "净利润同比", "value": _round(yoy_net_profit), "unit": "%", "category": "成长", "formula": "本期净利润同比"},
            {"name": "资产负债率", "value": _round(debt_ratio), "unit": "%", "category": "风险", "formula": "总负债 / 总资产"},
            {"name": "流动比率", "value": _round(current_ratio), "unit": "x", "category": "风险", "formula": "流动资产 / 流动负债"},
            {"name": "PE", "value": _round(pe), "unit": "x", "category": "估值", "formula": "股价 / EPS 或 市值 / 净利润"},
            {"name": "PB", "value": _round(pb), "unit": "x", "category": "估值", "formula": "股价 / 每股净资产 或 市值 / 净资产"},
            {"name": "PS", "value": _round(ps), "unit": "x", "category": "估值", "formula": "市值 / 营收"},
            {"name": "市值", "value": _round(market_cap, 2), "unit": "元", "category": "估值", "formula": "股价 × 总股本"},
            {"name": "近5日成交量变化", "value": _round(volume_5d_change), "unit": "%", "category": "交易", "formula": "(近5日均量-前5日均量)/前5日均量"},
            {"name": "当日换手率", "value": _round(latest_turnover), "unit": "%", "category": "交易", "formula": "当日成交量 / 流通股本"},
            {"name": "近5日换手率均值", "value": _round(turnover_5d_avg), "unit": "%", "category": "交易", "formula": "近5个交易日换手率均值"},
            {"name": "Beta(近100周)", "value": _round(beta_100w), "unit": "x", "category": "风险", "formula": "Cov(个股周收益,基准周收益)/Var(基准周收益)"},
            {"name": "Sharpe Ratio", "value": _round(sharpe), "unit": "x", "category": "收益质量", "formula": "(日均收益-无风险利率)/波动率 × sqrt(252)"},
            {"name": "经营现金流", "value": _round(operating_cashflow, 2), "unit": "元", "category": "现金流", "formula": "经营活动现金流量净额"},
        ]
        factors = [row for row in factors if row.get("value") is not None]

        metric_snapshot = {
            "symbol": symbol,
            "price": _round(price, 4),
            "eps": _round(eps, 6),
            "revenue": _round(revenue, 2),
            "net_profit": _round(net_profit, 2),
            "total_assets": _round(total_assets, 2),
            "total_liabilities": _round(total_liabilities, 2),
            "equity": _round(equity, 2),
            "roe": _round(roe, 6),
            "debt_ratio": _round(debt_ratio, 6),
            "gross_margin": _round(gross_margin, 6),
            "shares_total": _round(shares_total, 0),
        }

        return {
            "factor_formula": "专家综合分=Σ(专家分×权重)。量化因子用于增强财务/估值/风险解释，不直接替代专家打分。",
            "factor_weights": {
                "news": 0.18,
                "stock_data": 0.32,
                "macro": 0.15,
                "financial": 0.20,
                "fundamental": 0.15,
            },
            "factors": factors,
            "metric_snapshot": metric_snapshot,
            "annual_series_3y": annual_series,
            "calc_meta": {
                "daily_returns_samples": len(daily_returns),
                "weekly_returns_samples": len(stock_weekly_ret),
                "benchmark_weekly_samples": len(benchmark_weekly_ret),
            },
        }
