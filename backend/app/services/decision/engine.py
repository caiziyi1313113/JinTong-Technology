from math import floor
from typing import List, Dict

from app.models.profile import UserProfile
'''
1）engine.py：把五个专家信号融合成最终决策（核心决策引擎）

它做的事：给每个专家分权重 → 算加权分数 → 决定 buy/sell/hold → 计算仓位、止损止盈、建议股数。

engine

关键点：

权重随风险等级变化：低风险更看 fundamental/financial；高风险更看 technical/news
RISK_WEIGHTS = {...} 

engine

不同持仓周期（short/medium/long）用不同阈值、止损止盈、bias：HORIZON_TEMPLATES 

engine

把专家信号 score（0~1）按 confidence 和权重加权：_weighted_score() 

engine

把专家分成两类：

情绪类：news/macro/fundamental

数据类：technical/financial
用它们比较“是否一致/冲突”，冲突就加风险提示 signal_conflict 

engine

仓位和建议股数是按风险预算算的：根据 assets * risk_budget 和止损到现价的风险距离，算可承受亏损对应的股数，同时也受 max_single_position 限制。

engine

如果你已经有持仓，它会进入“管理持仓模式”：调整止损止盈，并给出建议减仓比例 reduce_ratio。

engine

输出大概长这样：

{
  "action": "buy|sell|hold",
  "position_size": 0.123,
  "risk_notes": [...],
  "rationale": {..., "trade_advice": {...}}
}

（被 analysis.py / planner.py 调用）
'''
#不同持风险承受度用不同权重
RISK_WEIGHTS = {
    "low": {"fundamental": 0.35, "financial": 0.3, "technical": 0.15, "news": 0.1, "macro": 0.1},
    "medium": {"fundamental": 0.3, "financial": 0.25, "technical": 0.2, "news": 0.15, "macro": 0.1},
    "high": {"fundamental": 0.2, "financial": 0.2, "technical": 0.3, "news": 0.2, "macro": 0.1},
}

# 不同持仓周期用不同的决策模板，包含权重偏向、阈值、止损止盈等参数
HORIZON_TEMPLATES = {
    "short": {
        "bias": {"fundamental": 0.8, "financial": 0.9, "technical": 1.3, "news": 1.2, "macro": 0.8},
        "thresholds": {"buy": 0.63, "sell": 0.37},
        "stop_loss_pct": 0.035,
        "take_profit_pct": 0.07,
        "trailing_stop_pct": 0.025,
        "hold_days": "1-15d",
        "entry_buffer_pct": 0.012,
    },
    "medium": {
        "bias": {"fundamental": 1.0, "financial": 1.0, "technical": 1.0, "news": 1.0, "macro": 1.0},
        "thresholds": {"buy": 0.6, "sell": 0.35},
        "stop_loss_pct": 0.06,
        "take_profit_pct": 0.15,
        "trailing_stop_pct": 0.05,
        "hold_days": "1-6m",
        "entry_buffer_pct": 0.018,
    },
    "long": {
        "bias": {"fundamental": 1.25, "financial": 1.2, "technical": 0.8, "news": 0.85, "macro": 1.1},
        "thresholds": {"buy": 0.57, "sell": 0.32},
        "stop_loss_pct": 0.1,
        "take_profit_pct": 0.28,
        "trailing_stop_pct": 0.09,
        "hold_days": "6m+",
        "entry_buffer_pct": 0.025,
    },
}

STYLE_FACTOR = {"stable": 0.85, "balanced": 1.0, "aggressive": 1.2}
SENTIMENT_EXPERTS = {"news", "macro", "fundamental"}
DATA_EXPERTS = {"technical", "financial"}


def _normalize_horizon(value: str | None) -> str:
    raw = (value or "medium").lower()
    if raw in {"short", "day", "swing"}:
        return "short"
    if raw in {"medium", "mid", "middle"}:
        return "medium"
    if raw in {"long", "longterm", "long-term"}:
        return "long"
    return "medium"


def _normalize_style(value: str | None) -> str:
    raw = (value or "balanced").lower()
    if raw in {"stable", "conservative", "steady"}:
        return "stable"
    if raw in {"aggressive", "challenge", "high-beta"}:
        return "aggressive"
    return "balanced"

# 不同专家最终应该占多大权重，根据风险等级和持仓周期调整
'''
这个函数在算：

不同专家最终应该占多大权重

输入：

base：基础权重，比如低风险用户更重基本面

bias：根据投资周期再调一次，比如短线更重技术面、新闻面
'''
def _blend_weights(base: Dict[str, float], bias: Dict[str, float]) -> Dict[str, float]:
    merged = {}
    for expert in {"fundamental", "financial", "technical", "news", "macro"}:
        merged[expert] = max(0.01, base.get(expert, 0.1) * bias.get(expert, 1.0))
    total = sum(merged.values()) or 1.0
    return {k: v / total for k, v in merged.items()}
# 加权分数
'''
输入：
signals：每个专家输出的结果
weights：每个专家的权重
selected：可选，只挑部分专家来算
专家贡献 = 专家分数 × 专家权重 × 专家置信度
'''

def _weighted_score(signals: List[dict], weights: Dict[str, float], selected: set[str] | None = None) -> float:
    weighted = 0.0
    total_weight = 0.0
    for signal in signals:
        name = signal["expert_name"]
        if selected and name not in selected:
            continue
        confidence = max(0.0, min(1.0, signal["confidence"]))
        score = max(0.0, min(1.0, signal["score"]))
        weight = weights.get(name, 0.1) * confidence
        weighted += score * weight
        total_weight += weight

    return weighted / total_weight if total_weight else 0.5

# 情绪派和数据派是不是一致
'''
输入：
sentiment_score
data_score
先算差值：
gap = abs(sentiment_score - data_score)
'''
def _make_alignment(sentiment_score: float, data_score: float) -> tuple[str, str]:
    gap = abs(sentiment_score - data_score)
    if gap <= 0.08 or ((sentiment_score >= 0.55 and data_score >= 0.55) or (sentiment_score <= 0.45 and data_score <= 0.45)):
        return "aligned", "Sentiment and data perspectives are consistent."
    if sentiment_score > data_score:
        return "conflict", "Sentiment is stronger than market/financial data."
    return "conflict", "Market/financial data is stronger than sentiment."

# 在当前风险约束下，最多建议买/卖多少股
def _risk_based_shares(profile: UserProfile, current_price: float, stop_loss_price: float, position_ratio: float) -> tuple[float, int]:
    assets = max(0.0, float(profile.assets))
    risk_budget = max(0.005, min(float(profile.risk_budget), 0.3))
    max_single_position = max(0.02, min(float(profile.max_single_position), 0.8))
    tolerable_loss_amount = assets * risk_budget
    per_share_risk = max(current_price - stop_loss_price, current_price * 0.003)
    shares_by_risk = floor(tolerable_loss_amount / per_share_risk) if per_share_risk > 0 else 0
    capital_cap = min(assets * max_single_position, assets * max(0.0, position_ratio))
    shares_by_capital = floor(capital_cap / current_price) if current_price > 0 else 0
    suggested_shares = max(0, min(shares_by_risk, shares_by_capital))
    return round(tolerable_loss_amount, 2), suggested_shares

'''
fuse_signals

把上面所有结果合起来，算出：

买 / 卖 / 持有

仓位比例

止损止盈

建议股数

交易说明
'''

def fuse_signals(profile: UserProfile, signals: List[dict], current_price: float, position: dict | None = None) -> dict:
    horizon = _normalize_horizon(profile.investment_horizon)
    style = _normalize_style(profile.style)
    template = HORIZON_TEMPLATES[horizon]
    weights = _blend_weights(
        RISK_WEIGHTS.get(profile.risk_level, RISK_WEIGHTS["medium"]),
        template["bias"],
    )

    risk_notes = []
    for signal in signals:
        risk_notes.extend(signal.get("risk_flags", []))

    fused_score = _weighted_score(signals, weights)
    sentiment_score = _weighted_score(signals, weights, SENTIMENT_EXPERTS)
    data_score = _weighted_score(signals, weights, DATA_EXPERTS)
    alignment, conflict_reason = _make_alignment(sentiment_score, data_score)

    risk_notes = sorted(set(risk_notes))
    if alignment == "conflict":
        risk_notes.append("signal_conflict")
    risk_notes = sorted(set(risk_notes))

    style_factor = STYLE_FACTOR.get(style, 1.0)
    buy_threshold = template["thresholds"]["buy"] + (0.02 if profile.risk_level == "low" else -0.02 if profile.risk_level == "high" else 0.0)
    sell_threshold = template["thresholds"]["sell"] + (0.03 if profile.risk_level == "low" else -0.03 if profile.risk_level == "high" else 0.0)

    if fused_score >= buy_threshold:
        action = "buy"
    elif fused_score <= sell_threshold:
        action = "sell"
    else:
        action = "hold"

    base = 0.08 if profile.risk_level == "low" else 0.15 if profile.risk_level == "medium" else 0.24
    horizon_factor = 0.85 if horizon == "short" else 1.0 if horizon == "medium" else 1.12
    position_size = (base + (fused_score - 0.5) * 0.6) * horizon_factor * style_factor
    position_size = max(0.0, min(position_size, max(0.02, min(float(profile.max_single_position), 0.8))))

    px = max(0.01, current_price)
    entry_buffer_pct = template["entry_buffer_pct"]
    entry_low = round(px * (1 - entry_buffer_pct), 4)
    entry_high = round(px * (1 + entry_buffer_pct), 4)
    stop_loss_price = round(px * (1 - template["stop_loss_pct"]), 4)
    take_profit_price = round(px * (1 + template["take_profit_pct"]), 4)
    trailing_stop_pct = template["trailing_stop_pct"]
    ladder_prices = [entry_low, round(px, 4), entry_high]

    tolerable_loss_amount, suggested_shares = _risk_based_shares(
        profile=profile,
        current_price=px,
        stop_loss_price=stop_loss_price,
        position_ratio=position_size,
    )

    if position and float(position.get("quantity", 0)) > 0:
        held_qty = int(position.get("quantity", 0))
        avg_price = float(position.get("avg_price", px))
        stop_loss_price = round(min(stop_loss_price, avg_price * (1 - max(0.02, min(float(profile.max_drawdown), 0.5)))), 4)
        take_profit_price = round(max(take_profit_price, avg_price * (1 + template["take_profit_pct"] * 0.8)), 4)
        reduce_ratio = 0.5 if action == "sell" else 0.2 if alignment == "conflict" else 0.0
        suggested_sell_shares = floor(held_qty * reduce_ratio)
        trade_advice = {
            "mode": "manage_position",
            "hold_days": template["hold_days"],
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "trailing_stop_pct": trailing_stop_pct,
            "reduce_ratio": round(reduce_ratio, 3),
            "suggested_sell_shares": max(0, suggested_sell_shares),
        }
    else:
        trade_advice = {
            "mode": "new_entry",
            "hold_days": template["hold_days"],
            "entry_range": [entry_low, entry_high],
            "ladder_buy_prices": ladder_prices,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "trailing_stop_pct": trailing_stop_pct,
            "suggested_buy_shares": suggested_shares,
        }

    if alignment == "conflict":
        decision_note = "Signals conflict. Reduce size or wait for confirmation."
    elif action == "buy":
        decision_note = "Signals are aligned. Entry can be staged with ladder prices."
    elif action == "sell":
        decision_note = "Downside risk is dominant. Protect capital first."
    else:
        decision_note = "No strong edge. Keep monitoring."

    rationale = {
        "fused_score": round(fused_score, 4),
        "sentiment_score": round(sentiment_score, 4),
        "data_score": round(data_score, 4),
        "alignment": alignment,
        "conflict_reason": conflict_reason,
        "decision_note": decision_note,
        "thresholds": {"buy": round(buy_threshold, 4), "sell": round(sell_threshold, 4)},
        "risk_level": profile.risk_level,
        "investment_horizon": horizon,
        "style": style,
        "weights": weights,
        "tolerable_loss_amount": tolerable_loss_amount,
        "target_return": profile.target_return,
        "trade_advice": trade_advice,
    }

    return {
        "action": action,
        "position_size": round(position_size, 3),
        "risk_notes": risk_notes,
        "rationale": rationale,
    }
