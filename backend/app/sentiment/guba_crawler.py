from __future__ import annotations

from datetime import date, datetime
import json
import logging
import re
from typing import Any

import requests

from app.core.market_scope import normalize_symbol
from app.sentiment.cleaning import clean_sentiment_text, repair_mojibake_text
from app.sentiment.domain import SentimentSourceItem

logger = logging.getLogger(__name__)

ARTICLE_LIST_RE = re.compile(r"var\s+article_list\s*=\s*(\{.*?\});\s*var\s+other_list", re.DOTALL)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MOJIBAKE_RE = re.compile(
    r"(?:\u00e2\u20ac|\u00c3|\u00c2|[\u00e6\u00e5\u00e7\u00e9\u00e8\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f0\u00f1\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8\u00f9\u00fa\u00fb\u00fc\u00fd\u00fe\u00ff]|\ufffd)"
)
PAGE_TIMEOUT_SECONDS = 18
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


def _page_url(code: str, page: int) -> str:
    if page <= 1:
        return f"https://guba.eastmoney.com/list,{code}.html"
    return f"https://guba.eastmoney.com/list,{code}_{page}.html"


def _parse_publish_time(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # Some list records only expose "03-13 22:40".
    for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            temp = datetime.strptime(text, fmt)
            return temp.replace(year=datetime.now().year)
        except ValueError:
            continue
    return None


def _extract_article_list(html_text: str) -> dict:
    match = ARTICLE_LIST_RE.search(html_text)
    if not match:
        return {}
    blob = re.sub(r":\s*undefined", ": null", match.group(1))
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        logger.warning("guba parse failed: invalid article_list JSON")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _payload_quality(payload: dict) -> int:
    rows = payload.get("re")
    if not isinstance(rows, list):
        return -10_000
    sample_parts: list[str] = []
    for row in rows[:40]:
        if not isinstance(row, dict):
            continue
        sample_parts.append(str(row.get("post_title") or ""))
        sample_parts.append(str(row.get("post_content") or ""))
    sample = " ".join(part for part in sample_parts if part).strip()
    if not sample:
        return 0

    cjk = len(CJK_RE.findall(sample))
    mojibake = len(MOJIBAKE_RE.findall(sample))
    replacement = sample.count("\ufffd")
    return cjk * 3 - mojibake * 2 - replacement * 8


def _candidate_encodings(response: requests.Response) -> list[str]:
    raw_encodings = [
        response.encoding,
        getattr(response, "apparent_encoding", None),
        "utf-8",
        "gb18030",
        "gbk",
        "gb2312",
        "latin-1",
    ]
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_encodings:
        encoding = str(raw or "").strip().lower()
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        result.append(encoding)
    return result


def _extract_best_article_list(response: requests.Response) -> dict:
    content = response.content or b""
    best_payload: dict | None = None
    best_score = -10_000

    for encoding in _candidate_encodings(response):
        try:
            decoded = content.decode(encoding)
        except Exception:
            continue
        payload = _extract_article_list(decoded)
        if not payload:
            continue
        score = _payload_quality(payload)
        if score > best_score:
            best_payload = payload
            best_score = score

    if best_payload is not None:
        return best_payload
    return _extract_article_list(response.text)


def fetch_guba_posts(
    symbol: str,
    *,
    target_date: date | None,
    max_pages: int = 5,
    max_items: int = 120,
) -> list[SentimentSourceItem]:
    """
    Crawl Eastmoney guba list pages and keep user post texts for sentiment scoring.
    """
    code = normalize_symbol(symbol)
    if not code:
        return []

    session = requests.Session()
    headers = {"User-Agent": UA, "Referer": "https://guba.eastmoney.com/"}

    items: list[SentimentSourceItem] = []
    seen_keys: set[str] = set()

    for page in range(1, max(1, max_pages) + 1):
        url = _page_url(code, page)
        try:
            response = session.get(url, headers=headers, timeout=PAGE_TIMEOUT_SECONDS)
            response.raise_for_status()
        except Exception as exc:
            logger.warning("guba crawl failed | symbol=%s page=%s error=%s", code, page, exc)
            break

        payload = _extract_best_article_list(response)
        rows = payload.get("re")
        if not isinstance(rows, list) or not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_code = normalize_symbol(str(row.get("stockbar_code", "")))
            if row_code != code:
                continue

            # Keep discussion-like posts; skip official news and notices.
            try:
                post_type = int(row.get("post_type", -1))
            except Exception:
                post_type = -1
            if post_type not in {0, 20}:
                continue

            published_at = _parse_publish_time(
                row.get("post_publish_time") or row.get("post_display_time") or row.get("post_last_time")
            )
            if target_date and (published_at is None or published_at.date() != target_date):
                continue

            raw_title = repair_mojibake_text(str(row.get("post_title") or "").strip())
            raw_content = repair_mojibake_text(str(row.get("post_content") or "").strip())
            raw_text = raw_content or raw_title
            cleaned = clean_sentiment_text(raw_text, max_chars=200)
            if not cleaned:
                continue

            external_id = str(row.get("post_id") or "").strip() or None
            dedupe_key = external_id or cleaned
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            source_url = str(row.get("art_unique_url") or "").strip() or None
            if source_url is None and external_id:
                source_url = f"https://guba.eastmoney.com/news,{code},{external_id}.html"

            items.append(
                SentimentSourceItem(
                    source_type="guba",
                    external_id=external_id,
                    source_url=source_url,
                    title=raw_title or None,
                    text=cleaned,
                    published_at=published_at,
                    extra={
                        "post_type": post_type,
                        "post_comment_count": row.get("post_comment_count"),
                        "post_click_count": row.get("post_click_count"),
                        "user_nickname": row.get("user_nickname"),
                    },
                )
            )

            if len(items) >= max(1, max_items):
                return items

    return items
