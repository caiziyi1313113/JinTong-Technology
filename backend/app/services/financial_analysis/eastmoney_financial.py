from __future__ import annotations

from typing import Any


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:
            return None
        return number

    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "None", "nan", "NaN"}:
        return None

    # Support common Chinese unit suffixes.
    unit_scale = 1.0
    if text.endswith("%"):
        text = text[:-1]
    elif text.endswith("亿"):
        unit_scale = 1e8
        text = text[:-1]
    elif text.endswith("万"):
        unit_scale = 1e4
        text = text[:-1]

    try:
        number = float(text)
    except Exception:
        return None
    if number != number:
        return None
    return number * unit_scale


def pick_first_numeric(raw: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        if key not in raw:
            continue
        value = to_float(raw.get(key))
        if value is not None:
            return value
    return None


def pick_first_text(raw: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text in {"", "-", "--", "None", "nan", "NaN"}:
            continue
        return text
    return None


def extract_core_metrics(raw: dict[str, Any]) -> dict[str, float | None]:
    """
    Unified extraction for Eastmoney/CNInfo mixed raw payloads.
    This function only maps raw keys to normalized financial fields.
    """
    revenue = pick_first_numeric(
        raw,
        [
            "TOTALOPERATEREVE",
            "TOTAL_OPERATE_INCOME",
            "OPERATE_INCOME",
            "REVENUE",
            "F035N",
            "F006N",
        ],
    )
    net_profit = pick_first_numeric(
        raw,
        [
            "PARENTNETPROFIT",
            "NETPROFIT",
            "NET_PROFIT",
            "PARENT_NETPROFIT",
            "F028N",
            "F027N",
            "F012N",
        ],
    )

    # Balance-sheet keys: expanded for Eastmoney GBALANCE variants.
    total_assets = pick_first_numeric(
        raw,
        [
            "TOTAL_ASSETS",
            "TOTAL_ASSET",
            "ASSETS_TOTAL",
            "ASSET_TOTAL",
            "TOTALASSETS",
            "TOTASSET",
            "F038N",
            "F040N",
        ],
    )
    total_liabilities = pick_first_numeric(
        raw,
        [
            "TOTAL_LIABILITIES",
            "TOTAL_LIAB",
            "TOTAL_LIABILITY",
            "LIABILITIES_TOTAL",
            "TOTLIAB",
            "TOTLIABILITY",
            "TOTALDEBT",
            "F061N",
            "F062N",
        ],
    )
    equity = pick_first_numeric(
        raw,
        [
            "TOTAL_EQUITY",
            "TOTAL_OWNER_EQUITY",
            "TOTAL_OWNER_EQUITIES",
            "TOTAL_OWNERS_EQUITY",
            "TOTAL_OWNERS_EQUITIES",
            "SHAREHOLDERS_EQUITY",
            "NET_ASSETS",
            "F070N",
            "F071N",
            "F073N",
        ],
    )
    current_assets = pick_first_numeric(
        raw,
        [
            "CURRENT_ASSETS",
            "TOTAL_CURRENT_ASSETS",
            "TOT_CUR_ASSETS",
            "CUR_ASSETS",
            "F019N",
            "F018N",
            "TCA",
        ],
    )
    current_liabilities = pick_first_numeric(
        raw,
        [
            "CURRENT_LIABILITIES",
            "TOTAL_CURRENT_LIAB",
            "TOTAL_CURRENT_LIABILITIES",
            "TOT_CUR_LIAB",
            "CUR_LIAB",
            "F052N",
            "F051N",
            "TCL",
        ],
    )

    eps = pick_first_numeric(raw, ["EPSJB", "EPS", "BASIC_EPS", "F003N"])
    bps = pick_first_numeric(raw, ["BPS", "F008N", "NET_ASSET_PER_SHARE"])
    roe = pick_first_numeric(raw, ["ROEJQ", "ROE", "F014N"])
    gross_margin = pick_first_numeric(raw, ["XSMLL", "GROSS_MARGIN", "F078N", "GROSSPROFITMARGIN"])
    operating_cashflow = pick_first_numeric(raw, ["MGJYXJJE", "NETCASH_OPERATE", "OPERATE_NET_CASHFLOW", "F015N"])

    return {
        "revenue": revenue,
        "net_profit": net_profit,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "equity": equity,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "eps": eps,
        "bps": bps,
        "roe": roe,
        "gross_margin": gross_margin,
        "operating_cashflow": operating_cashflow,
    }

