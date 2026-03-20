from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import statistics
from typing import Any


TREND_LONG_TEXT = "建议投资者做多"
TREND_SHORT_TEXT = "建议短期投资者卖出。建议长期投资者结合基本面情况，需进行进一步分析。目前可能属于市场过度反应阶段。"
TREND_REVERSAL_TEXT = "市场预期出现转折，投资者需结合基本面进行进一步分析。建议投资者提前反转布局，把握投资时机。"


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def raw_to_norm(raw: float) -> float:
    return clip((clip(raw, -1.0, 1.0) + 1.0) / 2.0, 0.0, 1.0)


def sentiment_label(norm_score: float) -> str:
    if norm_score >= 0.55:
        return "乐观"
    if norm_score <= 0.45:
        return "悲观"
    return "中性"


def mean_score(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def combine_channel_scores(
    *,
    news_raw: float,
    guba_raw: float,
    news_count: int,
    guba_count: int,
) -> float:
    total = max(0, news_count) + max(0, guba_count)
    if total <= 0:
        return 0.0
    return (news_raw * max(0, news_count) + guba_raw * max(0, guba_count)) / total


@dataclass
class TrendResult:
    deltas: list[float]
    trend_5d: float | None
    signal: str
    conclusion: str | None


def _delta_sign(value: float, eps: float = 1e-6) -> int:
    if value > eps:
        return 1
    if value < -eps:
        return -1
    return 0


def _is_reversal_pattern(deltas: list[float]) -> bool:
    """
    Detect 5-step two-run reversal patterns:
    - 两天上三天下 / 三天上两天下
    - 三天下两天上 / 两天下三天上
    """
    if len(deltas) < 5:
        return False
    signs = [_delta_sign(v) for v in reversed(deltas[:5])]  # old -> new
    if any(s == 0 for s in signs):
        return False

    runs: list[tuple[int, int]] = []
    current = signs[0]
    count = 1
    for sign in signs[1:]:
        if sign == current:
            count += 1
        else:
            runs.append((current, count))
            current = sign
            count = 1
    runs.append((current, count))
    if len(runs) != 2:
        return False
    lengths = sorted([runs[0][1], runs[1][1]])
    return lengths == [2, 3]


def compute_trend(scores_newest_first: list[float]) -> TrendResult:
    # Δ(t, t-1), Δ(t-1, t-2)...最多5个
    deltas = [
        float(scores_newest_first[i] - scores_newest_first[i + 1])
        for i in range(max(0, min(5, len(scores_newest_first) - 1)))
    ]
    trend_5d = None
    if len(scores_newest_first) >= 6:
        trend_5d = float(scores_newest_first[0] - scores_newest_first[5])

    signal = "none"
    conclusion = None
    if len(scores_newest_first) >= 3:
        s0, s1, s2 = scores_newest_first[0], scores_newest_first[1], scores_newest_first[2]
        if s0 > s1 > s2:
            signal = "up_3d"
            conclusion = TREND_LONG_TEXT
        elif s0 < s1 < s2:
            signal = "down_3d"
            conclusion = TREND_SHORT_TEXT
        elif _is_reversal_pattern(deltas):
            signal = "reversal_5step"
            conclusion = TREND_REVERSAL_TEXT

    return TrendResult(deltas=deltas, trend_5d=trend_5d, signal=signal, conclusion=conclusion)


def valuation_level_from_signals(
    *,
    latest_close: float | None,
    recent_closes: list[float],
    latest_pb: float | None,
    recent_pbs: list[float],
    latest_pe: float | None,
    recent_pes: list[float],
) -> tuple[str, str]:
    """
    Heuristic intrinsic-value level:
    - Lower-than-history valuation / price implies higher value level.
    """
    score = 0
    reasons: list[str] = []

    valid_closes = [float(v) for v in recent_closes if isinstance(v, (int, float)) and v > 0]
    if latest_close and len(valid_closes) >= 20:
        mean_close = float(sum(valid_closes) / len(valid_closes))
        deviation = (latest_close - mean_close) / mean_close if mean_close else 0.0
        if deviation <= -0.05:
            score += 1
            reasons.append("现价低于近60日均价")
        elif deviation >= 0.05:
            score -= 1
            reasons.append("现价高于近60日均价")

    valid_pbs = [float(v) for v in recent_pbs if isinstance(v, (int, float)) and v > 0]
    if latest_pb and len(valid_pbs) >= 20:
        median_pb = float(statistics.median(valid_pbs))
        if latest_pb <= median_pb * 0.9:
            score += 1
            reasons.append("PB低于近阶段中位数")
        elif latest_pb >= median_pb * 1.1:
            score -= 1
            reasons.append("PB高于近阶段中位数")

    valid_pes = [float(v) for v in recent_pes if isinstance(v, (int, float)) and v > 0]
    if latest_pe and len(valid_pes) >= 20:
        median_pe = float(statistics.median(valid_pes))
        if latest_pe <= median_pe * 0.9:
            score += 1
            reasons.append("PE低于近阶段中位数")
        elif latest_pe >= median_pe * 1.1:
            score -= 1
            reasons.append("PE高于近阶段中位数")

    level = "高" if score >= 1 else "低"
    reason = "；".join(reasons) if reasons else "估值样本不足，默认低估值信号不成立"
    return level, reason


def strategy_matrix_advice(valuation_level: str, sentiment_level: str) -> str:
    if valuation_level == "高" and sentiment_level == "悲观":
        return "强买入"
    if valuation_level == "高" and sentiment_level == "乐观":
        return "持有"
    if valuation_level == "低" and sentiment_level == "乐观":
        return "高风险"
    if valuation_level == "低" and sentiment_level == "悲观":
        return "避免"
    return "观望"


def strategy_summary(valuation_level: str, sentiment_level: str, matrix_advice: str, trend_conclusion: str | None) -> str:
    base = f"基本面价值={valuation_level}，市场情绪={sentiment_level}，投资含义={matrix_advice}。"
    if trend_conclusion:
        return f"{base}{trend_conclusion}"
    return base


def _sentiment_to_signal(
    norm_score: float,
    *,
    positive_threshold: float = 0.55,
    negative_threshold: float = 0.45,
) -> int:
    if norm_score >= positive_threshold:
        return 1
    if norm_score <= negative_threshold:
        return -1
    return 0


def _return_to_signal(day_return: float, *, flat_band: float = 0.0001) -> int:
    if day_return > flat_band:
        return 1
    if day_return < -flat_band:
        return -1
    return 0


def corr_with_next_day_return(series: list[tuple[date, float, float | None]]) -> tuple[float | None, int, dict[str, Any]]:
    """
    Compatibility note:
    - Keep historical function name for API/DB compatibility.
    - Actual metric now measures same-day signal correlation.

    series: sorted by date ascending, each item = (trade_date, sentiment_norm, close).
    Build two 3-class signals for each day t (from second row onward):
      sentiment_t in {+1, 0, -1}  (积极/中立/消极)
      price_t     in {+1, 0, -1}  (涨/平/跌), based on (close_t-close_{t-1})/close_{t-1}
    Then compute Pearson correlation on encoded discrete signals:
      corr(sentiment_signal_t, price_signal_t)
    """
    positive_threshold = 0.55
    negative_threshold = 0.45
    flat_band = 0.0001  # absolute same-day return within ±0.01% is treated as "flat".

    if len(series) < 2:
        return None, 0, {
            "metric_version": "same_day_signal_v1",
            "sample_size": 0,
        }

    xs: list[float] = []
    ys: list[float] = []
    matched = 0
    opposite = 0
    neutral_involved = 0
    confusion: dict[str, int] = {
        "pos_up": 0,
        "pos_flat": 0,
        "pos_down": 0,
        "neu_up": 0,
        "neu_flat": 0,
        "neu_down": 0,
        "neg_up": 0,
        "neg_flat": 0,
        "neg_down": 0,
    }

    for idx in range(1, len(series)):
        _, sentiment_today, close_today = series[idx]
        _, _, close_prev = series[idx - 1]
        if close_today is None or close_prev is None or close_prev <= 0:
            continue

        day_return = float((close_today - close_prev) / close_prev)
        sentiment_signal = _sentiment_to_signal(
            float(sentiment_today),
            positive_threshold=positive_threshold,
            negative_threshold=negative_threshold,
        )
        price_signal = _return_to_signal(day_return, flat_band=flat_band)

        xs.append(float(sentiment_signal))
        ys.append(float(price_signal))

        if sentiment_signal == price_signal:
            matched += 1
        if sentiment_signal != 0 and sentiment_signal == -price_signal:
            opposite += 1
        if sentiment_signal == 0 or price_signal == 0:
            neutral_involved += 1

        row_key = "pos" if sentiment_signal > 0 else "neg" if sentiment_signal < 0 else "neu"
        col_key = "up" if price_signal > 0 else "down" if price_signal < 0 else "flat"
        confusion[f"{row_key}_{col_key}"] += 1

    n = len(xs)
    meta: dict[str, Any] = {
        "metric_version": "same_day_signal_v1",
        "definition": "corr(sentiment_signal_t, price_signal_t), t uses same-day close vs previous close",
        "sentiment_thresholds": {
            "positive": positive_threshold,
            "negative": negative_threshold,
        },
        "price_flat_band": flat_band,
        "sample_size": n,
        "agreement_rate": round(matched / n, 4) if n > 0 else None,
        "opposite_rate": round(opposite / n, 4) if n > 0 else None,
        "neutral_involved_rate": round(neutral_involved / n, 4) if n > 0 else None,
        "confusion_matrix": confusion,
    }

    if n < 6:
        return None, n, meta

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom <= 1e-12:
        return None, n, meta
    corr = clip(cov / denom, -1.0, 1.0)
    meta["corr_method"] = "pearson_on_discrete_signals"
    return corr, n, meta


def reliability_label(corr_value: float | None, sample_size: int) -> str:
    if sample_size < 6:
        return "数据不足"
    if corr_value is None:
        return "信号单一"
    abs_corr = abs(corr_value)
    if abs_corr >= 0.50:
        return "高可靠"
    if abs_corr >= 0.30:
        return "中等可靠"
    return "较低可靠"
