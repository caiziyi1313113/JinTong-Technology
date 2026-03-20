from __future__ import annotations

from typing import Any


QUESTION_WEIGHTS = {
    "loss_aversion": 0.35,
    "risk_comfort": 0.30,
    "time_horizon": 0.15,
    "financial_literacy": 0.20,
}


def _clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except Exception:
        score = 1
    return max(1, min(4, score))


def _safe_money(value: Any) -> float:
    try:
        money = float(value)
    except Exception:
        money = 0.0
    return max(0.0, money)


def _funds_bucket(disposable_funds: float) -> str:
    if disposable_funds < 50000:
        return "micro"
    if disposable_funds < 200000:
        return "small"
    if disposable_funds < 1000000:
        return "medium"
    return "large"


def compute_questionnaire_profile(questionnaire_answers: dict[str, Any] | None) -> dict[str, Any]:
    answers = questionnaire_answers or {}
    disposable_funds = _safe_money(answers.get("disposable_funds", 0.0))
    d1 = _clamp_score(answers.get("loss_aversion", 2))
    d2 = _clamp_score(answers.get("risk_comfort", 2))
    d3 = _clamp_score(answers.get("time_horizon", 2))
    d4 = _clamp_score(answers.get("financial_literacy", 2))

    total_score = (
        d1 * QUESTION_WEIGHTS["loss_aversion"]
        + d2 * QUESTION_WEIGHTS["risk_comfort"]
        + d3 * QUESTION_WEIGHTS["time_horizon"]
        + d4 * QUESTION_WEIGHTS["financial_literacy"]
    )
    rsi = (total_score - 1.0) / 3.0
    rsi = max(0.0, min(1.0, rsi))

    if rsi < 0.25:
        risk_level = "low"
        persona = "conservative"
        style = "stable"
    elif rsi < 0.50:
        risk_level = "medium"
        persona = "steady"
        style = "balanced"
    elif rsi < 0.75:
        risk_level = "medium"
        persona = "growth"
        style = "balanced"
    else:
        risk_level = "high"
        persona = "aggressive"
        style = "aggressive"

    bucket = _funds_bucket(disposable_funds)
    bucket_cap_ratio = {
        "micro": 0.06,
        "small": 0.08,
        "medium": 0.12,
        "large": 0.16,
    }[bucket]
    recommended_position_cap = round(max(0.03, min(0.4, bucket_cap_ratio + (rsi - 0.5) * 0.16)), 4)
    recommended_risk_budget = round(max(0.005, min(0.12, 0.01 + 0.09 * rsi)), 4)
    suggested_order_budget = round(disposable_funds * recommended_position_cap, 2)

    return {
        "disposable_funds": round(disposable_funds, 2),
        "funds_bucket": bucket,
        "dimension_scores": {
            "loss_aversion": d1,
            "risk_comfort": d2,
            "time_horizon": d3,
            "financial_literacy": d4,
        },
        "total_score": round(total_score, 4),
        "risk_sensitivity_index": round(rsi, 6),
        "risk_level": risk_level,
        "persona": persona,
        "style": style,
        "target_return": round(0.06 + 0.24 * rsi, 4),
        "risk_budget": recommended_risk_budget,
        "max_single_position": recommended_position_cap,
        "suggested_order_budget": suggested_order_budget,
    }
