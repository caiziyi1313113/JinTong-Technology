from __future__ import annotations

from datetime import date, timedelta
import time
from typing import Any

from app.services.financial_analysis.eastmoney_financial import to_float


def _safe_float(value: Any) -> float | None:
    return to_float(value)


def _percentile(value: float | None, pool: list[float]) -> float | None:
    if value is None or not pool:
        return None
    sorted_pool = sorted(pool)
    less_equal = sum(1 for item in sorted_pool if item <= value)
    return less_equal / len(sorted_pool)


class IndustryContextBuilder:
    """
    Industry board and peer valuation context from AKShare (Eastmoney / THS).
    """

    def __init__(self, *, request_interval_seconds: float = 0.35) -> None:
        self.request_interval_seconds = max(0.05, float(request_interval_seconds))

    def _ak(self):
        import akshare as ak  # type: ignore

        return ak

    def _sleep(self) -> None:
        time.sleep(self.request_interval_seconds)

    @staticmethod
    def _pick_industry_board(industry_name: str | None, board_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not industry_name:
            return board_rows[0] if board_rows else None

        name = str(industry_name).strip().lower()
        if not name:
            return board_rows[0] if board_rows else None

        exact = [row for row in board_rows if str(row.get("板块名称", "")).strip().lower() == name]
        if exact:
            return exact[0]

        contains = [row for row in board_rows if name in str(row.get("板块名称", "")).strip().lower()]
        if contains:
            return contains[0]

        return board_rows[0] if board_rows else None

    def build(
        self,
        *,
        symbol: str,
        industry_name: str | None,
        company_pe: float | None,
        company_pb: float | None,
    ) -> dict[str, Any]:
        try:
            ak = self._ak()
        except Exception as exc:
            return {"available": False, "reason": f"akshare_unavailable: {exc}"}

        try:
            boards_df = ak.stock_board_industry_name_em()
        except Exception as exc:
            return {"available": False, "reason": f"industry_board_list_failed: {exc}"}
        if boards_df is None or boards_df.empty:
            return {"available": False, "reason": "industry_board_list_empty"}

        board_rows = []
        for _, row in boards_df.iterrows():
            board_rows.append({str(k): row.get(k) for k in boards_df.columns})

        board = self._pick_industry_board(industry_name, board_rows)
        if not board:
            return {"available": False, "reason": "industry_board_not_found"}

        board_name = str(board.get("板块名称", "")).strip() or None
        board_code = str(board.get("板块代码", "")).strip() or None
        if not board_name:
            return {"available": False, "reason": "industry_board_name_empty"}

        self._sleep()
        peers_df = None
        peers_error: str | None = None
        for candidate in [board_name, board_code]:
            if not candidate:
                continue
            try:
                peers_df = ak.stock_board_industry_cons_em(symbol=str(candidate))
            except Exception as exc:
                peers_error = str(exc)
                peers_df = None
                continue
            if peers_df is not None and not peers_df.empty:
                break

        peer_rows: list[dict[str, Any]] = []
        if peers_df is not None and not peers_df.empty:
            for _, row in peers_df.iterrows():
                peer_rows.append(
                    {
                        "code": str(row.get("代码", "")).zfill(6),
                        "name": str(row.get("名称", "")).strip(),
                        "latest_price": _safe_float(row.get("最新价")),
                        "chg_pct": _safe_float(row.get("涨跌幅")),
                        "turnover_rate": _safe_float(row.get("换手率")),
                        "pe_dynamic": _safe_float(row.get("市盈率-动态")),
                        "pb": _safe_float(row.get("市净率")),
                    }
                )
        peer_rows = [row for row in peer_rows if row.get("code")]
        pe_pool = [row["pe_dynamic"] for row in peer_rows if row.get("pe_dynamic") is not None and row["pe_dynamic"] > 0]
        pb_pool = [row["pb"] for row in peer_rows if row.get("pb") is not None and row["pb"] > 0]

        pe_median = sorted(pe_pool)[len(pe_pool) // 2] if pe_pool else None
        pb_median = sorted(pb_pool)[len(pb_pool) // 2] if pb_pool else None
        pe_percentile = _percentile(company_pe, pe_pool)
        pb_percentile = _percentile(company_pb, pb_pool)

        target_peer = None
        for row in peer_rows:
            if row.get("code") == symbol:
                target_peer = row
                break

        self._sleep()
        start_date = (date.today() - timedelta(days=180)).strftime("%Y%m%d")
        end_date = date.today().strftime("%Y%m%d")
        trend = {}
        try:
            hist_df = ak.stock_board_industry_hist_em(
                symbol=board_name,
                start_date=start_date,
                end_date=end_date,
                period="日k",
                adjust="",
            )
            if hist_df is not None and not hist_df.empty:
                closes = [float(v) for v in hist_df["收盘"].tolist() if _safe_float(v) is not None]
                if len(closes) >= 61:
                    trend = {
                        "ret_20d": (closes[-1] / closes[-21] - 1.0) if closes[-21] else None,
                        "ret_60d": (closes[-1] / closes[-61] - 1.0) if closes[-61] else None,
                    }
                elif len(closes) >= 21:
                    trend = {
                        "ret_20d": (closes[-1] / closes[-21] - 1.0) if closes[-21] else None,
                        "ret_60d": None,
                    }
        except Exception:
            trend = {}

        return {
            "available": True,
            "industry_name": industry_name or board_name,
            "board_name": board_name,
            "board_code": board_code,
            "board_snapshot": {
                "latest_pct": _safe_float(board.get("涨跌幅")),
                "turnover_rate": _safe_float(board.get("换手率")),
                "up_count": int(_safe_float(board.get("上涨家数")) or 0),
                "down_count": int(_safe_float(board.get("下跌家数")) or 0),
            },
            "peer_count": len(peer_rows),
            "target_peer": target_peer,
            "peer_valuation_stats": {
                "pe_median": pe_median,
                "pb_median": pb_median,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
            },
            "peer_samples": peer_rows[:20],
            "industry_trend": trend,
            "errors": {"peer_error": peers_error} if peers_error else {},
            "sources": [
                "ak.stock_board_industry_name_em",
                "ak.stock_board_industry_cons_em",
                "ak.stock_board_industry_hist_em",
            ],
        }

