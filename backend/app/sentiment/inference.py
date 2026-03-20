from __future__ import annotations

from dataclasses import asdict
import logging
import threading
from types import SimpleNamespace

try:
    from app.core.config import settings
except Exception:  # pragma: no cover
    settings = SimpleNamespace(
        sentiment_batch_size=16,
        sentiment_guba_model_name="Fearao/RoBERTa_based_on_eastmoney_guba_comments",
        sentiment_guba_tokenizer_name="uer/roberta-base-finetuned-chinanews-chinese",
        sentiment_news_model_name="yiyanghkust/finbert-tone-chinese",
        hf_cache_dir="",
    )
from app.sentiment.domain import SentimentScoredItem, SentimentSourceItem

logger = logging.getLogger(__name__)


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class DualModelSentimentScorer:
    """
    Two-model sentiment scorer:
    - Guba posts: Fearao/RoBERTa_based_on_eastmoney_guba_comments
    - News: yiyanghkust/finbert-tone-chinese
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guba_pipeline = None
        self._news_pipeline = None
        self._batch_size = int(getattr(settings, "sentiment_batch_size", 16) or 16)
        self._guba_model_name = str(
            getattr(settings, "sentiment_guba_model_name", "Fearao/RoBERTa_based_on_eastmoney_guba_comments")
        )
        self._guba_tokenizer_name = str(
            getattr(settings, "sentiment_guba_tokenizer_name", "uer/roberta-base-finetuned-chinanews-chinese")
        )
        self._news_model_name = str(
            getattr(settings, "sentiment_news_model_name", "yiyanghkust/finbert-tone-chinese")
        )
        self._hf_cache_dir = str(getattr(settings, "hf_cache_dir", "") or "").strip() or None

    def score_news(self, items: list[SentimentSourceItem]) -> list[SentimentScoredItem]:
        if not items:
            return []
        pipeline_obj = self._get_news_pipeline()
        return self._score_with_pipeline(items, pipeline_obj, source_type="news")

    def score_guba(self, items: list[SentimentSourceItem]) -> list[SentimentScoredItem]:
        if not items:
            return []
        pipeline_obj = self._get_guba_pipeline()
        return self._score_with_pipeline(items, pipeline_obj, source_type="guba")

    def _get_news_pipeline(self):
        if self._news_pipeline is not None:
            return self._news_pipeline
        with self._lock:
            if self._news_pipeline is not None:
                return self._news_pipeline
            self._news_pipeline = self._build_pipeline(
                model_name=self._news_model_name,
                tokenizer_name=self._news_model_name,
            )
            return self._news_pipeline

    def _get_guba_pipeline(self):
        if self._guba_pipeline is not None:
            return self._guba_pipeline
        with self._lock:
            if self._guba_pipeline is not None:
                return self._guba_pipeline
            self._guba_pipeline = self._build_pipeline(
                model_name=self._guba_model_name,
                tokenizer_name=self._guba_tokenizer_name,
            )
            return self._guba_pipeline

    def _build_pipeline(self, *, model_name: str, tokenizer_name: str):
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer, TextClassificationPipeline
        except Exception as exc:
            raise RuntimeError("transformers is required for sentiment model inference") from exc

        try:
            import torch  # type: ignore

            device = 0 if torch.cuda.is_available() else -1
        except Exception:
            device = -1

        logger.info("loading sentiment model | model=%s tokenizer=%s device=%s", model_name, tokenizer_name, device)
        model = AutoModelForSequenceClassification.from_pretrained(model_name, cache_dir=self._hf_cache_dir)
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_name, cache_dir=self._hf_cache_dir)
        return TextClassificationPipeline(
            model=model,
            tokenizer=tokenizer,
            return_all_scores=True,
            device=device,
        )

    def _score_with_pipeline(self, items: list[SentimentSourceItem], pipeline_obj, *, source_type: str) -> list[SentimentScoredItem]:
        texts = [item.text for item in items]
        if not texts:
            return []

        try:
            outputs = pipeline_obj(
                texts,
                truncation=True,
                max_length=256,
                batch_size=max(1, self._batch_size),
            )
        except Exception as exc:
            raise RuntimeError(f"sentiment pipeline inference failed: {exc}") from exc

        if not isinstance(outputs, list):
            outputs = []

        # Some transformers versions may return a single list[dict] for one input.
        if outputs and isinstance(outputs[0], dict):
            outputs = [outputs]

        if len(outputs) != len(items):
            # Fallback to single-item inference to avoid index mismatch.
            logger.warning("pipeline output length mismatch, fallback single inference")
            outputs = []
            for text in texts:
                row = pipeline_obj(text, truncation=True, max_length=256)
                if isinstance(row, list) and row and isinstance(row[0], dict):
                    outputs.append(row)
                elif isinstance(row, list):
                    outputs.append(row[0] if row else [])
                else:
                    outputs.append([])

        scored: list[SentimentScoredItem] = []
        for item, raw_scores in zip(items, outputs):
            pos, neu, neg = self._parse_probs(raw_scores, source_type=source_type)
            label = "positive"
            best = pos
            if neg > best:
                label = "negative"
                best = neg
            if neu > best:
                label = "neutral"

            raw = _clip(pos - neg, -1.0, 1.0)
            norm = _clip((raw + 1.0) / 2.0, 0.0, 1.0)
            scored.append(
                SentimentScoredItem(
                    **asdict(item),
                    label=label,
                    positive_prob=pos,
                    neutral_prob=neu,
                    negative_prob=neg,
                    score_raw=raw,
                    score_norm=norm,
                )
            )
        return scored

    def _parse_probs(self, raw_scores, *, source_type: str) -> tuple[float, float, float]:
        pos = 0.0
        neu = 0.0
        neg = 0.0
        if not isinstance(raw_scores, list):
            return pos, neu, neg

        for row in raw_scores:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label", "")).strip()
            score = _clip(float(row.get("score", 0.0) or 0.0), 0.0, 1.0)
            upper = label.upper()
            lower = label.lower()
            if source_type == "news":
                if upper in {"LABEL_1", "POSITIVE"}:
                    pos = max(pos, score)
                elif upper in {"LABEL_2", "NEGATIVE"}:
                    neg = max(neg, score)
                elif upper in {"LABEL_0", "NEUTRAL"}:
                    neu = max(neu, score)
                elif "POS" in upper:
                    pos = max(pos, score)
                elif "NEG" in upper:
                    neg = max(neg, score)
                elif "NEU" in upper:
                    neu = max(neu, score)
            else:
                if "positive" in lower or "pos" in lower:
                    pos = max(pos, score)
                elif "negative" in lower or "neg" in lower:
                    neg = max(neg, score)
                elif "neutral" in lower or "neu" in lower:
                    neu = max(neu, score)

        # Binary model might not have neutral dimension.
        if source_type == "guba" and neu <= 0.0:
            neu = _clip(1.0 - pos - neg, 0.0, 1.0)
        if source_type == "news" and (pos + neu + neg) <= 0.0:
            neu = 1.0

        return _clip(pos, 0.0, 1.0), _clip(neu, 0.0, 1.0), _clip(neg, 0.0, 1.0)
