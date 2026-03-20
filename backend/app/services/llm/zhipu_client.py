from __future__ import annotations

import ast
import json
import logging
import random
import re
import threading
import time
from typing import Any

from app.core.config import settings


logger = logging.getLogger(__name__)


class LLMClientError(RuntimeError):
    pass


class ZhipuClient:
    def __init__(self) -> None:
        self.api_key = settings.zhipu_api_key.strip()
        self.role_api_keys = {
            "news": str(settings.zhipu_api_key_news or "").strip(),
            "stock_data": str(settings.zhipu_api_key_stock_data or "").strip(),
            "macro": str(settings.zhipu_api_key_macro or "").strip(),
            "financial": str(settings.zhipu_api_key_financial or "").strip(),
            "fundamental": str(settings.zhipu_api_key_fundamental or "").strip(),
            "investment": str(settings.zhipu_api_key_investment or "").strip(),
        }
        self.model = settings.zhipu_model
        self.timeout = settings.llm_timeout_seconds
        self.thinking_type = settings.zhipu_thinking_type.strip()
        self.max_tokens = settings.zhipu_max_tokens
        self.retry_max_attempts = max(1, int(settings.zhipu_retry_max_attempts))
        self.retry_base_delay_seconds = max(0.0, float(settings.zhipu_retry_base_delay_seconds))
        self.retry_max_delay_seconds = max(0.0, float(settings.zhipu_retry_max_delay_seconds))
        self.retry_jitter_seconds = max(0.0, float(settings.zhipu_retry_jitter_seconds))
        self.rate_limit_interval_seconds = max(0.0, float(settings.zhipu_rate_limit_interval_seconds))
        self.allow_cross_role_key_fallback = bool(settings.zhipu_allow_cross_role_key_fallback)
        # Thread-safe state for key-level pacing and client reuse.
        self._rate_limit_lock = threading.Lock()
        self._key_next_allowed_at: dict[str, float] = {}
        self._client_lock = threading.Lock()
        self._client_cache: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        if self.allow_cross_role_key_fallback:
            return bool(self.api_key or any(self.role_api_keys.values()))
        # Strict role-key mode: do not treat generic key as LLM-enabled for expert flows.
        return bool(any(self.role_api_keys.values()))

    def _resolve_api_key(self, role: str | None = None, api_key: str | None = None) -> str:
        # Priority: explicit override > role-scoped key > default key.
        # In strict mode (no cross-role fallback), role call does not fall back to default key.
        if api_key and str(api_key).strip():
            return str(api_key).strip()
        role_name = str(role or "").strip()
        role_key = self.role_api_keys.get(role_name)
        if role_key:
            return role_key
        if role_name and not self.allow_cross_role_key_fallback:
            return ""
        return self.api_key

    def _candidate_api_keys(self, role: str | None = None, api_key: str | None = None) -> list[str]:
        """
        Build ordered key candidates for one request.

        Order:
        1) explicit api_key (if provided)
        2) if role is provided:
           - strict mode: role-scoped key only
           - fallback mode: role-scoped key -> default key -> other role keys
        3) if role is not provided:
           - default key, then role keys (fallback mode only)
        """
        explicit = str(api_key or "").strip()
        if explicit:
            return [explicit]

        keys: list[str] = []

        def push(value: str | None) -> None:
            token = str(value or "").strip()
            if token and token not in keys:
                keys.append(token)

        role_name = str(role or "").strip()
        if role_name:
            # Strict routing: when role is provided, use only that role key.
            if not self.allow_cross_role_key_fallback:
                push(self.role_api_keys.get(role_name))
                return keys

            push(self.role_api_keys.get(role_name))
            push(self.api_key)
            for role_key in self.role_api_keys.values():
                push(role_key)
            return keys

        # No role: keep generic behavior.
        push(self.api_key)
        if self.allow_cross_role_key_fallback:
            for role_key in self.role_api_keys.values():
                push(role_key)
        return keys

    @staticmethod
    def _mask_key(key: str) -> str:
        value = str(key or "")
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"

    def _create_client(self, api_key: str):
        try:
            from zai import ZhipuAiClient  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise LLMClientError(
                "zai-sdk is not installed. Run `pip install zai-sdk==0.2.2`."
            ) from exc
        return ZhipuAiClient(api_key=api_key)

    def _get_client(self, api_key: str):
        with self._client_lock:
            cached = self._client_cache.get(api_key)
            if cached is not None:
                return cached
            created = self._create_client(api_key)
            self._client_cache[api_key] = created
            return created

    @staticmethod
    def _read_nested(source: Any, path: list[Any]) -> Any:
        cursor = source
        for key in path:
            if cursor is None:
                return None
            if isinstance(key, int):
                if isinstance(cursor, list) and len(cursor) > key:
                    cursor = cursor[key]
                else:
                    return None
                continue
            if isinstance(cursor, dict):
                cursor = cursor.get(key)
            else:
                cursor = getattr(cursor, key, None)
        return cursor

    def _extract_content(self, response: Any) -> str:
        content = self._read_nested(response, ["choices", 0, "message", "content"])
        if isinstance(content, str) and content.strip():
            return content

        # Some SDK versions may return list-style content blocks.
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("content")
                else:
                    text = getattr(part, "text", None) or getattr(part, "content", None)
                if text:
                    chunks.append(str(text))
            if chunks:
                return "".join(chunks)

        message = self._read_nested(response, ["choices", 0, "message"])
        if message is not None:
            message_text = str(message)
            if message_text.strip():
                return message_text

        response_text = str(response)
        if response_text.strip():
            return response_text

        raise LLMClientError("Zhipu response content is empty")

    def _extract_json(self, content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if not text:
            raise LLMClientError("LLM response is empty")

        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
        fenced_body = fenced.group(1).strip() if fenced else ""

        balanced = self._extract_first_balanced_object(text)
        if not balanced and fenced_body:
            balanced = self._extract_first_balanced_object(fenced_body)

        candidates: list[str] = []
        for candidate in (text, fenced_body, balanced):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        parse_errors: list[str] = []
        for candidate in candidates:
            cleaned = self._clean_json_text_with_script(candidate)
            prepared_candidates = [candidate]
            if cleaned and cleaned != candidate:
                prepared_candidates.append(cleaned)
            for prepared in prepared_candidates:
                if not prepared:
                    continue
                try:
                    parsed = json.loads(prepared)
                    as_object = self._coerce_json_object(parsed)
                    if as_object is not None:
                        return as_object
                except json.JSONDecodeError as exc:
                    parse_errors.append(str(exc))

                try:
                    literal = ast.literal_eval(prepared)
                    as_object = self._coerce_json_object(literal)
                    if as_object is not None:
                        return as_object
                except Exception:
                    pass

        err_tail = f" | parse_errors={parse_errors[:2]}" if parse_errors else ""
        raise LLMClientError(f"LLM response does not contain valid JSON object{err_tail}")

    @staticmethod
    def _coerce_json_object(parsed: Any) -> dict[str, Any] | None:
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
        return None

    @staticmethod
    def _extract_first_balanced_object(text: str) -> str:
        source = str(text or "")
        start = source.find("{")
        if start < 0:
            return ""
        in_string = False
        escaping = False
        depth = 0
        begin = -1
        for idx in range(start, len(source)):
            ch = source[idx]
            if in_string:
                if escaping:
                    escaping = False
                elif ch == "\\":
                    escaping = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                if depth == 0:
                    begin = idx
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and begin >= 0:
                    return source[begin : idx + 1]
                if depth < 0:
                    break
        return ""

    @staticmethod
    def _sanitize_json_like(text: str) -> str:
        source = str(text or "").strip()
        if not source:
            return source

        if source.startswith("```") and source.endswith("```"):
            source = re.sub(r"^```(?:json)?\s*|\s*```$", "", source, flags=re.IGNORECASE).strip()

        first = ZhipuClient._extract_first_balanced_object(source)
        if first:
            source = first

        translate_table = str.maketrans(
            {
                "\u201c": '"',
                "\u201d": '"',
                "\u2018": "'",
                "\u2019": "'",
                "\uff1a": ":",
                "\uff0c": ",",
                "\u3010": "[",
                "\u3011": "]",
                "\uff08": "(",
                "\uff09": ")",
            }
        )
        source = source.translate(translate_table).lstrip("\ufeff")

        source = re.sub(r"//[^\n\r]*", "", source)
        source = re.sub(r"/\*[\s\S]*?\*/", "", source)
        source = re.sub(r",(\s*[}\]])", r"\1", source)
        source = re.sub(r"\bNone\b", "null", source)
        source = re.sub(r"\bTrue\b", "true", source)
        source = re.sub(r"\bFalse\b", "false", source)
        return source.strip()

    @staticmethod
    def _clean_json_text_with_script(text: str) -> str:
        """
        Internal JSON cleanup script:
        normalize noisy LLM text to json-like payload before parsing.
        """
        return ZhipuClient._sanitize_json_like(text)

    @staticmethod
    def _is_response_format_unsupported(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            "response_format" in text
            and (
                "unsupported" in text
                or "unknown" in text
                or "invalid" in text
                or "unexpected" in text
                or "not allow" in text
            )
        )

    @staticmethod
    def _is_json_parse_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            "valid json object" in text
            or "failed to parse json" in text
            or "json is not an object" in text
        )

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status
        response = getattr(exc, "response", None)
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
        matched = re.search(r"Error code:\s*(\d{3})", str(exc))
        if matched:
            try:
                return int(matched.group(1))
            except Exception:
                return None
        return None

    @classmethod
    def _is_rate_limited(cls, exc: Exception) -> bool:
        status_code = cls._extract_status_code(exc)
        if status_code == 429:
            return True
        text = str(exc or "")
        lowered = text.lower()
        return (
            "1302" in text
            or '"code":"1302"' in lowered
            or "rate limit" in lowered
            or "too many requests" in lowered
            or ("429" in lowered and "request" in lowered)
        )

    def _wait_for_key_slot(self, api_key: str) -> None:
        """
        Apply lightweight per-key pacing so concurrent experts don't spike one key.
        """
        interval = self.rate_limit_interval_seconds
        if interval <= 0:
            return

        wait_seconds = 0.0
        with self._rate_limit_lock:
            now = time.monotonic()
            next_ready = self._key_next_allowed_at.get(api_key, now)
            if next_ready > now:
                wait_seconds = next_ready - now
                self._key_next_allowed_at[api_key] = next_ready + interval
            else:
                self._key_next_allowed_at[api_key] = now + interval
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _backoff_sleep(self, retry_index: int) -> float:
        """
        retry_index starts at 1 for first retry.
        """
        base = self.retry_base_delay_seconds * (2 ** max(0, retry_index - 1))
        delay = min(base, self.retry_max_delay_seconds)
        jitter = random.uniform(0.0, self.retry_jitter_seconds) if self.retry_jitter_seconds > 0 else 0.0
        total = max(0.0, delay + jitter)
        if total > 0:
            time.sleep(total)
        return total

    def chat_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        role: str | None = None,
        api_key: str | None = None,
        strict_json: bool = False,
    ) -> dict[str, Any]:
        candidate_keys = self._candidate_api_keys(role=role, api_key=api_key)
        if not candidate_keys:
            raise LLMClientError("Zhipu API key is not configured")

        effective_system_prompt = system_prompt
        if strict_json:
            effective_system_prompt = (
                f"{system_prompt}\n\n"
                "[Output Contract]\n"
                "Return one valid JSON object only.\n"
                "Do not output markdown, code fences, prose, or any prefix/suffix text.\n"
                "Use double-quoted keys, true/false booleans, and null for empty values."
            )

        request_payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": effective_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if self.thinking_type:
            request_payload["thinking"] = {"type": self.thinking_type}
        response_format_enabled = bool(strict_json)

        last_error: Exception | None = None
        total_keys = len(candidate_keys)
        for key_index, resolved_key in enumerate(candidate_keys, start=1):
            client = self._get_client(resolved_key)
            for attempt in range(1, self.retry_max_attempts + 1):
                self._wait_for_key_slot(resolved_key)
                logger.info(
                    "llm request start | model=%s role=%s key=%s attempt=%s/%s key_idx=%s/%s thinking_type=%s user_prompt_len=%s",
                    self.model,
                    role or "default",
                    self._mask_key(resolved_key),
                    attempt,
                    self.retry_max_attempts,
                    key_index,
                    total_keys,
                    self.thinking_type or "none",
                    len(user_prompt),
                )
                try:
                    payload_for_call = dict(request_payload)
                    if response_format_enabled:
                        payload_for_call["response_format"] = {"type": "json_object"}
                    response = client.chat.completions.create(**payload_for_call)
                    content = self._extract_content(response)
                    logger.info(
                        "llm request done | model=%s role=%s key=%s content_len=%s strict_json=%s",
                        self.model,
                        role or "default",
                        self._mask_key(resolved_key),
                        len(content or ""),
                        strict_json,
                    )
                    parsed = self._extract_json(content)
                    logger.info(
                        "llm json parsed | model=%s role=%s key=%s keys=%s",
                        self.model,
                        role or "default",
                        self._mask_key(resolved_key),
                        sorted(parsed.keys()),
                    )
                    return parsed
                except Exception as exc:
                    if response_format_enabled and self._is_response_format_unsupported(exc):
                        response_format_enabled = False
                        logger.warning(
                            "llm response_format unsupported, downgrade format mode | model=%s role=%s key=%s error=%s",
                            self.model,
                            role or "default",
                            self._mask_key(resolved_key),
                            exc,
                        )
                        continue

                    if strict_json and self._is_json_parse_error(exc):
                        logger.warning(
                            "llm strict-json parse failed, stop retry and fallback | model=%s role=%s key=%s error=%s",
                            self.model,
                            role or "default",
                            self._mask_key(resolved_key),
                            exc,
                        )
                        raise LLMClientError(f"Strict JSON parse failed: {exc}") from exc

                    last_error = exc
                    rate_limited = self._is_rate_limited(exc)
                    has_more_keys = key_index < total_keys

                    if rate_limited and has_more_keys:
                        logger.warning(
                            "llm rate-limited, rotate key | model=%s role=%s key=%s key_idx=%s/%s error=%s",
                            self.model,
                            role or "default",
                            self._mask_key(resolved_key),
                            key_index,
                            total_keys,
                            exc,
                        )
                        break

                    if attempt < self.retry_max_attempts:
                        delay = self._backoff_sleep(attempt)
                        logger.warning(
                            "llm request failed, retrying | model=%s role=%s key=%s attempt=%s/%s retry_in=%.2fs rate_limited=%s error=%s",
                            self.model,
                            role or "default",
                            self._mask_key(resolved_key),
                            attempt,
                            self.retry_max_attempts,
                            delay,
                            rate_limited,
                            exc,
                        )
                        continue

                    if has_more_keys:
                        logger.warning(
                            "llm request failed, rotate key | model=%s role=%s key=%s key_idx=%s/%s error=%s",
                            self.model,
                            role or "default",
                            self._mask_key(resolved_key),
                            key_index,
                            total_keys,
                            exc,
                        )
                        break

                    logger.exception(
                        "llm request failed | model=%s role=%s key=%s attempts=%s error=%s",
                        self.model,
                        role or "default",
                        self._mask_key(resolved_key),
                        self.retry_max_attempts,
                        exc,
                    )
                    raise LLMClientError(
                        f"Zhipu request failed after {self.retry_max_attempts} attempts: {exc}"
                    ) from exc

        if last_error is not None:
            raise LLMClientError(f"Zhipu request failed after key rotation: {last_error}") from last_error
        raise LLMClientError("Zhipu request failed")


zhipu_client = ZhipuClient()
