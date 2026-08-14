import json
import os
import re
from http.cookiejar import Cookie
from pathlib import Path
import tempfile
import urllib.parse

import httpx

try:
    from curl_cffi import CurlMime  # pyright: ignore[reportMissingImports]
    from curl_cffi.requests import Session as CurlSession  # pyright: ignore[reportMissingImports]
    HAS_CURL_CFFI = True
except ImportError:
    CurlMime = None  # type: ignore[assignment]
    HAS_CURL_CFFI = False  # pyright: ignore[reportConstantRedefinition]

from loguru import logger
from typing import Any, Optional, cast
from paypal.models import SessionState
from paypal.traffic_recorder import get_global_traffic_recorder
from config import USER_AGENT, BROWSER_PROFILE


EUAT_COOKIE_NAME = "AV894Kt2TSumQQrJwe-8mzmyREO"
CAPTCHA_SOLVED_CFCI = "modxo_vaulted_not_recurring-CAPTCHA_SOLVED"
CAPTCHA_FRONTEND_DISABLE_MODE = "frontend_disable"
CAPTCHA_MANUAL_REQUIRED_MODE = "manual_required"
_CAPTCHA_FRONTEND_DISABLE_MODES = {
    "1",
    "true",
    "yes",
    "on",
    "bypass",
    "console",
    "console-v2",
    "fake",
    "fake-close",
    "fake_close",
    "frontend-disable",
    "frontend_disable",
}
_CAPTCHA_MANUAL_REQUIRED_MODES = {
    "",
    "0",
    "false",
    "no",
    "off",
    "disable",
    "disabled",
    "manual",
    "manual-required",
    "manual_required",
    "official",
    "browser",
}
_CAPTCHA_REMOVED_SOLVER_MODES = {
    "real",
    "real-solver",
    "real_solver",
    "solve",
    "solver",
    "capsolver",
    "cap_solver",
}


def _dict_value(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _ua_high_entropy(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    ua_data = _dict_value(
        profile.get("user_agent_data")
        or profile.get("uaData")
        or profile.get("ua_data")
    )
    high_entropy = _dict_value(ua_data.get("highEntropy") or ua_data.get("high_entropy"))
    return ua_data, high_entropy


def _ua_brand_entries(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for item in value:
        entry = _dict_value(item)
        brand = str(entry.get("brand") or entry.get("b") or "")
        version = str(entry.get("version") or entry.get("v") or "")
        if brand and version:
            entries.append({"brand": brand, "version": version})
    return entries


def _low_entropy_ua_brands(profile: dict[str, Any]) -> list[dict[str, str]]:
    ua_data, high_entropy = _ua_high_entropy(profile)
    runtime_brands = _ua_brand_entries(high_entropy.get("brands") or ua_data.get("brands"))
    if runtime_brands:
        return runtime_brands
    major = str(profile.get("chrome_major") or "150")
    return [
        {"brand": "Not;A=Brand", "version": "8"},
        {"brand": "Chromium", "version": major},
        {"brand": "Google Chrome", "version": major},
    ]


def _full_version_ua_brands(profile: dict[str, Any]) -> list[dict[str, str]]:
    ua_data, high_entropy = _ua_high_entropy(profile)
    runtime_full_versions = _ua_brand_entries(high_entropy.get("fullVersionList") or ua_data.get("fullVersionList"))
    if runtime_full_versions:
        return runtime_full_versions
    full_version = str(profile.get("chrome_full_version") or f"{profile.get('chrome_major') or '150'}.0.0.0")
    return [
        {"brand": "Not;A=Brand", "version": "8.0.0.0"},
        {"brand": "Chromium", "version": full_version},
        {"brand": "Google Chrome", "version": full_version},
    ]


def _format_sec_ch_ua(entries: list[dict[str, str]]) -> str:
    return ", ".join(f'"{item["brand"]}";v="{item["version"]}"' for item in entries)


def _load_dotenv_value(name: str) -> str:
    """Read one value from local .env without adding a runtime dependency."""
    if os.getenv(name):
        return os.getenv(name, "").strip()
    roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
    seen: set[Path] = set()
    for root in roots:
        env_path = root / ".env"
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(name, value)
                return value
        except Exception:
            continue
    return ""


def _captcha_mode_raw() -> str:
    return (
        _load_dotenv_value("PAYPAL_CAPTCHA_BYPASS_MODE")
        or _load_dotenv_value("PAYPAL_CAPTCHA_MODE")
        or ""
    ).strip()


def _normalize_captcha_mode(mode: str) -> str:
    return (mode or "").strip().lower().replace(" ", "_")


def _env_truthy(name: str) -> bool:
    return _load_dotenv_value(name).strip().lower() in {"1", "true", "yes", "on"}


def strict_browser_risk_enabled() -> bool:
    return _load_dotenv_value("PAYPAL_STRICT_BROWSER_RISK").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "strict",
    }


def paypal_captcha_bypass_mode() -> str:
    mode = _captcha_mode_raw()
    normalized = _normalize_captcha_mode(mode)
    dashed = normalized.replace("_", "-")
    if not normalized:
        # 默认 frontend_disable: authchallenge 用本地 synthetic close 绕过
        # (8/11 BA-XXXXXXXXXXXXXXX1 成功路径), 不依赖环境变量显式配置。
        if strict_browser_risk_enabled() and not _env_truthy("PAYPAL_ALLOW_SYNTHETIC_CAPTCHA"):
            logger.warning(
                "Default CAPTCHA mode frontend_disable is ignored while "
                "PAYPAL_STRICT_BROWSER_RISK=1; falling back to manual_required."
            )
            return CAPTCHA_MANUAL_REQUIRED_MODE
        return CAPTCHA_FRONTEND_DISABLE_MODE
    if normalized in _CAPTCHA_FRONTEND_DISABLE_MODES or dashed in _CAPTCHA_FRONTEND_DISABLE_MODES:
        if strict_browser_risk_enabled() and not _env_truthy("PAYPAL_ALLOW_SYNTHETIC_CAPTCHA"):
            logger.warning(
                "Ignoring synthetic CAPTCHA mode {} while PAYPAL_STRICT_BROWSER_RISK=1.",
                mode,
            )
            return CAPTCHA_MANUAL_REQUIRED_MODE
        return CAPTCHA_FRONTEND_DISABLE_MODE
    if normalized in _CAPTCHA_REMOVED_SOLVER_MODES or dashed in _CAPTCHA_REMOVED_SOLVER_MODES:
        logger.warning(
            "Ignoring removed external CAPTCHA solver mode {}; manual verification is required.",
            mode,
        )
        return CAPTCHA_MANUAL_REQUIRED_MODE
    if normalized in _CAPTCHA_MANUAL_REQUIRED_MODES or dashed in _CAPTCHA_MANUAL_REQUIRED_MODES:
        return CAPTCHA_MANUAL_REQUIRED_MODE
    if mode:
        logger.warning("Unsupported CAPTCHA mode {}; falling back to manual_required.", mode)
    return CAPTCHA_MANUAL_REQUIRED_MODE


def captcha_frontend_disable_enabled() -> bool:
    return paypal_captcha_bypass_mode() == CAPTCHA_FRONTEND_DISABLE_MODE


def build_common_headers(state: SessionState | None = None) -> dict[str, str]:
    """Low-entropy Client Hints only.

    Chrome sends ``sec-ch-ua``, ``sec-ch-ua-mobile`` and
    ``sec-ch-ua-platform`` on every request.  High-entropy hints like
    ``sec-ch-device-memory``, ``sec-ch-ua-arch``, ``sec-ch-ua-model``
    and ``sec-ch-ua-full-version-list`` are only added **after** the
    server responds with ``Accept-CH`` and only to **same-origin**
    sub-resource requests, never on the initial navigation.
    """
    profile = cast(dict[str, object], (
        getattr(state, "browser_profile", None)
        if state is not None
        else None
    ) or BROWSER_PROFILE)
    user_agent = str(profile.get("user_agent") or USER_AGENT)
    language = str(profile.get("language") or "pt-BR")
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": f"{language},pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "sec-ch-ua": _format_sec_ch_ua(_low_entropy_ua_brands(profile)),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": str(profile.get("sec_ch_platform") or '"Linux"'),
    }


def build_high_entropy_hints(state: SessionState | None = None) -> dict[str, str]:
    """High-entropy Client Hints sent only to same-origin after Accept-CH."""
    profile = cast(dict[str, object], (
        getattr(state, "browser_profile", None)
        if state is not None
        else None
    ) or BROWSER_PROFILE)
    return {
        "sec-ch-ua-arch": str(profile.get("sec_ch_arch") or '"x86"'),
        "sec-ch-device-memory": str(profile.get("device_memory") or "8"),
        "sec-ch-ua-model": '""',
        "sec-ch-ua-full-version-list": (
            _format_sec_ch_ua(_full_version_ua_brands(profile))
        ),
    }


# Hints that PayPal's Permissions-Policy delegates to c.paypal.com.
# ch-ua-arch, ch-ua-full-version-list, ch-ua-model are listed;
# ch-device-memory is NOT, so Chrome omits it for cross-origin.
_DELEGATED_HINT_KEYS = frozenset({
    "sec-ch-ua-arch",
    "sec-ch-ua-full-version-list",
    "sec-ch-ua-model",
})


def _mask_middle(value: str, left: int = 6, right: int = 4) -> str:
    if len(value) <= left + right:
        return "<redacted>"
    return f"{value[:left]}...{value[-right:]}"


def _mask_email(value: str) -> str:
    if "@" not in value:
        return "<redacted>"
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[:2]}***{local[-1:]}@{domain}"


def _mask_digits(value: str, keep: int = 4) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= keep:
        return "<redacted>"
    return f"{'*' * (len(digits) - keep)}{digits[-keep:]}"


def _mask_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except Exception:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    sensitive_markers = (
        "token",
        "secret",
        "nonce",
        "client_secret",
        "client_metadata_id",
        "clientmetadataid",
        "correlation_id",
        "correlationid",
        "ctx_id",
        "ctxid",
        "cmid",
        "ssrt",
        "request_id",
        "requestid",
        "sealed_result",
        "sealedresult",
        "visitor_token",
        "visitortoken",
        "payment_intent",
        "ba_token",
        "stripe_session_id",
    )
    query = []
    changed = False
    for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        compact = key.lower().replace("_", "").replace("-", "")
        if any(marker.replace("_", "") in compact for marker in sensitive_markers):
            query.append((key, _mask_middle(item) if item else "<redacted>"))
            changed = True
        else:
            query.append((key, item))
    if not changed:
        return value
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            "<fragment-redacted>" if parsed.fragment else "",
        )
    )


def _mask_embedded_urls(value: str) -> str:
    return re.sub(r"https?://[^\s\"'<>]+", lambda match: _mask_url(match.group(0)), value)


def _mask_inline_sensitive_pairs(value: str) -> str:
    sensitive_keys = (
        "ba_token",
        "ec_token",
        "billingAgreementId",
        "billingAgreementToken",
        "token",
        "accessToken",
        "access_token",
        "password",
        "securityCode",
        "cvv",
        "pin",
        "otp",
        "ssrt",
        "ctxId",
        "ctx_id",
        "cmid",
        "clientMetadataId",
        "client_metadata_id",
        "correlationId",
        "correlation_id",
        "requestId",
        "request_id",
        "sealedResult",
        "sealed_result",
        "visitorToken",
        "visitor_token",
        "authorization",
        "cookie",
        "euat",
    )
    key_pattern = "|".join(re.escape(key) for key in sensitive_keys)
    value = re.sub(
        rf"(?i)([\"']?\b(?:{key_pattern})\b[\"']?\s*[:=]\s*)([\"']?)([^&,\"'\s}}{{]+)([\"']?)",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>{match.group(4)}",
        value,
    )
    value = re.sub(r"\bBA-[A-Za-z0-9]{8,80}\b", lambda match: _mask_middle(match.group(0)), value)
    value = re.sub(r"\bEC-[A-Za-z0-9]{8,80}\b", lambda match: _mask_middle(match.group(0)), value)
    return value


def sanitize_for_log(value: Any, key: str = "") -> Any:
    """Remove secrets and high-risk PII before writing diagnostics."""
    if isinstance(value, dict):
        return {k: sanitize_for_log(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_log(item, key) for item in value]
    if not isinstance(value, str):
        return value

    lowered_key = key.lower()
    compact_key = lowered_key.replace("_", "").replace("-", "")

    if compact_key in {"password", "securitycode", "cvv", "pin"}:
        return "<redacted>"
    if "authorization" in compact_key or "cookie" in compact_key:
        return "<redacted>"
    if "accesstoken" in compact_key or "euat" in compact_key:
        return "<redacted>"
    if compact_key in {
        "sealedresult",
        "visitortoken",
        "requestid",
        "correlationid",
        "clientmetadataid",
        "cmid",
        "ssrt",
        "ctxid",
    }:
        return _mask_middle(value)
    if compact_key in {"token", "batoken", "ectoken", "billingagreementid", "billingagreementtoken"}:
        return _mask_middle(value)
    if "url" in compact_key and value.startswith(("http://", "https://")):
        return _mask_url(value)
    if compact_key in {"cardnumber", "encryptednumber"}:
        return _mask_digits(value)
    if compact_key in {"cpf", "identitydocument", "document", "value"}:
        return "<redacted>"
    if compact_key == "email":
        return _mask_email(value)
    if compact_key in {"phonenumber", "phone", "number"} and sum(ch.isdigit() for ch in value) >= 8:
        return _mask_digits(value)

    return _mask_inline_sensitive_pairs(_mask_embedded_urls(value))


def _paypal_debug_id(headers: httpx.Headers) -> str:
    for name in ("paypal-debug-id", "Paypal-Debug-Id", "PayPal-Debug-Id"):
        value = headers.get(name)
        if value:
            return value
    return ""


def _header_values(headers: Any, name: str) -> list[str]:
    values: list[str] = []
    for method_name in ("get_list", "get_all"):
        getter = getattr(headers, method_name, None)
        if callable(getter):
            try:
                got = getter(name)
                if got:
                    if isinstance(got, (list, tuple)):
                        values.extend(str(item) for item in got if item is not None)
                    else:
                        values.append(str(got))
            except Exception:
                pass

    for key in (name, name.lower(), name.title()):
        try:
            value = headers.get(key)
        except Exception:
            value = None
        if value:
            values.append(str(value))

    raw = getattr(headers, "raw", None)
    if raw:
        for key, value in raw:
            try:
                key_text = key.decode("latin1") if isinstance(key, bytes) else str(key)
                if key_text.lower() != name.lower():
                    continue
                value_text = value.decode("latin1") if isinstance(value, bytes) else str(value)
                values.append(value_text)
            except Exception:
                continue

    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _extract_cookie_value_from_headers(headers: Any, cookie_name: str) -> str:
    pattern = re.compile(r"(?:^|,\s*)" + re.escape(cookie_name) + r"=([^;,]*)")
    for header in _header_values(headers, "set-cookie"):
        match = pattern.search(header or "")
        if match:
            return urllib.parse.unquote(match.group(1) or "")
    return ""


def looks_like_paypal_authchallenge(text: str) -> bool:
    """Return True when a PayPal endpoint answered with authchallenge HTML.

    PayPal sometimes returns the front-end Security Challenge document from a
    GraphQL endpoint with HTTP 200.  Treating that body as JSON raises a generic
    JSONDecodeError and loses the real cause, so callers can catch the typed
    exception below and decide whether to retry/ignore the front-end challenge.
    """
    head = (text or "").lstrip()[:20000].lower()
    if not head.startswith("<"):
        return False
    return any(
        marker in head
        for marker in (
            "authchallenge",
            "authchallengenodeweb",
            "data-captcha-type",
            "/auth/validatecaptcha",
            "hcaptchapassive",
            "recaptcha",
            "captcha",
        )
    )


class PayPalAuthChallenge(RuntimeError):
    """Raised when PayPal returns authchallenge HTML instead of GraphQL JSON."""

    def __init__(self, operation_name: str, status_code: int, debug_id: str, html: str):
        self.operation_name = operation_name
        self.status_code = status_code
        self.debug_id = debug_id
        self.html = html or ""
        super().__init__(
            f"{operation_name} returned PayPal authchallenge HTML "
            f"status={status_code} paypal_debug_id={debug_id or '<missing>'}"
        )


class PayPalSession:
    """Manages HTTP session with cookie persistence and logging."""

    def __init__(
        self,
        state: SessionState,
        proxy_url: str | None = None,
        proxy_label: str = "",
    ):
        self.state = state
        self.proxy_url = proxy_url
        self.proxy_label = proxy_label or ("代理已开启" if proxy_url else "代理关闭")
        self._accept_ch_received = os.getenv(
            "PAYPAL_FORCE_HIGH_ENTROPY_CH",
            "0",
        ).strip().lower() not in {"0", "false", "no", "off"}
        self._high_entropy_hints = build_high_entropy_hints(state)
        self.traffic_recorder = get_global_traffic_recorder()
        self._use_curl = HAS_CURL_CFFI and os.getenv(
            "PAYPAL_USE_CURL_CFFI",
            "1",
        ).strip().lower() not in {"0", "false", "no", "off"}
        client_kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "timeout": httpx.Timeout(30.0),
            "headers": build_common_headers(state),
            # Chrome negotiates HTTP/2 with PayPal. This does not make httpx's
            # TLS ClientHello identical to Chrome, but it removes the very loud
            # HTTP/1.1-only mismatch and keeps connection reuse closer to a
            # browser session.
            "http2": os.getenv("PAYPAL_HTTP2", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            # 保证“关闭代理”时不被 HTTP_PROXY/HTTPS_PROXY 环境变量意外接管。
            "trust_env": False,
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        if self._use_curl:
            impersonate = os.getenv("PAYPAL_CURL_IMPERSONATE", "chrome").strip() or "chrome"
            curl_session_factory = cast(Any, globals().get("CurlSession"))
            if curl_session_factory is None:
                raise RuntimeError("curl_cffi is not available")
            self.client = curl_session_factory(impersonate=impersonate)
            self.client.headers.update(build_common_headers(state))
            self.client.timeout = 30
            self.client.allow_redirects = False
            if proxy_url:
                self.client.proxies = {"https": proxy_url, "http": proxy_url}
            logger.info("HTTP client: curl_cffi ({})", impersonate)
        else:
            self.client = httpx.Client(**client_kwargs)
            logger.info("HTTP client: httpx (http2={})", client_kwargs.get("http2"))
        logger.info("HTTP outbound proxy: {}", self.proxy_label)

    @staticmethod
    def _unlink_security_challenge_cache() -> list[str]:
        patterns = (
            "paypal_gql_*_last.html",
            "paypal_gql_*_last.json",
            "pps_gql_*_last.html",
            "pps_gql_*_last.json",
            "pps_validatecaptcha_*.json",
            "pps_hcaptchapassive_*.json",
        )
        removed: list[str] = []
        roots: list[Path] = []
        seen: set[str] = set()
        for raw in ("/tmp", tempfile.gettempdir()):
            try:
                root = Path(raw)
                key = str(root.resolve()) if root.exists() else str(root)
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            roots.append(root)

        for root in roots:
            try:
                if not root.is_dir():
                    continue
                for pattern in patterns:
                    for path in root.glob(pattern):
                        try:
                            if path.is_file():
                                path.unlink()
                                removed.append(str(path))
                        except Exception:
                            pass
            except Exception:
                pass
        return removed

    def purge_security_challenge_state(
        self,
        challenge_html: str = "",
        reason: str = "",
        *,
        clear_cookies: bool = False,
        clear_files: bool = False,
    ) -> dict[str, Any]:
        """Remove local front-end challenge traces.

        GuJumpgate's browser-side cleanup only removes the visible captcha DOM;
        it does **not** delete PayPal session cookies.  Keeping cookies is
        important here: deleting l7_az/x-pp-s/nsid/tsrce can make the next
        request escalate from hcaptchapassive to reCAPTCHA.  Cookie/file purge
        remains available for explicit hard resets, but the default frontend
        delete path is intentionally non-destructive.
        """
        removed_files = self._unlink_security_challenge_cache() if clear_files else []
        removed_cookies: set[str] = set()
        challenge_cookie_names = {"tsrce", "nsid", "x-pp-s", "l7_az"}
        if clear_cookies:
            if self._use_curl:
                cookie_names: set[str] = set()
                try:
                    cookie_names.update(str(name) for name, _ in self.client.cookies.items())
                except Exception:
                    try:
                        cookie_names.update(
                            str(getattr(cookie, "name", cookie)) for cookie in self.client.cookies
                        )
                    except Exception:
                        pass
                for name in sorted(challenge_cookie_names & cookie_names):
                    try:
                        self.client.cookies.delete(name)
                        removed_cookies.add(name)
                    except Exception:
                        try:
                            del self.client.cookies[name]
                            removed_cookies.add(name)
                        except Exception:
                            pass
            else:
                jar = self.client.cookies.jar
                for cookie in list(jar):
                    name = getattr(cookie, "name", "")
                    if name not in challenge_cookie_names:
                        continue
                    try:
                        jar.clear(
                            domain=getattr(cookie, "domain", None),
                            path=getattr(cookie, "path", None),
                            name=name,
                        )
                        removed_cookies.add(name)
                    except Exception:
                        pass
            if "nsid" in removed_cookies:
                self.state.nsid = ""
            self._sync_state_cookies()

        head = challenge_html or ""
        markers = [
            marker
            for marker in (
                "authchallenge",
                "Security Challenge",
                "data-captcha-type",
                "recaptcha",
                "hcaptchapassive",
            )
            if marker in head
        ]
        logger.warning(
            "PayPal authchallenge frontend state purged reason={} files={} cookies={} markers={} clear_cookies={} clear_files={}",
            reason or "unknown",
            len(removed_files),
            ",".join(sorted(removed_cookies)) or "-",
            ",".join(markers) or "-",
            clear_cookies,
            clear_files,
        )
        return {
            "reason": reason,
            "removed_files": removed_files,
            "removed_cookies": sorted(removed_cookies),
            "markers": markers,
        }

    def close(self):
        self.client.close()

    def _sync_state_cookies(self):
        """Pull important cookies into SessionState after each request."""
        cookie_dict = {}
        if self._use_curl:
            cookies = self.client.cookies
            try:
                for name, value in cookies.items():
                    cookie_dict[str(name)] = str(value)
            except Exception:
                for cookie in cookies:
                    name = getattr(cookie, "name", cookie)
                    value = getattr(cookie, "value", None)
                    if value is None:
                        try:
                            value = cookies[name]
                        except Exception:
                            continue
                    cookie_dict[str(name)] = str(value)
        else:
            jar = self.client.cookies
            # PayPal may set the same cookie name for multiple domain/path scopes
            # (ddgl is a common example). httpx.Cookies.items() raises
            # CookieConflict in that case, so iterate the underlying jar instead.
            for cookie in jar.jar:
                if isinstance(cookie, Cookie):
                    cookie_dict[cookie.name] = cookie.value
        self.state.update_from_cookies(cookie_dict)

    def _sync_state_response_headers(self, resp: Any) -> None:
        """Capture auth cookies directly from Set-Cookie in case the jar misses them."""
        try:
            token = _extract_cookie_value_from_headers(
                getattr(resp, "headers", {}),
                EUAT_COOKIE_NAME,
            )
        except Exception:
            token = ""
        if token and token != self.state.euat_token:
            self.state.euat_token = token
            logger.info("EUAT cookie captured from response Set-Cookie header len={}", len(token))

    def export_cookies_for_browser(self) -> list[dict[str, Any]]:
        """Export current HTTP-session cookies in Playwright add_cookies shape."""
        exported: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_cookie(
            name: str,
            value: str,
            *,
            domain: str = "",
            path: str = "/",
            secure: bool = True,
            http_only: bool = False,
        ) -> None:
            if not name or value is None:
                return
            domain = domain or ".paypal.com"
            path = path or "/"
            key = (name, domain, path)
            if key in seen:
                return
            seen.add(key)
            exported.append(
                {
                    "name": str(name),
                    "value": str(value),
                    "domain": str(domain),
                    "path": str(path),
                    "secure": bool(secure),
                    "httpOnly": bool(http_only),
                }
            )

        try:
            jar = getattr(getattr(self, "client", None), "cookies", None)
            raw_jar = getattr(jar, "jar", None)
            if raw_jar is not None:
                for cookie in raw_jar:
                    add_cookie(
                        getattr(cookie, "name", ""),
                        getattr(cookie, "value", ""),
                        domain=getattr(cookie, "domain", "") or ".paypal.com",
                        path=getattr(cookie, "path", "/") or "/",
                        secure=bool(getattr(cookie, "secure", True)),
                        http_only=bool(getattr(cookie, "_rest", {}).get("HttpOnly", False))
                        if isinstance(getattr(cookie, "_rest", None), dict)
                        else False,
                    )
            elif jar is not None:
                for name, value in jar.items():
                    add_cookie(str(name), str(value))
        except Exception:
            pass
        return exported

    def import_browser_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Import cookies captured from a real browser into the HTTP session."""
        if not cookies:
            return
        cookie_dict: dict[str, str] = {}
        jar = getattr(getattr(self, "client", None), "cookies", None)
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if not name or value is None:
                continue
            domain = str(cookie.get("domain") or ".paypal.com")
            path = str(cookie.get("path") or "/")
            secure = bool(cookie.get("secure", True))
            try:
                if jar is not None and hasattr(jar, "set"):
                    try:
                        jar.set(name, value, domain=domain, path=path, secure=secure)
                    except TypeError:
                        jar.set(name, value, domain=domain, path=path)
                elif jar is not None:
                    jar[name] = value
            except Exception:
                try:
                    self.client.cookies.set(name, value, domain=domain, path=path)
                except Exception:
                    pass
            cookie_dict[name] = value
        if cookie_dict:
            self.state.update_from_cookies(cookie_dict)

    @staticmethod
    def _coerce_curl_multipart_file_part(name: str, value: Any) -> dict[str, Any]:
        """Convert httpx/requests-style ``files`` entries to curl_cffi CurlMime parts.

        curl_cffi 0.15 removed requests/httpx-compatible ``files=`` support and
        raises ``NotImplementedError`` unless callers pass ``multipart=``.  The
        PayPal ModXO server actions in this project use httpx's convenient text
        field form: ``files=[("_1_ctxId", (None, ctx_id)), ...]``.  Keep those
        call sites client-agnostic by translating them at the session boundary.
        """
        part: dict[str, Any] = {"name": str(name)}

        if isinstance(value, tuple):
            filename = value[0] if len(value) >= 1 else None
            content = value[1] if len(value) >= 2 else b""
            content_type = value[2] if len(value) >= 3 else None

            if filename is not None:
                part["filename"] = str(filename)
            if content_type is not None:
                part["content_type"] = str(content_type)
        else:
            content = value

        if isinstance(content, bytes):
            data = content
        elif isinstance(content, bytearray):
            data = bytes(content)
        else:
            data = str(content).encode()
        part["data"] = data
        return part

    @classmethod
    def _files_to_curl_multipart(cls, files: Any):
        if CurlMime is None:
            raise RuntimeError("curl_cffi multipart support is unavailable")

        if isinstance(files, dict):
            iterable = files.items()
        else:
            iterable = files

        return CurlMime.from_list(
            [cls._coerce_curl_multipart_file_part(name, value) for name, value in iterable]
        )

    def _prepare_curl_kwargs(self, kwargs: dict[str, Any], *, move_content: bool = False) -> dict[str, Any]:
        if "follow_redirects" in kwargs:
            kwargs["allow_redirects"] = kwargs.pop("follow_redirects")
        if move_content and "content" in kwargs:
            content = kwargs.pop("content")
            if "data" not in kwargs:
                kwargs["data"] = content
        if "files" in kwargs and kwargs["files"] is not None:
            kwargs["multipart"] = self._files_to_curl_multipart(kwargs.pop("files"))
        return kwargs

    def _inject_high_entropy_hints(self, url: str, kwargs: dict[str, Any]) -> None:
        if not self._accept_ch_received:
            return
        host = urllib.parse.urlparse(url).hostname or ""
        is_same_origin = (host == "www.paypal.com")
        is_delegated = (host in {"c.paypal.com", "c6.paypal.com"})
        if not is_same_origin and not is_delegated:
            return
        if is_same_origin:
            hints = dict(self._high_entropy_hints)
        else:
            hints = {k: v for k, v in self._high_entropy_hints.items()
                     if k in _DELEGATED_HINT_KEYS}
        headers = kwargs.get("headers")
        if headers is None:
            kwargs["headers"] = hints
        else:
            merged = dict(hints)
            merged.update(headers)
            kwargs["headers"] = merged

    def _check_accept_ch(self, resp: Any) -> None:
        """Activate high-entropy hints once the server sends Accept-CH."""
        if self._accept_ch_received:
            return
        try:
            accept_ch = resp.headers.get("accept-ch") or ""
            if "sec-ch-" in accept_ch.lower():
                self._accept_ch_received = True
        except Exception:
            pass

    def _effective_headers_for_record(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Approximate headers that will be sent on the wire."""
        headers: dict[str, Any] = {}
        try:
            if self._use_curl:
                headers.update(dict(getattr(self.client, "headers", {}) or {}))
            else:
                headers.update(dict(getattr(self.client, "headers", {}) or {}))
        except Exception:
            headers.update(build_common_headers(self.state))
        try:
            headers.update(dict(kwargs.get("headers") or {}))
        except Exception:
            pass
        return headers

    @staticmethod
    def _is_datadome_covered_host(url: str) -> bool:
        try:
            host = (urllib.parse.urlparse(url).hostname or "").lower()
        except Exception:
            return False
        return host in {"paypal.com", "venmo.com"} or host.endswith(".paypal.com") or host.endswith(".venmo.com")

    def _inject_datadome_header_if_needed(self, url: str, kwargs: dict[str, Any]) -> None:
        """Mirror PayPal's DataDome bootstrap hook.

        Captured checkout pages monkey-patch fetch/XMLHttpRequest and add
        x-datadome-clientid to same-site PayPal/Venmo requests when the
        datadome cookie has not yet been materialized.  Keep the behavior at the
        session boundary so all protocol requests are covered consistently.
        """
        client_id = getattr(self.state, "datadome_clientid", "") or ""
        if not client_id or getattr(self.state, "datadome_cookie", ""):
            return
        if not self._is_datadome_covered_host(url):
            return

        raw_headers = kwargs.get("headers") or {}
        try:
            headers = dict(raw_headers)
        except Exception:
            headers = {}
        if any(str(name).lower() == "x-datadome-clientid" for name in headers):
            return
        headers["x-datadome-clientid"] = client_id
        try:
            self.state.datadome_header_injected = True
        except Exception:
            pass
        kwargs["headers"] = headers

    @staticmethod
    def _is_captcha_fake_200_endpoint(url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if host not in {"paypal.com", "www.paypal.com"}:
            return False
        return path in {"/auth/validatecaptcha", "/auth/verifyhcaptchapassive"}

    @staticmethod
    def _is_captcha_block_204_asset(url: str) -> bool:
        try:
            value = urllib.parse.urlparse(url).geturl().lower()
        except Exception:
            value = str(url or "").lower()
        return any(
            marker in value
            for marker in (
                "ngrlcaptcha",
                "hcaptchapassive",
                "hcaptcha.html",
                "recaptcha_v2",
                "recaptcha_v3",
                "captcha-standalone",
                "/auth/createchallenge/",
            )
        )

    @staticmethod
    def _synthetic_response(
        method: str,
        url: str,
        *,
        status_code: int,
        body: str = "",
        content_type: str = "text/plain; charset=utf-8",
    ) -> httpx.Response:
        return httpx.Response(
            status_code=status_code,
            content=body.encode("utf-8"),
            headers={
                "content-type": content_type,
                "x-paypal-captcha-bypass": CAPTCHA_FRONTEND_DISABLE_MODE,
            },
            request=httpx.Request(method, url),
        )

    def _captcha_frontend_disable_response(
        self,
        method: str,
        url: str,
        *,
        force: bool = False,
    ) -> httpx.Response | None:
        if not force and not captcha_frontend_disable_enabled():
            return None
        if self._is_captcha_fake_200_endpoint(url):
            try:
                self.state.captcha_synthetic_used = True
            except Exception:
                pass
            logger.info(
                "{} {} -> synthetic CAPTCHA close 200 (frontend_disable)",
                method,
                url,
            )
            return self._synthetic_response(
                method,
                url,
                status_code=200,
                body="<html><body></body></html>",
                content_type="text/html; charset=utf-8",
            )
        if self._is_captcha_block_204_asset(url):
            try:
                self.state.captcha_synthetic_used = True
            except Exception:
                pass
            logger.info(
                "{} {} -> synthetic CAPTCHA asset 204 (frontend_disable)",
                method,
                url,
            )
            return self._synthetic_response(
                method,
                url,
                status_code=204,
                body="",
                content_type="text/plain; charset=utf-8",
            )
        return None

    def get(self, url: str, **kwargs) -> httpx.Response:
        logger.debug(f"GET {url}")
        disable_captcha_synthetic = bool(kwargs.pop("disable_captcha_synthetic", False))
        force_captcha_synthetic = bool(kwargs.pop("force_captcha_synthetic", False))
        self._inject_high_entropy_hints(url, kwargs)
        self._inject_datadome_header_if_needed(url, kwargs)
        req_id = None
        if self.traffic_recorder is not None:
            req_id = self.traffic_recorder.record_request(
                "GET",
                url,
                kwargs,
                headers=self._effective_headers_for_record(kwargs),
            )
        synthetic = (
            None
            if disable_captcha_synthetic
            else self._captcha_frontend_disable_response(
                "GET",
                url,
                force=force_captcha_synthetic,
            )
        )
        if synthetic is not None:
            if self.traffic_recorder is not None and req_id is not None:
                self.traffic_recorder.record_response(
                    req_id,
                    "GET",
                    url,
                    synthetic,
                    synthetic=True,
                )
            return synthetic
        if self._use_curl:
            kwargs = self._prepare_curl_kwargs(kwargs)
        resp = self._request_with_retry("GET", url, kwargs, req_id)
        self._check_accept_ch(resp)
        self._sync_state_cookies()
        self._sync_state_response_headers(resp)
        if self.traffic_recorder is not None and req_id is not None:
            self.traffic_recorder.record_response(req_id, "GET", url, resp)
        logger.debug(f"  -> {resp.status_code} ({len(resp.content)} bytes)")
        return resp

    def _request_with_retry(self, method: str, url: str, kwargs: dict[str, Any], req_id: Any):
        """网络层重试: 仅对代理隧道/连接类瞬时错误重试 (502 CONNECT / 超时 / 断连)。

        711 等住宅代理偶发 CONNECT 502, 重试 2 次即可跨过; HTTP 4xx/5xx 响应不重试。
        """
        import time as _time

        max_tries = int(os.getenv("PAYPAL_SESSION_RETRY", "3"))
        last_exc: Exception | None = None
        for attempt in range(1, max_tries + 1):
            try:
                if method == "GET":
                    return self.client.get(url, **kwargs)
                return self.client.post(url, **kwargs)
            except Exception as exc:
                last_exc = exc
                text = f"{type(exc).__name__}: {exc}"
                markers = (
                    "ProxyError", "CONNECT tunnel", "Connection reset",
                    "RemoteDisconnected", "timed out", "Timeout", "502", "503",
                )
                if not any(m in text for m in markers):
                    raise
                if attempt >= max_tries:
                    break
                delay = 1.5 * attempt
                logger.warning(
                    "{} {} attempt {}/{} transient network error: {}; retrying in {:.1f}s",
                    method, url, attempt, max_tries, text[:160], delay,
                )
                _time.sleep(delay)
        assert last_exc is not None
        if self.traffic_recorder is not None and req_id is not None:
            try:
                self.traffic_recorder.record_response(
                    req_id, method, url, None, error=str(last_exc),
                )
            except Exception:
                pass
        raise last_exc

    def post(self, url: str, **kwargs) -> httpx.Response:
        logger.debug(f"POST {url}")
        disable_captcha_synthetic = bool(kwargs.pop("disable_captcha_synthetic", False))
        force_captcha_synthetic = bool(kwargs.pop("force_captcha_synthetic", False))
        self._inject_high_entropy_hints(url, kwargs)
        self._inject_datadome_header_if_needed(url, kwargs)
        req_id = None
        if self.traffic_recorder is not None:
            req_id = self.traffic_recorder.record_request(
                "POST",
                url,
                kwargs,
                headers=self._effective_headers_for_record(kwargs),
            )
        synthetic = (
            None
            if disable_captcha_synthetic
            else self._captcha_frontend_disable_response(
                "POST",
                url,
                force=force_captcha_synthetic,
            )
        )
        if synthetic is not None:
            if self.traffic_recorder is not None and req_id is not None:
                self.traffic_recorder.record_response(
                    req_id,
                    "POST",
                    url,
                    synthetic,
                    synthetic=True,
                )
            return synthetic
        multipart = None
        if self._use_curl:
            kwargs = self._prepare_curl_kwargs(kwargs, move_content=True)
            multipart = kwargs.get("multipart")
        try:
            resp = self._request_with_retry("POST", url, kwargs, req_id)
        finally:
            if multipart is not None:
                try:
                    multipart.close()
                except Exception:
                    pass
        self._check_accept_ch(resp)
        self._sync_state_cookies()
        self._sync_state_response_headers(resp)
        if self.traffic_recorder is not None and req_id is not None:
            self.traffic_recorder.record_response(req_id, "POST", url, resp)
        logger.debug(f"  -> {resp.status_code} ({len(resp.content)} bytes)")
        return resp

    def graphql(self, operation_name: str, query: str, variables: dict[str, object],
                extra_headers: Optional[dict[str, object]] = None,
                extra_body: Optional[dict[str, object]] = None,
                batched: bool = False,
                endpoint: Optional[str] = None,
                graphql_error_level: str = "ERROR") -> dict[str, object] | list[dict[str, object]]:
        """Send a GraphQL request to PayPal's graphql endpoint."""
        url = endpoint or "https://www.paypal.com/graphql"
        if operation_name and endpoint is None:
            url = f"{url}?{operation_name}"

        context_token = str(
            variables.get("token")
            or variables.get("billingAgreementId")
            or self.state.ec_token
            or self.state.ba_token
        )
        referer = (
            self.state.signup_url
            if self.state.ec_token
            else f"https://www.paypal.com/pay?token={self.state.ba_token}&ul=1"
        )
        profile = getattr(self.state, "browser_profile", None) or BROWSER_PROFILE
        app_name = "checkoutuinodeweb" if operation_name == "authorize" else "checkoutuinodeweb_weasley"
        # Browser/Roxy checkoutweb sends the active checkout token (EC/BA) as
        # both PayPal-Client-Context and PayPal-Client-Metadata-Id.  Keep the
        # random per-session UUID only as a last-resort fallback for requests
        # that genuinely do not have a checkout context token.
        metadata_id = context_token or self.state.paypal_client_metadata_id
        headers = {
            "Content-Type": "application/json",
            "X-App-Name": app_name,
            "X-Requested-With": "fetch",
            "PayPal-Client-Context": context_token,
            "PayPal-Client-Metadata-Id": metadata_id,
            "X-Country": str(profile.get("country") or "BR"),
            "X-Locale": str(profile.get("locale") or "pt_BR"),
            "Origin": "https://www.paypal.com",
            "Referer": referer,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if self.state.euat_token:
            headers["X-PayPal-Internal-EUAT"] = self.state.euat_token
        if extra_headers:
            # Passing None removes a default header. This is needed for the
            # browser-captured final Hagrid authorize call, which posts to
            # /graphql/ without PayPal-Client-Context/X-Country/X-Locale.
            for key, value in extra_headers.items():
                if value is None:
                    headers.pop(key, None)
                else:
                    headers[key] = str(value)

        payload_item: dict[str, object] = {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
        if extra_body:
            # checkoutweb/weasley injects fn_sync_data at the top level of the
            # GraphQL JSON body for SignUpNewMemberMutation.
            payload_item.update(extra_body)

        payload = [payload_item] if batched else payload_item

        resp = self.post(url, json=payload, headers=headers)
        debug_id = _paypal_debug_id(resp.headers)
        logger.info(
            "GraphQL {} HTTP {} bytes={} paypal_debug_id={}",
            operation_name,
            resp.status_code,
            len(resp.content),
            debug_id or "<missing>",
        )

        try:
            result = resp.json()
        except ValueError:
            text = resp.text
            # Keep the full HTML separately for post-mortem/replay.  The log is
            # intentionally truncated, but authchallenge hidden fields normally
            # live near the bottom of the document.
            try:
                if text.lstrip().startswith("<"):
                    Path(f"/tmp/paypal_gql_{operation_name}_last.html").write_text(
                        text,
                        encoding="utf-8",
                    )
                Path(f"/tmp/paypal_gql_{operation_name}_last.json").write_text(
                    json.dumps(
                        {
                            "status_code": resp.status_code,
                            "paypal_debug_id": debug_id,
                            "response_headers": dict(resp.headers),
                            "response_head": text[:4000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

            if looks_like_paypal_authchallenge(text):
                logger.warning(
                    "GraphQL {} returned PayPal authchallenge HTML: status={} paypal_debug_id={} body={}",
                    operation_name,
                    resp.status_code,
                    debug_id or "<missing>",
                    text[:1200],
                )
                raise PayPalAuthChallenge(operation_name, resp.status_code, debug_id, text)
            logger.error(
                "GraphQL {} returned non-JSON response: status={} paypal_debug_id={} body={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                text[:2000],
            )
            raise

        result_items = result if isinstance(result, list) else [result]
        for item in result_items:
            if not isinstance(item, dict) or not item.get("errors"):
                continue

            logger.log(
                (graphql_error_level or "ERROR").upper(),
                "GraphQL {} returned errors: status={} paypal_debug_id={} errors={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                json.dumps(
                    sanitize_for_log(item.get("errors")),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            logger.debug(
                "GraphQL {} sanitized variables: {}",
                operation_name,
                json.dumps(
                    sanitize_for_log(variables),
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        return result
