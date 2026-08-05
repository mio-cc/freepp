from __future__ import annotations

import hashlib
import html as html_lib
import importlib
import json
import logging
import os
import random
import re
import time
import urllib.parse
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast


_config = importlib.import_module("config")
MTR_API_KEY = str(getattr(_config, "MTR_API_KEY", ""))
MTR_CHANNEL = str(getattr(_config, "MTR_CHANNEL", "iwc-mxo"))
MTR_ROXY_WAIT_SECONDS = float(getattr(_config, "MTR_ROXY_WAIT_SECONDS", 20.0))
MTR_RUNTIME_MODE = str(getattr(_config, "MTR_RUNTIME_MODE", "python_generated"))


logger = logging.getLogger(__name__)


class _MtrState(Protocol):
    mtr_channel: str
    mtr_client_metadata_id: str
    mtr_api_key: str
    mtr_dfp_script_url: str
    mtr_get_status: int
    mtr_post_status: int
    mtr_request_id: str
    mtr_sealed_result: str
    mtr_runtime_source: str
    mtr_visitor_token: str
    mtr_is_qa: bool
    mtr_completed: bool
    mtr_completed_cmid: str
    browser_profile: dict[str, object]
    screen: dict[str, object]
    viewport: dict[str, object]
    device_fingerprint: dict[str, object]


class _RoxyCaptureProfile(Protocol):
    def __call__(self, *, keep_browser: bool, proxy_url: object = None) -> dict[str, object]: ...


class _RoxyMtrRunner(Protocol):
    def __call__(
        self,
        roxy_browser: dict[str, object],
        page_url: str,
        *,
        dfp_config: dict[str, object],
        dfp_script_url: str,
        cookies: list[dict[str, object]] | None = None,
        wait_seconds: float = 20.0,
    ) -> dict[str, object]: ...


class _MtrSession(Protocol):
    def get(self, url: str, **kwargs: object) -> object: ...

    def post(self, url: str, **kwargs: object) -> object: ...


@dataclass
class _GeneratedMtrState:
    mtr_channel: str
    mtr_client_metadata_id: str
    mtr_api_key: str
    mtr_dfp_script_url: str
    browser_profile: dict[str, object]
    screen: dict[str, object]
    viewport: dict[str, object]
    mtr_get_status: int = 0
    mtr_post_status: int = 0
    mtr_request_id: str = ""
    mtr_sealed_result: str = ""
    mtr_runtime_source: str = "python_generated"
    mtr_visitor_token: str = ""
    mtr_is_qa: bool = False
    mtr_completed: bool = False
    mtr_completed_cmid: str = ""
    device_fingerprint: dict[str, object] = field(default_factory=dict)


MTR_BASE = "https://www.paypal.com/mtr/1a7c3460cd8c343771081839499ed7a0"
MTR_X0_PATH = "/AvQ9/Gr6-8k/ViQEi/xLu1/x0"
DEFAULT_DFP_SCRIPT_URL = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
MTR_RUNTIME_BLOCK = "block"
MTR_RUNTIME_PYTHON_GENERATED = "python_generated"
MTR_RUNTIME_ROXY = "roxy"
MTR_RUNTIME_HEADLESS = "headless"
MTR_RUNTIME_AUTO = "auto"
MTR_RUNTIME_OFF = "off"
MTR_COMPRESSED_MARKER = (3, 14)
MTR_UNCOMPRESSED_MARKER = (3, 13)
MTR_BODY_MARKER = bytes(MTR_COMPRESSED_MARKER)
MTR_COMPRESSION_THRESHOLD = 1024
MTR_XOR_KEY_LENGTH = 9
MTR_JS_SIGNAL_LAYOUT: tuple[tuple[str, int, str], ...] = (
    ("s1", -1, "none"), ("s2", 0, "list"), ("s3", 0, "int"), ("s4", 0, "int"),
    ("s5", 0, "list"), ("s6", 0, "list"), ("s7", 0, "int"), ("s9", 0, "str"),
    ("s10", 0, "bool"), ("s11", 0, "bool"), ("s12", 0, "bool"), ("s13", 0, "bool"),
    ("s14", -1, "none"), ("s15", 0, "str"), ("s16", 0, "list"), ("s17", 0, "dict"),
    ("s19", 0, "dict"), ("s20", 0, "list"), ("s21", 0, "float"), ("s22", 0, "int"),
    ("s23", -3, "none"), ("s24", 0, "int"), ("s27", 0, "str"), ("s28", 0, "list"),
    ("s29", 0, "int"), ("s30", -1, "none"), ("s32", 0, "bool"), ("s33", 0, "bool"),
    ("s36", -1, "none"), ("s37", 0, "str"), ("s38", 0, "int"), ("s39", 0, "bool"),
    ("s40", 0, "bool"), ("s41", -1, "none"), ("s42", 0, "int"), ("s43", 0, "bool"),
    ("s44", 0, "bool"), ("s45", 0, "list"), ("s46", 0, "str"), ("s48", 0, "list"),
    ("s49", 0, "list"), ("s50", 0, "int"), ("s51", 0, "dict"), ("s52", -2, "none"),
    ("s55", -1, "none"), ("s56", 0, "str"), ("s57", 0, "float"), ("s58", 0, "dict"),
    ("s59", 0, "bool"), ("s60", 0, "bool"), ("s61", 0, "bool"), ("s62", 0, "bool"),
    ("s63", 0, "bool"), ("s64", 0, "bool"), ("s65", 0, "bool"), ("s66", -1, "none"),
    ("s67", -1, "none"), ("s68", 0, "bool"), ("s69", 0, "list"), ("s70", -4, "none"),
    ("s71", 0, "dict"), ("s72", 0, "bool"), ("s74", 0, "dict"), ("s75", 0, "dict"),
    ("s76", 0, "str"), ("s77", -3, "dict"), ("s79", -3, "list"), ("s80", 0, "bool"),
    ("s81", 0, "int"), ("s82", 0, "str"), ("s83", 0, "list"), ("s84", 0, "dict"),
    ("s85", -1, "none"), ("s86", -1, "none"), ("s87", 0, "dict"), ("s89", 0, "str"),
    ("s91", 0, "bool"), ("s92", 0, "dict"), ("s93", 0, "dict"), ("s94", 0, "dict"),
    ("s95", -1, "none"), ("s96", -2, "none"), ("s97", -3, "none"), ("s98", 0, "bool"),
    ("s99", 0, "bool"), ("s101", 0, "str"), ("s102", 0, "bool"), ("s103", 0, "str"),
    ("s104", 0, "int"), ("s106", 0, "bool"), ("s117", 0, "int"), ("s118", 0, "bool"),
    ("s119", 0, "str"), ("s120", 0, "bool"), ("s123", 0, "str"), ("s130", 0, "list"),
    ("s131", 0, "list"), ("s132", 0, "str"), ("s133", 0, "str"), ("s135", 0, "int"),
    ("s136", 0, "bool"), ("s139", 0, "bool"), ("s142", 0, "bool"), ("s144", -2, "none"),
    ("s145", 0, "list"), ("s146", 0, "bool"), ("s148", 0, "str"), ("s149", -1, "none"),
    ("s150", 0, "dict"), ("s151", -1, "none"), ("s152", 0, "int"), ("s153", 0, "bool"),
    ("s154", 0, "dict"), ("s155", 0, "dict"), ("s156", 0, "list"), ("s157", 0, "dict"),
    ("s158", 0, "bool"), ("s159", 0, "bool"), ("s160", -2, "none"), ("s162", 0, "bool"),
    ("s163", 0, "bool"), ("s165", 0, "dict"), ("s166", 0, "dict"), ("s167", -5, "none"),
    ("s200", 0, "float"), ("s201", 0, "bool"), ("s202", 0, "str"),
)


def strict_mtr_required() -> bool:
    return os.getenv("PAYPAL_REQUIRE_MTR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def roxy_runtime_fallback_enabled() -> bool:
    value = (
        _load_dotenv_value("PAYPAL_ROXY_RUNTIME_FALLBACK")
        or _load_dotenv_value("PAYPAL_ROXY_FALLBACK")
        or "1"
    ).strip().lower()
    return value not in {"0", "false", "no", "off", "strict", "disabled", "disable"}


def headless_runtime_fallback_enabled() -> bool:
    value = (
        _load_dotenv_value("PAYPAL_HEADLESS_RUNTIME_FALLBACK")
        or _load_dotenv_value("PAYPAL_LOCAL_HEADLESS_FALLBACK")
        or "1"
    ).strip().lower()
    return value not in {"0", "false", "no", "off", "strict", "disabled", "disable"}


def mtr_runtime_mode(mode: str | None = None) -> str:
    mode = (mode if mode is not None else os.getenv("PAYPAL_MTR_RUNTIME", str(MTR_RUNTIME_MODE or MTR_RUNTIME_PYTHON_GENERATED))).strip().lower().replace("-", "_")
    if not mode:
        return MTR_RUNTIME_PYTHON_GENERATED
    if mode in {"0", "false", "no", "disabled", "disable", "skip"}:
        return MTR_RUNTIME_OFF
    if mode in {"python", "protocol", "generated", "python_generated"}:
        return MTR_RUNTIME_PYTHON_GENERATED
    if mode in {"roxy", "browser", "real_browser", "chrome", "chromium"}:
        return MTR_RUNTIME_ROXY
    if mode in {"headless", "headless_optimized", "optimized_headless", "local_headless", "playwright", "local_playwright"}:
        return MTR_RUNTIME_HEADLESS
    if mode in {"auto", "prefer_roxy", "roxy_auto"}:
        return MTR_RUNTIME_AUTO
    if mode in {"block", "strict", "browser", "real_browser", "real_browser_required"}:
        return MTR_RUNTIME_BLOCK
    return MTR_RUNTIME_BLOCK


def mtr_roxy_wait_seconds() -> float:
    raw = os.getenv("PAYPAL_MTR_ROXY_WAIT_SECONDS", "").strip()
    if raw:
        try:
            return max(2.0, min(float(raw), 90.0))
        except ValueError:
            pass
    return max(2.0, min(float(MTR_ROXY_WAIT_SECONDS), 90.0))


def mtr_headless_wait_seconds() -> float:
    raw = os.getenv("PAYPAL_MTR_HEADLESS_WAIT_SECONDS", "").strip()
    if raw:
        try:
            return max(2.0, min(float(raw), 90.0))
        except ValueError:
            pass
    return mtr_roxy_wait_seconds()


def _load_dotenv_value(name: str) -> str:
    """Read one .env value without requiring python-dotenv."""
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
                _ = os.environ.setdefault(name, value)
                return value
        except Exception:
            continue
    return ""


def default_mtr_channel() -> str:
    return (
        _load_dotenv_value("PAYPAL_MTR_CHANNEL")
        or _load_dotenv_value("MTR_CHANNEL")
        or str(MTR_CHANNEL or "")
        or "iwc-mxo"
    ).strip()


def default_mtr_api_key() -> str:
    return (
        _load_dotenv_value("PAYPAL_MTR_API_KEY")
        or _load_dotenv_value("MTR_API_KEY")
        or str(MTR_API_KEY or "")
    ).strip()


def _query_value(url: str, *names: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url or "")
    except Exception:
        return ""
    wanted = {name.lower() for name in names}
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in wanted and value:
            return value.strip()
    return ""


def _looks_like_ba_token(value: str) -> bool:
    return bool(re.fullmatch(r"BA-[A-Za-z0-9]{8,80}", (value or "").strip()))


def _looks_like_uuid(value: str) -> bool:
    return bool(re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        (value or "").strip(),
    ))


def derive_mtr_client_metadata_id(state: object, page_url: str = "") -> str:
    """Choose a cmid fallback when dfpconfig is absent from the HTML."""
    explicit = (
        _load_dotenv_value("PAYPAL_MTR_CLIENT_METADATA_ID")
        or _load_dotenv_value("MTR_CLIENT_METADATA_ID")
    ).strip()
    if explicit:
        return explicit
    existing = str(getattr(state, "mtr_client_metadata_id", "") or "").strip()
    if _looks_like_ba_token(existing):
        return existing
    token = _query_value(
        page_url,
        "token",
        "ba_token",
        "billingAgreementToken",
    )
    if _looks_like_ba_token(token):
        return token
    ba_token = str(getattr(state, "ba_token", "") or "").strip()
    if _looks_like_ba_token(ba_token):
        return ba_token
    token = _query_value(
        page_url,
        "clientMetaDataId",
        "client_metadata_id",
        "cmid",
    )
    if token:
        return token
    if existing:
        return existing
    paypal_cmid = str(getattr(state, "paypal_client_metadata_id", "") or "").strip()
    if paypal_cmid:
        return paypal_cmid
    return ""


def ensure_mtr_config(state: object, *, page_url: str = "") -> bool:
    """Fill missing MTR dfpconfig fields from stable runtime/config fallbacks."""
    channel = str(getattr(state, "mtr_channel", "") or "").strip()
    api_key = str(getattr(state, "mtr_api_key", "") or "").strip()
    cmid = str(getattr(state, "mtr_client_metadata_id", "") or "").strip()
    fallback_cmid = derive_mtr_client_metadata_id(state, page_url)
    if not channel:
        setattr(state, "mtr_channel", default_mtr_channel())
    # If the page only exposed a generic UUID-like clientMetadataId while the
    # checkout URL/state has the BA token, use the BA token for MTR cmid.  A
    # partial dfpconfig (cmid only, no channel/api key) should not pin MTR to a
    # random page UUID.
    if (
        fallback_cmid
        and (
            not cmid
            or (_looks_like_ba_token(fallback_cmid) and not _looks_like_ba_token(cmid))
            or ((not channel or not api_key) and _looks_like_uuid(cmid) and _looks_like_ba_token(fallback_cmid))
        )
    ):
        setattr(state, "mtr_client_metadata_id", fallback_cmid)
    if not api_key:
        setattr(state, "mtr_api_key", default_mtr_api_key())
    return bool(
        str(getattr(state, "mtr_channel", "") or "").strip()
        and str(getattr(state, "mtr_client_metadata_id", "") or "").strip()
        and str(getattr(state, "mtr_api_key", "") or "").strip()
    )


def _json_loads_lenient(raw: str) -> dict[str, object]:
    text = html_lib.unescape(raw or "").strip()
    if not text:
        return {}
    candidates: list[str] = []

    def add(value: str) -> None:
        value = html_lib.unescape((value or "").strip())
        if value and value not in candidates:
            candidates.append(value)

    add(text)
    current = text
    for _ in range(5):
        decoded = current.replace(r"\/", "/").replace(r"\u0022", '"')
        try:
            decoded = bytes(decoded, "utf-8").decode("unicode_escape")
        except Exception:
            decoded = decoded.replace(r"\"", '"')
        add(decoded)
        if decoded == current:
            break
        current = decoded

    for candidate in candidates:
        value: object = candidate
        for _ in range(3):
            if not isinstance(value, str):
                break
            stripped = value.strip()
            if not stripped:
                break
            if not (
                stripped.startswith("{")
                or stripped.startswith("[")
                or (stripped.startswith('"') and stripped.endswith('"'))
            ):
                break
            try:
                value = cast(object, json.loads(stripped))
            except Exception:
                break
            if isinstance(value, dict):
                return cast(dict[str, object], value)
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        try:
            data = cast(object, json.loads(candidate))
        except Exception:
            continue
        if isinstance(data, dict):
            return cast(dict[str, object], data)
    return {}

def _extract_mtr_config_from_dfp_config_property(html: str) -> dict[str, object]:
    """Extract the actual `dfpMetaData.dfpConfig` JSON string from React Flight/RSC."""
    if not html:
        return {}
    variants: list[str] = []

    def add(value: str) -> None:
        value = html_lib.unescape(value or "")
        if value and value not in variants:
            variants.append(value)

    add(html)
    current = html
    for _ in range(4):
        decoded = current.replace(r"\/", "/").replace(r"\u0022", '"')
        try:
            decoded = bytes(decoded, "utf-8").decode("unicode_escape")
        except Exception:
            decoded = decoded.replace(r"\"", '"')
        add(decoded)
        if decoded == current:
            break
        current = decoded

    for text in variants:
        start = 0
        while True:
            idx = text.find("dfpConfig", start)
            if idx < 0:
                break
            window = text[idx:idx + 5000]
            # The dfpConfig value is a JSON string whose payload is a flat JSON
            # object.  In raw React Flight it looks like:
            #   \\"dfpConfig\\":\\"{\\\\\\"dfpChannel\\\\\\":...}\\"
            # After one/two decode layers it becomes either:
            #   "dfpConfig":"{\"dfpChannel\":...}"
            # or:
            #   "dfpConfig":"{"dfpChannel":...}".
            for match in re.finditer(r"\{.{0,1800}?\}", window, re.S):
                candidate = match.group(0)
                if "dfpChannel" not in candidate and "fppAPIKey" not in candidate:
                    continue
                data = _json_loads_lenient(candidate)
                if data.get("dfpChannel") or data.get("fppAPIKey"):
                    return data
            # Also handle a cleaner JSON-string property shape.
            for match in re.finditer(r'"dfpConfig"\s*:\s*"((?:\\.|[^"\\])*)"', window, re.S):
                data = _json_loads_lenient(match.group(1))
                if data.get("dfpChannel") or data.get("fppAPIKey"):
                    return data
            start = idx + len("dfpConfig")
    return {}


def _extract_mtr_config_by_fields(html: str) -> dict[str, object]:
    """Fallback for Next/RSC chunks that contain escaped dfpconfig fields."""
    text = html_lib.unescape(html or "")
    variants = [text]
    if r"\"" in text or r"\u0022" in text:
        try:
            variants.append(bytes(text, "utf-8").decode("unicode_escape"))
        except Exception:
            variants.append(text.replace(r"\"", '"').replace(r"\/", "/").replace(r"\u0022", '"'))

    def pick(patterns: tuple[str, ...]) -> str:
        for body in variants:
            for pattern in patterns:
                match = re.search(pattern, body, re.I | re.S)
                if match:
                    return html_lib.unescape(match.group(1)).strip()
        return ""

    channel = pick((
        r'"dfpChannel"\s*:\s*"([^"]+)"',
        r"'dfpChannel'\s*:\s*'([^']+)'",
        r'\bdfpChannel\b["\']?\s*[:=]\s*["\']([^"\']+)',
    ))
    cmid = pick((
        r'"clientMetaDataId"\s*:\s*"([^"]+)"',
        r'"clientMetadataId"\s*:\s*"([^"]+)"',
        r"'clientMetaDataId'\s*:\s*'([^']+)'",
        r'\bclientMetaDataId\b["\']?\s*[:=]\s*["\']([^"\']+)',
    ))
    api_key = pick((
        r'"fppAPIKey"\s*:\s*"([^"]+)"',
        r"'fppAPIKey'\s*:\s*'([^']+)'",
        r'\bfppAPIKey\b["\']?\s*[:=]\s*["\']([^"\']+)',
    ))
    is_qa_text = pick((
        r'"isQA"\s*:\s*(true|false)',
        r"'isQA'\s*:\s*(true|false)",
        r'\bisQA\b["\']?\s*[:=]\s*(true|false)',
    ))
    data: dict[str, object] = {}
    if channel:
        data["dfpChannel"] = channel
    if cmid:
        data["clientMetaDataId"] = cmid
    if api_key:
        data["fppAPIKey"] = api_key
    if is_qa_text:
        data["isQA"] = is_qa_text.lower() == "true"
    # Do not return a lone generic clientMetadataId from the page bootstrap as
    # an MTR config.  It caused partial "captured" logs and then fell back to a
    # manual API key.  Only field-level extraction tied to dfp fields is useful.
    if data and not (data.get("dfpChannel") or data.get("fppAPIKey")):
        return {}
    return data


def extract_mtr_config(html: str) -> dict[str, object]:
    """Extract the JSON payload from PayPal's `<script id=dfpconfig>`."""
    if not html:
        return {}
    for pattern in (
        r'<script[^>]*\bid=["\']dfpconfig["\'][^>]*>(.*?)</script>',
        r'<script[^>]*\bid=\\?["\']dfpconfig\\?["\'][^>]*>(.*?)</script>',
    ):
        match = re.search(pattern, html, re.I | re.S)
        if not match:
            continue
        data = _json_loads_lenient(match.group(1))
        if data:
            return data

    data = _extract_mtr_config_from_dfp_config_property(html)
    if data:
        return data

    # Some PayPal builds inline dfpData/dfpconfig inside Next.js/RSC chunks
    # rather than as a dedicated script tag.  Read the exact fields without
    # relying on a particular chunk shape.
    return _extract_mtr_config_by_fields(html)


def merge_mtr_config_with_fallbacks(config: dict[str, object], state: object, *, page_url: str = "") -> dict[str, object]:
    """Return a complete dfp config where page values win over defaults."""
    merged = dict(config or {})
    if not merged.get("dfpChannel"):
        merged["dfpChannel"] = str(getattr(state, "mtr_channel", "") or default_mtr_channel())
    if not merged.get("clientMetaDataId"):
        merged["clientMetaDataId"] = derive_mtr_client_metadata_id(state, page_url)
    if not merged.get("fppAPIKey"):
        merged["fppAPIKey"] = str(getattr(state, "mtr_api_key", "") or default_mtr_api_key())
    if "isQA" not in merged:
        merged["isQA"] = bool(getattr(state, "mtr_is_qa", False))
    return merged



def extract_dfp_script_url(html: str) -> str:
    """Prefer the exact dfp.js URL from the live page when present."""
    for text in (html or "", html_lib.unescape(html or "")):
        match = re.search(r'https://www\.paypalobjects\.com/[^"\']+/dfp\.js', text)
        if match:
            return match.group(0)
    return DEFAULT_DFP_SCRIPT_URL


def mtr_get_url(api_key: str) -> str:
    return f"{MTR_BASE}{MTR_X0_PATH}?q={urllib.parse.quote(api_key or '')}"


def mtr_post_url(*, channel: str, cmid: str, browser_timezone: str, api_key: str, csrf_nonce: str = "") -> str:
    query = {
        "chnl": channel or "iwc-mxo",
        "cmid": cmid,
        "cr": csrf_nonce or "undefined",
        "btz": browser_timezone or "UTC",
        "emf": "true",
        "ci": "js/3.12.1",
        "q": api_key,
    }
    return f"{MTR_BASE}?{urllib.parse.urlencode(query)}"


def _profile(state: _MtrState) -> dict[str, object]:
    return state.browser_profile or {}


def _screen(state: _MtrState) -> dict[str, object]:
    return state.screen or {}


def _viewport(state: _MtrState) -> dict[str, object]:
    return state.viewport or {}


def _dfp(state: _MtrState) -> dict[str, object]:
    return state.device_fingerprint or {}


def _dict_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = cast(list[object], value)
    elif isinstance(value, tuple):
        items = list(cast(tuple[object, ...], value))
    else:
        return []
    return [str(item) for item in items if item is not None and str(item)]


def _string_list_or_default(value: object, default: list[str]) -> list[str]:
    if isinstance(value, list):
        items = cast(list[object], value)
    elif isinstance(value, tuple):
        items = list(cast(tuple[object, ...], value))
    else:
        return default
    return [str(item) for item in items if item is not None and str(item)]


def _int_list(value: object) -> list[int]:
    if isinstance(value, list):
        items = cast(list[object], value)
    elif isinstance(value, tuple):
        items = list(cast(tuple[object, ...], value))
    else:
        return []
    return [_int_value(item) for item in items]


def _float_list(value: object) -> list[float]:
    if isinstance(value, list):
        items = cast(list[object], value)
    elif isinstance(value, tuple):
        items = list(cast(tuple[object, ...], value))
    else:
        return []
    return [_float_value(item) for item in items]


def _str_value(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return text if text else default


def _int_value(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, (str, int, float)):
            return int(value)
    except Exception:
        pass
    return default


def _float_value(value: object, default: float = 0.0) -> float:
    try:
        if isinstance(value, (str, int, float)):
            return float(value)
    except Exception:
        pass
    return default


def _bool_value(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default

def _stable_hash(value: object, length: int = 32) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _to_int32(value: float) -> int:
    integer = int(value) & 0xFFFFFFFF
    return integer - 0x100000000 if integer >= 0x80000000 else integer


def _dfp_add64(target: list[int], value: list[int]) -> None:
    e = target[0] >> 16
    r = target[0] & 0xFFFF
    o = target[1] >> 16
    i = target[1] & 0xFFFF
    u = value[0] >> 16
    c = value[0] & 0xFFFF
    a = value[1] >> 16
    s = 0
    l = 0
    f = 0
    d = 0
    d += i + (value[1] & 0xFFFF)
    f += d >> 16
    d &= 0xFFFF
    f += o + a
    l += f >> 16
    f &= 0xFFFF
    l += r + c
    s += l >> 16
    l &= 0xFFFF
    s += e + u
    s &= 0xFFFF
    target[0] = _u32((s << 16) | l)
    target[1] = _u32((f << 16) | d)


def _dfp_mul64(target: list[int], value: list[int]) -> None:
    e = target[0] >> 16
    r = target[0] & 0xFFFF
    o = target[1] >> 16
    i = target[1] & 0xFFFF
    u = value[0] >> 16
    c = value[0] & 0xFFFF
    a = value[1] >> 16
    s = value[1] & 0xFFFF
    l = 0
    f = 0
    d = 0
    v = 0
    v += i * s
    d += v >> 16
    v &= 0xFFFF
    d += o * s
    f += d >> 16
    d &= 0xFFFF
    d += i * a
    f += d >> 16
    d &= 0xFFFF
    f += r * s
    l += f >> 16
    f &= 0xFFFF
    f += o * a
    l += f >> 16
    f &= 0xFFFF
    f += i * c
    l += f >> 16
    f &= 0xFFFF
    l += e * s + r * a + o * c + i * u
    l &= 0xFFFF
    target[0] = _u32((l << 16) | f)
    target[1] = _u32((d << 16) | v)


def _dfp_rotl64(target: list[int], bits: int) -> None:
    bits %= 64
    original_high = target[0]
    if bits == 32:
        target[0] = target[1]
        target[1] = original_high
    elif bits < 32:
        target[0] = _u32((original_high << bits) | (target[1] >> (32 - bits)))
        target[1] = _u32((target[1] << bits) | (original_high >> (32 - bits)))
    else:
        bits -= 32
        target[0] = _u32((target[1] << bits) | (original_high >> (32 - bits)))
        target[1] = _u32((original_high << bits) | (target[1] >> (32 - bits)))


def _dfp_shift_left64(target: list[int], bits: int) -> None:
    bits %= 64
    if bits == 0:
        return
    if bits < 32:
        target[0] = _u32(target[1] >> (32 - bits))
        target[1] = _u32(target[1] << bits)
    else:
        target[0] = _u32(target[1] << (bits - 32))
        target[1] = 0


def _dfp_xor64(target: list[int], value: list[int]) -> None:
    target[0] = _u32(target[0] ^ value[0])
    target[1] = _u32(target[1] ^ value[1])


def _dfp_fmix64(target: list[int]) -> None:
    value = [0, target[0] >> 1]
    _dfp_xor64(target, value)
    _dfp_mul64(target, [4283543511, 3981806797])
    value[1] = target[0] >> 1
    _dfp_xor64(target, value)
    _dfp_mul64(target, [3301882366, 444984403])
    value[1] = target[0] >> 1
    _dfp_xor64(target, value)


def _dfp_text_hash(text: str, seed: int = 0) -> str:
    data = text.encode("utf-8")
    length = len(data)
    tail = length % 16
    block_end = length - tail
    h1 = [0, seed]
    h2 = [0, seed]
    k1 = [0, 0]
    k2 = [0, 0]
    c1 = [2277735313, 289559509]
    c2 = [1291169091, 658871167]
    c3 = [0, 5]
    c4 = [0, 1390208809]
    c5 = [0, 944331445]
    for index in range(0, block_end, 16):
        k1[0] = _u32(data[index + 4] | data[index + 5] << 8 | data[index + 6] << 16 | data[index + 7] << 24)
        k1[1] = _u32(data[index] | data[index + 1] << 8 | data[index + 2] << 16 | data[index + 3] << 24)
        k2[0] = _u32(data[index + 12] | data[index + 13] << 8 | data[index + 14] << 16 | data[index + 15] << 24)
        k2[1] = _u32(data[index + 8] | data[index + 9] << 8 | data[index + 10] << 16 | data[index + 11] << 24)
        _dfp_mul64(k1, c1)
        _dfp_rotl64(k1, 31)
        _dfp_mul64(k1, c2)
        _dfp_xor64(h1, k1)
        _dfp_rotl64(h1, 27)
        _dfp_add64(h1, h2)
        _dfp_mul64(h1, c3)
        _dfp_add64(h1, c4)
        _dfp_mul64(k2, c2)
        _dfp_rotl64(k2, 33)
        _dfp_mul64(k2, c1)
        _dfp_xor64(h2, k2)
        _dfp_rotl64(h2, 31)
        _dfp_add64(h2, h1)
        _dfp_mul64(h2, c3)
        _dfp_add64(h2, c5)
    k1 = [0, 0]
    k2 = [0, 0]
    temp = [0, 0]
    for case in range(tail, 0, -1):
        if case >= 9:
            temp = [0, data[block_end + case - 1]]
            if case > 9:
                _dfp_shift_left64(temp, (case - 9) * 8)
                _dfp_xor64(k2, temp)
            else:
                _dfp_xor64(k2, temp)
                _dfp_mul64(k2, c2)
                _dfp_rotl64(k2, 33)
                _dfp_mul64(k2, c1)
                _dfp_xor64(h2, k2)
        else:
            temp = [0, data[block_end + case - 1]]
            if case > 1:
                _dfp_shift_left64(temp, (case - 1) * 8)
                _dfp_xor64(k1, temp)
            else:
                _dfp_xor64(k1, temp)
                _dfp_mul64(k1, c1)
                _dfp_rotl64(k1, 31)
                _dfp_mul64(k1, c2)
                _dfp_xor64(h1, k1)
    length_pair = [0, length]
    _dfp_xor64(h1, length_pair)
    _dfp_xor64(h2, length_pair)
    _dfp_add64(h1, h2)
    _dfp_add64(h2, h1)
    _dfp_fmix64(h1)
    _dfp_fmix64(h2)
    _dfp_add64(h1, h2)
    _dfp_add64(h2, h1)
    return f"{h1[0] & 0xFFFFFFFF:08x}{h1[1] & 0xFFFFFFFF:08x}{h2[0] & 0xFFFFFFFF:08x}{h2[1] & 0xFFFFFFFF:08x}"


def _dfp_source_hash(value: object, default: str) -> str:
    text = _str_value(value)
    if text and text not in {"unsupported", "unstable", "skipped"}:
        return _dfp_text_hash(text)
    return default


def _mtr_random_probe_values(value: object) -> list[int]:
    randoms = _float_list(value)
    if len(randoms) < 7:
        return []
    previous: float = randoms[0]
    values: list[int] = []
    for index in range(1, 7):
        current: float = randoms[index]
        delta: float = (previous - current) * 2147483648.0
        values.append(_to_int32(delta))
        previous = current
    return values


def _crc32_text(text: str) -> int:
    return zlib.crc32(text.encode("utf-8")) & 0xFFFFFFFF


def _window_key_probe(value: object) -> dict[str, object]:
    items: list[object] = cast(list[object], value) if isinstance(value, list) else []
    result: dict[str, object] = {}
    for item in items:
        if isinstance(item, dict):
            entry = cast(dict[str, object], item)
            name = _str_value(entry.get("name") or entry.get("n"))
            descriptor = _str_value(entry.get("descriptor") or entry.get("p"), "undefined")
        else:
            name = _str_value(item)
            descriptor = "undefined"
        if not name:
            continue
        result[str(_crc32_text(name))] = {
            "i": True,
            "t": None,
            "s": name[-3:],
            "e": 12,
            "p": descriptor[:50],
        }
    return result


def _random_bytes(length: int) -> bytes:
    if hasattr(random, "randbytes"):
        return random.randbytes(length)
    return bytes(random.getrandbits(8) for _ in range(length))


def _deflate_raw(payload: bytes) -> bytes:
    compressor = zlib.compressobj(level=6, wbits=-15)
    return compressor.compress(payload) + compressor.flush()


def _mtr_envelope(payload: bytes, *, compressed: bool) -> bytes:
    seed = random.getrandbits(8)
    marker = MTR_COMPRESSED_MARKER if compressed else MTR_UNCOMPRESSED_MARKER
    pad_len = random.randint(0, 3)
    padding = _random_bytes(pad_len)
    key = _random_bytes(MTR_XOR_KEY_LENGTH)
    mixed = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(payload))
    header = bytes(
        [
            seed,
            (seed + marker[0]) % 256,
            (seed + marker[1]) % 256,
            (seed + pad_len) % 256,
        ]
    )
    return header + padding + key + mixed


def _hashed_url_for_mtr(url: str) -> str:
    parts = urllib.parse.urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return _stable_hash(url, 64)
    path = f"/{_stable_hash(parts.path or '/', 64)}"
    query = _stable_hash(parts.query, 64) if parts.query else ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))

def _mtr_signal(status: int, value: object) -> dict[str, object]:
    return {"s": status, "v": value}


def _default_signal_value(kind: str) -> object:
    if kind == "list":
        return []
    if kind == "dict":
        return {}
    if kind == "bool":
        return False
    if kind == "int":
        return 0
    if kind == "float":
        return 0.0
    if kind == "str":
        return ""
    return None


def _uses_runtime_profile(profile: dict[str, object]) -> bool:
    return _str_value(profile.get("fingerprint_source")).lower() in {
        "roxy",
        "runtime",
        "browser",
        "headless",
        "local_headless",
        "playwright",
        "local_playwright",
    }


def _fingerprint_source(profile: dict[str, object], dfp: dict[str, object] | None = None) -> str:
    dfp_source = _str_value((dfp or {}).get("source"))
    profile_source = _str_value(profile.get("fingerprint_source"))
    return (profile_source or dfp_source or "random").strip().lower().replace("-", "_")


def _state_fingerprint_source(state: _MtrState) -> str:
    return _fingerprint_source(_profile(state), _dfp(state))


def _apply_runtime_profile_to_state(state: _MtrState, runtime: dict[str, object]) -> None:
    state.browser_profile = _dict_value(runtime.get("browser_profile"))
    state.screen = _dict_value(runtime.get("screen"))
    state.viewport = _dict_value(runtime.get("viewport"))
    state.device_fingerprint = _dict_value(runtime.get("device_fingerprint"))
    roxy_browser = _dict_value(runtime.get("roxy_browser"))
    if roxy_browser and hasattr(state, "roxy_browser"):
        setattr(state, "roxy_browser", roxy_browser)
    if hasattr(state, "fingerprint_source"):
        setattr(state, "fingerprint_source", _state_fingerprint_source(state))


def _ensure_mtr_runtime_fingerprint_source(session: _MtrSession, state: _MtrState, source: str) -> None:
    if _state_fingerprint_source(state) == source:
        return
    fingerprint_module = importlib.import_module("paypal.fingerprint")
    generate_runtime_profile = cast(Callable[..., dict[str, object]], getattr(fingerprint_module, "generate_runtime_profile"))
    strict_env = "PAYPAL_ROXY_FINGERPRINT_STRICT" if source == MTR_RUNTIME_ROXY else "PAYPAL_HEADLESS_FINGERPRINT_STRICT"
    previous_strict = os.environ.get(strict_env)
    os.environ[strict_env] = "1"
    try:
        runtime = generate_runtime_profile(
            source,
            roxy_proxy_url=getattr(session, "proxy_url", None) or "",
            keep_roxy_browser=source == MTR_RUNTIME_ROXY,
        )
    finally:
        if previous_strict is None:
            os.environ.pop(strict_env, None)
        else:
            os.environ[strict_env] = previous_strict
    runtime_profile = _dict_value(runtime.get("browser_profile"))
    runtime_dfp = _dict_value(runtime.get("device_fingerprint"))
    if _fingerprint_source(runtime_profile, runtime_dfp) != source:
        raise RuntimeError(f"MTR {source} runtime requires fingerprint_source={source}")
    _apply_runtime_profile_to_state(state, runtime)


def _mtr_profile_str(profile: dict[str, object], key: str, default: str) -> str:
    override = _str_value(profile.get(f"mtr_{key}"))
    if override:
        return override
    runtime_value = _str_value(profile.get(key))
    if _uses_runtime_profile(profile) and runtime_value:
        return runtime_value
    return default


def _mtr_profile_int(profile: dict[str, object], key: str, default: int) -> int:
    override = profile.get(f"mtr_{key}")
    if override is not None:
        return _int_value(override, default)
    if _uses_runtime_profile(profile):
        return _int_value(profile.get(key), default)
    return default


def _user_agent_data(profile: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
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
    for item in cast(list[object], value):
        entry = _dict_value(item)
        brand = _str_value(entry.get("brand") or entry.get("b"))
        version = _str_value(entry.get("version") or entry.get("v"))
        if brand and version:
            entries.append({"brand": brand, "version": version})
    return entries


def _payload_overrides(profile: dict[str, object], dfp: dict[str, object]) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for source in (profile, dfp):
        for key in ("mtr_payload_overrides", "mtr_top_level_overrides", "payload_overrides"):
            value = _dict_value(source.get(key))
            if value:
                overrides.update(value)
    return overrides


def _apply_signal_overrides(
    signals: dict[str, dict[str, object]],
    profile: dict[str, object],
    dfp: dict[str, object],
) -> None:
    for source in (profile, dfp):
        raw_overrides = _dict_value(source.get("mtr_signal_overrides") or source.get("signal_overrides"))
        for key, value in raw_overrides.items():
            if key not in signals:
                continue
            signal_value = _dict_value(value)
            if signal_value and "v" in signal_value:
                signals[key] = _mtr_signal(_int_value(signal_value.get("s"), _int_value(signals[key].get("s"))), signal_value.get("v"))
            else:
                signals[key] = _mtr_signal(_int_value(signals[key].get("s")), value)
        for key in list(signals):
            raw_key = f"mtr_{key}"
            if raw_key in source:
                signals[key] = _mtr_signal(_int_value(signals[key].get("s")), source.get(raw_key))


def _chrome_brand_signals(profile: dict[str, object]) -> tuple[list[dict[str, str]], list[dict[str, str]], str, str]:
    ua_data, high_entropy = _user_agent_data(profile)
    runtime_brands = _ua_brand_entries(high_entropy.get("brands") or ua_data.get("brands"))
    runtime_full_version_list = _ua_brand_entries(high_entropy.get("fullVersionList") or ua_data.get("fullVersionList"))
    if _uses_runtime_profile(profile) and runtime_brands:
        chrome_full_version = _str_value(
            high_entropy.get("uaFullVersion")
            or high_entropy.get("fullVersion")
            or profile.get("chrome_full_version"),
            "150.0.7871.46",
        )
        brands = [{"b": item["brand"], "v": item["version"]} for item in runtime_brands]
        full_version_list = runtime_full_version_list or [
            {"brand": item["brand"], "version": chrome_full_version if item["brand"] != "Not.A/Brand" else "99.0.0.0"}
            for item in runtime_brands
        ]
        brand_json = json.dumps(runtime_brands, separators=(",", ":"))
        return brands, full_version_list, brand_json, chrome_full_version

    chrome_major = _str_value(profile.get("chrome_major"), "150")
    chrome_full_version = _str_value(profile.get("chrome_full_version"), "150.0.7871.46")
    brands = [
        {"b": "Not;A=Brand", "v": "8"},
        {"b": "Chromium", "v": chrome_major},
        {"b": "Google Chrome", "v": chrome_major},
    ]
    full_version_list = [
        {"brand": "Not;A=Brand", "version": "8.0.0.0"},
        {"brand": "Chromium", "version": chrome_full_version},
        {"brand": "Google Chrome", "version": chrome_full_version},
    ]
    brand_json = json.dumps(
        [{"brand": item["b"], "version": item["v"]} for item in brands],
        separators=(",", ":"),
    )
    return brands, full_version_list, brand_json, chrome_full_version


def _plugin_signal_value(plugins: list[dict[str, object]]) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for plugin in plugins:
        mime_types: list[dict[str, str]] = []
        raw_mime_types = plugin.get("mimeTypes")
        if isinstance(raw_mime_types, list):
            for item in cast(list[object], raw_mime_types):
                mime_type = _dict_value(item)
                if mime_type:
                    type_value = _str_value(mime_type.get("type"))
                    if type_value:
                        mime_types.append({"type": type_value, "suffixes": _str_value(mime_type.get("suffixes"))})
                    continue
                type_value = _str_value(item)
                if type_value:
                    mime_types.append({"type": type_value, "suffixes": "pdf"})
        values.append(
            {
                "name": _str_value(plugin.get("name")),
                "description": _str_value(plugin.get("description"), "Portable Document Format"),
                "mimeTypes": mime_types,
            }
        )
    return values


def _font_metric_rect(width: float) -> dict[str, object]:
    return {
        "x": 8,
        "y": 10,
        "left": 8,
        "right": 8 + width,
        "bottom": 27,
        "height": 17,
        "top": 10,
        "width": width,
        "font": '"Times New Roman"',
    }


def _rect_value(value: object, default_width: float) -> dict[str, object]:
    rect = _dict_value(value)
    if rect:
        return rect
    return _font_metric_rect(default_width)


def _navigator_webdriver(profile: dict[str, object], dfp: dict[str, object]) -> bool:
    return _bool_value(dfp.get("navigator_webdriver"), _bool_value(profile.get("navigator_webdriver"), False))


def _headless_chrome(profile: dict[str, object], dfp: dict[str, object]) -> bool:
    source = _fingerprint_source(profile, dfp)
    ua = _str_value(profile.get("user_agent"))
    return source == MTR_RUNTIME_HEADLESS or "HeadlessChrome" in ua


def _browser_detect_flags(profile: dict[str, object], dfp: dict[str, object]) -> dict[str, bool]:
    webdriver = _navigator_webdriver(profile, dfp)
    return {
        "awesomium": False,
        "cef": False,
        "cefsharp": False,
        "coachjs": False,
        "fminer": False,
        "geb": False,
        "nightmarejs": False,
        "phantomas": False,
        "phantomjs": False,
        "rhino": False,
        "selenium": False,
        "webdriverio": False,
        "webdriver": webdriver,
        "headless_chrome": _headless_chrome(profile, dfp),
    }


def _webgl_signal_value(profile: dict[str, object]) -> dict[str, object]:
    vendor = _str_value(profile.get("webgl_vendor"), "WebKit")
    renderer = _str_value(profile.get("webgl_renderer"), "WebKit WebGL")
    unmasked_vendor = _str_value(profile.get("gpu_vendor"), "Google Inc. (Google)")
    unmasked_renderer = _str_value(
        profile.get("gpu_renderer"),
        "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)",
    )
    return {
        "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
        "vendor": vendor,
        "vendorUnmasked": unmasked_vendor,
        "renderer": renderer,
        "rendererUnmasked": unmasked_renderer,
        "shadingLanguageVersion": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
    }


def _css_color_probe_defaults() -> dict[str, str]:
    return {
        "ac": "rgb(0, 117, 255)",
        "act": "rgb(255, 255, 255)",
        "at": "rgb(255, 0, 0)",
        "ab": "rgb(0, 0, 0)",
        "aca": "rgb(255, 255, 255)",
        "aw": "rgb(255, 255, 255)",
        "b": "rgb(255, 255, 255)",
        "bh": "rgb(239, 239, 239)",
        "bs": "rgb(239, 239, 239)",
        "bb": "rgb(0, 0, 0)",
        "bf": "rgb(239, 239, 239)",
        "bt": "rgb(0, 0, 0)",
        "ft": "rgb(0, 0, 0)",
        "gt": "rgb(128, 128, 128)",
        "h": "rgba(0, 65, 198, 0.8)",
        "ht": "rgb(255, 255, 255)",
        "ib": "rgb(255, 255, 255)",
        "ic": "rgb(255, 255, 255)",
        "ict": "rgb(128, 128, 128)",
        "it": "rgb(0, 0, 0)",
        "lt": "rgb(0, 0, 238)",
        "m": "rgb(255, 255, 0)",
        "me": "rgb(255, 255, 255)",
        "s": "rgb(255, 255, 255)",
        "tdds": "rgb(0, 0, 0)",
        "tdf": "rgb(239, 239, 239)",
        "tdh": "rgb(0, 0, 0)",
        "tdls": "rgb(0, 0, 0)",
        "tds": "rgb(0, 0, 0)",
        "vt": "rgb(85, 26, 139)",
        "w": "rgb(255, 255, 255)",
        "wf": "rgb(0, 0, 0)",
        "wt": "rgb(0, 0, 0)",
        "si": "rgb(25, 103, 210)",
        "sit": "rgb(255, 255, 255)",
    }


def _font_probe_names(profile: dict[str, object]) -> list[str]:
    platform = _str_value(profile.get("platform"), "Linux x86_64").lower()
    if "win" in platform:
        return ["Calibri", "MT Extra", "Marlett", "Segoe UI Light", "SimHei"]
    if "mac" in platform:
        return ["Helvetica Neue", "Menlo", "Monaco", "Apple Color Emoji", "Arial"]
    return ["DejaVu Sans", "Liberation Sans", "Noto Sans", "Ubuntu", "Noto Color Emoji"]


def _font_width_defaults(profile: dict[str, object]) -> dict[str, float]:
    platform = _str_value(profile.get("platform"), "Linux x86_64").lower()
    if "win" in platform:
        return {
            "default": 119.421875,
            "apple": 119.421875,
            "serif": 119.421875,
            "sans": 115.1875,
            "mono": 105.984375,
            "min": 7.4375,
            "system": 121.328125,
        }
    if "mac" in platform:
        return {
            "default": 122.640625,
            "apple": 122.640625,
            "serif": 121.53125,
            "sans": 118.90625,
            "mono": 109.25,
            "min": 7.0,
            "system": 122.640625,
        }
    return {
        "default": 124.04347527516074,
        "apple": 124.04347527516074,
        "serif": 122.734375,
        "sans": 118.421875,
        "mono": 111.328125,
        "min": 7.4375,
        "system": 124.04347527516074,
    }


def _dfp_null_read_stack(script_url: str) -> str:
    return (
        "TypeError: Cannot read properties of null (reading '0')\n"
        f"    at kt ({script_url}:1:51961)\n"
        f"    at {script_url}:1:82628\n"
        f"    at s ({script_url}:1:22826)\n"
        f"    at {script_url}:1:24783\n"
        "    at new Promise (<anonymous>)\n"
        f"    at {script_url}:1:24747\n"
        f"    at {script_url}:1:25150\n"
        f"    at l ({script_url}:1:22996)"
    )


def _navigator_probe_names() -> list[str]:
    return [
        "getGamepads",
        "javaEnabled",
        "sendBeacon",
        "vibrate",
        "Navigator",
        "adAuctionComponents",
        "runAdAuction",
        "canLoadAdAuctionFencedFrame",
        "clearAppBadge",
        "getBattery",
        "getUserMedia",
        "requestMIDIAccess",
        "requestMediaKeySystemAccess",
        "setAppBadge",
        "webkitGetUserMedia",
        "clearOriginJoinedAdInterestGroups",
        "createAuctionNonce",
        "joinAdInterestGroup",
        "leaveAdInterestGroup",
        "updateAdInterestGroups",
        "deprecatedReplaceInURN",
        "deprecatedURNToURL",
        "getInstalledRelatedApps",
        "getInterestGroupAdAuctionData",
        "registerProtocolHandler",
        "unregisterProtocolHandler",
    ]


def _mtr_date_pair(dfp: dict[str, object], *, now_ms: int, timezone_offset: int) -> list[int]:
    raw = dfp.get("mtr_s45") or dfp.get("date_pair")
    if isinstance(raw, list):
        values = cast(list[object], raw)
        if len(values) >= 2:
            return [_int_value(values[0]), _int_value(values[1])]
    return [now_ms, now_ms - timezone_offset * 60 * 1000]


def _mtr_storage_uuid(state: _MtrState, profile: dict[str, object], dfp: dict[str, object]) -> str:
    for value in (
        dfp.get("mtr_s94_uuid"),
        dfp.get("session_storage_uuid"),
        profile.get("mtr_s94_uuid"),
        profile.get("session_storage_uuid"),
    ):
        text = _str_value(value).lower()
        if _looks_like_uuid(text) and text != "00000000-0000-0000-0000-000000000000":
            return text
    material = {
        "cmid": state.mtr_client_metadata_id,
        "api_key": state.mtr_api_key,
        "device_salt": _str_value(dfp.get("device_salt")),
        "page_start_time_ms": _int_value(getattr(state, "page_start_time_ms", 0)),
    }
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(material, sort_keys=True, separators=(",", ":"))))


def _build_mtr_js_like_signals(
    state: _MtrState,
    *,
    page_url: str,
    x0_token: str,
    s48_values: list[int],
) -> dict[str, dict[str, object]]:
    profile = _profile(state)
    screen = _screen(state)
    viewport = _viewport(state)
    dfp = _dfp(state)
    plugins = _plugin_inventory(dfp)
    languages = _profile_languages(profile)
    language = languages[0] if languages else "pt-BR"
    platform = _mtr_profile_str(profile, "platform", "Linux x86_64")
    user_agent = _str_value(profile.get("user_agent"))
    app_version = user_agent.removeprefix("Mozilla/") if user_agent.startswith("Mozilla/") else user_agent
    width = _int_value(viewport.get("width"), 567)
    height = _int_value(viewport.get("height"), 700)
    screen_width = _int_value(screen.get("width"), 1536)
    screen_height = _int_value(screen.get("height"), 864)
    color_depth = _int_value(screen.get("colorDepth"), 24)
    timezone = _str_value(profile.get("timezone"), "America/Sao_Paulo")
    timezone_offset = _int_value(profile.get("timezone_offset_minutes"), 180)
    heap_limit = _int_value(dfp.get("js_heap_size_limit"), 4_395_630_592)
    device_pixel_ratio = _float_value(profile.get("device_pixel_ratio"), 1.0)
    now_ms = _int_value(dfp.get("mtr_now_ms"), int(time.time() * 1000))
    script_url = state.mtr_dfp_script_url or DEFAULT_DFP_SCRIPT_URL
    canvas_hash = _str_value(dfp.get("cv_sig"), "cf845af5c17f8505dbe10c1afc548dcd")
    canvas_geometry_hash = _str_value(dfp.get("canvas_geometry_hash"), "2179b48bae2d564d33eadf7e35c993d8")
    canvas_text_hash = _str_value(dfp.get("canvas_text_hash"), "eb611ff983beb6aa8d103977e0bf5db7")
    webgl_hash = _str_value(dfp.get("webgl_ext_hash"), "61910b3d5a471a8fdfd2ba33d12bc53b")
    font_hash = _str_value(dfp.get("font_hash"), _stable_hash({"fonts": profile}))
    brands, full_version_list, brand_json, chrome_full_version = _chrome_brand_signals(profile)
    ua_data, high_entropy = _user_agent_data(profile)
    platform_name = (
        _str_value(high_entropy.get("platform") or ua_data.get("platform"))
        if _uses_runtime_profile(profile)
        else ""
    ) or _mtr_profile_str(profile, "sec_ch_platform", '"Linux"').strip('"') or "Linux"
    platform_version = (
        _str_value(high_entropy.get("platformVersion") or ua_data.get("platformVersion"))
        if _uses_runtime_profile(profile)
        else ""
    ) or _mtr_profile_str(profile, "sec_ch_platform_version", '""').strip('"')
    architecture = (
        _str_value(high_entropy.get("architecture") or ua_data.get("architecture"))
        if _uses_runtime_profile(profile)
        else ""
    ) or _mtr_profile_str(profile, "sec_ch_arch", '"x86"').strip('"') or "x86"
    outer_width = _mtr_profile_int(profile, "outer_width", width)
    outer_height = _mtr_profile_int(profile, "outer_height", height)
    inner_width = _mtr_profile_int(profile, "inner_width", width)
    inner_height = _mtr_profile_int(profile, "inner_height", height)
    canvas_signal = _dict_value(dfp.get("canvas_signal"))
    canvas_geometry_hash = _str_value(
        dfp.get("canvas_geometry_hash") or canvas_signal.get("geometry"),
    ) or _dfp_source_hash(dfp.get("canvas_geometry_data_url"), canvas_geometry_hash)
    canvas_text_hash = _str_value(
        dfp.get("canvas_text_hash") or canvas_signal.get("text"),
    ) or _dfp_source_hash(dfp.get("canvas_text_data_url"), canvas_text_hash)
    canvas_winding = _bool_value(dfp.get("canvas_winding"), _bool_value(canvas_signal.get("winding"), True))
    browser_markers = _string_list_or_default(dfp.get("browser_markers"), ["chrome"])
    storage_quota = _int_value(dfp.get("storage_quota"), max(0, (_int_value(profile.get("device_memory"), 8) - 1) * 1_073_741_824))
    math_fingerprint_hash = _str_value(dfp.get("math_fingerprint_hash")) or _dfp_source_hash(dfp.get("math_fingerprint_source"), canvas_hash)
    random_probe_values = _int_list(dfp.get("mtr_s48") or dfp.get("random_probe_values")) or _mtr_random_probe_values(dfp.get("random_probe_randoms")) or s48_values
    performance_now_deltas = _float_list(dfp.get("performance_now_deltas")) or [0.09999999776482582, 0.10000000149011612]
    font_widths = _dict_value(dfp.get("font_widths")) or _font_width_defaults(profile)
    css_system_colors = _dict_value(dfp.get("css_system_colors")) or _css_color_probe_defaults()
    browser_components = _dict_value(dfp.get("browser_components")) or {"wv": False, "wvp": False, "pr": False, "ck": True, "pt": False, "fp": False}
    window_property_markers = _string_list_or_default(dfp.get("window_property_markers"), ["Iterator", "chrome", "WebAssembly"])
    navigator_prototype_markers = _dict_value(dfp.get("navigator_prototype_markers")) or {"l": 80, "p": [{"i": 21, "n": "onLine"}, {"i": 22, "n": "webdriver"}, {"i": 27, "n": "getGamepads"}]}
    storage_uuid_value = _str_value(dfp.get("session_storage_uuid")) or _mtr_storage_uuid(state, profile, dfp)
    performance_time_origin = _float_value(dfp.get("performance_time_origin"), float(now_ms) + 0.8)

    signals: dict[str, dict[str, object]] = {
        key: _mtr_signal(status, _default_signal_value(kind))
        for key, status, kind in MTR_JS_SIGNAL_LAYOUT
    }
    values: dict[str, object] = {
        "s2": [[language]],
        "s3": color_depth,
        "s4": 8,
        "s5": [screen_height, screen_width],
        "s6": [0, 0, 0, 0],
        "s7": _mtr_profile_int(profile, "hardware_concurrency", 12),
        "s9": timezone,
        "s10": True,
        "s11": True,
        "s12": True,
        "s13": False,
        "s15": platform,
        "s16": _plugin_signal_value(plugins),
        "s17": {"winding": canvas_winding, "geometry": canvas_geometry_hash, "text": canvas_text_hash},
        "s19": {"maxTouchPoints": _int_value(profile.get("max_touch_points"), 0), "touchEvent": False, "touchStart": False},
        "s20": _font_probe_names(profile),
        "s21": _float_value(dfp.get("font_measurement"), 124.04347527516074),
        "s22": 23,
        "s24": 33,
        "s27": "Google Inc.",
        "s28": browser_markers,
        "s29": storage_quota,
        "s32": True,
        "s33": False,
        "s37": "srgb",
        "s38": 0,
        "s39": False,
        "s40": False,
        "s42": 0,
        "s43": False,
        "s44": False,
        "s45": _mtr_date_pair(dfp, now_ms=now_ms, timezone_offset=timezone_offset),
        "s46": math_fingerprint_hash,
        "s48": random_probe_values,
        "s49": performance_now_deltas,
        "s50": heap_limit,
        "s51": font_widths,
        "s56": x0_token,
        "s57": device_pixel_ratio,
        "s58": {
            "b": brands,
            "m": False,
            "p": platform_name,
            "h": {
                "brands": brand_json,
                "mobile": "false",
                "platform": platform_name,
                "platformVersion": platform_version,
                "architecture": architecture,
                "bitness": _str_value(profile.get("sec_ch_bitness"), "64"),
                "model": "",
                "uaFullVersion": chrome_full_version,
                "fullVersionList": json.dumps(full_version_list, separators=(",", ":")),
            },
            "nah": [],
        },
        "s59": False,
        "s60": False,
        "s61": True,
        "s62": False,
        "s63": False,
        "s64": False,
        "s65": False,
        "s68": False,
        "s69": [{"l": page_url, "f": ""}],
        "s71": {"w": "https://www.paypal.com", "l": "https://www.paypal.com", "a": []},
        "s72": True,
        "s74": _webgl_signal_value(profile),
        "s75": {
            "contextAttributes": _str_value(dfp.get("webgl_context_attributes_hash"), "6b1ed336830d2bc96442a9d76373252a"),
            "parameters": _str_value(dfp.get("webgl_parameters_hash"), "57a2cddb99538d50a0138430ed0720c5"),
            "parameters2": _str_value(dfp.get("webgl_parameters2_hash"), "3649a5f2a375c04762da32de699eb915"),
            "shaderPrecisions": _str_value(dfp.get("webgl_shader_precisions_hash"), "38a06fe03c499fb674a257f2e361878a"),
            "extensions": _str_value(dfp.get("webgl_extensions_hash"), "a96513a0dc5a765b2c5cc7b5cc6d7c18"),
            "extensionParameters": _str_value(dfp.get("webgl_extension_parameters_hash"), "f40866fd9bd8241231c0fe773d5f67fe"),
            "extensionParameters2": _str_value(dfp.get("webgl_extension_parameters2_hash"), "003f43d00ddcea29c1b1a9be057f4f31"),
            "unsupportedExtensions": [],
        },
        "s76": _str_value(dfp.get("webgl_render_hash")) or _dfp_source_hash(dfp.get("webgl_render_data_url"), webgl_hash),
        "s77": _window_key_probe(dfp.get("window_key_slice")) or {
            "24374072": {"i": True, "t": None, "s": "Top", "e": 12, "p": "undefined"},
            "701226668": {"i": True, "t": None, "s": "eft", "e": 12, "p": "undefined"},
            "1559911021": {"i": True, "t": None, "s": "ion", "e": 12, "p": "undefined"},
            "2571407330": {"i": True, "t": None, "s": "dia", "e": 12, "p": "undefined"},
        },
        "s79": [{"n": "default.ini", "l": -1}],
        "s80": _bool_value(dfp.get("pdf_viewer_enabled"), True),
        "s81": 255,
        "s82": language,
        "s83": [language],
        "s84": {"w": screen_width, "h": screen_height},
        "s87": css_system_colors,
        "s89": "",
        "s91": False,
        "s92": _rect_value(dfp.get("mathml_rect"), 265.515625),
        "s93": _rect_value(dfp.get("emoji_rect"), 1597.078125),
        "s94": {"u": storage_uuid_value, "e": [], "s": []},
        "s98": True,
        "s99": True,
        "s101": user_agent,
        "s102": True,
        "s103": app_version,
        "s104": _int_value(dfp.get("connection_rtt"), _int_value(profile.get("connection_rtt"), 100)),
        "s106": _bool_value(dfp.get("notification_permission_mismatch"), False),
        "s117": len(plugins),
        "s118": True,
        "s119": _dfp_null_read_stack(script_url),
        "s120": False,
        "s123": "20030107",
        "s130": ["function", "function"],
        "s131": ["lang", "dir", "data-ppui-mode"],
        "s132": "function close() { [native code] }",
        "s133": "[object External]",
        "s135": _int_value(dfp.get("mime_type_count"), len(_mime_types(plugins))),
        "s136": True,
        "s139": True,
        "s142": False,
        "s145": _navigator_probe_names(),
        "s146": False,
        "s148": "function bind() { [native code] }",
        "s150": {"outerWidth": outer_width, "outerHeight": outer_height, "innerWidth": inner_width, "innerHeight": inner_height},
        "s152": 2,
        "s153": True,
        "s154": browser_components,
        "s155": {},
        "s156": window_property_markers,
        "s157": _browser_detect_flags(profile, dfp),
        "s158": True,
        "s159": False,
        "s162": False,
        "s163": True,
        "s165": {"isTrusted": False},
        "s166": navigator_prototype_markers,
        "s200": _float_value(dfp.get("mtr_s200") or dfp.get("performance_time_origin"), performance_time_origin),
        "s201": False,
        "s202": language,
    }
    for key, value in values.items():
        status = signals[key]["s"]
        signals[key] = _mtr_signal(_int_value(status), value)
    _apply_signal_overrides(signals, profile, dfp)
    _ = screen_width
    _ = screen_height
    _ = font_hash
    return signals


def _profile_languages(profile: dict[str, object]) -> list[str]:
    language = _str_value(profile.get("language"), "pt-BR")
    language_root = language.split("-", 1)[0]
    languages = _string_list(profile.get("languages"))
    if languages:
        return languages
    return [language, language_root, "en-US", "en"]


def _client_hints(profile: dict[str, object]) -> dict[str, object]:
    chrome_major = _str_value(profile.get("chrome_major"), "150")
    chrome_full_version = _str_value(profile.get("chrome_full_version"), f"{chrome_major}.0.0.0")
    platform = _str_value(profile.get("sec_ch_platform"), '"Linux"').strip('"')
    architecture = _str_value(profile.get("sec_ch_arch"), '"x86"').strip('"')
    return {
        "brands": [
            {"brand": "Chromium", "version": chrome_major},
            {"brand": "Google Chrome", "version": chrome_major},
            {"brand": "Not_A Brand", "version": "24"},
        ],
        "fullVersionList": [
            {"brand": "Chromium", "version": chrome_full_version},
            {"brand": "Google Chrome", "version": chrome_full_version},
            {"brand": "Not_A Brand", "version": "24.0.0.0"},
        ],
        "mobile": False,
        "platform": platform or "Linux",
        "architecture": architecture or "x86",
        "bitness": _str_value(profile.get("sec_ch_bitness"), "64").strip('"'),
        "model": _str_value(profile.get("sec_ch_model")),
        "wow64": False,
    }


def _default_plugins() -> list[dict[str, object]]:
    return [
        {
            "name": "PDF Viewer",
            "filename": "internal-pdf-viewer",
            "description": "Portable Document Format",
            "mimeTypes": ["application/pdf", "text/pdf"],
        },
        {
            "name": "Chrome PDF Viewer",
            "filename": "internal-pdf-viewer",
            "description": "Portable Document Format",
            "mimeTypes": ["application/pdf", "text/pdf"],
        },
        {
            "name": "Chromium PDF Viewer",
            "filename": "internal-pdf-viewer",
            "description": "Portable Document Format",
            "mimeTypes": ["application/pdf", "text/pdf"],
        },
        {
            "name": "Microsoft Edge PDF Viewer",
            "filename": "internal-pdf-viewer",
            "description": "Portable Document Format",
            "mimeTypes": ["application/pdf", "text/pdf"],
        },
        {
            "name": "WebKit built-in PDF",
            "filename": "internal-pdf-viewer",
            "description": "Portable Document Format",
            "mimeTypes": ["application/pdf", "text/pdf"],
        },
    ]


def _plugin_inventory(dfp: dict[str, object]) -> list[dict[str, object]]:
    plugins: list[dict[str, object]] = []
    raw_plugins = dfp.get("plugins")
    if isinstance(raw_plugins, list):
        for item in cast(list[object], raw_plugins):
            plugin = _dict_value(item)
            if not plugin:
                continue
            name = _str_value(plugin.get("name") or plugin.get("n"))
            if not name:
                continue
            raw_mime_types = plugin.get("mimeTypes") or plugin.get("mT")
            mime_types: list[object] = []
            if isinstance(raw_mime_types, list):
                for mime_item in cast(list[object], raw_mime_types):
                    mime_type = _dict_value(mime_item)
                    if mime_type:
                        type_value = _str_value(mime_type.get("type"))
                        if type_value:
                            mime_types.append({"type": type_value, "suffixes": _str_value(mime_type.get("suffixes"))})
                        continue
                    type_value = _str_value(mime_item)
                    if type_value:
                        mime_types.append(type_value)
            plugins.append(
                {
                    "name": name,
                    "filename": _str_value(plugin.get("filename") or plugin.get("fn"), "internal-pdf-viewer"),
                    "description": _str_value(plugin.get("description") or plugin.get("d"), "Portable Document Format"),
                    "mimeTypes": mime_types or ["application/pdf"],
                }
            )
        return plugins
    return _default_plugins()


def _mime_types(plugins: list[dict[str, object]]) -> list[str]:
    values: list[str] = []
    for plugin in plugins:
        raw_mime_types = plugin.get("mimeTypes")
        if not isinstance(raw_mime_types, list):
            continue
        for item in cast(list[object], raw_mime_types):
            mime_type = _dict_value(item)
            value = _str_value(mime_type.get("type")) if mime_type else _str_value(item)
            if value and value not in values:
                values.append(value)
    return values


def _timing_module(dfp: dict[str, object]) -> dict[str, object]:
    timings = _dict_value(dfp.get("timings"))
    return {
        "ttDfp": round(_float_value(timings.get("tt_dfp"), 34.0), 6),
        "ttCanvas": round(_float_value(timings.get("tt_canvas"), 16.0), 6),
        "ttWebglBasic": round(_float_value(timings.get("tt_webgl_basic"), 13.0), 6),
        "ttWebglExt": round(_float_value(timings.get("tt_webgl_ext"), 17.0), 6),
        "ttStorage": round(_float_value(timings.get("tt_storage"), 0.0), 6),
        "ttMath": round(_float_value(timings.get("tt_math"), 0.10000000149011612), 6),
    }


def _memory_module(profile: dict[str, object], dfp: dict[str, object]) -> dict[str, object]:
    memory = _dict_value(dfp.get("js_memory"))
    heap_limit = _int_value(dfp.get("js_heap_size_limit"), 4_395_630_592)
    used_heap = _int_value(memory.get("used"), 24_000_000)
    total_heap = _int_value(memory.get("total"), max(64_000_000, used_heap + 24_000_000))
    return {
        "deviceMemory": _int_value(profile.get("device_memory"), 8),
        "jsHeapSizeLimit": heap_limit,
        "usedJSHeapSize": used_heap,
        "totalJSHeapSize": total_heap,
    }


def build_mtr_id_module(state: _MtrState, *, page_url: str) -> dict[str, object]:
    profile = _profile(state)
    screen = _screen(state)
    viewport = _viewport(state)
    dfp = _dfp(state)
    timezone_offset = _int_value(profile.get("timezone_offset_minutes"), 0)
    inner_width = _int_value(viewport.get("width"))
    inner_height = _int_value(viewport.get("height"))
    webgl_extensions = _string_list(dfp.get("webgl_extensions"))
    font_hash = _str_value(dfp.get("font_hash"), _stable_hash({"fonts": profile.get("platform", "Win32")}))
    audio_value = _str_value(dfp.get("audio_val"), "0.0000840000")
    timings = _timing_module(dfp)
    timing_now = round(
        180.0
        + _float_value(timings.get("ttDfp"))
        + _float_value(timings.get("ttCanvas"))
        + _float_value(timings.get("ttWebglBasic"))
        + _float_value(timings.get("ttWebglExt")),
        3,
    )
    return {
        "s1": _str_value(profile.get("user_agent")),
        "s2": _str_value(profile.get("user_agent")),
        "s3": {
            "width": _int_value(screen.get("width")),
            "height": _int_value(screen.get("height")),
            "availWidth": _int_value(screen.get("availWidth"), _int_value(screen.get("width"))),
            "availHeight": _int_value(screen.get("availHeight"), _int_value(screen.get("height"))),
        },
        "s4": _int_value(screen.get("colorDepth"), 24),
        "s5": {
            "innerWidth": inner_width,
            "innerHeight": inner_height,
            "outerWidth": _int_value(profile.get("outer_width"), inner_width + 16),
            "outerHeight": _int_value(profile.get("outer_height"), inner_height + 88),
            "devicePixelRatio": _float_value(profile.get("device_pixel_ratio"), 1.0),
        },
        "s6": 5,
        "s7": timezone_offset,
        "s8": _str_value(profile.get("language"), "pt-BR"),
        "s9": _profile_languages(profile),
        "s10": "Google Inc.",
        "s11": _int_value(profile.get("hardware_concurrency"), 8),
        "s12": _int_value(profile.get("device_memory"), 8),
        "s13": {
            "cookieEnabled": True,
            "javaEnabled": False,
            "pdfViewerEnabled": True,
            "maxTouchPoints": _int_value(profile.get("max_touch_points"), 0),
        },
        "s14": {
            "localStorage": True,
            "sessionStorage": True,
            "indexedDB": True,
            "openDatabase": False,
        },
        "s15": _str_value(dfp.get("canvas_h"), _stable_hash({"canvas": profile})),
        "s16": {
            "vendor": _str_value(profile.get("gpu_vendor"), "Google Inc. (NVIDIA Corporation)"),
            "renderer": _str_value(profile.get("gpu_renderer"), "ANGLE (NVIDIA, OpenGL ES 3.2)"),
            "webglVendor": _str_value(profile.get("webgl_vendor"), "WebKit"),
            "webglRenderer": _str_value(profile.get("webgl_renderer"), "WebKit WebGL"),
        },
        "s17": _str_value(dfp.get("webgl_ext_hash"), _stable_hash({"webgl": profile})),
        "s18": audio_value,
        "s19": {
            "serif": 148,
            "sans": 144,
            "mono": 133,
            "hash": font_hash,
        },
        "s20": _str_value(dfp.get("cv_sig"), _stable_hash({"canvasSignature": profile})),
        "s21": {
            "extensionsHash": _str_value(dfp.get("webgl_ext_hash"), _stable_hash({"webgl": profile})),
            "extensionCount": len(webgl_extensions),
            "extensions": webgl_extensions[:96],
        },
        "s22": {
            "audioValue": audio_value,
            "sampleRate": 48000,
            "state": "closed",
        },
        "s23": _timing_module(dfp),
        "s24": _memory_module(profile, dfp),
        "s25": {
            "fontHash": font_hash,
            "source": _str_value(dfp.get("source"), _str_value(profile.get("fingerprint_source"), "random")),
        },
        "s41": {
            "invertedColors": False,
            "forcedColors": False,
            "prefersContrast": "no-preference",
            "prefersReducedMotion": "no-preference",
            "prefersReducedTransparency": False,
            "colorGamut": "srgb",
            "monochrome": False,
        },
        "s56": "x0",
        "s61": {
            "timeOrigin": int(time.time() * 1000) - int(timing_now),
            "now": timing_now,
        },
        "url": _hashed_url_for_mtr(page_url),
        "referrer": "",
        "timezone": _str_value(profile.get("timezone"), "UTC"),
        "platform": _str_value(profile.get("platform"), "Win32"),
        "clientHints": _client_hints(profile),
        "connection": {
            "effectiveType": _str_value(profile.get("connection_effective_type"), "4g"),
            "rtt": _str_value(profile.get("connection_rtt"), "150"),
            "downlink": _str_value(profile.get("connection_downlink"), "10"),
        },
    }


def build_mtr_bd_module(state: _MtrState) -> dict[str, object]:
    profile = _profile(state)
    dfp = _dfp(state)
    plugins = _plugin_inventory(dfp)
    mime_types = _mime_types(plugins)
    return {
        "webdriver": _navigator_webdriver(profile, dfp),
        "headless": _headless_chrome(profile, dfp),
        "automationGlobals": ["navigator.webdriver"] if _navigator_webdriver(profile, dfp) else [],
        "windowProcess": False,
        "phantom": False,
        "nightmare": False,
        "cef": False,
        "pluginsConsistent": True,
        "mimeTypesConsistent": True,
        "errorStackFormat": "chromium",
        "adblock": {"detected": False, "selectors": []},
        "brand": "chromium",
        "chromeMajor": _int_value(profile.get("chrome_major"), 150),
        "chromeFullVersion": _str_value(profile.get("chrome_full_version"), "150.0.7871.46"),
        "chromeRuntime": True,
        "permissions": {
            "notifications": "default",
            "geolocation": "prompt",
            "camera": "prompt",
            "microphone": "prompt",
        },
        "plugins": plugins,
        "pluginCount": len(plugins),
        "mimeTypes": mime_types,
        "mimeTypeCount": len(mime_types),
        "pdfViewerEnabled": True,
        "navigatorPrototypeClean": True,
    }


def build_mtr_si_module(state: _MtrState) -> dict[str, object]:
    profile = _profile(state)
    dfp = _dfp(state)
    page_start = _int_value(getattr(state, "page_start_time_ms", 0), int(time.time() * 1000))
    return {
        "s77": {
            "integration": "paypal-checkout",
            "locale": _str_value(profile.get("locale"), "pt_BR"),
            "country": _str_value(profile.get("country"), "BR"),
            "channel": state.mtr_channel,
            "clientMetadataId": state.mtr_client_metadata_id,
            "fingerprintSource": _str_value(profile.get("fingerprint_source"), _str_value(dfp.get("source"), "random")),
            "timezone": _str_value(profile.get("timezone"), "UTC"),
            "pageStartTime": page_start,
            "createdAt": int(time.time() * 1000),
        },
        "s78": {
            "apiKeyHash": _stable_hash(state.mtr_api_key, 16),
            "dfpScriptHash": _stable_hash(state.mtr_dfp_script_url or DEFAULT_DFP_SCRIPT_URL, 16),
            "deviceSaltHash": _stable_hash(_str_value(dfp.get("device_salt")), 16),
        },
    }


def build_mtr_request_object(state: _MtrState, *, page_url: str, x0_token: str) -> dict[str, object]:
    profile = _profile(state)
    dfp = _dfp(state)
    signals = _build_mtr_js_like_signals(
        state,
        page_url=page_url,
        x0_token=x0_token,
        s48_values=[-2147483, -2147483, -2147483, -2147483, -2147483, -2147483],
    )
    payload: dict[str, object] = {
        "c": state.mtr_api_key,
        "m": "s",
        "mo": ["id", "bd", "si"],
        "s56": signals.pop("s56"),
        "s67": signals.pop("s67"),
        "sc": {"u": None},
        "gt": 1,
        "ab": {"noop": _str_value(dfp.get("ab_noop"), "b")},
    }
    payload.update(signals)
    payload["lr"] = []
    payload["url"] = page_url
    payload.update(_payload_overrides(profile, dfp))
    return payload


def custom_json_serialize(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def serialize_mtr_body(value: dict[str, object]) -> bytes:
    payload = custom_json_serialize(value)
    if len(payload) > MTR_COMPRESSION_THRESHOLD:
        return _mtr_envelope(_deflate_raw(payload), compressed=True)
    return _mtr_envelope(payload, compressed=False)


def _response_text(resp: object) -> str:
    text = getattr(resp, "text", "")
    if isinstance(text, str):
        return text
    content = getattr(resp, "content", b"")
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return str(text or "")


def parse_mtr_response(resp: object) -> dict[str, object]:
    text = _response_text(resp).strip()
    if text:
        try:
            data_from_text = cast(object, json.loads(text))
            if isinstance(data_from_text, dict):
                return cast(dict[str, object], data_from_text)
        except Exception:
            pass
    try:
        data = cast(object, getattr(resp, "json")())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, object], data)


def _status_code(resp: object) -> int:
    return _int_value(getattr(resp, "status_code", 0), 0)


def _mtr_headers(page_url: str, *, content_type: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Origin": "https://www.paypal.com",
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def mtr_fetch_bootstrap(session: _MtrSession, state: _MtrState, *, page_url: str) -> str:
    resp = session.get(mtr_get_url(state.mtr_api_key), headers=_mtr_headers(page_url))
    state.mtr_get_status = _status_code(resp)
    token = _response_text(resp).strip()
    if state.mtr_get_status != 200 or not re.fullmatch(r"[A-Za-z0-9+/=_-]{8,1022}", token):
        raise RuntimeError(f"MTR x0 bootstrap failed status={state.mtr_get_status} token_len={len(token)}")
    return token


def apply_mtr_response(state: _MtrState, response_data: dict[str, object]) -> bool:
    request_id = _str_value(response_data.get("requestId"))
    sealed_result = _str_value(response_data.get("sealedResult"))
    products = _dict_value(response_data.get("products"))
    identification = _dict_value(products.get("identification"))
    data = _dict_value(identification.get("data"))
    result = _dict_value(data.get("result"))
    visitor_token = _str_value(
        data.get("visitorToken")
        or data.get("visitor_token")
        or result.get("visitorToken")
        or result.get("visitor_token")
    )
    state.mtr_request_id = request_id
    state.mtr_sealed_result = sealed_result
    state.mtr_visitor_token = visitor_token
    ok = bool(request_id and sealed_result)
    state.mtr_completed = ok
    state.mtr_completed_cmid = state.mtr_client_metadata_id if ok else ""
    return ok


def generate_mtr_body_with_node(
    *,
    _page_url: str,
    _dfp_config: dict[str, object],
    _dfp_script_source: str,
    _mtr_get_response_text: str,
    _browser_profile: dict[str, object],
    _screen: dict[str, object],
    _viewport: dict[str, object],
    _device_fingerprint: dict[str, object] | None = None,
) -> dict[str, object]:
    generated_state = _GeneratedMtrState(
        mtr_channel=_str_value(_dfp_config.get("dfpChannel"), "iwc-mxo"),
        mtr_client_metadata_id=_str_value(_dfp_config.get("clientMetaDataId")),
        mtr_api_key=_str_value(_dfp_config.get("fppAPIKey")),
        mtr_dfp_script_url=_dfp_script_source or DEFAULT_DFP_SCRIPT_URL,
        browser_profile=_browser_profile,
        screen=_screen,
        viewport=_viewport,
        device_fingerprint=_device_fingerprint or {},
    )
    return build_mtr_request_object(
        generated_state,
        page_url=_page_url,
        x0_token=_mtr_get_response_text,
    )


def _export_browser_cookies(session: _MtrSession) -> list[dict[str, object]]:
    export_cookies = getattr(session, "export_cookies_for_browser", None)
    cookies: list[dict[str, object]] = []
    if callable(export_cookies):
        exported_cookies = cast(Callable[[], object], export_cookies)()
        if isinstance(exported_cookies, list):
            for item in cast(list[object], exported_cookies):
                if isinstance(item, dict):
                    cookies.append(cast(dict[str, object], item))
    return cookies


def _import_browser_cookies(session: _MtrSession, result_cookies: object) -> None:
    import_cookies = getattr(session, "import_browser_cookies", None)
    if callable(import_cookies) and result_cookies:
        _ = cast(Callable[[object], object], import_cookies)(result_cookies)


def _apply_mtr_browser_result(
    state: _MtrState,
    result: dict[str, object],
    *,
    runtime_source: str,
    runtime_label: str,
) -> bool:
    returned_dfp_config = result.get("dfp_config")
    if not isinstance(returned_dfp_config, dict):
        extracted_dfp_config = result.get("extracted_dfp_config")
        if isinstance(extracted_dfp_config, dict) and isinstance(extracted_dfp_config.get("config"), dict):
            returned_dfp_config = extracted_dfp_config.get("config")
    if isinstance(returned_dfp_config, dict):
        returned_config = cast(dict[str, object], returned_dfp_config)
        if returned_config.get("dfpChannel"):
            state.mtr_channel = _str_value(returned_config.get("dfpChannel"))
        if returned_config.get("clientMetaDataId"):
            state.mtr_client_metadata_id = _str_value(returned_config.get("clientMetaDataId"))
        if returned_config.get("fppAPIKey"):
            state.mtr_api_key = _str_value(returned_config.get("fppAPIKey"))
        state.mtr_is_qa = bool(returned_config.get("isQA", getattr(state, "mtr_is_qa", False)))

    state.mtr_get_status = _int_value(result.get("x0_status"), 0)
    state.mtr_post_status = _int_value(result.get("post_status"), 0)
    state.mtr_runtime_source = runtime_source
    response_data = result.get("raw_response")
    if not isinstance(response_data, dict):
        response_data = {
            "requestId": result.get("requestId") or "",
            "sealedResult": result.get("sealedResult") or "",
            "products": {
                "identification": {
                    "data": {
                        "visitorToken": result.get("visitorToken") or "",
                    }
                }
            },
        }
    ok = apply_mtr_response(state, cast(dict[str, object], response_data))
    if getattr(state, "mtr_browser_result", None) is not None:
        responses = result.get("responses")
        response_list = cast(list[object], responses) if isinstance(responses, list) else []
        result_cookies = result.get("cookies")
        cookie_count = len(cast(list[object], result_cookies)) if isinstance(result_cookies, list) else 0
        setattr(state, "mtr_browser_result", {
            "ok": ok,
            "runtime": runtime_source,
            "status": result.get("status"),
            "url": result.get("url"),
            "x0_status": result.get("x0_status"),
            "post_status": result.get("post_status"),
            "request_id_present": bool(result.get("requestId")),
            "sealed_result_present": bool(result.get("sealedResult")),
            "cookie_count": cookie_count,
            "injected_dfp": bool(result.get("injected_dfp")),
            "responses": response_list,
                "inject_error": result.get("inject_error") or "",
                "extracted_dfp_config": result.get("extracted_dfp_config") or {},
                "debug_log_path": result.get("debug_log_path") or "",
                "intercept": result.get("intercept") or {},
            })
    if not ok and strict_mtr_required():
        raise RuntimeError(
            f"MTR {runtime_label} browser run did not return sealedResult x0_status={state.mtr_get_status} post_status={state.mtr_post_status}"
        )
    return ok


def _send_mtr_with_roxy_browser(session: _MtrSession, state: _MtrState, *, page_url: str) -> bool:
    # Roxy mode can extract the live dfpconfig from the browser DOM/RSC after
    # navigation, so do not require a Python-side API key before opening Chrome.
    _ = ensure_mtr_config(state, page_url=page_url)

    roxy_module = importlib.import_module("paypal.roxy_fingerprint")
    capture_roxy_runtime_profile = cast(
        _RoxyCaptureProfile,
        getattr(roxy_module, "capture_roxy_runtime_profile"),
    )
    run_mtr_with_roxy_browser = cast(
        _RoxyMtrRunner,
        getattr(roxy_module, "run_mtr_with_roxy_browser"),
    )
    roxy_browser_matches_proxy = cast(
        Callable[[dict[str, object], object], bool],
        getattr(roxy_module, "roxy_browser_matches_proxy"),
    )
    close_roxy_browser = cast(
        Callable[..., None],
        getattr(roxy_module, "close_roxy_browser"),
    )

    proxy_url = getattr(session, "proxy_url", None) or ""
    roxy_browser_value = getattr(state, "roxy_browser", {}) or {}
    roxy_browser = _dict_value(roxy_browser_value)
    if roxy_browser.get("cdp_info") and not roxy_browser_matches_proxy(roxy_browser, proxy_url):
        logger.info("Existing Roxy browser proxy does not match current HTTP proxy; reopening with current proxy.")
        try:
            close_roxy_browser(roxy_browser, delete=True)
        except Exception as exc:
            logger.debug("Roxy mismatched-proxy browser cleanup failed: %s", exc)
        roxy_browser = {}
        setattr(state, "roxy_browser", roxy_browser)
    if not roxy_browser.get("cdp_info"):
        runtime = capture_roxy_runtime_profile(
            keep_browser=True,
            proxy_url=proxy_url,
        )
        roxy_browser = _dict_value(runtime.get("roxy_browser"))
        setattr(state, "roxy_browser", roxy_browser)
        _apply_runtime_profile_to_state(state, runtime)

    dfp_config: dict[str, object] = {
        "dfpChannel": state.mtr_channel or "iwc-mxo",
        "clientMetaDataId": state.mtr_client_metadata_id,
        "fppAPIKey": state.mtr_api_key,
        "isQA": bool(getattr(state, "mtr_is_qa", False)),
    }
    result = run_mtr_with_roxy_browser(
        roxy_browser,
        page_url,
        dfp_config=dfp_config,
        dfp_script_url=state.mtr_dfp_script_url or DEFAULT_DFP_SCRIPT_URL,
        cookies=_export_browser_cookies(session),
        wait_seconds=mtr_roxy_wait_seconds(),
    )
    result_cookies = result.get("cookies")
    _import_browser_cookies(session, result_cookies)
    return _apply_mtr_browser_result(state, result, runtime_source=MTR_RUNTIME_ROXY, runtime_label="roxy")


def _send_mtr_with_local_headless(session: _MtrSession, state: _MtrState, *, page_url: str) -> bool:
    _ = ensure_mtr_config(state, page_url=page_url)

    local_headless_module = importlib.import_module("paypal.local_headless")
    run_mtr_with_local_headless = cast(
        Callable[..., dict[str, object]],
        getattr(local_headless_module, "run_mtr_with_local_headless"),
    )
    proxy_url = getattr(session, "proxy_url", None) or ""
    dfp_config: dict[str, object] = {
        "dfpChannel": state.mtr_channel or "iwc-mxo",
        "clientMetaDataId": state.mtr_client_metadata_id,
        "fppAPIKey": state.mtr_api_key,
        "isQA": bool(getattr(state, "mtr_is_qa", False)),
    }
    result = run_mtr_with_local_headless(
        page_url,
        dfp_config=dfp_config,
        dfp_script_url=state.mtr_dfp_script_url or DEFAULT_DFP_SCRIPT_URL,
        cookies=_export_browser_cookies(session),
        wait_seconds=mtr_headless_wait_seconds(),
        proxy_url=proxy_url,
        browser_profile=state.browser_profile,
        screen=state.screen,
        viewport=state.viewport,
    )
    _import_browser_cookies(session, result.get("cookies"))
    return _apply_mtr_browser_result(state, result, runtime_source=MTR_RUNTIME_HEADLESS, runtime_label="headless")


def send_mtr_signals(
    session: _MtrSession,
    state: _MtrState,
    *,
    page_url: str,
    runtime_mode: str | None = None,
) -> bool:
    mode = mtr_runtime_mode(runtime_mode)
    if mode == MTR_RUNTIME_OFF:
        state.mtr_runtime_source = "disabled"
        if strict_mtr_required():
            raise RuntimeError("MTR is disabled while PAYPAL_REQUIRE_MTR=1")
        return False

    if mode in {MTR_RUNTIME_ROXY, MTR_RUNTIME_AUTO}:
        try:
            _ensure_mtr_runtime_fingerprint_source(session, state, MTR_RUNTIME_ROXY)
            return _send_mtr_with_roxy_browser(session, state, page_url=page_url)
        except Exception as exc:
            if mode == MTR_RUNTIME_ROXY and not roxy_runtime_fallback_enabled():
                raise
            if getattr(state, "mtr_browser_result", None) is not None:
                setattr(state, "mtr_browser_result", {"ok": False, "error": str(exc)})
            logger.warning("MTR roxy browser runtime failed; falling back to python_generated: %s", exc)
            mode = MTR_RUNTIME_PYTHON_GENERATED

    if mode == MTR_RUNTIME_HEADLESS:
        try:
            _ensure_mtr_runtime_fingerprint_source(session, state, MTR_RUNTIME_HEADLESS)
            return _send_mtr_with_local_headless(session, state, page_url=page_url)
        except Exception as exc:
            if not headless_runtime_fallback_enabled():
                raise
            if getattr(state, "mtr_browser_result", None) is not None:
                setattr(state, "mtr_browser_result", {"ok": False, "runtime": MTR_RUNTIME_HEADLESS, "error": str(exc)})
            logger.warning("MTR local headless runtime failed; falling back to python_generated: %s", exc)
            mode = MTR_RUNTIME_PYTHON_GENERATED

    if mode == MTR_RUNTIME_PYTHON_GENERATED:
        _ = ensure_mtr_config(state, page_url=page_url)
        if not (state.mtr_channel and state.mtr_client_metadata_id and state.mtr_api_key):
            state.mtr_runtime_source = "missing_mtr_config"
            if strict_mtr_required():
                raise RuntimeError("MTR dfpconfig is missing channel, cmid, or api key")
            return False
        x0_token = mtr_fetch_bootstrap(session, state, page_url=page_url)
        payload = build_mtr_request_object(state, page_url=page_url, x0_token=x0_token)
        body = serialize_mtr_body(payload)
        profile = _profile(state)
        post_url = mtr_post_url(
            channel=state.mtr_channel,
            cmid=state.mtr_client_metadata_id,
            browser_timezone=_str_value(profile.get("timezone"), "UTC"),
            api_key=state.mtr_api_key,
        )
        resp = session.post(
            post_url,
            content=body,
            headers=_mtr_headers(page_url, content_type="text/plain"),
            timeout=30,
        )
        state.mtr_post_status = _status_code(resp)
        state.mtr_runtime_source = MTR_RUNTIME_PYTHON_GENERATED
        response_data = parse_mtr_response(resp)
        ok = apply_mtr_response(state, response_data)
        if not ok and strict_mtr_required():
            raise RuntimeError(
                f"MTR python_generated POST did not return sealedResult status={state.mtr_post_status}"
            )
        return ok

    state.mtr_runtime_source = "missing_real_browser_runtime"
    message = (
        "MTR sealedResult is missing for "
        f"{page_url}. Per automation_vs_real_browser_risk_diff.md, pure protocol code must not synthesize or submit this browser-runtime device proof."
    )
    if strict_mtr_required():
        raise RuntimeError(message)
    logger.warning(message)
    return False
