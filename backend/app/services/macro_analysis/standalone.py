from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.macro_news import MacroNews
from app.services.data_ingest import akshare_service
from app.services.experts_v2.prompts import (
    MACRO_STANDALONE_SYSTEM_PROMPT,
    build_macro_standalone_user_prompt,
)
from app.services.llm import LLMClientError, zhipu_client


logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        parsed = float(value)
        if parsed != parsed:
            return default
        return parsed
    except Exception:
        return default


class MacroStandaloneService:
    def _ak(self):
        try:
            import akshare as ak  # type: ignore
        except Exception:
            return None
        return ak

    def _latest_macro_rows(self, db: Session, *, limit: int = 40) -> list[dict[str, Any]]:
        rows = (
            db.query(MacroNews)
            .order_by(MacroNews.published_at.desc().nullslast(), MacroNews.created_at.desc())
            .limit(max(1, limit))
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "title": row.title,
                    "content": (row.content or "")[:360],
                    "source": row.source,
                    "published_at": row.published_at.isoformat() if row.published_at else None,
                    "metadata": row.news_metadata if isinstance(row.news_metadata, dict) else {},
                }
            )
        return out

    def _fetch_indices_snapshot(self) -> dict[str, Any]:
        ak = self._ak()
        if not ak:
            return {"available": False, "reason": "akshare_unavailable"}
        try:
            df = ak.stock_zh_index_spot_em()
        except Exception as exc:
            return {"available": False, "reason": f"index_spot_failed: {exc}"}
        if df is None or df.empty:
            return {"available": False, "reason": "index_spot_empty"}

        targets = {
            "上证指数": None,
            "深证成指": None,
            "创业板指": None,
            "北证50": None,
        }
        rows = []
        for _, row in df.iterrows():
            item = {str(k): row.get(k) for k in df.columns}
            rows.append(item)
        for name in list(targets.keys()):
            hit = next((row for row in rows if str(row.get("名称", "")).strip() == name), None)
            if hit:
                targets[name] = {
                    "latest": _safe_float(hit.get("最新价")),
                    "chg_pct": _safe_float(hit.get("涨跌幅")),
                    "turnover": _safe_float(hit.get("成交额")),
                }
        return {
            "available": True,
            "indices": targets,
            "raw_count": len(rows),
        }

    def _fetch_industry_snapshot(self) -> dict[str, Any]:
        ak = self._ak()
        if not ak:
            return {"available": False, "reason": "akshare_unavailable"}

        result: dict[str, Any] = {"available": True, "ths": {}, "em": {}}
        try:
            ths_df = ak.stock_board_industry_summary_ths()
            if ths_df is not None and not ths_df.empty:
                rows = []
                for _, row in ths_df.iterrows():
                    rows.append({str(k): row.get(k) for k in ths_df.columns})
                rows_sorted = sorted(
                    rows,
                    key=lambda item: _safe_float(item.get("涨跌幅"), -999.0) or -999.0,
                    reverse=True,
                )
                result["ths"] = {
                    "top_up": rows_sorted[:10],
                    "top_down": list(reversed(rows_sorted[-10:])),
                }
        except Exception as exc:
            result["ths_error"] = str(exc)

        try:
            em_df = ak.stock_board_industry_name_em()
            if em_df is not None and not em_df.empty:
                rows = []
                for _, row in em_df.iterrows():
                    rows.append({str(k): row.get(k) for k in em_df.columns})
                rows_sorted = sorted(
                    rows,
                    key=lambda item: _safe_float(item.get("涨跌幅"), -999.0) or -999.0,
                    reverse=True,
                )
                result["em"] = {
                    "top_up": rows_sorted[:10],
                    "top_down": list(reversed(rows_sorted[-10:])),
                }
        except Exception as exc:
            result["em_error"] = str(exc)

        return result

    @staticmethod
    def _default_report(*, context: dict[str, Any]) -> dict[str, Any]:
        macro_rows = context.get("macro_news") if isinstance(context.get("macro_news"), list) else []
        indices = context.get("china_market_indices", {})
        industry = context.get("industry_boards", {})
        top_up = (((industry.get("ths") or {}).get("top_up")) if isinstance(industry, dict) else []) or []
        top_down = (((industry.get("ths") or {}).get("top_down")) if isinstance(industry, dict) else []) or []
        top_up_names = [str(row.get("板块")) for row in top_up[:3] if isinstance(row, dict)]
        top_down_names = [str(row.get("板块")) for row in top_down[:3] if isinstance(row, dict)]

        sh = (((indices or {}).get("indices") or {}).get("上证指数") or {}) if isinstance(indices, dict) else {}
        sz = (((indices or {}).get("indices") or {}).get("深证成指") or {}) if isinstance(indices, dict) else {}
        cy = (((indices or {}).get("indices") or {}).get("创业板指") or {}) if isinstance(indices, dict) else {}

        avg_idx = []
        for item in (sh, sz, cy):
            value = _safe_float(item.get("chg_pct"), None) if isinstance(item, dict) else None
            if value is not None:
                avg_idx.append(value)
        avg_move = sum(avg_idx) / len(avg_idx) if avg_idx else 0.0

        if avg_move >= 0.8:
            bias = "偏积极"
            risk_pref = "提升"
        elif avg_move <= -0.8:
            bias = "偏谨慎"
            risk_pref = "下降"
        else:
            bias = "偏中性"
            risk_pref = "中性"

        style = "成长" if any("软件" in x or "计算机" in x or "通信" in x for x in top_up_names) else "混合"
        return {
            "macro_overview": {
                "overall_judgement": bias,
                "risk_preference": risk_pref,
                "market_style": style,
                "core_view": "指数与行业轮动信号显示市场处于结构性分化阶段，需结合政策与资金面进行行业选择。",
            },
            "china_macro": {
                "economic_growth": "短期增长修复节奏仍受需求端与地产链条影响，结构性分化明显。",
                "inflation": "通胀压力整体可控，需求侧修复强度仍需观察。",
                "liquidity": "流动性环境以稳为主，政策节奏对风险偏好影响较大。",
                "credit_expansion": "信用扩张处于观察阶段，社融和信贷结构决定后续弹性。",
                "policy_signal": "政策导向偏向稳增长和结构优化，重点关注财政与产业政策共振。",
                "summary": "中国宏观环境呈现“弱复苏+结构分化”特征。",
            },
            "china_market": {
                "index_state": f"上证/深成/创业板当日涨跌幅约为 {sh.get('chg_pct')} / {sz.get('chg_pct')} / {cy.get('chg_pct')}",
                "turnover_and_funds": "成交额与资金风格切换决定短期波动上限。",
                "risk_appetite": f"当前风险偏好判断为{risk_pref}。",
                "style_signal": f"风格信号偏{style}。",
                "summary": "市场处于政策预期与盈利验证并行阶段。",
            },
            "industry_rotation": {
                "strong_sectors": top_up_names or ["暂无明确强势行业"],
                "weak_sectors": top_down_names or ["暂无明确弱势行业"],
                "rotation_logic": "行业轮动由政策预期、景气验证与资金偏好共同驱动。",
                "summary": "短线强弱分化显著，建议优先跟踪资金与政策共振行业。",
            },
            "global_macro": {
                "fed_and_rates": "海外利率路径与风险资产估值中枢仍是关键外部变量。",
                "usd_and_bonds": "美元与美债利率波动会通过估值折现与资金流向影响A股。",
                "commodities": "大宗商品变化将影响上游周期和通胀预期。",
                "geopolitics": "地缘政治事件将阶段性放大风险偏好波动。",
                "summary": "外部扰动仍在，需动态评估传导弹性。",
            },
            "market_implication": {
                "short_term": "短期更关注资金风险偏好与政策预期差。",
                "medium_term": "中期取决于盈利修复与信用扩张持续性。",
                "structural": "结构上继续围绕政策支持与景气改善方向配置。",
            },
            "beneficiaries_and_risks": {
                "beneficiary_sectors": top_up_names or ["政策受益与高景气方向"],
                "pressured_sectors": top_down_names or ["景气下行与估值承压方向"],
                "key_risks": ["海外利率超预期", "政策落地节奏不及预期", "风险偏好回落"],
            },
            "final_conclusion": {
                "macro_to_investment_bias": bias,
                "portfolio_suggestion": "维持分层仓位管理，优先配置政策与景气共振方向，保留防御仓位。",
                "one_sentence_summary": "当前宏观环境以结构性机会为主，策略上应重行业选择与风险控制。",
            },
            "meta": {
                "fallback": True,
                "macro_news_count": len(macro_rows),
            },
        }

    @staticmethod
    def _normalize_report(payload: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return MacroStandaloneService._default_report(context=context)

        base = MacroStandaloneService._default_report(context=context)
        for key, value in payload.items():
            if key not in base:
                continue
            if isinstance(base[key], dict) and isinstance(value, dict):
                merged = dict(base[key])
                merged.update(value)
                base[key] = merged
            else:
                base[key] = value
        meta = base.get("meta") if isinstance(base.get("meta"), dict) else {}
        meta.update({"fallback": False})
        base["meta"] = meta
        return base

    def generate_report(self, db: Session) -> dict[str, Any]:
        today = date.today()
        refresh = {
            "global_news": akshare_service.sync_global_news(db, limit=180),
            "market_layers": akshare_service.sync_market_overview_layers(db, as_of_date=today),
        }

        macro_rows = self._latest_macro_rows(db, limit=60)
        indices_snapshot = self._fetch_indices_snapshot()
        industry_snapshot = self._fetch_industry_snapshot()
        context = {
            "as_of_date": today.isoformat(),
            "refresh": refresh,
            "macro_news": macro_rows,
            "china_market_indices": indices_snapshot,
            "industry_boards": industry_snapshot,
            "world_events_window_days": 7,
        }

        if not zhipu_client.enabled:
            logger.info("macro standalone llm disabled, using fallback synthesis")
            return self._default_report(context=context)

        try:
            payload = zhipu_client.chat_json(
                system_prompt=MACRO_STANDALONE_SYSTEM_PROMPT,
                user_prompt=build_macro_standalone_user_prompt(context),
                temperature=0.1,
                role="macro",
                strict_json=True,
            )
            return self._normalize_report(payload, context=context)
        except (LLMClientError, Exception) as exc:
            logger.exception("macro standalone llm failed, fallback used | error=%s", exc)
            return self._default_report(context=context)


macro_standalone_service = MacroStandaloneService()
