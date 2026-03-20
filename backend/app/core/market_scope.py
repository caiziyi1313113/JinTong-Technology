from __future__ import annotations

from typing import Iterable

TARGET_MARKET = "SZ_MAIN_A"
SZ_MAIN_A_PREFIXES: tuple[str, ...] = ("000", "001", "002", "003")


def normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def exchange_from_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "UNKNOWN"


def market_from_symbol(symbol: str) -> str:
    code = normalize_symbol(symbol)
    if code.startswith(SZ_MAIN_A_PREFIXES):
        return "SZ_MAIN_A"
    if code.startswith(("300", "301")):
        return "SZ_CHINEXT_A"
    if code.startswith(("600", "601", "603", "605")):
        return "SH_MAIN_A"
    if code.startswith("688"):
        return "SH_STAR_A"
    if code.startswith("900"):
        return "SH_B"
    if code.startswith("200"):
        return "SZ_B"
    if code.startswith(("4", "8")):
        return "BJ_A"
    return "UNKNOWN"


def is_target_symbol(symbol: str) -> bool:
    return market_from_symbol(symbol) == TARGET_MARKET


def filter_target_symbols(symbols: Iterable[str]) -> list[str]:
    out: list[str] = []
    for symbol in symbols:
        code = normalize_symbol(symbol)
        if code and is_target_symbol(code):
            out.append(code)
    return out

