import re

from app.services.sentiment.lexicon import POSITIVE_WORDS, NEGATIVE_WORDS

'''
这是一个词典情绪打分：

lexicon.py：定义正面/负面词集合（POSITIVE_WORDS/NEGATIVE_WORDS）。

lexicon

sentiment.py：用正则提取英文单词，数正负词差值，得到一个情绪分数。

sentiment

score_text(text) 返回一个浮点数：正面越多越大，负面越多越小
'''

WORD_RE = re.compile(r"[a-zA-Z]+")


def score_text(text: str) -> float:
    tokens = [t.lower() for t in WORD_RE.findall(text or "")]
    if not tokens:
        return 0.0
    pos = sum(1 for t in tokens if t in POSITIVE_WORDS)
    neg = sum(1 for t in tokens if t in NEGATIVE_WORDS)
    return (pos - neg) / max(1, len(tokens) / 5)
