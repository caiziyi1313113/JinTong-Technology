from __future__ import annotations

import html
import re

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
EMOTICON_RE = re.compile(r"\[[^\[\]]{1,10}\]")
WHITESPACE_RE = re.compile(r"\s+")
EMOJI_RE = re.compile(r"[\U00010000-\U0010FFFF]")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
MOJIBAKE_RE = re.compile(
    r"(?:\u00e2\u20ac|\u00c3|\u00c2|[\u00e6\u00e5\u00e7\u00e9\u00e8\u00ea\u00eb\u00ec\u00ed\u00ee\u00ef\u00f0\u00f1\u00f2\u00f3\u00f4\u00f5\u00f6\u00f8\u00f9\u00fa\u00fb\u00fc\u00fd\u00fe\u00ff]|\ufffd)"
)


def _text_quality_score(text: str) -> int:
    cjk = len(CJK_RE.findall(text))
    mojibake = len(MOJIBAKE_RE.findall(text))
    replacement = text.count("\ufffd")
    control = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
    return cjk * 3 - mojibake * 2 - replacement * 8 - control * 8


def _looks_like_mojibake(text: str) -> bool:
    if not text:
        return False
    mojibake = len(MOJIBAKE_RE.findall(text))
    if mojibake < 2:
        return False
    cjk = len(CJK_RE.findall(text))
    return mojibake >= max(2, cjk)


def repair_mojibake_text(text: str) -> str:
    """
    Repair common mojibake for Chinese text, such as UTF-8 bytes decoded as latin1/cp1252.
    """
    raw = str(text or "")
    if not raw:
        return ""
    if not _looks_like_mojibake(raw):
        return raw

    transforms = (
        ("latin-1", "utf-8"),
        ("cp1252", "utf-8"),
    )

    candidates: list[str] = [raw]
    seen = {raw}

    for source_encoding, target_encoding in transforms:
        try:
            candidate = raw.encode(source_encoding).decode(target_encoding)
        except Exception:
            continue
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    raw_score = _text_quality_score(raw)
    best = raw
    best_score = raw_score

    for candidate in candidates[1:]:
        score = _text_quality_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score

    if best_score >= raw_score + 3:
        return best
    return raw


def clean_sentiment_text(text: str, *, max_chars: int = 200, min_chars: int = 3) -> str:
    """
    Normalize raw forum/news text for downstream sentiment models.
    """
    cleaned = repair_mojibake_text(str(text or ""))
    cleaned = html.unescape(cleaned)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = EMOTICON_RE.sub(" ", cleaned)
    cleaned = EMOJI_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = WHITESPACE_RE.sub(" ", cleaned).strip()

    if not cleaned:
        return ""
    if len(cleaned) < min_chars:
        return ""
    if len(cleaned) > max_chars:
        return ""
    return cleaned
