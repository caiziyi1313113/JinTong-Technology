from __future__ import annotations

"""
专家编排器（v2）。

这个模块负责把“数据准备 -> 五专家分析 -> 投资建议”串成一条可执行链路。
它的目标不是让单个专家完美，而是保证整条链路稳定、可回退、输出结构统一。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.market_scope import is_target_symbol, market_from_symbol, normalize_symbol
from app.models.block_trade import BlockTradeRecord
from app.models.company_financial import CompanyFinancial
from app.models.company_financial_event import CompanyFinancialEvent
from app.models.company_fundamental import CompanyFundamental
from app.models.document import Document
from app.models.macro_news import MacroNews
from app.models.market import MarketData
from app.models.position import Position
from app.models.profile import UserProfile
from app.models.stock import Stock
from app.models.stock_kline import StockKline
from app.models.stock_quote import StockQuote
from app.services.decision.engine import fuse_signals
from app.services.experts import financial as legacy_financial
from app.services.experts import fundamental as legacy_fundamental
from app.services.experts import macro as legacy_macro
from app.services.experts import news as legacy_news
from app.services.experts import technical as legacy_technical
from app.services.experts_v2.prompts import (
    INVESTMENT_SYSTEM_PROMPT,
    build_expert_user_prompt,
    build_investment_user_prompt,
    get_expert_system_prompt,
)
from app.services.financial_analysis import industry_context_builder, quant_factor_engine
from app.services.llm import LLMClientError, zhipu_client


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpertDef:
    """
    专家元信息。

    - key: v2 统一 key（用于聚合与输出）
    - label: 给提示词/前端展示的中文名称
    - legacy_key: 当 LLM 失败时，回退 legacy runner 用的映射键
    """

    key: str
    label: str
    legacy_key: str


# 固定执行顺序（也对应前端进度条顺序）
EXPERTS: tuple[ExpertDef, ...] = (
    ExpertDef("news", "新闻专家", "news"),
    ExpertDef("stock_data", "股票数据专家", "technical"),
    ExpertDef("macro", "宏观面专家", "macro"),
    ExpertDef("financial", "财务数据专家", "financial"),
    ExpertDef("fundamental", "公司基本情况专家", "fundamental"),
)

# 专家权重（总和建议为 1.0）
EXPERT_WEIGHTS: dict[str, float] = {
    "news": 0.18,
    "stock_data": 0.32,
    "macro": 0.15,
    "financial": 0.20,
    "fundamental": 0.15,
}


# legacy 回退执行器
LEGACY_RUNNERS = {
    "news": legacy_news.run,
    "technical": legacy_technical.run,
    "macro": legacy_macro.run,
    "financial": legacy_financial.run,
    "fundamental": legacy_fundamental.run,
}


class ExpertOrchestrator:
    @staticmethod
    def _log_fallback(
        *,
        scope: str,
        symbol: str,
        expert_key: str,
        reason: str,
    ) -> None:
        logger.warning(
            "LLM_FALLBACK_TRIGGERED | scope=%s symbol=%s expert=%s reason=%s",
            scope,
            symbol,
            expert_key,
            reason,
        )

    @staticmethod
    def _signal_from_score(score: float) -> str:
        """
        把 0-100 分值映射成离散信号。
        """
        if score >= 55:
            return "buy"
        if score <= 45:
            return "sell"
        return "hold"

    @staticmethod
    def _direction(score: float) -> int:
        """
        分值方向离散化：
        1 = 看多, -1 = 看空, 0 = 中性。
        """
        if score >= 55:
            return 1
        if score <= 45:
            return -1
        return 0

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """
        float 容错解析，统一处理 None/非法值/NaN。
        """
        try:
            if value is None:
                return default
            parsed = float(value)
            if parsed != parsed:  # NaN
                return default
            return parsed
        except Exception:
            return default

    @staticmethod
    def _is_empty_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, float) and value != value:  # NaN
            return True
        if isinstance(value, str):
            text = value.strip()
            return text in {"", "-", "--", "null", "none", "nan"}
        if isinstance(value, (list, tuple, set, dict)) and len(value) == 0:
            return True
        return False

    @classmethod
    def _compact_dict(
        cls,
        data: dict[str, Any],
        *,
        keep_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        compacted: dict[str, Any] = {}
        for key, value in data.items():
            if keep_keys and key in keep_keys:
                compacted[key] = value
                continue
            if isinstance(value, dict):
                nested = cls._compact_dict(value)
                if nested:
                    compacted[key] = nested
                continue
            if isinstance(value, list):
                items: list[Any] = []
                for item in value:
                    if isinstance(item, dict):
                        nested = cls._compact_dict(item)
                        if nested:
                            items.append(nested)
                    elif not cls._is_empty_value(item):
                        items.append(item)
                if items:
                    compacted[key] = items
                continue
            if cls._is_empty_value(value):
                continue
            compacted[key] = value
        return compacted

    @staticmethod
    def _default_profile() -> SimpleNamespace:
        """
        用户未提供画像时的兜底画像。
        """
        return SimpleNamespace(
            risk_level="medium",
            investment_horizon="medium",
            income=0.0,
            assets=300000.0,
            disposable_funds=200000.0,
            experience_years=3.0,
            max_drawdown=0.2,
            risk_budget=0.02,
            target_return=0.12,
            max_single_position=0.15,
            style="balanced",
            persona="balanced_growth",
            questionnaire_answers={},
            preferences={},
        )

    def _get_or_create_stock(self, db: Session, symbol: str) -> Stock:
        """
        统一做代码规范化 + 市场范围校验 + stock 记录存在性保证。
        """
        code = normalize_symbol(symbol)
        if not is_target_symbol(code):
            raise ValueError("Only Shenzhen main-board A shares are supported")
        stock = db.query(Stock).filter(Stock.symbol == code).first()
        if stock:
            return stock
        stock = Stock(symbol=code, name=code, market=market_from_symbol(code))
        db.add(stock)
        db.commit()
        db.refresh(stock)
        return stock

    def _serialize_doc(self, doc: Document) -> dict[str, Any]:
        """
        把 Document 模型压缩成轻量文本块，供 LLM 使用。
        """
        return {
            "title": doc.title,
            "content": (doc.content or "")[:400],
            "source": doc.source,
            "published_at": doc.published_at.isoformat() if doc.published_at else None,
            "doc_type": doc.doc_type,
            "metadata": doc.doc_metadata or {},
        }

    def _technical_stats(self, closes: list[float]) -> dict[str, Any]:
        """
        轻量技术统计（无外部 TA 库依赖）。
        """
        if len(closes) < 5:
            return {}
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None
        ret_5d = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0.0
        ret_20d = (closes[-1] - closes[-20]) / closes[-20] if len(closes) >= 20 and closes[-20] else 0.0

        gains = []
        losses = []
        lookback = min(14, len(closes) - 1)
        for i in range(-lookback, 0):
            delta = closes[i] - closes[i - 1]
            if delta >= 0:
                gains.append(delta)
            else:
                losses.append(abs(delta))
        avg_gain = sum(gains) / lookback if lookback else 0.0
        avg_loss = sum(losses) / lookback if lookback else 0.0
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

        return {
            "ma5": round(ma5, 4),
            "ma20": round(ma20, 4) if ma20 else None,
            "ma60": round(ma60, 4) if ma60 else None,
            "ret_5d": round(ret_5d, 6),
            "ret_20d": round(ret_20d, 6),
            "rsi14": round(rsi, 4),
        }

    def _financial_row_highlights(self, row: CompanyFinancial) -> dict[str, Any]:
        """
        Extract high-value fields from company_financials.raw so LLM sees more than base EPS/revenue.
        """
        raw = row.raw if isinstance(row.raw, dict) else {}
        dataset = (row.dataset or "").lower()
        if not raw:
            return {}

        field_map: dict[str, dict[str, str]] = {
            "p_stock2300": {
                "cash_funds": "F006N",
                "accounts_receivable": "F009N",
                "inventory": "F015N",
                "current_assets_total": "F019N",
                "fixed_assets": "F025N",
                "assets_total": "F038N",
                "accounts_payable": "F042N",
                "current_liabilities_total": "F052N",
                "liabilities_total": "F061N",
                "undistributed_profit": "F065N",
                "parent_equity": "F073N",
                "equity_total": "F070N",
            },
            "p_stock2301": {
                "total_operating_revenue": "F035N",
                "total_operating_cost": "F036N",
                "operating_profit": "F018N",
                "profit_total": "F024N",
                "net_profit": "F027N",
                "parent_net_profit": "F028N",
                "rd_expense": "F056N",
                "basic_eps": "F031N",
                "diluted_eps": "F032N",
            },
            "p_stock2302": {
                "net_cash_from_operations": "F015N",
                "net_cash_from_investing": "F027N",
                "net_cash_from_financing": "F036N",
                "net_cash_increase": "F039N",
                "cash_beginning": "F040N",
                "cash_ending": "F041N",
            },
            "p_stock2303": {
                "eps": "F003N",
                "bps": "F008N",
                "roe": "F014N",
                "debt_ratio": "F041N",
                "current_ratio": "F042N",
                "quick_ratio": "F043N",
                "gross_margin": "F078N",
                "operating_cashflow": "F105N",
                "parent_net_profit": "F102N",
            },
            "p_stock2238": {
                "forecast_type": "F003V",
                "forecast_content": "F004V",
                "forecast_reason": "F005V",
                "net_profit_lower": "F007N",
                "net_profit_upper": "F008N",
                "profit_change_lower": "F009N",
                "profit_change_upper": "F010N",
            },
            "p_stock2239": {
                "audited_flag": "F002C",
                "domestic_auditor": "F004V",
                "domestic_opinion": "F007V",
                "overseas_auditor": "F010V",
                "overseas_opinion": "F013V",
                "nonstandard_matters": "F008V",
            },
            "p_ods3302": {
                "product_name": "F003V",
                "industry_code": "F004V",
                "industry_name": "F005V",
                "segment_revenue": "F006N",
                "segment_cost": "F007N",
            },
            "p_stock2328": {
                "net_profit": "F003N",
                "assets_total": "F004N",
                "equity_ex_minority": "F005N",
                "eps": "F006N",
                "roe": "F007N",
            },
            "p_stock2387": {
                "revenue": "F005N",
                "revenue_yoy": "F006N",
                "operating_profit": "F007N",
                "operating_profit_yoy": "F008N",
                "parent_net_profit": "F011N",
                "deducted_net_profit": "F013N",
                "gross_margin": "F015N",
                "net_margin": "F017N",
                "eps": "F033N",
                "net_cash_from_operations": "F051N",
            },
            "p_stock2237": {
                "report_period": "F001D",
                "scheduled_disclosure_date": "F002D",
                "actual_disclosure_date": "F006D",
            },
            "p_stock2237_inc": {
                "report_period": "F001D",
                "scheduled_disclosure_date": "F002D",
                "actual_disclosure_date": "F006D",
            },
        }

        selected = field_map.get(dataset, {})
        highlights: dict[str, Any] = {}
        for label, key in selected.items():
            value = raw.get(key)
            if value in (None, "", "-", "--"):
                continue
            highlights[label] = value

        if highlights:
            return highlights

        fallback: dict[str, Any] = {}
        for key, value in raw.items():
            if value in (None, "", "-", "--"):
                continue
            if not isinstance(key, str):
                continue
            if key.startswith("F") or key in {"SECCODE", "SECNAME", "DECLAREDATE", "STARTDATE", "ENDDATE", "RPTDATE"}:
                fallback[key] = value
            if len(fallback) >= 20:
                break
        return fallback
    def build_context(
        self,
        db: Session,
        *,
        stock: Stock,
        as_of_date: date | None,
        user_id: int | None,
    ) -> dict[str, Any]:
        """
        构建统一分析上下文（context）。

        这个函数是整个编排器的数据入口，负责把不同模型的数据规整到同一字典结构。
        """
        target_date = as_of_date or date.today()
        start_date = target_date - timedelta(days=180)

        # 最新快照行情（优先 quote，回退到日线 close）
        latest_quote = (
            db.query(StockQuote)
            .filter(StockQuote.stock_id == stock.id)
            .order_by(StockQuote.quote_time.desc())
            .first()
        )
        latest_market = (
            db.query(MarketData)
            .filter(MarketData.stock_id == stock.id)
            .order_by(MarketData.date.desc())
            .first()
        )

        # 日线序列（用于 MA/RSI/短中期收益）
        market_rows = (
            db.query(MarketData)
            .filter(MarketData.stock_id == stock.id, MarketData.date >= start_date)
            .order_by(MarketData.date.asc())
            .all()
        )
        closes = [float(row.close) for row in market_rows]

        # 周/月 K（用于更长周期趋势）
        weekly_rows = (
            db.query(StockKline)
            .filter(StockKline.stock_id == stock.id, StockKline.period == "weekly")
            .order_by(StockKline.trade_date.desc())
            .limit(12)
            .all()
        )
        monthly_rows = (
            db.query(StockKline)
            .filter(StockKline.stock_id == stock.id, StockKline.period == "monthly")
            .order_by(StockKline.trade_date.desc())
            .limit(12)
            .all()
        )

        # 近 30 天大宗交易
        block_trades = (
            db.query(BlockTradeRecord)
            .filter(
                BlockTradeRecord.stock_symbol == stock.symbol,
                BlockTradeRecord.trade_date >= target_date - timedelta(days=30),
            )
            .order_by(BlockTradeRecord.trade_date.desc())
            .limit(30)
            .all()
        )

        # 个股相关新闻/公告
        news_cutoff = datetime.combine(target_date, datetime.min.time()) - timedelta(days=7)
        news_docs = (
            db.query(Document)
            .filter(
                or_(Document.stock_id == stock.id, Document.stock_symbol == stock.symbol),
                Document.doc_type.in_(
                    [
                        "news",
                        "announcement",
                        "research_report",
                        "market_sentiment",
                        "peer_comparison",
                        "company_profile",
                        "financial_snapshot",
                        "business_composition",
                        "pledge_risk",
                    ]
                ),
                Document.published_at >= news_cutoff,
            )
            .order_by(Document.published_at.desc().nullslast(), Document.created_at.desc())
            .limit(30)
            .all()
        )

        # 宏观/政策类文档（独立表）
        macro_docs = (
            db.query(MacroNews)
            .order_by(MacroNews.published_at.desc().nullslast(), MacroNews.created_at.desc())
            .limit(30)
            .all()
        )
        # 市场总览层数据（来自 documents 表）
        market_docs = (
            db.query(Document)
            .filter(
                Document.stock_symbol.is_(None),
                Document.doc_type.in_(["market_overview", "market_heat"]),
            )
            .order_by(Document.published_at.desc().nullslast(), Document.created_at.desc())
            .limit(30)
            .all()
        )

        # 最新基本面快照 & 最近财务报表
        fundamental = (
            db.query(CompanyFundamental)
            .filter(CompanyFundamental.stock_id == stock.id)
            .order_by(CompanyFundamental.snapshot_date.desc())
            .first()
        )

        financial_rows = (
            db.query(CompanyFinancial)
            .filter(CompanyFinancial.stock_id == stock.id)
            .order_by(CompanyFinancial.report_date.desc(), CompanyFinancial.id.desc())
            .limit(60)
            .all()
        )
        financial_event_rows = (
            db.query(CompanyFinancialEvent)
            .filter(CompanyFinancialEvent.stock_id == stock.id)
            .order_by(CompanyFinancialEvent.event_date.desc(), CompanyFinancialEvent.id.desc())
            .limit(30)
            .all()
        )

        # 当前用户该股票持仓（仅 open）
        open_position = None
        if user_id is not None:
            open_position = (
                db.query(Position)
                .filter(
                    Position.user_id == user_id,
                    Position.stock_id == stock.id,
                    Position.status == "open",
                )
                .order_by(Position.updated_at.desc())
                .first()
            )

        latest_price_for_quant = (
            float(latest_quote.latest_price)
            if latest_quote and latest_quote.latest_price is not None
            else (float(latest_market.close) if latest_market and latest_market.close is not None else None)
        )
        quant_payload = quant_factor_engine.compute(
            db=db,
            stock_id=stock.id,
            symbol=stock.symbol,
            latest_price=latest_price_for_quant,
            market_rows=market_rows,
            weekly_rows=weekly_rows,
            latest_quote=latest_quote,
            financial_rows=financial_rows,
            fundamental=fundamental,
        )
        quant_factor_values = {
            str(item.get("name")): item.get("value")
            for item in (quant_payload.get("factors") or [])
            if isinstance(item, dict) and item.get("name")
        }
        company_pe = self._safe_float(quant_factor_values.get("PE"), None)
        company_pb = self._safe_float(quant_factor_values.get("PB"), None)
        industry_payload = industry_context_builder.build(
            symbol=stock.symbol,
            industry_name=(fundamental.industry if fundamental and fundamental.industry else stock.sector),
            company_pe=company_pe,
            company_pb=company_pb,
        )

        quant_factors_clean: list[dict[str, Any]] = []
        for item in quant_payload.get("factors") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = self._safe_float(item.get("value"), None)
            if not name or value is None:
                continue
            clean_item = dict(item)
            clean_item["name"] = name
            clean_item["value"] = value
            quant_factors_clean.append(clean_item)

        metric_snapshot_raw = quant_payload.get("metric_snapshot", {})
        metric_snapshot_clean = (
            self._compact_dict(metric_snapshot_raw, keep_keys={"symbol"})
            if isinstance(metric_snapshot_raw, dict)
            else {}
        )

        annual_series_clean: list[dict[str, Any]] = []
        for row in quant_payload.get("annual_series_3y") or []:
            if not isinstance(row, dict):
                continue
            clean_row = self._compact_dict(row, keep_keys={"year", "report_date"})
            metric_keys = [key for key in clean_row.keys() if key not in {"year", "report_date"}]
            if metric_keys:
                annual_series_clean.append(clean_row)

        financial_payload_rows: list[dict[str, Any]] = []
        for row in financial_rows:
            row_payload = {
                "report_date": row.report_date.isoformat(),
                "report_name": row.report_name,
                "report_type": row.report_type,
                "declare_date": row.declare_date.isoformat() if row.declare_date else None,
                "start_date": row.start_date.isoformat() if row.start_date else None,
                "end_date": row.end_date.isoformat() if row.end_date else None,
                "source": row.source,
                "dataset": row.dataset,
                "row_key": row.row_key,
                "object_id": row.object_id,
                "change_code": row.change_code,
                "eps": row.eps,
                "revenue": row.revenue,
                "net_profit": row.net_profit,
                "gross_margin": row.gross_margin,
                "roe": row.roe,
                "asset_liability_ratio": row.asset_liability_ratio,
                "operating_cashflow": row.operating_cashflow,
                "yoy_revenue": row.yoy_revenue,
                "yoy_net_profit": row.yoy_net_profit,
                "highlights": self._financial_row_highlights(row),
            }
            clean_row = self._compact_dict(
                row_payload,
                keep_keys={"report_date", "report_name", "report_type", "source", "dataset"},
            )
            financial_metric_keys = {
                "eps",
                "revenue",
                "net_profit",
                "gross_margin",
                "roe",
                "asset_liability_ratio",
                "operating_cashflow",
                "yoy_revenue",
                "yoy_net_profit",
                "highlights",
            }
            if any(key in clean_row for key in financial_metric_keys):
                financial_payload_rows.append(clean_row)

        financial_event_payload_rows: list[dict[str, Any]] = []
        for row in financial_event_rows:
            row_payload = {
                "event_date": row.event_date.isoformat(),
                "event_name": row.event_name,
                "event_type": row.event_type,
                "declare_date": row.declare_date.isoformat() if row.declare_date else None,
                "start_date": row.start_date.isoformat() if row.start_date else None,
                "end_date": row.end_date.isoformat() if row.end_date else None,
                "source": row.source,
                "dataset": row.dataset,
                "row_key": row.row_key,
                "object_id": row.object_id,
                "change_code": row.change_code,
                "raw": row.raw if isinstance(row.raw, dict) else {},
            }
            clean_row = self._compact_dict(
                row_payload,
                keep_keys={"event_date", "event_name", "event_type", "source", "dataset"},
            )
            financial_event_payload_rows.append(clean_row)

        context = {
            "stock": {
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "sector": stock.sector,
            },
            "as_of_date": target_date.isoformat(),
            "latest_quote": {
                "quote_time": latest_quote.quote_time.isoformat() if latest_quote else None,
                "latest_price": float(latest_quote.latest_price) if latest_quote else (float(latest_market.close) if latest_market else None),
                "change_pct": float(latest_quote.change_pct) if latest_quote and latest_quote.change_pct is not None else None,
                "volume": float(latest_quote.volume) if latest_quote and latest_quote.volume is not None else None,
                "amount": float(latest_quote.amount) if latest_quote and latest_quote.amount is not None else None,
            },
            "daily_kline": [
                {
                    "date": row.date.isoformat(),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in market_rows[-120:]
            ],
            "weekly_kline": [
                {
                    "date": row.trade_date.isoformat(),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in reversed(weekly_rows)
            ],
            "monthly_kline": [
                {
                    "date": row.trade_date.isoformat(),
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }
                for row in reversed(monthly_rows)
            ],
            "technical_stats": self._technical_stats(closes),
            "block_trades": [
                {
                    "trade_date": row.trade_date.isoformat(),
                    "deal_price": row.deal_price,
                    "premium_discount": row.premium_discount,
                    "volume": row.volume,
                    "amount": row.amount,
                }
                for row in block_trades
            ],
            "news": [self._serialize_doc(doc) for doc in news_docs],
            "macro": [
                {
                    "title": doc.title,
                    "content": (doc.content or "")[:400],
                    "source": doc.source,
                    "published_at": doc.published_at.isoformat() if doc.published_at else None,
                    "doc_type": "macro",
                    "metadata": doc.news_metadata or {},
                }
                for doc in macro_docs
            ],
            "market_overview": [self._serialize_doc(doc) for doc in market_docs],
            "fundamental": {
                "snapshot_date": fundamental.snapshot_date.isoformat() if fundamental else None,
                "industry": fundamental.industry if fundamental else None,
                "main_business": fundamental.main_business if fundamental else None,
                "business_scope": fundamental.business_scope if fundamental else None,
                "company_intro": fundamental.company_intro if fundamental else None,
            },
            "expert_score_formula": quant_payload.get("factor_formula"),
            "expert_score_weights": quant_payload.get("factor_weights"),
            "quant_factors": quant_factors_clean,
            "financial_metric_snapshot": metric_snapshot_clean,
            "financial_annual_series_3y": annual_series_clean,
            "industry_context": industry_payload,
            "financials": financial_payload_rows,
            "financial_events": financial_event_payload_rows,
            "position": {
                "quantity": open_position.quantity,
                "avg_price": open_position.avg_price,
                "updated_at": open_position.updated_at.isoformat(),
            }
            if open_position
            else None,
            # Data-link visibility: helps verify domain data is ready before expert calls.
            "data_coverage": {
                "news_count": len(news_docs),
                "macro_count": len(macro_docs),
                "market_overview_count": len(market_docs),
                "financial_count": len(financial_payload_rows),
                "financial_event_count": len(financial_event_payload_rows),
                "has_fundamental": bool(fundamental),
                "daily_kline_count": len(market_rows[-120:]),
                "quant_factor_count": len(quant_factors_clean),
                "industry_peer_count": int((industry_payload or {}).get("peer_count") or 0),
            },
        }
        return context

    def _legacy_fallback(self, db: Session, stock: Stock, profile: UserProfile | SimpleNamespace, expert: ExpertDef) -> dict[str, Any]:
        """
        专家级回退逻辑：调用 legacy 模块并转成 v2 标准字段。
        """
        self._log_fallback(
            scope="expert",
            symbol=stock.symbol,
            expert_key=expert.key,
            reason=f"legacy_runner={expert.legacy_key}",
        )
        runner = LEGACY_RUNNERS[expert.legacy_key]
        raw = runner(db, stock, profile)  # legacy payload
        score_100 = self._safe_float(raw.get("score"), 0.5) * 100
        signal = raw.get("signal", "neutral")
        signal = "buy" if signal in {"bullish", "buy"} else "sell" if signal in {"bearish", "sell"} else "hold"

        return {
            "signal": signal,
            "score": round(max(0.0, min(100.0, score_100)), 2),
            "confidence": round(max(0.0, min(1.0, self._safe_float(raw.get("confidence"), 0.3))), 4),
            "summary": "; ".join(raw.get("key_factors", [])[:2]) or f"{expert.label} fallback output",
            "key_points": raw.get("key_factors", []) or ["No structured factors from fallback"],
            "risks": raw.get("risk_flags", []) or ["fallback_mode"],
            "evidence": raw.get("evidence", []) or [],
            "fallback": True,
        }

    def _normalize_expert_output(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        标准化 LLM 专家输出，保证下游字段稳定且可展示。
        """
        signal = str(payload.get("signal", "hold")).strip().lower()
        if signal not in {"buy", "hold", "sell"}:
            signal = self._signal_from_score(self._safe_float(payload.get("score"), 50.0))

        score = max(0.0, min(100.0, self._safe_float(payload.get("score"), 50.0)))
        confidence = max(0.0, min(1.0, self._safe_float(payload.get("confidence"), 0.5)))
        summary = str(payload.get("summary", "")).replace("\n", " ").strip() or "No summary"
        thesis = str(payload.get("thesis", "")).replace("\n", " ").strip()

        # New schema supports structured key_points. Keep a text projection for legacy UI/storage.
        raw_key_points = payload.get("key_points") or []
        if not isinstance(raw_key_points, list):
            raw_key_points = [raw_key_points]
        key_points: list[str] = []
        key_points_structured: list[dict[str, str]] = []
        for item in raw_key_points[:8]:
            if isinstance(item, dict):
                fact = str(item.get("fact", "")).replace("\n", " ").strip()
                interpretation = str(item.get("interpretation", "")).replace("\n", " ").strip()
                investment_meaning = str(item.get("investment_meaning", "")).replace("\n", " ").strip()
                if not any([fact, interpretation, investment_meaning]):
                    continue
                key_points_structured.append(
                    {
                        "fact": fact,
                        "interpretation": interpretation,
                        "investment_meaning": investment_meaning,
                    }
                )
                key_points.append(
                    f"事实: {fact or '-'} | 解读: {interpretation or '-'} | 投资含义: {investment_meaning or '-'}"
                )
            else:
                text = str(item).replace("\n", " ").strip()
                if not text:
                    continue
                key_points.append(text)
                key_points_structured.append(
                    {"fact": text, "interpretation": "", "investment_meaning": ""}
                )
        key_points = key_points[:6]
        key_points_structured = key_points_structured[:6]
        if len(key_points) < 3:
            key_points.extend(
                ["Insufficient structured points", "Please verify with raw context", "Cross-check timeline consistency"]
            )
            key_points = key_points[:3]

        # New schema supports structured risks. Keep a text projection for legacy UI/storage.
        raw_risks = payload.get("risks") or []
        if not isinstance(raw_risks, list):
            raw_risks = [raw_risks]
        risks: list[str] = []
        risks_structured: list[dict[str, str]] = []
        for item in raw_risks[:8]:
            if isinstance(item, dict):
                risk_name = str(item.get("risk", "")).replace("\n", " ").strip()
                trigger = str(item.get("trigger", "")).replace("\n", " ").strip()
                impact = str(item.get("impact", "")).replace("\n", " ").strip()
                if not any([risk_name, trigger, impact]):
                    continue
                risks_structured.append({"risk": risk_name, "trigger": trigger, "impact": impact})
                risks.append(f"风险: {risk_name or '-'} | 触发: {trigger or '-'} | 后果: {impact or '-'}")
            else:
                text = str(item).replace("\n", " ").strip()
                if not text:
                    continue
                risks.append(text)
                risks_structured.append({"risk": text, "trigger": "", "impact": ""})
        risks = risks[:6]
        risks_structured = risks_structured[:6]
        if len(risks) < 2:
            risks.extend(["Model uncertainty", "Execution risk"])

        evidence = payload.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        cleaned_evidence = []
        for item in evidence[:8]:
            if isinstance(item, dict):
                cleaned_evidence.append(
                    {
                        "type": str(item.get("type", "context")),
                        "detail": str(item.get("detail", ""))[:220],
                    }
                )
        while len(cleaned_evidence) < 4:
            cleaned_evidence.append({"type": "context", "detail": "insufficient evidence returned"})

        return {
            "signal": signal,
            "score": round(score, 2),
            "confidence": round(confidence, 4),
            "summary": summary,
            "thesis": thesis,
            "key_points": key_points,
            "key_points_structured": key_points_structured,
            "risks": risks,
            "risks_structured": risks_structured,
            "evidence": cleaned_evidence,
        }

    @staticmethod
    def _build_expert_prompt_context(expert_key: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        Build domain-scoped prompt context.
        Each expert only receives data slices related to its research scope.
        """
        common = {
            "stock": context.get("stock"),
            "latest_quote": context.get("latest_quote"),
        }
        daily_tail = context.get("daily_kline", [])[-90:]
        weekly = context.get("weekly_kline", [])
        monthly = context.get("monthly_kline", [])
        technical = context.get("technical_stats", {})
        block_trades = context.get("block_trades", [])
        news = context.get("news", [])
        macro = context.get("macro", [])
        market_overview = context.get("market_overview", [])
        financials = context.get("financials", [])
        financial_events = context.get("financial_events", [])
        fundamental = context.get("fundamental", {})

        if expert_key == "news":
            return {
                **common,
                "focus": "company_news_and_disclosures",
                "news": [
                    item
                    for item in news
                    if item.get("doc_type") in {"news", "announcement", "research_report", "market_sentiment"}
                ][:30],
            }
        if expert_key == "stock_data":
            return {
                **common,
                "focus": "kline_volume_momentum",
                "technical_stats": technical,
                "daily_kline_tail": daily_tail,
                "weekly_kline": weekly,
                "monthly_kline": monthly,
                "block_trades": block_trades[:40],
            }
        if expert_key == "macro":
            return {
                **common,
                "focus": "macro_policy_liquidity",
                "macro": macro[:40],
                "market_overview": market_overview[:30],
                "industry_context": context.get("industry_context", {}),
            }
        if expert_key == "financial":
            return {
                **common,
                "focus": "financial_statement_quality",
                "financials": financials[:40],
                "financial_events": financial_events[:30],
                "news": [item for item in news if item.get("doc_type") in {"financial_snapshot", "announcement"}][:20],
                "quant_factors": context.get("quant_factors", [])[:40],
                "expert_score_formula": context.get("expert_score_formula"),
                "expert_score_weights": context.get("expert_score_weights", {}),
                "financial_metric_snapshot": context.get("financial_metric_snapshot", {}),
                "financial_annual_series_3y": context.get("financial_annual_series_3y", []),
                "industry_context": context.get("industry_context", {}),
            }
        if expert_key == "fundamental":
            return {
                **common,
                "focus": "business_quality_and_competitiveness",
                "fundamental": fundamental,
                "news": [
                    item
                    for item in news
                    if item.get("doc_type") in {"company_profile", "business_composition", "peer_comparison", "news"}
                ][:20],
                "financials_brief": financials[:12],
                "industry_context": context.get("industry_context", {}),
            }
        return {
            **common,
            "technical_stats": technical,
            "daily_kline_tail": daily_tail,
            "weekly_kline": weekly,
            "monthly_kline": monthly,
            "block_trades": block_trades[:30],
            "news": news[:20],
            "macro": macro[:20],
            "market_overview": market_overview[:20],
            "fundamental": fundamental,
            "financials": financials[:10],
            "financial_events": financial_events[:10],
            "quant_factors": context.get("quant_factors", [])[:20],
            "industry_context": context.get("industry_context", {}),
        }

    @staticmethod
    def _hash_payload(payload: Any) -> str:
        """
        Stable hash for prompt-scoped data slices.

        This hash is used to decide whether each expert needs re-run.
        """
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _build_domain_fingerprints(self, context: dict[str, Any]) -> dict[str, str]:
        """
        Build one fingerprint per expert from its scoped context.
        """
        fingerprints: dict[str, str] = {}
        for expert in EXPERTS:
            scoped = self._build_expert_prompt_context(expert.key, context)
            fingerprints[expert.key] = self._hash_payload(scoped)
        return fingerprints

    def _build_investment_signature(
        self,
        *,
        experts_payload: dict[str, dict[str, Any]],
        aggregate: dict[str, Any],
        context: dict[str, Any],
        profile: UserProfile | SimpleNamespace,
        run_context: str,
        investment_scope_signature: str | None = None,
    ) -> str:
        """
        Build a deterministic fingerprint for investment-advice inputs.

        Why:
        - five experts may be unchanged and reusable,
        - but investment advice still needs refresh when user scope changes
          (position/trades/profile) or aggregate changes.
        """
        profile_payload = {
            "risk_level": getattr(profile, "risk_level", "medium"),
            "investment_horizon": getattr(profile, "investment_horizon", "medium"),
            "style": getattr(profile, "style", "balanced"),
            "assets": self._safe_float(getattr(profile, "assets", 0.0), 0.0),
            "max_single_position": self._safe_float(getattr(profile, "max_single_position", 0.15), 0.15),
            "risk_budget": self._safe_float(getattr(profile, "risk_budget", 0.02), 0.02),
            "persona": getattr(profile, "persona", "balanced_growth"),
            "questionnaire_answers": getattr(profile, "questionnaire_answers", {}) or {},
        }
        position_payload = context.get("position") if isinstance(context.get("position"), dict) else {}
        latest_quote = context.get("latest_quote") if isinstance(context.get("latest_quote"), dict) else {}
        compact_experts: dict[str, dict[str, Any]] = {}
        for key, value in experts_payload.items():
            if not isinstance(value, dict):
                continue
            compact_experts[key] = {
                "signal": value.get("signal"),
                "score": value.get("score"),
                "confidence": value.get("confidence"),
                "summary": value.get("summary"),
                "thesis": value.get("thesis"),
                "key_points": value.get("key_points") or [],
                "risks": value.get("risks") or [],
                "evidence": value.get("evidence") or [],
            }
        payload = {
            "run_context": run_context,
            "profile": profile_payload,
            "position": position_payload,
            "latest_quote": {
                "latest_price": latest_quote.get("latest_price"),
                "quote_time": latest_quote.get("quote_time"),
            },
            "aggregate": aggregate,
            "experts": compact_experts,
            # Upstream may provide an explicit user-scope signature
            # (e.g., profile/trade/position hash) to force recomputation.
            "scope_signature": investment_scope_signature or "",
        }
        return self._hash_payload(payload)

    def _call_expert_llm(self, expert: ExpertDef, context: dict[str, Any]) -> dict[str, Any]:
        """
        调用 LLM 执行单专家分析。

        目前只给对应专家喂单个方面的专家的数据

        注意这里只喂“裁剪后的上下文”，避免 prompt 过重。
        """
        prompt_context = {
            "expert": expert.label,
            "focus": expert.key,
            **self._build_expert_prompt_context(expert.key, context),
        }

        logger.info(
            "expert llm start | symbol=%s expert=%s news=%d macro=%d market_overview=%d financial_rows=%d financial_events=%d",
            (context.get("stock") or {}).get("symbol"),
            expert.key,
            len(prompt_context.get("news") or []),
            len(prompt_context.get("macro") or []),
            len(prompt_context.get("market_overview") or []),
            len(prompt_context.get("financials") or []),
            len(prompt_context.get("financial_events") or []),
        )
        started_at = time.perf_counter()
        payload = zhipu_client.chat_json(
            system_prompt=get_expert_system_prompt(expert.key),
            user_prompt=build_expert_user_prompt(expert.key, prompt_context),
            temperature=0.15,
            role=expert.key,
            strict_json=(expert.key == "macro"),
        )
        logger.info(
            "expert llm done | symbol=%s expert=%s elapsed_ms=%s",
            (context.get("stock") or {}).get("symbol"),
            expert.key,
            int((time.perf_counter() - started_at) * 1000),
        )
        normalized = self._normalize_expert_output(payload)
        if expert.key == "financial":
            normalized["quant_factors"] = prompt_context.get("quant_factors", [])[:40]
            normalized["expert_score_formula"] = prompt_context.get("expert_score_formula")
            normalized["expert_score_weights"] = prompt_context.get("expert_score_weights", {})
            normalized["financial_metric_snapshot"] = prompt_context.get("financial_metric_snapshot", {})
            normalized["financial_annual_series_3y"] = prompt_context.get("financial_annual_series_3y", [])
            normalized["industry_context"] = prompt_context.get("industry_context", {})
        return normalized

    def _to_legacy_signal(self, expert_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        把 v2 输出转换成 fuse_signals 能吃的 legacy 信号结构。
        """
        signal = payload["signal"]
        legacy_signal = "bullish" if signal == "buy" else "bearish" if signal == "sell" else "neutral"
        return {
            "expert_name": "technical" if expert_key == "stock_data" else expert_key,
            "signal": legacy_signal,
            "score": payload["score"] / 100.0,
            "confidence": payload["confidence"],
            "horizon": "multi",
            "key_factors": payload["key_points"],
            "risk_flags": payload["risks"],
            "evidence": payload["evidence"],
        }

    def _investment_fallback(
        self,
        profile: UserProfile | SimpleNamespace,
        experts_payload: dict[str, dict[str, Any]],
        current_price: float,
        position: dict[str, Any] | None,
        *,
        symbol: str = "",
    ) -> dict[str, Any]:
        """
        投资专家回退逻辑：基于 fuse_signals 生成交易计划。
        """
        self._log_fallback(
            scope="investment",
            symbol=symbol or "unknown",
            expert_key="investment",
            reason="engine_fuse_signals",
        )
        legacy_signals = [self._to_legacy_signal(key, value) for key, value in experts_payload.items()]
        fused = fuse_signals(profile, legacy_signals, current_price=current_price, position=position)
        advice = fused.get("rationale", {}).get("trade_advice", {})

        final_signal = "buy" if fused.get("action") == "buy" else "sell" if fused.get("action") == "sell" else "hold"
        has_position = bool(
            isinstance(position, dict) and self._safe_float(position.get("quantity"), 0.0) > 0
        )
        if not has_position and final_signal != "buy":
            final_signal = "not_buy"
        confidence = max(0.25, min(0.95, float(fused.get("rationale", {}).get("fused_score", 0.5))))

        entry_range = advice.get("entry_range") or [
            round(current_price * 0.98, 4),
            round(current_price * 1.02, 4),
        ]
        if not isinstance(entry_range, list) or len(entry_range) != 2:
            entry_range = [round(current_price * 0.98, 4), round(current_price * 1.02, 4)]

        position_ratio = float(fused.get("position_size", 0.1))
        capital_to_use = round(float(profile.assets) * position_ratio, 2)
        suggested_shares = int(advice.get("suggested_buy_shares") or advice.get("suggested_sell_shares") or 0)

        take_profit = advice.get("take_profit_price")
        stop_loss = advice.get("stop_loss_price")

        return {
            "final_signal": final_signal,
            "signal": final_signal,
            "score": round(float(fused.get("rationale", {}).get("fused_score", 50.0)) * 100, 2),
            "confidence": round(confidence, 4),
            "summary": fused.get("rationale", {}).get("decision_note", "fallback investment advice"),
            "explanation_steps": [
                f"综合得分={fused.get('rationale', {}).get('fused_score')}，基础动作={fused.get('action')}",
                "结合用户风险预算与单票仓位上限计算仓位建议",
                "根据当前价格区间给出分批建仓、止盈、止损与动态调整规则",
            ],
            "buy_strategy": {
                "conditions": [
                    "股价进入建议区间并确认量价配合",
                    "五专家综合评分维持在阈值以上",
                ],
                "price_range": entry_range,
                "staged_entry": [
                    "首笔建仓40%",
                    "回踩不破支撑再加仓30%",
                    "趋势确认后完成剩余30%",
                ],
            },
            "position_management": {
                "position_ratio": round(position_ratio, 4),
                "capital_to_use": capital_to_use,
                "suggested_shares": suggested_shares,
            },
            "take_profit_plan": [
                {
                    "target_price": take_profit,
                    "sell_ratio": 0.5,
                    "condition": "达到第一目标位后分批止盈",
                },
                {
                    "target_price": round(float(take_profit) * 1.08, 4) if take_profit else None,
                    "sell_ratio": 0.5,
                    "condition": "趋势延续时继续减仓",
                },
            ],
            "breakeven_plan": {
                "trigger_gain_pct": 0.08,
                "sell_ratio": 0.35,
                "note": "达到阶段涨幅后回收部分本金，降低剩余仓位风险",
            },
            "stop_loss_plan": {
                "stop_loss_price": stop_loss,
                "hard_exit_condition": "跌破止损位且成交量放大时执行止损",
            },
            "dynamic_adjustment": [
                "若宏观和新闻发生反向变化，调低仓位",
                "若财务/基本面更新明显改善，可上调目标位",
            ],
            "risk_warnings": fused.get("risk_notes", []),
            "wait_conditions": [
                "价格回到买入区间并出现量价确认" if final_signal in {"not_buy", "hold"} else "持续跟踪趋势和公告变化",
            ],
            "execution_logic": [
                {"title": "综合判断", "content": str(fused.get("rationale", {}).get("decision_note") or "")},
                {"title": "仓位规则", "content": "使用用户风险预算、单票上限和建议股数约束执行仓位"},
                {"title": "风险控制", "content": "按止损与分批止盈规则执行，避免情绪化加仓"},
            ],
            "expert_synthesis": {
                "bullish_factors": [
                    str(v.get("summary") or "")
                    for v in experts_payload.values()
                    if str(v.get("signal") or "").lower() == "buy"
                ][:5],
                "bearish_factors": [
                    str(v.get("summary") or "")
                    for v in experts_payload.values()
                    if str(v.get("signal") or "").lower() == "sell"
                ][:5],
                "conflicts": [
                    "Signals are conflicting across experts; lower confidence and execute conservatively."
                ]
                if len({str(v.get("signal") or "").lower() for v in experts_payload.values()}) > 1
                else [],
            },
            "raw_fused": fused,
        }

    def _call_investment_llm(
        self,
        *,
        context: dict[str, Any],
        experts_payload: dict[str, dict[str, Any]],
        aggregate: dict[str, Any],
        profile: UserProfile | SimpleNamespace,
    ) -> dict[str, Any]:
        """
        调用投资专家 LLM 生成最终投资策略。

        这里会显式注入每位专家的完整观点（结论 + 解释 + 证据 + 风险），
        避免投资专家只读到分数而忽略分析文本。
        """
        # Build rich expert packets for the investment model:
        # each expert includes text reasoning and structured evidence, not score only.
        expert_packets: dict[str, dict[str, Any]] = {}
        expert_opinion_digest: list[dict[str, Any]] = []
        for expert_key, payload in experts_payload.items():
            if not isinstance(payload, dict):
                continue
            packet = {
                "signal": payload.get("signal"),
                "score": payload.get("score"),
                "confidence": payload.get("confidence"),
                "summary": payload.get("summary"),
                "thesis": payload.get("thesis"),
                "key_points": payload.get("key_points") or [],
                "key_points_structured": payload.get("key_points_structured") or [],
                "risks": payload.get("risks") or [],
                "risks_structured": payload.get("risks_structured") or [],
                "evidence": payload.get("evidence") or [],
            }
            expert_packets[expert_key] = packet
            expert_opinion_digest.append(
                {
                    "expert": expert_key,
                    "signal": packet["signal"],
                    "score": packet["score"],
                    "confidence": packet["confidence"],
                    "thesis": packet["thesis"],
                    "summary": packet["summary"],
                }
            )

        # Also provide each expert's scoped data slice used during its reasoning,
        # so investment LLM can cross-check "观点 -> 数据依据" directly.
        expert_scoped_context = {
            expert.key: self._build_expert_prompt_context(expert.key, context)
            for expert in EXPERTS
        }

        investment_context = {
            "stock": context.get("stock"),
            "latest_quote": context.get("latest_quote"),
            "position": context.get("position"),
            "holding_state": {
                "has_position": bool(
                    isinstance(context.get("position"), dict)
                    and self._safe_float((context.get("position") or {}).get("quantity"), 0.0) > 0
                ),
                "position_quantity": self._safe_float((context.get("position") or {}).get("quantity"), 0.0)
                if isinstance(context.get("position"), dict)
                else 0.0,
            },
            "user_profile": {
                "risk_level": profile.risk_level,
                "investment_horizon": profile.investment_horizon,
                "style": profile.style,
                "assets": profile.assets,
                "max_single_position": profile.max_single_position,
                "risk_budget": profile.risk_budget,
                "persona": getattr(profile, "persona", "balanced_growth"),
                "questionnaire_answers": getattr(profile, "questionnaire_answers", {}),
            },
            "experts": expert_packets,
            "expert_opinion_digest": expert_opinion_digest,
            "expert_scoped_context": expert_scoped_context,
            "aggregate": aggregate,
        }

        logger.info(
            "investment llm start | symbol=%s experts=%s digest=%s",
            (context.get("stock") or {}).get("symbol"),
            sorted(expert_packets.keys()),
            len(expert_opinion_digest),
        )
        started_at = time.perf_counter()
        payload = zhipu_client.chat_json(
            system_prompt=INVESTMENT_SYSTEM_PROMPT,
            user_prompt=build_investment_user_prompt(investment_context),
            temperature=0.15,
            role="investment",
            strict_json=True,
        )
        logger.info(
            "investment llm done | symbol=%s elapsed_ms=%s",
            (context.get("stock") or {}).get("symbol"),
            int((time.perf_counter() - started_at) * 1000),
        )

        # 关键字段归一化，防止模型输出漂移影响前端展示
        final_signal = str(payload.get("final_signal") or payload.get("signal") or "hold").lower()
        if final_signal not in {"buy", "hold", "reduce", "sell", "not_buy"}:
            final_signal = "hold"
        payload["final_signal"] = final_signal
        payload["signal"] = final_signal
        payload["score"] = max(0.0, min(100.0, self._safe_float(payload.get("score"), aggregate.get("total_score", 50.0))))
        payload["confidence"] = max(0.0, min(1.0, self._safe_float(payload.get("confidence"), 0.5)))
        payload["summary"] = str(payload.get("summary", "")).replace("\n", " ").strip() or "No summary"
        explanation_steps = payload.get("explanation_steps") or []
        if not isinstance(explanation_steps, list):
            explanation_steps = [str(explanation_steps)]
        explanation_steps = [str(item).replace("\n", " ").strip() for item in explanation_steps if str(item).strip()][:6]
        if len(explanation_steps) < 3:
            explanation_steps.extend(
                [
                    "整合五专家方向与置信度，形成初步买卖倾向",
                    "结合用户可支配资金、仓位上限和风险预算约束仓位",
                    "输出分批交易与止盈止损规则，并给出动态调整条件",
                ][: 3 - len(explanation_steps)]
            )
        payload["explanation_steps"] = explanation_steps
        return payload

    @staticmethod
    def _allow_sell_signal(*, run_context: str, position: dict[str, Any] | None) -> bool:
        if not isinstance(position, dict):
            return False
        try:
            return float(position.get("quantity") or 0) > 0
        except Exception:
            return False

    def _apply_signal_policy(
        self,
        *,
        run_context: str,
        context: dict[str, Any],
        experts_payload: dict[str, dict[str, Any]],
        aggregate: dict[str, Any],
        investment_payload: dict[str, Any],
    ) -> None:
        allow_sell = self._allow_sell_signal(run_context=run_context, position=context.get("position"))
        if allow_sell:
            return

        for payload in experts_payload.values():
            if payload.get("signal") == "sell":
                payload["signal"] = "hold"
                payload["summary"] = f"{payload.get('summary', '')} (no-position sell filtered to hold)".strip()

        if aggregate.get("recommendation_action") == "sell":
            aggregate["recommendation_action"] = "hold"
            aggregate["conflict_note"] = f"{aggregate.get('conflict_note', '')} | no-position sell filtered to hold".strip()

        if str(investment_payload.get("final_signal") or "").strip().lower() in {"sell", "reduce", "hold"}:
            investment_payload["final_signal"] = "not_buy"
            risk_warnings = investment_payload.get("risk_warnings")
            if not isinstance(risk_warnings, list):
                risk_warnings = []
            risk_warnings.append("Current context has no sellable position; sell action is filtered to hold/not-buy.")
            investment_payload["risk_warnings"] = risk_warnings

    def analyze_stock(
        self,
        db: Session,
        *,
        stock_symbol: str,
        profile: UserProfile | None = None,
        run_context: str = "query",
        as_of_date: date | None = None,
        user_id: int | None = None,
        reuse_cache: dict[str, Any] | None = None,
        investment_scope_signature: str | None = None,
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        """
        编排器主入口：执行“5 专家 + 投资专家”的完整流程。

        流程：
        1) 股票校验与上下文构建
        2) 依次运行五位专家（LLM 失败自动 fallback）
        3) 计算总分/冲突
        4) 运行投资专家（LLM 失败自动 fallback）
        """
        stock = self._get_or_create_stock(db, stock_symbol)
        logger.info(
            "analysis start | symbol=%s run_context=%s llm_enabled=%s",
            stock.symbol,
            run_context,
            zhipu_client.enabled,
        )
        profile_obj: UserProfile | SimpleNamespace = profile or self._default_profile()
        context = self.build_context(db, stock=stock, as_of_date=as_of_date, user_id=user_id)
        coverage = context.get("data_coverage") if isinstance(context.get("data_coverage"), dict) else {}
        logger.info(
            "analysis context ready | symbol=%s daily=%s news=%s macro=%s market_overview=%s financial=%s financial_events=%s has_fundamental=%s",
            stock.symbol,
            coverage.get("daily_kline_count", 0),
            coverage.get("news_count", 0),
            coverage.get("macro_count", 0),
            coverage.get("market_overview_count", 0),
            coverage.get("financial_count", 0),
            coverage.get("financial_event_count", 0),
            coverage.get("has_fundamental", False),
        )

        # Per-expert cache reuse:
        # if one expert's scoped data fingerprint has not changed, reuse last result directly.
        previous_experts = {}
        previous_fingerprints = {}
        previous_investment: dict[str, Any] = {}
        previous_investment_signature: str | None = None
        if isinstance(reuse_cache, dict):
            if isinstance(reuse_cache.get("experts"), dict):
                previous_experts = reuse_cache.get("experts") or {}
            if isinstance(reuse_cache.get("domain_fingerprints"), dict):
                previous_fingerprints = reuse_cache.get("domain_fingerprints") or {}
            if isinstance(reuse_cache.get("investment"), dict):
                previous_investment = reuse_cache.get("investment") or {}
            if isinstance(reuse_cache.get("cache_meta"), dict):
                cached_sig = reuse_cache.get("cache_meta", {}).get("investment_signature")
                if isinstance(cached_sig, str) and cached_sig.strip():
                    previous_investment_signature = cached_sig.strip()
            if not previous_investment_signature:
                cached_sig = reuse_cache.get("investment_signature")
                if isinstance(cached_sig, str) and cached_sig.strip():
                    previous_investment_signature = cached_sig.strip()

        domain_fingerprints = self._build_domain_fingerprints(context)
        experts_payload: dict[str, dict[str, Any]] = {}
        experts_to_run: list[ExpertDef] = []
        reused_experts: list[str] = []
        for expert in EXPERTS:
            previous_payload = previous_experts.get(expert.key)
            previous_fp = previous_fingerprints.get(expert.key)
            current_fp = domain_fingerprints.get(expert.key)
            if isinstance(previous_payload, dict) and previous_fp and current_fp and previous_fp == current_fp:
                normalized = self._normalize_expert_output(previous_payload)
                normalized["fallback"] = bool(previous_payload.get("fallback", False))
                normalized["reused"] = True
                normalized["expert_name"] = expert.key
                normalized["expert_label"] = expert.label
                if expert.key == "financial":
                    normalized["quant_factors"] = context.get("quant_factors", [])[:40]
                    normalized["expert_score_formula"] = context.get("expert_score_formula")
                    normalized["expert_score_weights"] = context.get("expert_score_weights", {})
                    normalized["financial_metric_snapshot"] = context.get("financial_metric_snapshot", {})
                    normalized["financial_annual_series_3y"] = context.get("financial_annual_series_3y", [])
                    normalized["industry_context"] = context.get("industry_context", {})
                experts_payload[expert.key] = normalized
                reused_experts.append(expert.key)
            else:
                experts_to_run.append(expert)

        logger.info(
            "expert cache decision | symbol=%s reused=%s rerun=%s",
            stock.symbol,
            reused_experts,
            [item.key for item in experts_to_run],
        )

        if zhipu_client.enabled:
            worker_count = max(
                1,
                min(
                    max(1, len(experts_to_run)),
                    int(getattr(settings, "expert_parallel_workers", len(EXPERTS)) or len(EXPERTS)),
                ),
            )
            logger.info(
                "expert llm parallel start | symbol=%s workers=%s experts=%s",
                stock.symbol,
                worker_count,
                [expert.key for expert in experts_to_run],
            )
            future_map: dict[Any, tuple[int, ExpertDef]] = {}
            for index, expert in enumerate(EXPERTS, start=1):
                if callable(progress_callback):
                    progress_callback(
                        {
                            "phase": "expert_start",
                            "expert_key": expert.key,
                            "expert_label": expert.label,
                            "expert_index": index,
                            "expert_total": len(EXPERTS),
                        }
                    )
                if expert.key in reused_experts and callable(progress_callback):
                    # reused experts are considered complete immediately
                    progress_callback(
                        {
                            "phase": "expert_done",
                            "expert_key": expert.key,
                            "expert_label": expert.label,
                            "expert_index": len(experts_payload),
                            "expert_order": index,
                            "expert_total": len(EXPERTS),
                            "reused": True,
                        }
                    )
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="expert-llm") as executor:
                for index, expert in enumerate(EXPERTS, start=1):
                    if expert.key in reused_experts:
                        continue
                    future = executor.submit(self._call_expert_llm, expert, context)
                    future_map[future] = (index, expert)

                done_count = len(experts_payload)
                for future in as_completed(future_map):
                    index, expert = future_map[future]
                    try:
                        result = future.result()
                        result["fallback"] = False
                    except (LLMClientError, Exception) as exc:
                        logger.exception(
                            "expert llm failed, fallback to legacy | symbol=%s expert=%s error=%s",
                            stock.symbol,
                            expert.key,
                            exc,
                        )
                        self._log_fallback(
                            scope="expert",
                            symbol=stock.symbol,
                            expert_key=expert.key,
                            reason=f"llm_error={type(exc).__name__}",
                        )
                        # Fallback uses DB session, so keep this in the main thread.
                        result = self._legacy_fallback(db, stock, profile_obj, expert)

                    result["expert_name"] = expert.key
                    result["expert_label"] = expert.label
                    if expert.key == "financial":
                        result["quant_factors"] = context.get("quant_factors", [])[:40]
                        result["expert_score_formula"] = context.get("expert_score_formula")
                        result["expert_score_weights"] = context.get("expert_score_weights", {})
                        result["financial_metric_snapshot"] = context.get("financial_metric_snapshot", {})
                        result["financial_annual_series_3y"] = context.get("financial_annual_series_3y", [])
                        result["industry_context"] = context.get("industry_context", {})
                    experts_payload[expert.key] = result
                    done_count += 1
                    if callable(progress_callback):
                        progress_callback(
                            {
                                "phase": "expert_done",
                                "expert_key": expert.key,
                                "expert_label": expert.label,
                                "expert_index": done_count,
                                "expert_order": index,
                                "expert_total": len(EXPERTS),
                            }
                        )
        else:
            for index, expert in enumerate(EXPERTS, start=1):
                if callable(progress_callback):
                    progress_callback(
                        {
                            "phase": "expert_start",
                            "expert_key": expert.key,
                            "expert_label": expert.label,
                            "expert_index": index,
                            "expert_total": len(EXPERTS),
                        }
                    )
                if expert.key in reused_experts and callable(progress_callback):
                    progress_callback(
                        {
                            "phase": "expert_done",
                            "expert_key": expert.key,
                            "expert_label": expert.label,
                            "expert_index": len(experts_payload),
                            "expert_total": len(EXPERTS),
                            "reused": True,
                        }
                    )
                    continue
                logger.info(
                    "expert llm disabled, fallback directly | symbol=%s expert=%s",
                    stock.symbol,
                    expert.key,
                )
                self._log_fallback(
                    scope="expert",
                    symbol=stock.symbol,
                    expert_key=expert.key,
                    reason="llm_disabled",
                )
                result = self._legacy_fallback(db, stock, profile_obj, expert)
                result["expert_name"] = expert.key
                result["expert_label"] = expert.label
                if expert.key == "financial":
                    result["quant_factors"] = context.get("quant_factors", [])[:40]
                    result["expert_score_formula"] = context.get("expert_score_formula")
                    result["expert_score_weights"] = context.get("expert_score_weights", {})
                    result["financial_metric_snapshot"] = context.get("financial_metric_snapshot", {})
                    result["financial_annual_series_3y"] = context.get("financial_annual_series_3y", [])
                    result["industry_context"] = context.get("industry_context", {})
                experts_payload[expert.key] = result
                if callable(progress_callback):
                    progress_callback(
                        {
                            "phase": "expert_done",
                            "expert_key": expert.key,
                            "expert_label": expert.label,
                            "expert_index": len(experts_payload),
                            "expert_total": len(EXPERTS),
                        }
                    )

        # Keep canonical expert order for downstream aggregation/rendering.
        experts_payload = {expert.key: experts_payload[expert.key] for expert in EXPERTS if expert.key in experts_payload}

        # 加权总分 + 加权置信度
        weighted_score = 0.0
        weighted_confidence = 0.0
        for key, payload in experts_payload.items():
            weight = EXPERT_WEIGHTS[key]
            weighted_score += payload["score"] * weight
            weighted_confidence += payload["confidence"] * weight

        # 冲突检测：数据驱动（stock_data）vs 情绪驱动（news + macro）
        data_drive_score = experts_payload["stock_data"]["score"]
        emotion_drive_score = (experts_payload["news"]["score"] + experts_payload["macro"]["score"]) / 2
        conflict_signal = self._direction(data_drive_score) * self._direction(emotion_drive_score) < 0

        total_score = round(weighted_score, 2)
        recommendation_action = self._signal_from_score(total_score)
        recommendation_confidence = round(max(0.0, min(1.0, weighted_confidence)), 4)

        aggregate = {
            "total_score": total_score,
            "recommendation_action": recommendation_action,
            "recommendation_confidence": recommendation_confidence,
            "data_drive_score": round(data_drive_score, 2),
            "emotion_drive_score": round(emotion_drive_score, 2),
            "conflict_signal": conflict_signal,
            "score_breakdown": {k: round(v["score"], 2) for k, v in experts_payload.items()},
            "conflict_note": "数据驱动与情绪驱动信号冲突，请谨慎" if conflict_signal else "数据与情绪方向一致",
        }

        investment_signature = self._build_investment_signature(
            experts_payload=experts_payload,
            aggregate=aggregate,
            context=context,
            profile=profile_obj,
            run_context=run_context,
            investment_scope_signature=investment_scope_signature,
        )
        can_reuse_investment = bool(
            isinstance(previous_investment, dict)
            and previous_investment
            and previous_investment_signature
            and previous_investment_signature == investment_signature
        )

        latest_price = context.get("latest_quote", {}).get("latest_price") or 1.0
        current_price = self._safe_float(latest_price, 1.0)
        position = context.get("position")

        if callable(progress_callback):
            progress_callback({"phase": "investment_start"})

        if can_reuse_investment:
            logger.info(
                "investment cache reused | symbol=%s signature=%s",
                stock.symbol,
                investment_signature[:12],
            )
            investment_payload = dict(previous_investment)
            investment_payload["reused"] = True
            investment_payload["fallback"] = bool(previous_investment.get("fallback", False))
        else:
            if zhipu_client.enabled:
                try:
                    investment_payload = self._call_investment_llm(
                        context=context,
                        experts_payload=experts_payload,
                        aggregate=aggregate,
                        profile=profile_obj,
                    )
                    investment_payload["fallback"] = False
                except (LLMClientError, Exception) as exc:
                    logger.exception(
                        "investment llm failed, fallback to engine | symbol=%s error=%s",
                        stock.symbol,
                        exc,
                    )
                    investment_payload = self._investment_fallback(
                        profile_obj,
                        experts_payload,
                        current_price,
                        position,
                        symbol=stock.symbol,
                    )
                    investment_payload["fallback"] = True
            else:
                logger.info("investment llm disabled, fallback directly | symbol=%s", stock.symbol)
                self._log_fallback(
                    scope="investment",
                    symbol=stock.symbol,
                    expert_key="investment",
                    reason="llm_disabled",
                )
                investment_payload = self._investment_fallback(
                    profile_obj,
                    experts_payload,
                    current_price,
                    position,
                    symbol=stock.symbol,
                )
                investment_payload["fallback"] = True
            investment_payload["reused"] = False

        if callable(progress_callback):
            progress_callback({"phase": "investment_done"})

        self._apply_signal_policy(
            run_context=run_context,
            context=context,
            experts_payload=experts_payload,
            aggregate=aggregate,
            investment_payload=investment_payload,
        )

        result = {
            "stock_symbol": stock.symbol,
            "run_context": run_context,
            "generated_at": datetime.utcnow().isoformat(),
            "context": {
                "latest_quote": context.get("latest_quote"),
                "technical_stats": context.get("technical_stats"),
                "position": context.get("position"),
            },
            "experts": experts_payload,
            "domain_fingerprints": domain_fingerprints,
            "cache_meta": {
                "reused_experts": reused_experts,
                "rerun_experts": [item.key for item in experts_to_run],
                "reused_investment": can_reuse_investment,
                "rerun_investment": not can_reuse_investment,
                "investment_signature": investment_signature,
            },
            "aggregate": aggregate,
            "investment": investment_payload,
        }
        logger.info(
            "analysis done | symbol=%s total_score=%s action=%s conflict=%s",
            stock.symbol,
            aggregate.get("total_score"),
            aggregate.get("recommendation_action"),
            aggregate.get("conflict_signal"),
        )
        return result


expert_orchestrator = ExpertOrchestrator()
