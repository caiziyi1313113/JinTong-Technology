from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass
class CninfoResponse:
    records: list[dict[str, Any]]
    raw: dict[str, Any]
    object_id_max: int | None = None


class CninfoClientError(RuntimeError):
    pass


class CninfoClient:
    """Thin client for CNInfo webapi endpoints with header/cookie authentication."""

    def __init__(self) -> None:
        self.base_url = settings.cninfo_base_url.rstrip("/")
        self.timeout = max(5, int(settings.cninfo_timeout_seconds))
        self._session = self._build_session()
        self._headers_updated_at: datetime | None = None
        # Load cached headers on startup for non-interactive runs.
        self._load_cached_headers()

    @property
    def enabled(self) -> bool:
        return bool(settings.cninfo_enabled and settings.cninfo_accept_enckey and settings.cninfo_cookie)

    @staticmethod
    def _default_cache_path() -> Path:
        # backend/app/services/data_ingest/cninfo_service.py -> backend/
        backend_root = Path(__file__).resolve().parents[3]
        return backend_root / ".cninfo_headers.json"

    @staticmethod
    def _default_profile_dir() -> Path:
        backend_root = Path(__file__).resolve().parents[3]
        return backend_root / ".cninfo_profile"

    def _cache_path(self) -> Path:
        configured = str(settings.cninfo_headers_cache_file or "").strip()
        return Path(configured).expanduser().resolve() if configured else self._default_cache_path()

    def _profile_dir(self) -> Path:
        configured = str(settings.cninfo_profile_dir or "").strip()
        return Path(configured).expanduser().resolve() if configured else self._default_profile_dir()

    def _cache_file_mtime(self) -> datetime | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            return datetime.utcfromtimestamp(path.stat().st_mtime)
        except Exception:
            return None

    def _cache_file_is_newer(self) -> bool:
        mtime = self._cache_file_mtime()
        if mtime is None:
            return False
        if self._headers_updated_at is None:
            return True
        try:
            known = self._headers_updated_at.replace(tzinfo=None)
        except Exception:
            known = self._headers_updated_at
        return mtime > known

    def _load_cached_headers(self) -> bool:
        path = self._cache_path()
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("cninfo load cached headers failed | file=%s error=%s", path, exc)
            return False

        accept = str(payload.get("accept_enckey") or "").strip()
        cookie = str(payload.get("cookie") or "").strip()
        if not accept or not cookie:
            return False
        prev_accept = str(settings.cninfo_accept_enckey or "").strip()
        prev_cookie = str(settings.cninfo_cookie or "").strip()

        settings.cninfo_accept_enckey = accept
        settings.cninfo_cookie = cookie
        if payload.get("referer"):
            settings.cninfo_referer = str(payload["referer"])
        if payload.get("user_agent"):
            settings.cninfo_user_agent = str(payload["user_agent"])
        raw_updated = payload.get("updated_at")
        if raw_updated:
            try:
                self._headers_updated_at = datetime.fromisoformat(str(raw_updated).replace("Z", "+00:00"))
            except Exception:
                self._headers_updated_at = None

        changed = (accept != prev_accept) or (cookie != prev_cookie)
        log_fn = logger.info if changed else logger.debug
        log_fn(
            "cninfo cached headers loaded | file=%s changed=%s accept_len=%s cookie_len=%s",
            path,
            changed,
            len(settings.cninfo_accept_enckey or ""),
            len(settings.cninfo_cookie or ""),
        )
        return self.enabled

    def _save_cached_headers(self) -> None:
        if not self.enabled:
            return
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.utcnow()
        payload = {
            "accept_enckey": settings.cninfo_accept_enckey,
            "cookie": settings.cninfo_cookie,
            "referer": settings.cninfo_referer,
            "user_agent": settings.cninfo_user_agent,
            "updated_at": now.isoformat() + "Z",
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._headers_updated_at = now
        logger.info("cninfo cached headers saved | file=%s", path)

    def _headers_age_seconds(self) -> float | None:
        if self._headers_updated_at is None:
            return None
        try:
            delta = datetime.utcnow() - self._headers_updated_at.replace(tzinfo=None)
            return max(0.0, float(delta.total_seconds()))
        except Exception:
            return None

    def _headers_stale(self) -> bool:
        max_age = max(0, int(settings.cninfo_header_max_age_seconds))
        if max_age <= 0:
            return False
        age = self._headers_age_seconds()
        if age is None:
            # Unknown age; treat as stale so it can self-heal.
            return True
        return age > max_age

    def ensure_headers(self) -> bool:
        """Ensure headers exist and are fresh enough, auto-refresh if configured."""
        # Long-running backend process: hot-reload newer cache written by
        # external bootstrap/test scripts without requiring process restart.
        if self._cache_file_is_newer():
            self._load_cached_headers()

        if self.enabled and not self._headers_stale():
            return True

        # Missing/stale headers: retry loading from cache first.
        self._load_cached_headers()
        if self.enabled and not self._headers_stale():
            return True

        if not settings.cninfo_auto_bootstrap:
            # Keep backward-compatible behavior: caller may still attempt request
            # with current headers, while request() can detect auth payload failures.
            return self.enabled

        # Try browser refresh; if it fails and old headers exist, keep using old headers.
        ok = self.refresh_headers()
        if ok:
            return True
        return self.enabled

    def refresh_headers(self, *, headless: bool | None = None) -> bool:
        use_headless = settings.cninfo_bootstrap_headless if headless is None else bool(headless)
        try:
            headers = bootstrap_cninfo_headers(
                headless=use_headless,
                profile_dir=self._profile_dir(),
            )
        except Exception as exc:
            logger.warning("cninfo bootstrap headers failed | headless=%s error=%s", use_headless, exc)
            return False

        settings.cninfo_accept_enckey = str(headers.get("Accept-Enckey") or "").strip()
        settings.cninfo_cookie = str(headers.get("Cookie") or "").strip()
        if headers.get("Referer"):
            settings.cninfo_referer = str(headers["Referer"])
        if headers.get("User-Agent"):
            settings.cninfo_user_agent = str(headers["User-Agent"])

        logger.info(
            "cninfo headers refreshed | enabled=%s accept_len=%s cookie_len=%s",
            self.enabled,
            len(settings.cninfo_accept_enckey or ""),
            len(settings.cninfo_cookie or ""),
        )
        self._save_cached_headers()
        return self.enabled

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _headers(self) -> dict[str, str]:
        return {
            "Accept-Enckey": settings.cninfo_accept_enckey,
            "Cookie": settings.cninfo_cookie,
            "Referer": settings.cninfo_referer,
            "User-Agent": settings.cninfo_user_agent,
        }

    @staticmethod
    def _extract_records(payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []

        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]

        if isinstance(payload, dict):
            for key in (
                "records",
                "result",
                "results",
                "data",
                "rows",
                "announcements",
                "list",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
                if isinstance(value, dict):
                    nested = CninfoClient._extract_records(value)
                    if nested:
                        return nested
        return []

    @staticmethod
    def _payload_text(payload: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in (
            "resultmsg",
            "message",
            "msg",
            "error",
            "errmsg",
            "errorMsg",
            "tips",
            "status",
            "code",
            "errorCode",
            "resultcode",
        ):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                try:
                    parts.append(json.dumps(value, ensure_ascii=False))
                except Exception:
                    parts.append(str(value))
            else:
                parts.append(str(value))
        return " ".join(parts).strip()

    @staticmethod
    def _looks_like_auth_text(raw_text: str) -> bool:
        raw = str(raw_text or "").lower()
        markers = [
            "token",
            "enckey",
            "cookie",
            "login",
            "auth",
            "unauthorized",
            "forbidden",
            "invalid",
            "expired",
            "401",
            "403",
            "apifilter",
            "token null",
            "no permission",
        ]
        return any(marker in raw for marker in markers)

    def request(self, path: str, params: dict[str, Any] | None = None, method: str = "GET") -> CninfoResponse:
        if not self.ensure_headers():
            raise CninfoClientError("CNInfo client is not enabled. Please set cninfo_enabled/headers in .env")

        method = method.upper().strip()
        if method not in {"GET", "POST"}:
            raise CninfoClientError(f"Unsupported method: {method}")

        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        params = {**(params or {})}
        params.setdefault("format", "json")

        logger.info("cninfo request | method=%s url=%s params=%s", method, url, params)
        response = None
        payload: Any = None
        for attempt in range(2):
            try:
                if method == "GET":
                    response = self._session.get(url, params=params, headers=self._headers(), timeout=self.timeout)
                else:
                    response = self._session.post(url, data=params, headers=self._headers(), timeout=self.timeout)
                response.raise_for_status()
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                should_retry = (
                    attempt == 0
                    and settings.cninfo_auto_bootstrap
                    and settings.cninfo_bootstrap_retry_on_401
                    and status_code in {401, 403}
                )
                if should_retry:
                    logger.warning("cninfo http %s, retry after refresh | url=%s", status_code, url)
                    if self.ensure_headers():
                        continue
                raise CninfoClientError(f"CNInfo request failed: {exc}") from exc
            except Exception as exc:
                raise CninfoClientError(f"CNInfo request failed: {exc}") from exc

            try:
                payload = response.json()
            except Exception as exc:
                raise CninfoClientError(f"CNInfo response is not JSON: {exc}") from exc

            # CNInfo often returns HTTP 200 with business-level failure in payload.
            if isinstance(payload, dict):
                payload_code = (
                    payload.get("resultcode")
                    if payload.get("resultcode") is not None
                    else payload.get("code")
                )
                payload_msg = (
                    payload.get("resultmsg")
                    or payload.get("message")
                    or payload.get("msg")
                    or payload.get("error")
                    or payload.get("errmsg")
                    or ""
                )
                payload_text = self._payload_text(payload)

                def _is_success_code(value: Any) -> bool:
                    if value is None:
                        return True
                    normalized = str(value).strip().lower()
                    return normalized in {"0", "1", "200", "ok", "success", "true"}

                def _is_no_data_message(text: str) -> bool:
                    lowered = str(text or "").lower()
                    markers = ["no data", "not found", "empty", "nodata"]
                    return any(marker in lowered for marker in markers)

                records_preview = self._extract_records(payload)
                payload_failed = (not _is_success_code(payload_code)) and not (
                    _is_no_data_message(str(payload_msg)) and not records_preview
                )
                auth_like = self._looks_like_auth_text(payload_text)

                if payload_failed or auth_like:
                    should_retry = attempt == 0
                    if should_retry:
                        # First preference: hot-reload a newer cache generated by
                        # another process (e.g. test script) and retry once.
                        if self._cache_file_is_newer() and self._load_cached_headers():
                            logger.warning(
                                "cninfo payload error, retry after cache reload | url=%s code=%s msg=%s",
                                url,
                                payload_code,
                                payload_msg,
                            )
                            continue
                        # Second preference: optional Playwright refresh.
                        if settings.cninfo_auto_bootstrap and (auth_like or settings.cninfo_bootstrap_retry_on_401):
                            logger.warning(
                                "cninfo payload error, retry after forced refresh | url=%s code=%s msg=%s",
                                url,
                                payload_code,
                                payload_msg,
                            )
                            if self.refresh_headers():
                                continue
                    raise CninfoClientError(
                        f"CNInfo payload error: code={payload_code} msg={payload_msg or payload_text or '<empty>'}"
                    )
            break

        records = self._extract_records(payload)
        object_id_max = None
        for row in records:
            try:
                value = int(row.get("OBJECTID"))
            except Exception:
                continue
            object_id_max = value if object_id_max is None else max(object_id_max, value)

        return CninfoResponse(
            records=records,
            raw=payload if isinstance(payload, dict) else {"data": payload},
            object_id_max=object_id_max,
        )


def bootstrap_cninfo_headers(
    headless: bool = False,
    *,
    profile_dir: Path | None = None,
) -> dict[str, str]:
    """Capture Accept-Enckey + Cookie via Playwright.

    Uses persistent browser profile so login session can be reused across runs.
    """

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        raise CninfoClientError("playwright is not installed. Install playwright to bootstrap headers.") from exc

    captured: dict[str, str | None] = {"accept_enckey": None, "cookie": None}
    profile = (profile_dir or CninfoClient._default_profile_dir()).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    wait_ms = max(1000, int(settings.cninfo_bootstrap_wait_seconds) * 1000)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_request(req) -> None:
            if "p_stock" in req.url or "/api/stock/" in req.url or "/api/load/" in req.url:
                headers = req.headers
                enckey = headers.get("accept-enckey")
                if enckey:
                    captured["accept_enckey"] = enckey

        page.on("request", on_request)
        page.goto(settings.cninfo_referer, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)

        # If still missing and interactive terminal, allow manual trigger once.
        if not captured.get("accept_enckey") and (not headless) and sys.stdin and sys.stdin.isatty():
            print(
                "\n[CNInfo Bootstrap] Accept-Enckey not captured yet.\n"
                "Please login in the opened browser and open any CNInfo page that triggers /api/stock/ or /api/load/.\n"
                "Then return to terminal and press Enter..."
            )
            try:
                input()
            except EOFError:
                pass
            page.wait_for_timeout(2500)

        cookies = context.cookies()
        cookie_str = "; ".join([f"{item['name']}={item['value']}" for item in cookies])
        captured["cookie"] = cookie_str
        context.close()

    if not captured.get("accept_enckey"):
        raise CninfoClientError("Failed to capture Accept-Enckey. Trigger a CNInfo API request after login.")

    return {
        "Accept-Enckey": str(captured["accept_enckey"]),
        "User-Agent": settings.cninfo_user_agent,
        "Referer": settings.cninfo_referer,
        "Cookie": str(captured.get("cookie") or ""),
    }


cninfo_client = CninfoClient()

