from __future__ import annotations

from datetime import datetime
from typing import Any

EXPERT_LABEL_MAP = {
    "news": "新闻专家",
    "stock_data": "行情专家",
    "macro": "宏观专家",
    "financial": "财务专家",
    "fundamental": "基本面专家",
    "investment": "投资专家",
}

SIGNAL_LABEL_MAP = {
    "buy": "买入",
    "hold": "持有",
    "reduce": "减仓",
    "sell": "卖出",
    "not_buy": "不买入",
}

SCORE_BREAKDOWN_LABEL_MAP = {
    "news": "新闻面",
    "stock_data": "行情面",
    "macro": "宏观面",
    "financial": "财务面",
    "fundamental": "基本面",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        if parsed != parsed:
            return default
        return parsed
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_text(value: Any, max_len: int = 220) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:max_len]


def _clean_text_list(values: Any, *, max_items: int, max_len: int) -> list[str]:
    if isinstance(values, list):
        raw = values
    elif values is None:
        raw = []
    else:
        raw = [values]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_text(item, max_len=max_len)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= max_items:
            break
    return cleaned


def _normalize_signal(value: Any, *, fallback_score: float) -> str:
    signal = str(value or "").strip().lower()
    if signal in {"buy", "hold", "sell"}:
        return signal
    if fallback_score >= 55:
        return "buy"
    if fallback_score <= 45:
        return "sell"
    return "hold"


def _normalize_investment_signal(value: Any, *, fallback_score: float) -> str:
    signal = str(value or "").strip().lower()
    if signal in {"buy", "hold", "reduce", "sell", "not_buy"}:
        return signal
    if fallback_score >= 55:
        return "buy"
    if fallback_score <= 45:
        return "not_buy"
    return "hold"


def _normalize_price_range(value: Any, *, latest_price: float) -> list[float]:
    default_low = round(latest_price * 0.98, 4)
    default_high = round(latest_price * 1.02, 4)
    if not isinstance(value, list) or len(value) != 2:
        return [default_low, default_high]
    low = _as_float(value[0], default_low)
    high = _as_float(value[1], default_high)
    if low <= 0 and high <= 0:
        return [default_low, default_high]
    if low > high:
        low, high = high, low
    low = round(low if low > 0 else default_low, 4)
    high = round(high if high > 0 else default_high, 4)
    return [low, high]


def _signal_label(value: Any) -> str:
    signal = str(value or "").strip().lower()
    return SIGNAL_LABEL_MAP.get(signal, "观望")


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.8:
        return "较高"
    if confidence >= 0.6:
        return "中等"
    return "偏低"


def _build_score_breakdown_lines(aggregate: dict[str, Any]) -> list[str]:
    score_breakdown = aggregate.get("score_breakdown") if isinstance(aggregate.get("score_breakdown"), dict) else {}
    lines: list[str] = []
    for key, label in SCORE_BREAKDOWN_LABEL_MAP.items():
        value = score_breakdown.get(key)
        num = _as_float(value, float("nan"))
        if num == num:
            lines.append(f"{label}评分：{round(num, 2)}")
    return lines


def _to_percent(value: float, digits: int = 1) -> str:
    return f"{round(value * 100, digits)}%"


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _prefer_cn_text(text: Any, fallback: str, *, max_len: int = 220) -> str:
    cleaned = _clean_text(text, max_len=max_len)
    if cleaned and _has_cjk(cleaned):
        return cleaned
    return fallback


def _default_summary(
    *,
    signal: str,
    score: float,
    confidence: float,
    bullish_factors: list[str],
    bearish_factors: list[str],
) -> str:
    signal_label = _signal_label(signal)
    confidence_label = _confidence_label(confidence)
    bullish = bullish_factors[0] if bullish_factors else "暂无明显看多催化"
    bearish = bearish_factors[0] if bearish_factors else "暂无显著看空压制"
    return (
        f"综合评分{round(score, 2)}分，当前建议为“{signal_label}”，"
        f"置信度{round(confidence, 3)}（{confidence_label}）。"
        f"主要看多依据：{bullish}；主要约束因素：{bearish}。"
    )


def _build_expert_matrix(experts: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for expert_key, payload in experts.items():
        if not isinstance(payload, dict):
            continue
        signal = str(payload.get("signal") or "").strip().lower()
        score = _clamp(_as_float(payload.get("score"), 50.0), 0.0, 100.0)
        confidence = _clamp(_as_float(payload.get("confidence"), 0.5), 0.0, 1.0)
        summary = _prefer_cn_text(payload.get("summary"), "暂无摘要。", max_len=160)
        key_points_raw = _clean_text_list(payload.get("key_points"), max_items=3, max_len=160)
        risks_raw = _clean_text_list(payload.get("risks"), max_items=2, max_len=160)
        key_points = [item for item in key_points_raw if _has_cjk(item)]
        risks = [item for item in risks_raw if _has_cjk(item)]
        if not key_points and key_points_raw:
            key_points = ["原始关键要点以英文为主，建议结合原文核验。"]
        if not risks and risks_raw:
            risks = ["原始风险描述以英文为主，建议结合原文核验。"]
        rows.append(
            {
                "expert_key": expert_key,
                "expert_name": EXPERT_LABEL_MAP.get(expert_key, expert_key),
                "signal": signal,
                "signal_label": _signal_label(signal),
                "score": round(score, 2),
                "confidence": round(confidence, 4),
                "summary": summary,
                "key_points": key_points,
                "risks": risks,
            }
        )
    rows.sort(key=lambda row: row.get("score", 0.0), reverse=True)
    return rows


def _build_research_report(
    *,
    signal: str,
    score: float,
    confidence: float,
    summary: str,
    steps: list[str],
    aggregate: dict[str, Any],
    latest_price: float,
    price_range: list[float],
    position_ratio: float,
    capital_to_use: float,
    suggested_shares: int,
    take_profit_plan: list[dict[str, Any]],
    stop_loss_plan: dict[str, Any],
    dynamic_adjustment: list[str],
    wait_conditions: list[str],
    execution_logic: list[dict[str, str]],
    bullish_factors: list[str],
    bearish_factors: list[str],
    conflicts: list[str],
    risk_warnings: list[str],
    experts: dict[str, Any],
) -> dict[str, Any]:
    signal_label = _signal_label(signal)
    confidence_label = _confidence_label(confidence)
    total_score = _as_float(aggregate.get("total_score"), score)
    data_drive_score = _as_float(aggregate.get("data_drive_score"), 0.0)
    emotion_drive_score = _as_float(aggregate.get("emotion_drive_score"), 0.0)
    conflict_signal = bool(aggregate.get("conflict_signal"))
    score_breakdown_lines = _build_score_breakdown_lines(aggregate)

    buy_low, buy_high = price_range
    stop_loss_price = _as_float(stop_loss_plan.get("stop_loss_price"), 0.0)
    stop_loss_desc = _clean_text(stop_loss_plan.get("hard_exit_condition"), max_len=180)
    tp_lines: list[str] = []
    for idx, row in enumerate(take_profit_plan[:3], start=1):
        if not isinstance(row, dict):
            continue
        target = _as_float(row.get("target_price"), 0.0)
        ratio = _clamp(_as_float(row.get("sell_ratio"), 0.0), 0.0, 1.0)
        condition = _clean_text(row.get("condition"), max_len=120)
        tp_lines.append(
            f"第{idx}止盈位：目标价{round(target, 2)}，减仓比例{_to_percent(ratio)}"
            + (f"，触发条件：{condition}" if condition else "")
        )
    if not tp_lines:
        tp_lines.append("未识别到明确止盈分层，建议按波动与成交量动态落袋。")

    scenario_lines = [
        "乐观情景：若量价共振且专家信号一致性提升，可在风险预算内按分批计划加仓。",
        "中性情景：若价格在买入区间震荡，优先执行网格化分批建仓与仓位上限控制。",
        "悲观情景：若跌破止损位或出现系统性利空，应按硬性离场条件快速收缩风险敞口。",
    ]

    evidence_lines = [
        f"综合评分：{round(total_score, 2)}，建议方向：{signal_label}，置信度：{round(confidence, 3)}（{confidence_label}）。",
        f"数据驱动分：{round(data_drive_score, 2)}，情绪驱动分：{round(emotion_drive_score, 2)}，冲突信号：{'是' if conflict_signal else '否'}。",
    ]
    evidence_lines.extend(score_breakdown_lines)

    execution_lines = [
        f"最新参考价：{round(latest_price, 2)}，建议买入区间：{round(buy_low, 2)} - {round(buy_high, 2)}。",
        f"建议仓位：{_to_percent(position_ratio)}，建议股数：{max(0, suggested_shares)}。",
        f"建议投入资金：{round(max(0.0, capital_to_use), 2)}。",
        f"止损价：{round(stop_loss_price, 2)}。{stop_loss_desc}" if stop_loss_price > 0 else "止损价缺失，需补齐风险底线后再执行。",
    ]
    execution_lines.extend(tp_lines)

    experts_matrix = _build_expert_matrix(experts)
    expert_observations: list[str] = []
    for row in experts_matrix[:5]:
        key_points = row.get("key_points") if isinstance(row.get("key_points"), list) else []
        risks = row.get("risks") if isinstance(row.get("risks"), list) else []
        point = key_points[0] if key_points else "暂无关键要点。"
        risk = risks[0] if risks else "暂无突出风险。"
        expert_observations.append(
            f"{row.get('expert_name')}：方向{row.get('signal_label')}，评分{row.get('score')}，"
            f"置信度{row.get('confidence')}；要点：{point}；风险：{risk}"
        )

    synthesis_lines: list[str] = []
    if bullish_factors:
        synthesis_lines.append("看多因素：" + "；".join(bullish_factors[:4]))
    if bearish_factors:
        synthesis_lines.append("看空因素：" + "；".join(bearish_factors[:4]))
    if conflicts:
        synthesis_lines.append("冲突项：" + "；".join(conflicts[:4]))
    if not synthesis_lines:
        synthesis_lines.append("当前多空线索不充分，建议保持谨慎并等待增量数据。")

    action_checklist = [
        "确认账户可用资金、单票仓位上限与回撤阈值。",
        "按买入区间分批执行，避免单笔追高。",
        "设置并监控止损/止盈条件，触发后严格执行。",
        "每日复核专家信号一致性与宏观风险变化，必要时下调仓位。",
    ]

    if wait_conditions:
        action_checklist.append("等待条件：" + "；".join(wait_conditions[:3]))
    if dynamic_adjustment:
        action_checklist.append("动态调整：" + "；".join(dynamic_adjustment[:3]))
    if execution_logic:
        first_logic = execution_logic[0]
        logic_text = _clean_text(f"{first_logic.get('title') or ''} {first_logic.get('content') or ''}", max_len=180)
        if logic_text:
            action_checklist.append("执行逻辑重点：" + logic_text)

    return {
        "title": f"{signal_label}策略研究报告",
        "subtitle": "多专家融合决策与交易执行框架",
        "summary": summary,
        "rating": {
            "signal": signal,
            "signal_label": signal_label,
            "score": round(score, 2),
            "confidence": round(confidence, 4),
            "confidence_label": confidence_label,
        },
        "sections": [
            {"title": "一、投资结论", "points": [summary, f"建议动作：{signal_label}。"]},
            {"title": "二、核心证据", "points": evidence_lines},
            {"title": "三、多专家交叉验证", "points": expert_observations or ["暂无专家交叉验证信息。"]},
            {"title": "四、交易执行方案", "points": execution_lines},
            {"title": "五、多空因素与冲突消解", "points": synthesis_lines},
            {"title": "六、情景推演", "points": scenario_lines},
            {"title": "七、执行清单", "points": action_checklist},
            {"title": "八、风险提示", "points": risk_warnings[:8] or ["风险信息不足，需人工补充后再执行。"]},
        ],
        "expert_matrix": experts_matrix,
        "explanation_steps": steps,
        "disclaimer": "本报告由模型基于多源数据自动生成，仅供研究参考，不构成任何投资承诺或收益保证。",
        "generated_at": datetime.utcnow().isoformat(),
    }


def sanitize_expert_payloads(experts_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cleaned: dict[str, dict[str, Any]] = {}
    for expert_key, payload in (experts_payload or {}).items():
        if not isinstance(payload, dict):
            payload = {}

        score = _clamp(_as_float(payload.get("score"), 50.0), 0.0, 100.0)
        confidence = _clamp(_as_float(payload.get("confidence"), 0.5), 0.0, 1.0)
        signal = _normalize_signal(payload.get("signal"), fallback_score=score)
        summary = _clean_text(payload.get("summary") or "暂无摘要。")
        thesis = _clean_text(payload.get("thesis"), max_len=220)

        # Support structured key_points schema while keeping text list for UI compatibility.
        key_points_structured_raw = (
            payload.get("key_points_structured")
            if isinstance(payload.get("key_points_structured"), list)
            else payload.get("key_points")
        )
        key_points_structured: list[dict[str, str]] = []
        key_points: list[str] = []
        if isinstance(key_points_structured_raw, list):
            for item in key_points_structured_raw[:8]:
                if isinstance(item, dict):
                    fact = _clean_text(item.get("fact"), max_len=120)
                    interpretation = _clean_text(item.get("interpretation"), max_len=180)
                    investment_meaning = _clean_text(item.get("investment_meaning"), max_len=180)
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
                        _clean_text(
                            f"事实：{fact or '-'} | 解读：{interpretation or '-'} | 投资含义：{investment_meaning or '-'}",
                            max_len=220,
                        )
                    )
                else:
                    text = _clean_text(item, max_len=180)
                    if not text:
                        continue
                    key_points.append(text)
                    key_points_structured.append(
                        {"fact": text, "interpretation": "", "investment_meaning": ""}
                    )
        else:
            key_points = _clean_text_list(payload.get("key_points"), max_items=6, max_len=180)
            key_points_structured = [{"fact": text, "interpretation": "", "investment_meaning": ""} for text in key_points]
        key_points = key_points[:6]
        key_points_structured = key_points_structured[:6]

        # Support structured risks schema while keeping text list for UI compatibility.
        risks_structured_raw = (
            payload.get("risks_structured")
            if isinstance(payload.get("risks_structured"), list)
            else payload.get("risks")
        )
        risks_structured: list[dict[str, str]] = []
        risks: list[str] = []
        if isinstance(risks_structured_raw, list):
            for item in risks_structured_raw[:8]:
                if isinstance(item, dict):
                    risk = _clean_text(item.get("risk"), max_len=120)
                    trigger = _clean_text(item.get("trigger"), max_len=160)
                    impact = _clean_text(item.get("impact"), max_len=160)
                    if not any([risk, trigger, impact]):
                        continue
                    risks_structured.append({"risk": risk, "trigger": trigger, "impact": impact})
                    risks.append(
                        _clean_text(
                            f"风险：{risk or '-'} | 触发：{trigger or '-'} | 后果：{impact or '-'}",
                            max_len=200,
                        )
                    )
                else:
                    text = _clean_text(item, max_len=160)
                    if not text:
                        continue
                    risks.append(text)
                    risks_structured.append({"risk": text, "trigger": "", "impact": ""})
        else:
            risks = _clean_text_list(payload.get("risks"), max_items=6, max_len=160)
            risks_structured = [{"risk": text, "trigger": "", "impact": ""} for text in risks]
        risks = risks[:6]
        risks_structured = risks_structured[:6]

        if len(key_points) < 3:
            key_points.extend(
                [
                    "关键论据不完整，已启用最小安全格式。",
                    "请结合原始行情与披露数据二次核验。",
                    "执行前请再次校验时间线一致性。",
                ][: 3 - len(key_points)]
            )
        if len(risks) < 2:
            risks.extend(
                [
                    "模型置信度受限，建议降低单笔暴露。",
                    "市场波动可能导致结论快速变化。",
                ][: 2 - len(risks)]
            )

        evidence_items = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        evidence: list[dict[str, str]] = []
        for item in evidence_items[:8]:
            if not isinstance(item, dict):
                continue
            evidence_type = _clean_text(item.get("type") or "context", max_len=32) or "context"
            detail = _clean_text(item.get("detail"), max_len=220)
            if not detail:
                continue
            evidence.append({"type": evidence_type, "detail": detail})
        while len(evidence) < 4:
            evidence.append({"type": "context", "detail": "证据仍不充分，建议补充结构化数据来源。"})

        cleaned[expert_key] = {
            **payload,
            "signal": signal,
            "score": round(score, 2),
            "confidence": round(confidence, 4),
            "summary": summary,
            "thesis": thesis,
            "key_points": key_points,
            "key_points_structured": key_points_structured,
            "risks": risks,
            "risks_structured": risks_structured,
            "evidence": evidence,
            "fallback": bool(payload.get("fallback", False)),
        }
    return cleaned


def sanitize_investment_payload(
    payload: dict[str, Any] | None,
    *,
    aggregate: dict[str, Any] | None,
    latest_price: float,
    experts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    aggregate = aggregate if isinstance(aggregate, dict) else {}
    experts_payload = experts if isinstance(experts, dict) else {}

    score_hint = _as_float(aggregate.get("total_score"), 50.0)
    signal = _normalize_investment_signal(
        payload.get("final_signal") or payload.get("signal"),
        fallback_score=score_hint,
    )
    confidence = _clamp(_as_float(payload.get("confidence"), 0.5), 0.0, 1.0)
    score = _clamp(_as_float(payload.get("score"), score_hint), 0.0, 100.0)
    summary = _clean_text(payload.get("summary"), max_len=260)

    steps = _clean_text_list(payload.get("explanation_steps"), max_items=6, max_len=220)
    if len(steps) < 4:
        steps.extend(
            [
                "汇总五类专家方向与评分，形成初始交易信号。",
                "结合仓位约束、风险预算与账户条件，计算可执行头寸。",
                "给出买入区间、止盈分层、止损阈值与动态调整规则。",
                "当证据时效不足或信号冲突时，主动下调置信度并提示等待条件。",
            ][: 4 - len(steps)]
        )

    buy_strategy_raw = payload.get("buy_strategy") if isinstance(payload.get("buy_strategy"), dict) else {}
    buy_range_raw = payload.get("buy_range") if isinstance(payload.get("buy_range"), dict) else {}
    buy_conditions = _clean_text_list(buy_strategy_raw.get("conditions"), max_items=5, max_len=180)
    if not buy_conditions and buy_range_raw.get("condition"):
        buy_conditions = _clean_text_list([buy_range_raw.get("condition")], max_items=5, max_len=180)
    staged_entry = _clean_text_list(buy_strategy_raw.get("staged_entry"), max_items=5, max_len=180)
    if buy_range_raw:
        price_range = _normalize_price_range(
            [buy_range_raw.get("min"), buy_range_raw.get("max")],
            latest_price=latest_price,
        )
    else:
        price_range = _normalize_price_range(buy_strategy_raw.get("price_range"), latest_price=latest_price)

    pm_raw = payload.get("position_management") if isinstance(payload.get("position_management"), dict) else {}
    position_ratio = _clamp(_as_float(pm_raw.get("position_ratio"), payload.get("position_ratio") or 0.1), 0.0, 1.0)
    capital_to_use = max(0.0, _as_float(pm_raw.get("capital_to_use"), 0.0))
    suggested_shares = int(
        max(
            0.0,
            _as_float(pm_raw.get("suggested_shares"), payload.get("suggested_shares") or 0.0),
        )
    )

    tp_raw = payload.get("take_profit_plan") if isinstance(payload.get("take_profit_plan"), list) else []
    take_profit_plan: list[dict[str, Any]] = []
    for item in tp_raw[:4]:
        if not isinstance(item, dict):
            continue
        take_profit_plan.append(
            {
                "target_price": _as_float(item.get("target_price"), 0.0) or None,
                "sell_ratio": round(_clamp(_as_float(item.get("sell_ratio"), 0.0), 0.0, 1.0), 4),
                "condition": _prefer_cn_text(
                    item.get("condition"),
                    "达到分层目标位后按计划落袋，避免利润回撤。",
                    max_len=180,
                ),
            }
        )
    if not take_profit_plan:
        take_profit_plan = [
            {
                "target_price": round(latest_price * 1.08, 4),
                "sell_ratio": 0.5,
                "condition": "达到首个目标价后先兑现部分利润，降低回撤风险。",
            }
        ]

    breakeven_raw = payload.get("breakeven_plan") if isinstance(payload.get("breakeven_plan"), dict) else {}
    break_even_raw = payload.get("break_even_plan") if isinstance(payload.get("break_even_plan"), dict) else {}
    if break_even_raw and not breakeven_raw:
        breakeven_raw = {
            "trigger_gain_pct": break_even_raw.get("trigger_price"),
            "sell_ratio": break_even_raw.get("sell_ratio"),
            "note": break_even_raw.get("reason"),
        }
    breakeven_plan = {
        "trigger_gain_pct": round(_clamp(_as_float(breakeven_raw.get("trigger_gain_pct"), 0.08), 0.0, 1.0), 4),
        "sell_ratio": round(_clamp(_as_float(breakeven_raw.get("sell_ratio"), 0.35), 0.0, 1.0), 4),
        "note": _prefer_cn_text(
            breakeven_raw.get("note"),
            "分段盈利后回收部分本金，保留继续上行敞口。",
            max_len=180,
        ),
    }

    sl_raw = payload.get("stop_loss_plan") if isinstance(payload.get("stop_loss_plan"), dict) else {}
    stop_loss_raw = payload.get("stop_loss") if isinstance(payload.get("stop_loss"), dict) else {}
    stop_loss_price = _as_float(sl_raw.get("stop_loss_price"), _as_float(stop_loss_raw.get("price"), 0.0))
    if stop_loss_price <= 0:
        stop_loss_price = round(latest_price * 0.93, 4)
    stop_loss_plan = {
        "stop_loss_price": round(stop_loss_price, 4),
        "hard_exit_condition": _prefer_cn_text(
            sl_raw.get("hard_exit_condition") or stop_loss_raw.get("condition"),
            "跌破止损位且波动放大时执行硬性离场，避免亏损扩散。",
            max_len=180,
        )
        or "跌破止损位且波动放大时执行硬性离场，避免亏损扩散。",
    }

    dynamic_adjustment = _clean_text_list(payload.get("dynamic_adjustment"), max_items=6, max_len=180)
    risk_warnings = _clean_text_list(
        payload.get("risk_warnings") or payload.get("risks"),
        max_items=8,
        max_len=160,
    )
    wait_conditions = _clean_text_list(payload.get("wait_conditions"), max_items=6, max_len=180)
    execution_logic_raw = payload.get("execution_logic") if isinstance(payload.get("execution_logic"), list) else []
    execution_logic: list[dict[str, str]] = []
    for item in execution_logic_raw[:6]:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"), max_len=60)
        content = _clean_text(item.get("content"), max_len=220)
        if not title and not content:
            continue
        execution_logic.append({"title": title, "content": content})
    expert_synthesis = payload.get("expert_synthesis") if isinstance(payload.get("expert_synthesis"), dict) else {}
    bullish_factors = _clean_text_list(expert_synthesis.get("bullish_factors"), max_items=8, max_len=180)
    bearish_factors = _clean_text_list(expert_synthesis.get("bearish_factors"), max_items=8, max_len=180)
    conflicts = _clean_text_list(expert_synthesis.get("conflicts"), max_items=8, max_len=180)
    if not risk_warnings:
        risk_warnings = ["市场结构快速切换时应立即复核策略参数并收缩仓位。"]
    if not summary or summary.lower() == "no summary" or not _has_cjk(summary):
        summary = _default_summary(
            signal=signal,
            score=score,
            confidence=confidence,
            bullish_factors=bullish_factors,
            bearish_factors=bearish_factors,
        )

    research_report = _build_research_report(
        signal=signal,
        score=score,
        confidence=confidence,
        summary=summary,
        steps=steps,
        aggregate=aggregate,
        latest_price=latest_price,
        price_range=price_range,
        position_ratio=position_ratio,
        capital_to_use=capital_to_use,
        suggested_shares=suggested_shares,
        take_profit_plan=take_profit_plan,
        stop_loss_plan=stop_loss_plan,
        dynamic_adjustment=dynamic_adjustment,
        wait_conditions=wait_conditions,
        execution_logic=execution_logic,
        bullish_factors=bullish_factors,
        bearish_factors=bearish_factors,
        conflicts=conflicts,
        risk_warnings=risk_warnings,
        experts=experts_payload,
    )

    explanation_panel = {
        "headline": summary,
        "steps": steps,
        "execution": {
            "signal": signal,
            "price_range": price_range,
            "position_ratio": round(position_ratio, 4),
            "suggested_shares": suggested_shares,
            "stop_loss_price": stop_loss_plan["stop_loss_price"],
        },
        "risk_warnings": risk_warnings,
        "wait_conditions": wait_conditions,
        "report_title": research_report.get("title"),
        "generated_at": datetime.utcnow().isoformat(),
    }

    return {
        **payload,
        "signal": signal,
        "final_signal": signal,
        "score": round(score, 2),
        "confidence": round(confidence, 4),
        "summary": summary,
        "explanation_steps": steps,
        "buy_strategy": {
            "conditions": buy_conditions,
            "price_range": price_range,
            "staged_entry": staged_entry,
        },
        "position_management": {
            "position_ratio": round(position_ratio, 4),
            "capital_to_use": round(capital_to_use, 2),
            "suggested_shares": suggested_shares,
        },
        "take_profit_plan": take_profit_plan,
        "breakeven_plan": breakeven_plan,
        "stop_loss_plan": stop_loss_plan,
        "dynamic_adjustment": dynamic_adjustment,
        "risk_warnings": risk_warnings,
        "wait_conditions": wait_conditions,
        "execution_logic": execution_logic,
        "expert_synthesis": {
            "bullish_factors": bullish_factors,
            "bearish_factors": bearish_factors,
            "conflicts": conflicts,
        },
        "position_ratio": round(position_ratio, 4),
        "suggested_shares": suggested_shares,
        "buy_range": {
            "min": price_range[0],
            "max": price_range[1],
            "condition": buy_conditions[0] if buy_conditions else "",
        },
        "stop_loss": {
            "price": stop_loss_plan["stop_loss_price"],
            "condition": stop_loss_plan["hard_exit_condition"],
            "reason": stop_loss_plan["hard_exit_condition"],
        },
        "research_report": research_report,
        "explanation_panel": explanation_panel,
    }


def sanitize_report_for_storage(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"experts": {}, "aggregate": {}, "investment": {}}

    context = report.get("context") if isinstance(report.get("context"), dict) else {}
    latest_quote = context.get("latest_quote") if isinstance(context.get("latest_quote"), dict) else {}
    latest_price = _as_float(latest_quote.get("latest_price"), 0.0)
    if latest_price <= 0:
        latest_price = 1.0

    aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}
    experts = sanitize_expert_payloads(report.get("experts") if isinstance(report.get("experts"), dict) else {})
    investment = sanitize_investment_payload(
        report.get("investment") if isinstance(report.get("investment"), dict) else {},
        aggregate=aggregate,
        latest_price=latest_price,
        experts=experts,
    )
    return {
        **report,
        "experts": experts,
        "aggregate": aggregate,
        "investment": investment,
    }
