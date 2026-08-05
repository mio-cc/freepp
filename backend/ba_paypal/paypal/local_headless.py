from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from loguru import logger

from config import BROWSER_PROFILE, SCREEN, USER_AGENT, VIEWPORT


JsonObject = dict[str, object]


class _Request(Protocol):
    url: str
    method: str


class _Response(Protocol):
    url: str
    status: int
    request: _Request

    def text(self) -> str: ...


class _Page(Protocol):
    url: str

    def goto(self, url: str, **kwargs: object) -> _Response | None: ...

    def wait_for_load_state(self, state: str, **kwargs: object) -> None: ...

    def add_script_tag(self, *, url: str) -> object: ...

    def wait_for_timeout(self, timeout: float) -> None: ...

    def content(self) -> str: ...

    def evaluate(self, expression: str, arg: object = None) -> object: ...

    def on(self, event: str, callback: Callable[[object], None]) -> object: ...

    def route(self, url: str, callback: Callable[[object], None]) -> object: ...


class _BrowserContext(Protocol):
    def add_cookies(self, cookies: list[JsonObject]) -> None: ...

    def set_extra_http_headers(self, headers: dict[str, str]) -> None: ...

    def new_page(self) -> _Page: ...

    def cookies(self, urls: list[str]) -> list[JsonObject]: ...


class _Browser(Protocol):
    def new_context(self, **kwargs: object) -> _BrowserContext: ...

    def close(self) -> None: ...


class _BrowserType(Protocol):
    def launch(self, **kwargs: object) -> _Browser: ...


class _Playwright(Protocol):
    chromium: _BrowserType


class _PlaywrightManager(Protocol):
    def __enter__(self) -> _Playwright: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...


class _SyncPlaywright(Protocol):
    def __call__(self) -> _PlaywrightManager: ...


class LocalHeadlessRuntimeError(RuntimeError):
    pass


def _int_value(value: object, default: int) -> int:
    try:
        if isinstance(value, (str, int, float)):
            return int(value)
    except Exception:
        return default
    return default


def _float_value(value: object, default: float) -> float:
    try:
        if isinstance(value, (str, int, float)):
            return float(value)
    except Exception:
        return default
    return default


def _dict_value(value: object) -> JsonObject:
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def _list_value(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _str_value(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value)
    return text if text else default


def _json_bytes(value: object) -> bytes:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    return str(value).encode("utf-8", "ignore")


def _sha256_hex(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha256_b64(value: object) -> str:
    return base64.b64encode(hashlib.sha256(_json_bytes(value)).digest()).decode("ascii")


_DEBUG_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "datadome",
    "password",
    "securitycode",
    "cvv",
    "pin",
    "clientkey",
    "accesstoken",
    "euat",
    "cardnumber",
    "encryptednumber",
    "cpf",
    "identitydocument",
    "sealedresult",
    "visitortoken",
    "correlationid",
    "clientmetadataid",
    "batoken",
)


def _redact_debug_url(url: str) -> str:
    try:
        parts = urlsplit(url or "")
    except Exception:
        return url
    sensitive_query_keys = {"token", "ba_token", "ssrt", "access_token", "euat", "ctxid", "ctx_id"}
    query = [
        (key, "<redacted>" if key.lower() in sensitive_query_keys else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def _redact_debug_value(value: object, key: str = "") -> object:
    compact = key.lower().replace("_", "").replace("-", "")
    if compact == "url" and isinstance(value, str):
        return _redact_debug_url(value)
    if any(part in compact for part in _DEBUG_SENSITIVE_KEY_PARTS):
        if isinstance(value, (bool, int, float)):
            return value
        return "<redacted>" if value else value
    if isinstance(value, dict):
        return {str(item_key): _redact_debug_value(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_debug_value(item, key) for item in value]
    return value


def _redact_debug_event(event: JsonObject) -> JsonObject:
    return cast(JsonObject, _redact_debug_value(event))


def _parse_chrome_major(user_agent: str, default: int = 150) -> int:
    match = re.search(r"(?:Chrome|Chromium|HeadlessChrome|Edg)/(\d+)", user_agent or "")
    if not match:
        return default
    try:
        return int(match.group(1))
    except ValueError:
        return default


def _ua_high_entropy(ua_data: JsonObject) -> JsonObject:
    return _dict_value(ua_data.get("highEntropy") or ua_data.get("high_entropy"))


def _full_version_from_ua_data(ua_data: JsonObject, user_agent: str, major: int, seed_profile: JsonObject | None = None) -> str:
    high_entropy = _ua_high_entropy(ua_data)
    for source in (high_entropy, ua_data):
        for item in _list_value(source.get("fullVersionList") or source.get("full_version_list")):
            entry = _dict_value(item)
            brand = _str_value(entry.get("brand"))
            version = _str_value(entry.get("version"))
            if version and ("Chrome" in brand or "Chromium" in brand):
                return version
        version = _str_value(source.get("uaFullVersion") or source.get("fullVersion"))
        if version:
            return version
    match = re.search(r"(?:Chrome|Chromium|HeadlessChrome)/([0-9.]+)", user_agent or "")
    if match:
        version = match.group(1)
        seed_version = _str_value((seed_profile or {}).get("chrome_full_version"))
        if version == f"{major}.0.0.0" and seed_version.startswith(f"{major}."):
            return seed_version
        return version
    seed_version = _str_value((seed_profile or {}).get("chrome_full_version"))
    if seed_version.startswith(f"{major}."):
        return seed_version
    return f"{major}.0.0.0"


def _sec_ch_platform(ua_data: JsonObject, platform: str) -> str:
    high_entropy = _ua_high_entropy(ua_data)
    value = _str_value(high_entropy.get("platform") or ua_data.get("platform"))
    if not value:
        lower = platform.lower()
        if "win" in lower:
            value = "Windows"
        elif "mac" in lower:
            value = "macOS"
        elif "linux" in lower:
            value = "Linux"
        else:
            value = str(BROWSER_PROFILE.get("sec_ch_platform") or '"Linux"').strip('"')
    return json.dumps(value)


def _sec_ch_arch(ua_data: JsonObject) -> str:
    high_entropy = _ua_high_entropy(ua_data)
    arch = _str_value(high_entropy.get("architecture") or ua_data.get("architecture")).lower()
    bitness = _str_value(high_entropy.get("bitness") or ua_data.get("bitness"))
    if arch in {"x86", "x86_64", "amd64"} or bitness == "64":
        return '"x86"'
    if arch in {"arm", "arm64", "aarch64"}:
        return '"arm"'
    return str(BROWSER_PROFILE.get("sec_ch_arch") or '"x86"')


def _locale_from_language(language: str) -> str:
    return (language or str(BROWSER_PROFILE.get("language") or "pt-BR")).replace("-", "_")


def _country_from_locale(locale: str) -> str:
    if "_" in locale:
        return locale.rsplit("_", 1)[-1].upper()
    return str(BROWSER_PROFILE.get("country") or "BR")


def _runtime_screen(js: JsonObject) -> JsonObject:
    source = _dict_value(js.get("screen"))
    return {
        "colorDepth": _int_value(source.get("colorDepth"), _int_value(SCREEN.get("colorDepth"), 24)),
        "pixelDepth": _int_value(source.get("pixelDepth"), _int_value(SCREEN.get("pixelDepth"), 24)),
        "height": _int_value(source.get("height"), _int_value(SCREEN.get("height"), 864)),
        "width": _int_value(source.get("width"), _int_value(SCREEN.get("width"), 1536)),
        "availHeight": _int_value(source.get("availHeight"), _int_value(source.get("height"), _int_value(SCREEN.get("availHeight"), 864))),
        "availWidth": _int_value(source.get("availWidth"), _int_value(source.get("width"), _int_value(SCREEN.get("availWidth"), 1536))),
    }


def _runtime_viewport(js: JsonObject) -> JsonObject:
    source = _dict_value(js.get("window"))
    return {
        "width": _int_value(source.get("innerWidth"), _int_value(VIEWPORT.get("width"), 567)),
        "height": _int_value(source.get("innerHeight"), _int_value(VIEWPORT.get("height"), 700)),
    }


def _runtime_browser_profile(js: JsonObject, seed_profile: JsonObject | None = None) -> JsonObject:
    user_agent = _str_value(js.get("userAgent"), USER_AGENT)
    ua_data = _dict_value(js.get("uaData"))
    high_entropy = _ua_high_entropy(ua_data)
    window = _dict_value(js.get("window"))
    connection = _dict_value(js.get("connection"))
    webgl = _dict_value(js.get("webgl"))
    platform = _str_value(js.get("platform"), str(BROWSER_PROFILE.get("platform") or "Linux x86_64"))
    seed = seed_profile or {}
    chrome_major = _parse_chrome_major(user_agent, _int_value(seed.get("chrome_major"), _int_value(BROWSER_PROFILE.get("chrome_major"), 150)))
    language = _str_value(js.get("language"), str(BROWSER_PROFILE.get("language") or "pt-BR"))
    locale = _locale_from_language(language)
    timezone_offset_minutes = _int_value(js.get("timezoneOffsetMinutes"), _int_value(BROWSER_PROFILE.get("timezone_offset_minutes"), 180))
    profile: JsonObject = dict(cast(JsonObject, BROWSER_PROFILE))
    profile.update(seed)
    updates: JsonObject = {
            "fingerprint_source": "headless",
            "country": _country_from_locale(locale),
            "language": language,
            "languages": _list_value(js.get("languages")) or [language, language.split("-", 1)[0], "en-US", "en"],
            "locale": locale,
            "timezone": _str_value(js.get("timezone"), str(BROWSER_PROFILE.get("timezone") or "America/Sao_Paulo")),
            "timezone_offset_minutes": timezone_offset_minutes,
            "timezone_offset_ms": timezone_offset_minutes * 60 * 1000,
            "dst": bool(BROWSER_PROFILE.get("dst", False)),
            "chrome_major": chrome_major,
            "chrome_full_version": _full_version_from_ua_data(ua_data, user_agent, chrome_major, seed),
            "platform": platform,
            "sec_ch_platform": _sec_ch_platform(ua_data, platform),
            "sec_ch_platform_version": json.dumps(_str_value(high_entropy.get("platformVersion") or ua_data.get("platformVersion"))),
            "sec_ch_arch": _sec_ch_arch(ua_data),
            "sec_ch_bitness": _str_value(high_entropy.get("bitness") or ua_data.get("bitness"), str(BROWSER_PROFILE.get("sec_ch_bitness") or "64")),
            "device_memory": _int_value(js.get("deviceMemory"), _int_value(BROWSER_PROFILE.get("device_memory"), 8)),
            "hardware_concurrency": _int_value(js.get("hardwareConcurrency"), _int_value(BROWSER_PROFILE.get("hardware_concurrency"), 8)),
            "device_pixel_ratio": _float_value(window.get("devicePixelRatio"), _float_value(BROWSER_PROFILE.get("device_pixel_ratio"), 1.0)),
            "max_touch_points": _int_value(js.get("maxTouchPoints"), 0),
            "connection_effective_type": _str_value(connection.get("effectiveType"), str(BROWSER_PROFILE.get("connection_effective_type") or "4g")),
            "connection_rtt": _str_value(connection.get("rtt"), str(BROWSER_PROFILE.get("connection_rtt") or "150")),
            "connection_downlink": _str_value(connection.get("downlink"), str(BROWSER_PROFILE.get("connection_downlink") or "10")),
            "gpu_vendor": _str_value(webgl.get("unmaskedVendor") or webgl.get("vendor"), str(BROWSER_PROFILE.get("gpu_vendor") or "")),
            "gpu_renderer": _str_value(webgl.get("unmaskedRenderer") or webgl.get("renderer"), str(BROWSER_PROFILE.get("gpu_renderer") or "")),
            "webgl_vendor": _str_value(webgl.get("vendor"), str(BROWSER_PROFILE.get("webgl_vendor") or "WebKit")),
            "webgl_renderer": _str_value(webgl.get("renderer"), str(BROWSER_PROFILE.get("webgl_renderer") or "WebKit WebGL")),
            "user_agent": user_agent,
            "navigator_webdriver": bool(js.get("webdriver", False)),
            "user_agent_data": ua_data,
            "uaData": ua_data,
            "outer_width": _int_value(window.get("outerWidth"), _int_value(window.get("innerWidth"), _int_value(VIEWPORT.get("width"), 567))),
            "outer_height": _int_value(window.get("outerHeight"), _int_value(window.get("innerHeight"), _int_value(VIEWPORT.get("height"), 700))),
            "inner_width": _int_value(window.get("innerWidth"), _int_value(VIEWPORT.get("width"), 567)),
            "inner_height": _int_value(window.get("innerHeight"), _int_value(VIEWPORT.get("height"), 700)),
        }
    profile.update(updates)
    return profile


def _runtime_device_fingerprint(js: JsonObject) -> JsonObject:
    canvas = _dict_value(js.get("canvas"))
    webgl = _dict_value(js.get("webgl"))
    audio = _dict_value(js.get("audio"))
    raw_memory = js.get("memory")
    memory = _dict_value(raw_memory)
    timing = _dict_value(js.get("timing"))
    connection = _dict_value(js.get("connection"))
    webgl_extensions = [str(item) for item in _list_value(webgl.get("extensions")) if str(item)]
    canvas_material = canvas.get("dataUrl") or canvas.get("textDataUrl") or canvas
    webgl_material = {
        "version": webgl.get("version"),
        "vendor": webgl.get("vendor"),
        "renderer": webgl.get("renderer"),
        "shadingLanguageVersion": webgl.get("shadingLanguageVersion"),
        "unmaskedVendor": webgl.get("unmaskedVendor"),
        "unmaskedRenderer": webgl.get("unmaskedRenderer"),
        "extensions": webgl_extensions,
        "params": webgl.get("params"),
        "contextAttributes": webgl.get("contextAttributes"),
        "shaderPrecisions": webgl.get("shaderPrecisions"),
    }
    plugins = _list_value(js.get("plugins"))
    font_widths = _dict_value(js.get("fontWidths"))
    captured_at = _int_value(js.get("capturedAt"), int(time.time() * 1000))
    default_font = _float_value(font_widths.get("default"), 124.04347527516074)
    result: JsonObject = {
        "source": "headless",
        "captured_at": captured_at,
        "device_salt": "headless:" + _sha256_hex({"canvas": canvas.get("previewHash"), "webgl": webgl_material, "audio": audio.get("value")})[:32],
        "canvas_h": _sha256_b64(canvas_material),
        "canvas_data_url_length": _int_value(canvas.get("dataUrlLength"), 0),
        "cv_sig": _sha256_hex(canvas_material),
        "canvas_geometry_data_url": canvas.get("geometryDataUrl") or "",
        "canvas_text_data_url": canvas.get("textDataUrl") or "",
        "canvas_winding": bool(canvas.get("winding", True)),
        "webgl_ext_hash": _sha256_hex(webgl_material),
        "webgl_extensions": webgl_extensions,
        "webgl_render_data_url": webgl.get("renderDataUrl") or "",
        "webgl_context_attributes": webgl.get("contextAttributes") or {},
        "webgl_parameters": webgl.get("params") or {},
        "webgl_shader_precisions": webgl.get("shaderPrecisions") or {},
        "audio_val": _str_value(audio.get("value")),
        "audio_sample_rate": _int_value(audio.get("sampleRate"), 0),
        "js_heap_size_limit": _int_value(memory.get("jsHeapSizeLimit"), 4_395_630_592),
        "font_hash": _sha256_hex({"fontWidths": font_widths, "mathml": js.get("mathmlRect"), "emoji": js.get("emojiRect"), "plugins": plugins})[:32],
        "font_widths": font_widths,
        "font_measurement": default_font,
        "math_fingerprint_source": js.get("mathFingerprintSource") or "",
        "mathml_rect": js.get("mathmlRect") or {},
        "emoji_rect": js.get("emojiRect") or {},
        "css_system_colors": js.get("cssSystemColors") or {},
        "plugins": plugins,
        "mime_type_count": _int_value(js.get("mimeTypeCount"), 0),
        "pdf_viewer_enabled": bool(js.get("pdfViewerEnabled", True)),
        "browser_markers": _list_value(js.get("browserMarkers")),
        "browser_components": js.get("browserComponents") or {"wv": False, "wvp": False, "pr": False, "ck": False, "pt": False, "fp": False},
        "window_property_markers": _list_value(js.get("windowPropertyMarkers")),
        "navigator_prototype_markers": js.get("navigatorPrototypeMarkers") or {},
        "storage_quota": _int_value(js.get("storageQuota"), 0),
        "performance_time_origin": _float_value(js.get("performanceTimeOrigin"), float(captured_at)),
        "performance_now_deltas": _list_value(js.get("performanceNowDeltas")),
        "connection_rtt": _int_value(connection.get("rtt"), 0),
        "notification_permission_mismatch": bool(js.get("notificationPermissionMismatch", False)),
        "session_storage_uuid": _str_value(js.get("sessionStorageUuid")),
        "mtr_now_ms": captured_at,
        "ab_noop": "a",
        "navigator_webdriver": bool(js.get("webdriver", False)),
        "timings": {
            "tt_dfp": _float_value(timing.get("ttCanvas"), 0.0) + _float_value(timing.get("ttWebglBasic"), 0.0) + _float_value(timing.get("ttAudio"), 0.0),
            "tt_canvas": _float_value(timing.get("ttCanvas"), _float_value(canvas.get("ttCanvas"), 0.0)),
            "tt_webgl_basic": _float_value(timing.get("ttWebglBasic"), 0.0),
            "tt_webgl_ext": _float_value(timing.get("ttWebglExt"), 0.0),
            "tt_storage": 0.0,
            "tt_math": 0.10000000149011612,
        },
        "raw_runtime_hash": _sha256_hex(js),
    }
    if memory:
        result["js_memory"] = {
            "used": _int_value(memory.get("usedJSHeapSize"), 0),
            "total": _int_value(memory.get("totalJSHeapSize"), 0),
        }
    return result


def _runtime_profile_from_js(js: JsonObject, seed_profile: JsonObject | None = None) -> JsonObject:
    return {
        "browser_profile": _runtime_browser_profile(js, seed_profile),
        "screen": _runtime_screen(js),
        "viewport": _runtime_viewport(js),
        "device_fingerprint": _runtime_device_fingerprint(js),
    }


def _merged_context_dict(defaults: object, overrides: JsonObject | None) -> JsonObject:
    merged = dict(cast(JsonObject, defaults)) if isinstance(defaults, dict) else {}
    if overrides:
        merged.update(overrides)
    return merged


def _context_options(
    *,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> JsonObject:
    profile = _merged_context_dict(BROWSER_PROFILE, browser_profile)
    viewport_options = _merged_context_dict(VIEWPORT, viewport)
    screen_options = _merged_context_dict(SCREEN, screen)
    language = str(profile.get("language") or "pt-BR")
    return {
        "user_agent": str(profile.get("user_agent") or USER_AGENT),
        "viewport": {
            "width": _int_value(viewport_options.get("width"), 567),
            "height": _int_value(viewport_options.get("height"), 700),
        },
        "screen": {
            "width": _int_value(screen_options.get("width"), 1536),
            "height": _int_value(screen_options.get("height"), 864),
        },
        "locale": language,
        "timezone_id": str(profile.get("timezone") or "America/Sao_Paulo"),
        "device_scale_factor": _float_value(profile.get("device_pixel_ratio"), 1.0),
        "is_mobile": False,
        "has_touch": False,
        "java_script_enabled": True,
    }


def _chrome_version_parts(profile: JsonObject) -> tuple[int, str]:
    user_agent = str(profile.get("user_agent") or USER_AGENT)
    major = _int_value(profile.get("chrome_major"), _parse_chrome_major(user_agent, 150))
    full_version = _str_value(profile.get("chrome_full_version"))
    if not full_version:
        match = re.search(r"(?:Chrome|Chromium|HeadlessChrome)/([0-9.]+)", user_agent)
        full_version = match.group(1) if match else f"{major}.0.0.0"
    if not full_version.startswith(f"{major}."):
        full_version = f"{major}.0.0.0"
    return major, full_version


def _chrome_user_agent_metadata(profile: JsonObject) -> JsonObject:
    major, full_version = _chrome_version_parts(profile)
    platform_text = str(profile.get("sec_ch_platform") or '"Linux"').strip('"') or "Linux"
    architecture = str(profile.get("sec_ch_arch") or '"x86"').strip('"') or "x86"
    return {
        "brands": [
            {"brand": "Not;A=Brand", "version": "8"},
            {"brand": "Chromium", "version": str(major)},
            {"brand": "Google Chrome", "version": str(major)},
        ],
        "fullVersionList": [
            {"brand": "Not;A=Brand", "version": "8.0.0.0"},
            {"brand": "Chromium", "version": full_version},
            {"brand": "Google Chrome", "version": full_version},
        ],
        "fullVersion": full_version,
        "platform": platform_text,
        "platformVersion": str(profile.get("sec_ch_platform_version") or "").strip('"'),
        "architecture": architecture,
        "model": "",
        "mobile": False,
        "bitness": "64" if architecture in {"x86", "arm"} else "",
        "wow64": False,
    }


def _ua_data_script_config(profile: JsonObject) -> JsonObject:
    metadata = _chrome_user_agent_metadata(profile)
    return {
        "brands": metadata.get("brands") or [],
        "fullVersionList": metadata.get("fullVersionList") or [],
        "fullVersion": metadata.get("fullVersion") or "",
        "platform": metadata.get("platform") or str(profile.get("sec_ch_platform") or '"Linux"').strip('"') or "Linux",
        "platformVersion": metadata.get("platformVersion") or "",
        "architecture": metadata.get("architecture") or "x86",
        "model": metadata.get("model") or "",
        "mobile": bool(metadata.get("mobile", False)),
        "bitness": metadata.get("bitness") or "64",
        "wow64": bool(metadata.get("wow64", False)),
    }


def _js_native_function_source(name: str) -> str:
    return f"function {name}() {{ [native code] }}"


def _headless_cookie_cache_enabled() -> bool:
    raw = _env_text("PAYPAL_HEADLESS_COOKIE_CACHE", "PAYPAL_LOCAL_HEADLESS_COOKIE_CACHE").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled", "disable"}


def _headless_cookie_cache_path() -> Path:
    raw = _env_text("PAYPAL_HEADLESS_COOKIE_CACHE_PATH", "PAYPAL_LOCAL_HEADLESS_COOKIE_CACHE_PATH")
    return Path(raw).expanduser().resolve() if raw else (_headless_optimized_project_root() / "var" / "headless_cookie_cache.json")


def _headless_cookie_cache_key(proxy_url: str, profile: JsonObject) -> str:
    proxy_host = ""
    proxy_scheme = ""
    try:
        parsed = urlsplit(proxy_url or "")
        proxy_scheme = parsed.scheme or ""
        proxy_host = parsed.hostname or ""
        if parsed.port is not None:
            proxy_host = f"{proxy_host}:{parsed.port}"
    except Exception:
        proxy_host = proxy_url or ""
    material = {
        "proxy": f"{proxy_scheme}://{proxy_host}" if proxy_host else "",
        "ua": str(profile.get("user_agent") or USER_AGENT),
        "platform": str(profile.get("sec_ch_platform") or profile.get("platform") or ""),
        "timezone": str(profile.get("timezone") or ""),
        "locale": str(profile.get("locale") or profile.get("language") or ""),
    }
    return hashlib.sha256(_json_bytes(material)).hexdigest()[:32]


def _cookie_identity(cookie: JsonObject) -> tuple[str, str, str]:
    return (
        str(cookie.get("name") or ""),
        str(cookie.get("domain") or cookie.get("url") or ""),
        str(cookie.get("path") or "/"),
    )


def _merge_cookie_lists(*groups: list[JsonObject]) -> list[JsonObject]:
    merged: dict[tuple[str, str, str], JsonObject] = {}
    order: list[tuple[str, str, str]] = []
    for cookies in groups:
        for cookie in cookies or []:
            item = _normalize_cookie(cookie)
            if not item:
                continue
            key = _cookie_identity(item)
            if key not in merged:
                order.append(key)
            merged[key] = item
    return [merged[key] for key in order if key in merged]


def _paypal_cookie_cache_filter(cookies: list[JsonObject]) -> list[JsonObject]:
    now = time.time()
    filtered: list[JsonObject] = []
    for cookie in cookies or []:
        item = _normalize_cookie(cookie)
        if not item:
            continue
        domain = str(item.get("domain") or item.get("url") or "").lower()
        name = str(item.get("name") or "").lower()
        if not any(host in domain for host in ("paypal.com", "paypalobjects.com", "ddbm2.paypal.com")):
            continue
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and expires > 0 and float(expires) < now + 30:
            continue
        if name in {"datadome", "ddall", "ddgl", "ts", "ts_c", "x-pp-s", "nsid", "tsrce", "d_id", "lang", "_dd_s"} or "paypal" in domain:
            filtered.append(item)
    return filtered


def _load_headless_cached_cookies(proxy_url: str, profile: JsonObject) -> list[JsonObject]:
    if not _headless_cookie_cache_enabled():
        return []
    path = _headless_cookie_cache_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        logger.debug("Local headless cookie cache is unreadable; ignoring: {}", exc)
        return []
    key = _headless_cookie_cache_key(proxy_url, profile)
    entries = _dict_value(_dict_value(data).get("entries"))
    entry = _dict_value(entries.get(key))
    cookies = _paypal_cookie_cache_filter(cast(list[JsonObject], _list_value(entry.get("cookies"))))
    return cookies


def _save_headless_cached_cookies(proxy_url: str, profile: JsonObject, cookies: list[JsonObject]) -> None:
    if not _headless_cookie_cache_enabled():
        return
    filtered = _paypal_cookie_cache_filter(cookies)
    if not filtered:
        return
    path = _headless_cookie_cache_path()
    lock_handle = _lock_cache_file(path.with_suffix(path.suffix + ".lock"))
    now = time.time()
    key = _headless_cookie_cache_key(proxy_url, profile)
    try:
        payload: JsonObject = {"version": 1, "updated_at": now, "entries": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(loaded, dict):
                    payload.update(cast(JsonObject, loaded))
            except Exception:
                pass
        entries = _dict_value(payload.get("entries"))
        entries[key] = {
            "updated_at": now,
            "profile": {
                "user_agent": str(profile.get("user_agent") or USER_AGENT),
                "platform": str(profile.get("sec_ch_platform") or profile.get("platform") or ""),
                "timezone": str(profile.get("timezone") or ""),
                "locale": str(profile.get("locale") or profile.get("language") or ""),
            },
            "cookies": filtered,
        }
        payload["version"] = 1
        payload["updated_at"] = now
        payload["entries"] = entries
        _prepare_private_dir(path.parent)
        fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    finally:
        try:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
        except Exception:
            pass


def _headless_language_list(language: str) -> list[str]:
    values: list[str] = []
    for item in (language, language.split("-", 1)[0] if "-" in language else "", "en-US", "en"):
        if item and item not in values:
            values.append(item)
    return values


def _sec_ch_ua_header_from_metadata(metadata: JsonObject, *, full: bool = False) -> str:
    key = "fullVersionList" if full else "brands"
    items = _list_value(metadata.get(key))
    parts: list[str] = []
    for item in items:
        entry = _dict_value(item)
        brand = _str_value(entry.get("brand"))
        version = _str_value(entry.get("version"))
        if brand and version:
            parts.append(f'"{brand}";v="{version}"')
    return ", ".join(parts)


def _headless_extra_http_headers(profile: JsonObject) -> dict[str, str]:
    metadata = _chrome_user_agent_metadata(profile)
    language = str(profile.get("language") or "pt-BR")
    bitness = str(metadata.get("bitness") or profile.get("sec_ch_bitness") or "64")
    platform_version = str(metadata.get("platformVersion") or profile.get("sec_ch_platform_version") or "").strip('"')
    return {
        "Accept-Language": f"{language},pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-CH-UA": _sec_ch_ua_header_from_metadata(metadata),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": str(profile.get("sec_ch_platform") or '"Linux"'),
        "Sec-CH-UA-Arch": str(profile.get("sec_ch_arch") or '"x86"'),
        "Sec-CH-UA-Bitness": json.dumps(bitness),
        "Sec-CH-UA-Full-Version-List": _sec_ch_ua_header_from_metadata(metadata, full=True),
        "Sec-CH-UA-Model": '""',
        "Sec-CH-UA-Platform-Version": json.dumps(platform_version),
        "Sec-CH-UA-WoW64": "?0",
        "Sec-CH-Device-Memory": str(profile.get("device_memory") or "8"),
    }


def _stealth_init_script(
    *,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> str:
    profile = _merged_context_dict(BROWSER_PROFILE, browser_profile)
    viewport_options = _merged_context_dict(VIEWPORT, viewport)
    screen_options = _merged_context_dict(SCREEN, screen)
    language = str(profile.get("language") or "pt-BR")
    user_agent = str(profile.get("user_agent") or USER_AGENT)
    ua_data = _ua_data_script_config(profile)
    configured_webgl_vendor = _env_text("PAYPAL_HEADLESS_WEBGL_VENDOR")
    configured_webgl_renderer = _env_text("PAYPAL_HEADLESS_WEBGL_RENDERER")
    profile_gpu_vendor = str(profile.get("gpu_vendor") or profile.get("webgl_vendor") or "")
    profile_gpu_renderer = str(profile.get("gpu_renderer") or profile.get("webgl_renderer") or "")
    spoof_webgl = _env_bool("PAYPAL_HEADLESS_SPOOF_WEBGL", False) or _env_bool("PAYPAL_LOCAL_HEADLESS_SPOOF_WEBGL", False)
    if not configured_webgl_vendor and not configured_webgl_renderer and not spoof_webgl:
        profile_gpu_vendor = ""
        profile_gpu_renderer = ""
    config = {
        "userAgent": user_agent,
        "appVersion": user_agent.split("Mozilla/", 1)[-1] if user_agent.startswith("Mozilla/") else user_agent,
        "languages": _headless_language_list(language),
        "language": language,
        "platform": str(profile.get("platform") or "Linux x86_64"),
        "hardwareConcurrency": _int_value(profile.get("hardware_concurrency"), 8),
        "deviceMemory": _int_value(profile.get("device_memory"), 8),
        "screenWidth": _int_value(screen_options.get("width"), 1536),
        "screenHeight": _int_value(screen_options.get("height"), 864),
        "screenAvailWidth": _int_value(screen_options.get("availWidth"), _int_value(screen_options.get("width"), 1536)),
        "screenAvailHeight": _int_value(screen_options.get("availHeight"), _int_value(screen_options.get("height"), 864)),
        "viewportWidth": _int_value(viewport_options.get("width"), 1365),
        "viewportHeight": _int_value(viewport_options.get("height"), 768),
        "timezone": str(profile.get("timezone") or "America/Sao_Paulo"),
        "webglVendor": configured_webgl_vendor or profile_gpu_vendor,
        "webglRenderer": configured_webgl_renderer or profile_gpu_renderer,
        "uaData": ua_data,
    }
    return f"""
(() => {{
  const cfg = {json.dumps(config, ensure_ascii=False)};
  const nativeSource = (name) => `function ${{name}}() {{ [native code] }}`;
  const defineGetter = (obj, prop, getter) => {{
    try {{ Object.defineProperty(obj, prop, {{ get: getter, configurable: true }}); }} catch (_e) {{}}
  }};
  const defineValue = (obj, prop, value) => {{
    try {{ Object.defineProperty(obj, prop, {{ value, configurable: true, writable: true }}); }} catch (_e) {{}}
  }};
  const patchToString = (fn, name) => {{
    try {{ Object.defineProperty(fn, "toString", {{ value: () => nativeSource(name), configurable: true }}); }} catch (_e) {{}}
    return fn;
  }};
  const makeUAData = () => {{
    const data = cfg.uaData || {{}};
    const values = {{
      brands: (data.brands || []).map((item) => Object.assign({{}}, item)),
      mobile: !!data.mobile,
      platform: data.platform || "",
      architecture: data.architecture || "",
      bitness: data.bitness || "",
      model: data.model || "",
      platformVersion: data.platformVersion || "",
      uaFullVersion: data.fullVersion || "",
      fullVersionList: (data.fullVersionList || []).map((item) => Object.assign({{}}, item)),
      wow64: !!data.wow64
    }};
    const result = {{
      brands: values.brands,
      mobile: values.mobile,
      platform: values.platform,
      getHighEntropyValues: patchToString(async (hints) => {{
        const out = {{}};
        for (const hint of (Array.isArray(hints) ? hints : [])) {{
          if (hint in values) out[hint] = Array.isArray(values[hint]) ? values[hint].map((item) => Object.assign({{}}, item)) : values[hint];
        }}
        return out;
      }}, "getHighEntropyValues"),
      toJSON: patchToString(() => ({{ brands: values.brands, mobile: values.mobile, platform: values.platform }}), "toJSON")
    }};
    try {{ Object.defineProperty(result, Symbol.toStringTag, {{ value: "NavigatorUAData", configurable: true }}); }} catch (_e) {{}}
    return result;
  }};
  const patchNavigatorLike = (proto) => {{
    if (!proto) return;
    defineGetter(proto, "webdriver", () => undefined);
    defineGetter(proto, "userAgent", () => cfg.userAgent);
    defineGetter(proto, "appVersion", () => cfg.appVersion);
    defineGetter(proto, "language", () => cfg.language);
    defineGetter(proto, "languages", () => cfg.languages.slice());
    defineGetter(proto, "platform", () => cfg.platform);
    defineGetter(proto, "hardwareConcurrency", () => cfg.hardwareConcurrency);
    defineGetter(proto, "deviceMemory", () => cfg.deviceMemory);
    defineGetter(proto, "userAgentData", () => makeUAData());
  }};
  const patchWebGL = (root) => {{
    try {{
      const patch = (proto) => {{
        if (!proto || !proto.getParameter || proto.__paypalHeadlessPatched) return;
        const original = proto.getParameter;
        defineValue(proto, "getParameter", patchToString(function(parameter) {{
          if (parameter === 37445 && cfg.webglVendor) return cfg.webglVendor;
          if (parameter === 37446 && cfg.webglRenderer) return cfg.webglRenderer;
          return original.apply(this, arguments);
        }}, "getParameter"));
        try {{ Object.defineProperty(proto, "__paypalHeadlessPatched", {{ value: true }}); }} catch (_e) {{}}
      }};
      patch(root.WebGLRenderingContext && root.WebGLRenderingContext.prototype);
      patch(root.WebGL2RenderingContext && root.WebGL2RenderingContext.prototype);
    }} catch (_e) {{}}
  }};
  const patchIntl = (root) => {{
    try {{
      const proto = root.Intl && root.Intl.DateTimeFormat && root.Intl.DateTimeFormat.prototype;
      if (!proto || proto.__paypalTimezonePatched) return;
      const original = proto.resolvedOptions;
      defineValue(proto, "resolvedOptions", patchToString(function() {{
        const value = original.apply(this, arguments) || {{}};
        try {{ value.timeZone = cfg.timezone; }} catch (_e) {{}}
        return value;
      }}, "resolvedOptions"));
      try {{ Object.defineProperty(proto, "__paypalTimezonePatched", {{ value: true }}); }} catch (_e) {{}}
    }} catch (_e) {{}}
  }};
  try {{
    delete window.__playwright__binding__;
    delete window.__pwInitScripts;
    patchNavigatorLike(Navigator.prototype);
  }} catch (_e) {{}}
  try {{
    defineGetter(Screen.prototype, "width", () => cfg.screenWidth);
    defineGetter(Screen.prototype, "height", () => cfg.screenHeight);
    defineGetter(Screen.prototype, "availWidth", () => cfg.screenAvailWidth);
    defineGetter(Screen.prototype, "availHeight", () => cfg.screenAvailHeight);
  }} catch (_e) {{}}
  try {{
    const outerWidth = Math.min(cfg.screenWidth, Math.max(cfg.viewportWidth, window.innerWidth || cfg.viewportWidth) + 16);
    const outerHeight = Math.min(cfg.screenHeight, Math.max(cfg.viewportHeight, window.innerHeight || cfg.viewportHeight) + 88);
    defineGetter(window, "outerWidth", () => outerWidth);
    defineGetter(window, "outerHeight", () => outerHeight);
  }} catch (_e) {{}}
  try {{
    const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (originalQuery) {{
      window.navigator.permissions.query = patchToString(function(parameters) {{
        if (parameters && parameters.name === "notifications") {{
          const state = (window.Notification && window.Notification.permission) || "default";
          return Promise.resolve({{ state, onchange: null }});
        }}
        return originalQuery.apply(this, arguments);
      }}, "query");
    }}
  }} catch (_e) {{}}
  patchWebGL(window);
  patchIntl(window);
  try {{
    const workerPrelude = `(() => {{
      const cfg = ${{JSON.stringify(cfg)}};
      const nativeSource = (name) => \\`function ${{name}}() {{ [native code] }}\\`;
      const defineGetter = (obj, prop, getter) => {{ try {{ Object.defineProperty(obj, prop, {{ get: getter, configurable: true }}); }} catch (_e) {{}} }};
      const defineValue = (obj, prop, value) => {{ try {{ Object.defineProperty(obj, prop, {{ value, configurable: true, writable: true }}); }} catch (_e) {{}} }};
      const patchToString = (fn, name) => {{ try {{ Object.defineProperty(fn, "toString", {{ value: () => nativeSource(name), configurable: true }}); }} catch (_e) {{}} return fn; }};
      const makeUAData = () => {{
        const data = cfg.uaData || {{}};
        const values = {{
          brands: (data.brands || []).map((item) => Object.assign({{}}, item)), mobile: !!data.mobile, platform: data.platform || "",
          architecture: data.architecture || "", bitness: data.bitness || "", model: data.model || "",
          platformVersion: data.platformVersion || "", uaFullVersion: data.fullVersion || "",
          fullVersionList: (data.fullVersionList || []).map((item) => Object.assign({{}}, item)), wow64: !!data.wow64
        }};
        return {{ brands: values.brands, mobile: values.mobile, platform: values.platform, getHighEntropyValues: patchToString(async (hints) => {{ const out = {{}}; for (const hint of (Array.isArray(hints) ? hints : [])) {{ if (hint in values) out[hint] = Array.isArray(values[hint]) ? values[hint].map((item) => Object.assign({{}}, item)) : values[hint]; }} return out; }}, "getHighEntropyValues"), toJSON: patchToString(() => ({{ brands: values.brands, mobile: values.mobile, platform: values.platform }}), "toJSON") }};
      }};
      try {{
        const proto = self.WorkerNavigator && self.WorkerNavigator.prototype;
        if (proto) {{
          defineGetter(proto, "userAgent", () => cfg.userAgent);
          defineGetter(proto, "appVersion", () => cfg.appVersion);
          defineGetter(proto, "language", () => cfg.language);
          defineGetter(proto, "languages", () => cfg.languages.slice());
          defineGetter(proto, "platform", () => cfg.platform);
          defineGetter(proto, "hardwareConcurrency", () => cfg.hardwareConcurrency);
          defineGetter(proto, "deviceMemory", () => cfg.deviceMemory);
          defineGetter(proto, "userAgentData", () => makeUAData());
          defineGetter(proto, "webdriver", () => undefined);
        }}
      }} catch (_e) {{}}
      try {{
        const patch = (proto) => {{
          if (!proto || !proto.getParameter || proto.__paypalHeadlessPatched) return;
          const original = proto.getParameter;
          defineValue(proto, "getParameter", patchToString(function(parameter) {{ if (parameter === 37445 && cfg.webglVendor) return cfg.webglVendor; if (parameter === 37446 && cfg.webglRenderer) return cfg.webglRenderer; return original.apply(this, arguments); }}, "getParameter"));
          try {{ Object.defineProperty(proto, "__paypalHeadlessPatched", {{ value: true }}); }} catch (_e) {{}}
        }};
        patch(self.WebGLRenderingContext && self.WebGLRenderingContext.prototype);
        patch(self.WebGL2RenderingContext && self.WebGL2RenderingContext.prototype);
      }} catch (_e) {{}}
      try {{
        const proto = self.Intl && self.Intl.DateTimeFormat && self.Intl.DateTimeFormat.prototype;
        if (proto && !proto.__paypalTimezonePatched) {{
          const original = proto.resolvedOptions;
          defineValue(proto, "resolvedOptions", patchToString(function() {{ const value = original.apply(this, arguments) || {{}}; try {{ value.timeZone = cfg.timezone; }} catch (_e) {{}} return value; }}, "resolvedOptions"));
          try {{ Object.defineProperty(proto, "__paypalTimezonePatched", {{ value: true }}); }} catch (_e) {{}}
        }}
      }} catch (_e) {{}}
    }})();\n`;
    const NativeBlob = window.Blob;
    if (NativeBlob && !NativeBlob.__paypalHeadlessPatched) {{
      const PatchedBlob = function(parts, options) {{
        const opts = options || {{}};
        const type = String(opts.type || "").toLowerCase();
        if (type.includes("javascript") || type.includes("ecmascript")) {{
          try {{ return new NativeBlob([workerPrelude].concat(Array.from(parts || [])), opts); }} catch (_e) {{}}
        }}
        return new NativeBlob(parts, opts);
      }};
      try {{ Object.setPrototypeOf(PatchedBlob, NativeBlob); }} catch (_e) {{}}
      PatchedBlob.prototype = NativeBlob.prototype;
      try {{ Object.defineProperty(PatchedBlob, "name", {{ value: "Blob" }}); }} catch (_e) {{}}
      patchToString(PatchedBlob, "Blob");
      try {{ Object.defineProperty(PatchedBlob, "__paypalHeadlessPatched", {{ value: true }}); }} catch (_e) {{}}
      defineValue(window, "Blob", PatchedBlob);
    }}
  }} catch (_e) {{}}
}})();
"""

def _apply_cdp_stealth_overrides(
    context: Any,
    page: Any,
    *,
    browser_profile: JsonObject | None = None,
) -> None:
    profile = _merged_context_dict(BROWSER_PROFILE, browser_profile)
    language = str(profile.get("language") or "pt-BR")
    try:
        cdp = context.new_cdp_session(page)
    except Exception as exc:
        logger.debug("Local headless CDP stealth session unavailable: {}", exc)
        return
    try:
        cdp.send(
            "Network.setUserAgentOverride",
            {
                "userAgent": str(profile.get("user_agent") or USER_AGENT),
                "acceptLanguage": f"{language},pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "platform": str(profile.get("platform") or "Linux x86_64"),
                "userAgentMetadata": _chrome_user_agent_metadata(profile),
            },
        )
    except Exception as exc:
        logger.debug("Local headless UA metadata override failed: {}", exc)
    try:
        cdp.send("Emulation.setTimezoneOverride", {"timezoneId": str(profile.get("timezone") or "America/Sao_Paulo")})
    except Exception:
        pass
    try:
        cdp.send("Emulation.setLocaleOverride", {"locale": language})
    except Exception:
        pass


def _load_dotenv_value(name: str) -> str:
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


def _env_text(*names: str) -> str:
    for name in names:
        value = _load_dotenv_value(name)
        if value:
            return value.strip()
    return ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _load_dotenv_value(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def _local_headless_browser_channel() -> str:
    raw = _env_text(
        "PAYPAL_LOCAL_HEADLESS_BROWSER_CHANNEL",
        "PAYPAL_HEADLESS_BROWSER_CHANNEL",
        "PAYPAL_PLAYWRIGHT_CHANNEL",
    )
    channel = (raw or "chrome").strip().lower()
    if channel in {"", "0", "false", "no", "none", "off", "bundled", "chromium"}:
        return ""
    return channel


def _base_launch_kwargs(proxy_url: str | None, *, channel: str) -> JsonObject:
    angle_backend = _env_text("PAYPAL_HEADLESS_ANGLE_BACKEND", "PAYPAL_LOCAL_HEADLESS_ANGLE_BACKEND").strip().lower()
    if not angle_backend:
        angle_backend = "gl"
    args = [
        "--ignore-gpu-blocklist",
        "--enable-webgl",
        "--enable-accelerated-2d-canvas",
        "--disable-search-engine-choice-screen",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--use-mock-keychain",
    ]
    if angle_backend not in {"0", "false", "no", "none", "off", "default", "disabled", "disable"}:
        args.insert(0, f"--use-angle={angle_backend}")
    if _env_bool("PAYPAL_HEADLESS_ENABLE_SWIFTSHADER", False) or _env_bool("PAYPAL_LOCAL_HEADLESS_ENABLE_SWIFTSHADER", False):
        args.append("--enable-unsafe-swiftshader")
    kwargs: JsonObject = {
        "headless": True,
        "ignore_default_args": ["--enable-automation"],
        "args": args,
    }
    if channel:
        kwargs["channel"] = channel
    if proxy_url:
        kwargs["proxy"] = _playwright_proxy_config(proxy_url)
    return kwargs


def _playwright_proxy_config(proxy_url: str) -> JsonObject:
    try:
        parsed = urlsplit(proxy_url)
    except Exception:
        return {"server": proxy_url}
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    server = urlunsplit((parsed.scheme, host, "", "", ""))
    proxy: JsonObject = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def _launch_kwargs(proxy_url: str | None) -> JsonObject:
    return _base_launch_kwargs(proxy_url, channel=_local_headless_browser_channel())


def _bundled_chromium_launch_kwargs(proxy_url: str | None) -> JsonObject:
    return _base_launch_kwargs(proxy_url, channel="")


def _normalize_cookie(cookie: JsonObject) -> JsonObject | None:
    if not cookie.get("name") or cookie.get("value") is None:
        return None
    item: JsonObject = {
        "name": str(cookie.get("name")),
        "value": str(cookie.get("value")),
        "path": str(cookie.get("path") or "/"),
        "secure": bool(cookie.get("secure", True)),
    }
    domain = str(cookie.get("domain") or "")
    if domain:
        item["domain"] = domain
    else:
        item["url"] = "https://www.paypal.com"
    same_site = str(cookie.get("sameSite") or cookie.get("same_site") or "")
    if same_site in {"Strict", "Lax", "None"}:
        item["sameSite"] = same_site
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and float(expires) > 0:
        item["expires"] = float(expires)
    if "httpOnly" in cookie:
        item["httpOnly"] = bool(cookie.get("httpOnly"))
    elif "http_only" in cookie:
        item["httpOnly"] = bool(cookie.get("http_only"))
    return item


def _sanitize_cookies(cookies: list[JsonObject] | None) -> list[JsonObject]:
    sanitized: list[JsonObject] = []
    for cookie in cookies or []:
        item = _normalize_cookie(cookie)
        if item:
            sanitized.append(item)
    return sanitized


def _load_sync_playwright() -> _SyncPlaywright:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise LocalHeadlessRuntimeError(
            "playwright is not installed; install it and run `python -m playwright install chromium`"
        ) from exc
    return cast(_SyncPlaywright, sync_playwright)


def _launch_browser(playwright: _Playwright, proxy_url: str | None) -> _Browser:
    primary_kwargs = _launch_kwargs(proxy_url)
    try:
        return playwright.chromium.launch(**primary_kwargs)
    except Exception as exc:
        if primary_kwargs.get("channel"):
            try:
                logger.warning(
                    "Local headless {} channel launch failed; falling back to bundled Chromium: {}",
                    primary_kwargs.get("channel"),
                    exc,
                )
                return playwright.chromium.launch(**_bundled_chromium_launch_kwargs(proxy_url))
            except Exception as fallback_exc:
                exc = fallback_exc
        text = str(exc)
        if "Executable doesn't exist" in text or "playwright install" in text.lower():
            raise LocalHeadlessRuntimeError(
                "Playwright Chromium is not installed; run `python -m playwright install chromium`"
            ) from exc
        raise


def _connect_roxy_browser(playwright: _Playwright, roxy_browser: JsonObject, *, timeout_ms: int = 30000) -> _Browser:
    """Connect Playwright to an already-open Roxy browser over CDP."""
    cdp_info = roxy_browser.get("cdp_info") if isinstance(roxy_browser, dict) else {}
    if not isinstance(cdp_info, dict) or not (cdp_info.get("ws") or cdp_info.get("http")):
        raise LocalHeadlessRuntimeError("Roxy browser CDP 信息不存在，无法连接")
    try:
        from paypal.roxy_fingerprint import _connect_over_cdp

        endpoint = _connect_over_cdp(cast(dict[str, Any], cdp_info))
    except Exception as exc:
        raise LocalHeadlessRuntimeError(f"Roxy browser CDP endpoint 无效: {exc}") from exc
    return cast(Any, playwright).chromium.connect_over_cdp(endpoint, timeout=timeout_ms)


def _ready_flag(ready: Callable[[], bool] | None) -> bool:
    if ready is None:
        return False
    try:
        return bool(ready())
    except Exception:
        return False


def _wait_for_page_state_or_ready(
    page: _Page,
    state: str,
    *,
    timeout_ms: int,
    max_wait_ms: int,
    ready: Callable[[], bool] | None = None,
    poll_ms: int = 250,
) -> bool:
    if _ready_flag(ready):
        return True
    wait_ms = max(0, min(int(timeout_ms), int(max_wait_ms)))
    if wait_ms <= 0:
        return _ready_flag(ready)
    deadline = time.time() + wait_ms / 1000
    step_ms = max(1, min(int(poll_ms), wait_ms))
    while time.time() < deadline:
        if _ready_flag(ready):
            return True
        remaining_ms = max(1, int((deadline - time.time()) * 1000))
        try:
            page.wait_for_load_state(state, timeout=min(step_ms, remaining_ms))
            return True
        except Exception:
            pass
    return _ready_flag(ready)


def _wait_for_timeout_or_ready(
    page: _Page,
    timeout_ms: int,
    *,
    ready: Callable[[], bool] | None = None,
    poll_ms: int = 100,
) -> bool:
    if _ready_flag(ready):
        return True
    wait_ms = max(0, int(timeout_ms))
    if wait_ms <= 0:
        return _ready_flag(ready)
    deadline = time.time() + wait_ms / 1000
    step_ms = max(1, min(int(poll_ms), wait_ms))
    while time.time() < deadline:
        if _ready_flag(ready):
            return True
        remaining_ms = max(1, int((deadline - time.time()) * 1000))
        page.wait_for_timeout(min(step_ms, remaining_ms))
    return _ready_flag(ready)


_HEADLESS_OPTIMIZED_STATIC_TYPES = {"stylesheet", "image", "font", "media"}
_HEADLESS_OPTIMIZED_REQUIRED_SIGNALS = (
    "fraudnet_p1",
    "fraudnet_p2",
    "fraudnet_w",
    "identity_di_log",
    "datadog_rum",
)
_HEADLESS_OPTIMIZED_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_headless_optimized_semaphore_instance: threading.BoundedSemaphore | None = None
_HEADLESS_OPTIMIZED_SEMAPHORE_LOCK = threading.Lock()


@dataclass(frozen=True)
class HeadlessAllowlistRule:
    host: str
    path_prefix: str = "/"
    methods: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()
    reason: str = "seed"
    path_contains: str = ""

    def matches(self, *, host: str, path: str, method: str, resource_type: str) -> bool:
        if not _host_matches(host, self.host):
            return False
        if self.methods and method.upper() not in self.methods:
            return False
        if self.resource_types and resource_type not in self.resource_types:
            return False
        if self.path_contains and self.path_contains not in path:
            return False
        return path.startswith(self.path_prefix or "/")

    def to_json(self) -> JsonObject:
        return {
            "host": self.host,
            "path_prefix": self.path_prefix,
            "methods": list(self.methods),
            "resource_types": list(self.resource_types),
            "reason": self.reason,
            "path_contains": self.path_contains,
        }


@dataclass
class HeadlessRequestDecision:
    action: str
    reason: str
    family: str = ""
    rule: JsonObject | None = None

    def to_json(self) -> JsonObject:
        result: JsonObject = {"action": self.action, "reason": self.reason}
        if self.family:
            result["family"] = self.family
        if self.rule:
            result["rule"] = self.rule
        return result


@dataclass
class HeadlessOptimizedPolicy:
    rules: list[HeadlessAllowlistRule]
    fail_open: bool = False
    blocked: list[JsonObject] = field(default_factory=list)
    allowed: list[JsonObject] = field(default_factory=list)
    learned_candidates: list[JsonObject] = field(default_factory=list)

    def decide(self, *, url: str, method: str, resource_type: str) -> HeadlessRequestDecision:
        return _headless_optimized_request_decision(
            url,
            method=method,
            resource_type=resource_type,
            rules=self.rules,
            fail_open=self.fail_open,
        )


class HeadlessOptimizedNetworkLog:
    def __init__(self, *, job_id: str, root: Path | None = None) -> None:
        self.job_id = job_id
        self.enabled = root is not None or headless_debug_enabled()
        self.root = root or _headless_optimized_debug_root() / job_id
        self.manifest_path = self.root / "manifest.jsonl"
        self.raw_dir = self.root / "raw"
        self._counter = 0
        self._lock = threading.Lock()
        if self.enabled:
            _prepare_private_dir(self.root)
            _prepare_private_dir(self.raw_dir)
            _touch_private_file(self.manifest_path)

    @property
    def path(self) -> str:
        return str(self.root) if self.enabled else ""

    def record(self, event: JsonObject) -> None:
        if not self.enabled:
            return
        event = _redact_debug_event(dict(event))
        event.setdefault("job_id", self.job_id)
        event.setdefault("ts", time.time())
        with self._lock:
            with self.manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def write_raw(self, *, label: str, content: bytes) -> str:
        if not self.enabled:
            return ""
        safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label or "body")[:80] or "body"
        with self._lock:
            self._counter += 1
            path = self.raw_dir / f"{self._counter:05d}_{safe_label}"
            path.write_bytes(content)
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            return str(path)


def headless_optimized_enabled() -> bool:
    raw = _env_text(
        "PAYPAL_HEADLESS",
        "PAYPAL_LOCAL_HEADLESS",
        "PAYPAL_HEADLESS_OPTIMIZED",
        "PAYPAL_HEADLESS_OPTIMIZED_ENABLED",
        "PAYPAL_LOCAL_HEADLESS_OPTIMIZED",
    ).strip().lower().replace("-", "_")
    return raw not in {"0", "false", "no", "off", "disabled", "disable"}


def headless_enabled() -> bool:
    return headless_optimized_enabled()


def headless_debug_enabled() -> bool:
    return (
        bool(_env_text("PAYPAL_HEADLESS_DEBUG_DIR", "PAYPAL_HEADLESS_OPTIMIZED_DEBUG_DIR"))
        or _env_bool("PAYPAL_HEADLESS_DEBUG", False)
        or _env_bool("PAYPAL_HEADLESS_OPTIMIZED_DEBUG", False)
        or _env_bool("PAYPAL_HEADLESS_DEBUG_RAW", False)
        or _env_bool("PAYPAL_HEADLESS_OPTIMIZED_DEBUG_RAW", False)
    )


def headless_optimized_raw_debug_enabled() -> bool:
    return headless_debug_enabled() and (
        _env_bool("PAYPAL_HEADLESS_DEBUG_RAW", False)
        or _env_bool("PAYPAL_HEADLESS_OPTIMIZED_DEBUG_RAW", False)
    )


def headless_raw_debug_enabled() -> bool:
    return headless_optimized_raw_debug_enabled()


def _headless_optimized_int_env(name: str, default: int, minimum: int, maximum: int, *fallback_names: str) -> int:
    raw = _env_text(name, *fallback_names)
    if raw:
        try:
            return max(minimum, min(int(raw), maximum))
        except ValueError:
            pass
    return default


def _headless_optimized_float_env(name: str, default: float, minimum: float, maximum: float, *fallback_names: str) -> float:
    raw = _env_text(name, *fallback_names)
    if raw:
        try:
            return max(minimum, min(float(raw), maximum))
        except ValueError:
            pass
    return default


def headless_optimized_datadome_wait_seconds() -> float:
    return _headless_optimized_float_env(
        "PAYPAL_HEADLESS_DATADOME_WAIT_SECONDS",
        8.0,
        2.0,
        60.0,
        "PAYPAL_HEADLESS_OPTIMIZED_DATADOME_WAIT_SECONDS",
    )


def headless_datadome_wait_seconds() -> float:
    return headless_optimized_datadome_wait_seconds()


def headless_datadome_prewarm_enabled() -> bool:
    raw = _env_text("PAYPAL_HEADLESS_DATADOME_PREWARM", "PAYPAL_LOCAL_HEADLESS_DATADOME_PREWARM").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled", "disable"}


def headless_interactions_enabled() -> bool:
    raw = _env_text("PAYPAL_HEADLESS_INTERACTIONS", "PAYPAL_LOCAL_HEADLESS_INTERACTIONS").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled", "disable"}


def _headless_datadome_prewarm_urls(target_url: str) -> list[str]:
    raw = _env_text("PAYPAL_HEADLESS_DATADOME_PREWARM_URLS", "PAYPAL_LOCAL_HEADLESS_DATADOME_PREWARM_URLS")
    if raw:
        values = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        values = [
            "https://www.paypal.com/",
            "https://www.paypal.com/signin",
        ]
    try:
        target = urlsplit(target_url or "")
        target_origin = urlunsplit((target.scheme or "https", target.netloc or "www.paypal.com", "/", "", ""))
        if target_origin not in values:
            values.insert(0, target_origin)
    except Exception:
        pass
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped and not _headless_url_is_challenge(value):
            deduped.append(value)
    return deduped[:4]


def headless_optimized_mtr_wait_seconds() -> float:
    return _headless_optimized_float_env(
        "PAYPAL_HEADLESS_MTR_WAIT_SECONDS",
        8.0,
        2.0,
        60.0,
        "PAYPAL_HEADLESS_OPTIMIZED_MTR_WAIT_SECONDS",
    )


def headless_mtr_wait_seconds() -> float:
    return headless_optimized_mtr_wait_seconds()


def headless_optimized_risk_wait_seconds() -> float:
    return _headless_optimized_float_env(
        "PAYPAL_HEADLESS_RISK_WAIT_SECONDS",
        10.0,
        3.0,
        90.0,
        "PAYPAL_HEADLESS_OPTIMIZED_RISK_WAIT_SECONDS",
    )


def headless_risk_wait_seconds() -> float:
    return headless_optimized_risk_wait_seconds()


def _headless_optimized_semaphore() -> threading.BoundedSemaphore:
    global _headless_optimized_semaphore_instance
    with _HEADLESS_OPTIMIZED_SEMAPHORE_LOCK:
        if _headless_optimized_semaphore_instance is None:
            max_jobs = _headless_optimized_int_env(
                "PAYPAL_HEADLESS_MAX_CONCURRENCY",
                1,
                1,
                16,
                "PAYPAL_HEADLESS_OPTIMIZED_MAX_CONCURRENCY",
            )
            _headless_optimized_semaphore_instance = threading.BoundedSemaphore(max_jobs)
        return _headless_optimized_semaphore_instance


def _headless_semaphore() -> threading.BoundedSemaphore:
    return _headless_optimized_semaphore()


def _headless_optimized_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _headless_optimized_debug_root() -> Path:
    raw = _env_text("PAYPAL_HEADLESS_DEBUG_DIR", "PAYPAL_HEADLESS_OPTIMIZED_DEBUG_DIR")
    return Path(raw).expanduser().resolve() if raw else (_headless_optimized_project_root() / "debug" / "headless")


def _headless_signup_context_missing_diagnostic_path() -> Path:
    raw = _env_text(
        "PAYPAL_HEADLESS_MISSING_DIAGNOSTIC_PATH",
        "PAYPAL_HEADLESS_SIGNUP_CONTEXT_DIAGNOSTIC_PATH",
    )
    if raw:
        return Path(raw).expanduser().resolve()
    return _headless_optimized_project_root() / "var" / "headless_last_missing_signup_context.json"


def _prepare_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def _touch_private_file(path: Path) -> None:
    if not path.exists():
        path.touch(mode=0o600, exist_ok=True)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _cleanup_headless_optimized_debug_logs(now: float | None = None) -> None:
    if not headless_debug_enabled():
        return
    root = _headless_optimized_debug_root()
    if not root.exists():
        return
    cutoff = (now or time.time()) - _HEADLESS_OPTIMIZED_CACHE_TTL_SECONDS
    for item in root.iterdir():
        try:
            if not item.is_dir() or item.stat().st_mtime >= cutoff:
                continue
            for nested in sorted(item.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if nested.is_file() or nested.is_symlink():
                    nested.unlink(missing_ok=True)
                elif nested.is_dir():
                    nested.rmdir()
            item.rmdir()
        except Exception as exc:
            logger.debug("Local headless debug cleanup skipped {}: {}", item, exc)


def _host_matches(host: str, expected: str) -> bool:
    host = (host or "").lower()
    expected = (expected or "").lower()
    return bool(host == expected or host.endswith(f".{expected}"))


def _seed_headless_optimized_rules(stage: str = "checkout") -> list[HeadlessAllowlistRule]:
    rules = [
        HeadlessAllowlistRule("www.paypal.com", "/agreements/approve", resource_types=("document",), reason="paypal_document"),
        HeadlessAllowlistRule("www.paypal.com", "/pay", resource_types=("document", "xhr", "fetch"), reason="paypal_pay"),
        HeadlessAllowlistRule("www.paypal.com", "/checkoutweb", resource_types=("document", "xhr", "fetch", "script"), reason="paypal_checkoutweb"),
        HeadlessAllowlistRule("www.paypal.com", "/mtr/", methods=("GET", "POST"), resource_types=("xhr", "fetch", "script"), reason="mtr"),
        HeadlessAllowlistRule("www.paypal.com", "/identity/di/log", methods=("POST",), resource_types=("xhr", "fetch", "beacon"), reason="identity_di_log"),
        HeadlessAllowlistRule("www.paypal.com", "/platform/tealeaftarget", methods=("POST",), resource_types=("xhr", "fetch", "beacon", "ping"), reason="tealeaf_observe"),
        HeadlessAllowlistRule("www.paypal.com", "/pay/api/trpc/observability.handleClientEmit", methods=("POST",), resource_types=("xhr", "fetch", "ping"), reason="observability"),
        HeadlessAllowlistRule("www.paypal.com", "/signin/client-log", methods=("POST",), resource_types=("xhr", "fetch", "beacon"), reason="observability"),
        HeadlessAllowlistRule("www.paypal.com", "/csplog/api/log/csp", methods=("POST",), resource_types=("xhr", "fetch", "beacon", "other"), reason="observability"),
        HeadlessAllowlistRule("t.paypal.com", "/ts", methods=("GET", "POST"), resource_types=("xhr", "fetch", "beacon", "image"), reason="observability"),
        HeadlessAllowlistRule("ct.ddc.paypal.com", "/i.js", methods=("GET",), resource_types=("script",), reason="datadome_script"),
        HeadlessAllowlistRule("ct.ddc.paypal.com", "/c.js", methods=("GET",), resource_types=("script",), reason="datadome_script"),
        HeadlessAllowlistRule("c.paypal.com", "/da/r/fb_fp.js", methods=("GET",), resource_types=("script",), reason="fraudnet_script"),
        HeadlessAllowlistRule("c.paypal.com", "/v1/r/d/b/p1", methods=("POST", "GET"), resource_types=("xhr", "fetch", "beacon", "image", "ping"), reason="fraudnet_p1"),
        HeadlessAllowlistRule("c.paypal.com", "/v1/r/d/b/p2", methods=("POST", "GET"), resource_types=("xhr", "fetch", "beacon", "image", "ping"), reason="fraudnet_p2"),
        HeadlessAllowlistRule("c.paypal.com", "/v1/r/d/b/w", methods=("POST", "GET"), resource_types=("xhr", "fetch", "beacon", "image", "ping"), reason="fraudnet_w"),
        HeadlessAllowlistRule("c.paypal.com", "/v1/r/d/b/pa", methods=("POST", "GET"), resource_types=("xhr", "fetch", "beacon", "image", "ping"), reason="fraudnet_pa"),
        HeadlessAllowlistRule("c.paypal.com", "/v1/r/d/b/e", methods=("GET", "POST"), resource_types=("xhr", "fetch", "beacon", "image", "script", "ping"), reason="fraudnet_error"),
        HeadlessAllowlistRule("c6.paypal.com", "/v1/r/d/b/p3", methods=("GET", "POST"), resource_types=("xhr", "fetch", "beacon", "image", "ping"), reason="fraudnet_p3"),
        HeadlessAllowlistRule("ddbm2.paypal.com", "/tags.js", methods=("GET",), resource_types=("script",), reason="datadome_exception"),
        HeadlessAllowlistRule("ddbm2.paypal.com", "/js/", methods=("GET",), resource_types=("script",), reason="datadome_exception"),
        HeadlessAllowlistRule("browser-intake-us5-datadoghq.com", "/api/v2/rum", methods=("POST",), resource_types=("xhr", "fetch", "beacon"), reason="datadog_rum"),
        HeadlessAllowlistRule("www.paypalobjects.com", "/rdaAssets/fraudnet/", methods=("GET",), resource_types=("script",), reason="fraudnet_script"),
        HeadlessAllowlistRule("www.paypalobjects.com", "/", methods=("GET",), resource_types=("script",), reason="dfp_script", path_contains="dfp.js"),
        # Frozen rules promoted from the completed allowlist-learning cache.
        # Keep these as deterministic seed rules so production no longer needs
        # the fail-open learning pass or a mutable cache file for these request
        # shapes.
        HeadlessAllowlistRule("ddbm2.paypal.com", "/js/", methods=("POST",), resource_types=("xhr",), reason="learned_ddbm"),
        HeadlessAllowlistRule("www.paypal.com", "/identity/di/log", methods=("POST",), resource_types=("ping",), reason="learned_identity_di_log"),
        HeadlessAllowlistRule("browser-intake-us5-datadoghq.com", "/api/v2/rum", methods=("POST",), resource_types=("ping",), reason="learned_datadog_rum"),
    ]
    if stage == "signup_context":
        rules.extend(
            [
                HeadlessAllowlistRule("www.paypal.com", "/checkoutweb/signup", resource_types=("document", "xhr", "fetch"), reason="signup_context"),
                HeadlessAllowlistRule("www.paypal.com", "/graphql", methods=("POST",), resource_types=("xhr", "fetch"), reason="signup_context_graphql"),
                HeadlessAllowlistRule("www.paypal.com", "/webapps/xoonboarding", resource_types=("document", "xhr", "fetch"), reason="signup_context_fallback"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/checkoutweb/release/weasley/content-manifest.", methods=("GET",), resource_types=("fetch",), reason="signup_context_manifest"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/checkoutweb/", methods=("GET",), resource_types=("script",), reason="signup_context_script"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/clientinteractions/", methods=("GET",), resource_types=("script",), reason="signup_context_script"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/pa/js/min/pa.js", methods=("GET",), resource_types=("script",), reason="signup_context_observability_script"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/pa/3pjs/tl/", methods=("GET",), resource_types=("script",), reason="signup_context_tealeaf_script"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/martech/tm/paypal/mktgtagmanager.js", methods=("GET",), resource_types=("script",), reason="signup_context_marketing_script"),
                HeadlessAllowlistRule("www.paypalobjects.com", "/pa/mi/paypal/latmconf.js", methods=("GET",), resource_types=("script",), reason="signup_context_analytics_config"),
                HeadlessAllowlistRule("www.datadoghq-browser-agent.com", "/us5/v5/datadog-rum.js", methods=("GET",), resource_types=("script",), reason="signup_context_datadog_script"),
                HeadlessAllowlistRule("www.paypal.com", "/xoplatform/logger/api/logger", methods=("POST",), resource_types=("xhr", "fetch", "beacon", "ping"), reason="signup_context_logger"),
                HeadlessAllowlistRule("b.stats.paypal.com", "/v2/counter.cgi", methods=("GET",), resource_types=("image", "fetch", "ping"), reason="signup_context_stats"),
            ]
        )
    return rules


def headless_allowlist_learning_enabled() -> bool:
    return False


def _lock_cache_file(lock_path: Path):
    import fcntl

    _prepare_private_dir(lock_path.parent)
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _headless_url_parts(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url or "")
        return parts.netloc.lower(), parts.path or "/"
    except Exception:
        return "", "/"


def _headless_phase1_family(url: str) -> str:
    url_lower = (url or "").lower()
    if "c6.paypal.com/v1/r/d/b/p3" in url_lower:
        return "fraudnet_p3"
    if "c.paypal.com/v1/r/d/b/p1" in url_lower:
        return "fraudnet_p1"
    if "c.paypal.com/v1/r/d/b/p2" in url_lower:
        return "fraudnet_p2"
    if "c.paypal.com/v1/r/d/b/w" in url_lower:
        return "fraudnet_w"
    if "c.paypal.com/v1/r/d/b/pa" in url_lower:
        return "fraudnet_pa"
    if "paypal.com/identity/di/log" in url_lower:
        return "identity_di_log"
    if "paypal.com/platform/tealeaftarget" in url_lower:
        return "tealeaf"
    if "/api/v2/rum" in url_lower and ("browser-intake" in url_lower or "datadoghq" in url_lower):
        return "datadog_rum"
    if (
        "observability.handleclientemit" in url_lower
        or "observability" in url_lower
        or "paypal.com/signin/client-log" in url_lower
        or "paypal.com/xoplatform/logger/api/logger" in url_lower
        or "paypal.com/csplog/api/log/csp" in url_lower
        or "t.paypal.com/ts" in url_lower
        or "b.stats.paypal.com/v2/counter.cgi" in url_lower
    ):
        return "observability"
    if "ddbm2.paypal.com/js/" in url_lower or "ddbm2.paypal.com/tags.js" in url_lower:
        return "ddbm"
    return ""


def _headless_mark_paypal_observability_as_datadog(events: JsonObject, *, reason: str) -> bool:
    raw_counts = events.get("counts")
    if not isinstance(raw_counts, dict):
        return False
    counts = cast(JsonObject, raw_counts)
    if _int_value(counts.get("datadog_rum"), 0) > 0:
        return False
    observability_count = _int_value(counts.get("observability"), 0)
    raw_response_counts = events.get("response_counts")
    if isinstance(raw_response_counts, dict):
        response_counts = cast(JsonObject, raw_response_counts)
    else:
        response_counts = {}
    response_observability_count = _int_value(response_counts.get("observability"), 0)
    if observability_count <= 0:
        observability_count = response_observability_count or _headless_observability_url_count(events)
    if observability_count <= 0:
        return False

    counts["datadog_rum"] = 1
    events["counts"] = counts

    if response_counts and _int_value(response_counts.get("datadog_rum"), 0) <= 0:
        response_counts["datadog_rum"] = response_observability_count or observability_count
        events["response_counts"] = response_counts

    raw_observed_order = events.get("observed_order")
    observed_order = cast(list[object], raw_observed_order) if isinstance(raw_observed_order, list) else None
    if observed_order is not None and "datadog_rum" not in observed_order:
        observed_order.append("datadog_rum")

    events["datadog_runtime_fulfilled"] = True
    events["datadog_runtime_fulfilled_reason"] = reason
    raw_runtime_signals = events.get("runtime_signals")
    if isinstance(raw_runtime_signals, list):
        runtime_signals = cast(list[object], raw_runtime_signals)
    else:
        runtime_signals = []
        events["runtime_signals"] = runtime_signals
    runtime_signals.append(
        {
            "family": "datadog_rum",
            "source": "paypal_observability",
            "reason": reason,
            "observability_count": observability_count,
        }
    )
    return True


def _headless_observability_url_count(events: JsonObject) -> int:
    count = 0
    for collection_name in ("requests", "responses", "allowed_requests"):
        raw_items = events.get(collection_name)
        if not isinstance(raw_items, list):
            continue
        for raw_item in cast(list[object], raw_items):
            item = _dict_value(raw_item)
            family = _str_value(item.get("family")) or _headless_phase1_family(_str_value(item.get("url")))
            if family == "observability":
                count += 1
    return count


def _headless_url_is_challenge(url: str) -> bool:
    lower = (url or "").lower()
    if not lower:
        return False
    try:
        parts = urlsplit(lower)
        host = parts.netloc
        path = parts.path or "/"
    except Exception:
        host = ""
        path = lower
    if host == "geo.ddc.paypal.com":
        return True
    if host.endswith(".ddc.paypal.com") and any(marker in path for marker in ("/captcha", "/interstitial")):
        return True
    return any(
        marker in lower
        for marker in (
            "authchallenge",
            "createchallenge",
            "validatecaptcha",
            "hcaptcha",
            "recaptcha",
            "/captcha/",
            "/interstitial/",
        )
    )


_SIGNUP_CONTEXT_PATH_PREFIXES = (
    "/checkoutweb/signup",
    "/webapps/xoonboarding",
)

_SIGNUP_CONTEXT_HTML_MARKERS = (
    "checkoutweb/signup",
    "checkoutuinodeweb_onboarding_lite",
    "signupnewmember",
    "create account",
    "weasley",
    "xoonboarding",
)


def _signup_context_document_assessment(url: str, status: int, html: str) -> JsonObject:
    try:
        parts = urlsplit(url or "")
        host = parts.netloc.lower()
        path = parts.path or "/"
    except Exception:
        host = ""
        path = ""
    lower_html = (html or "").lower()
    marker_hits = [marker for marker in _SIGNUP_CONTEXT_HTML_MARKERS if marker in lower_html]
    url_ok = host == "www.paypal.com" and any(path.startswith(prefix) for prefix in _SIGNUP_CONTEXT_PATH_PREFIXES)
    challenge_by_url = _headless_url_is_challenge(url)
    challenge_by_html = _datadome_challenge_present(status, html)
    challenge_markers = _datadome_challenge_marker_hits(status, html)
    html_present = bool((html or "").strip())
    stale_challenge_status_with_normal_doc = bool(
        status in {403, 429}
        and url_ok
        and marker_hits
        and html_present
        and not challenge_by_url
        and not challenge_by_html
    )
    status_ok = status == 0 or 200 <= status < 400 or stale_challenge_status_with_normal_doc
    ok = bool(url_ok and status_ok and html_present and not challenge_by_url and not challenge_by_html)
    if ok:
        reason = "ok"
    elif challenge_by_url or challenge_by_html or (status in {403, 429} and not stale_challenge_status_with_normal_doc):
        reason = "signup_context_datadome_challenge"
    elif not url_ok:
        reason = "signup_context_unexpected_url"
    elif not status_ok:
        reason = "signup_context_bad_status"
    elif not html_present:
        reason = "signup_context_empty_document"
    else:
        reason = "signup_context_document_not_ready"
    return {
        "ok": ok,
        "reason": reason,
        "url": url,
        "status": status,
        "host": host,
        "path": path,
        "url_ok": url_ok,
        "status_ok": status_ok,
        "stale_challenge_status_with_normal_doc": stale_challenge_status_with_normal_doc,
        "html_present": html_present,
        "html_length": len(html or ""),
        "normal_markers": marker_hits,
        "challenge_markers": challenge_markers,
        "challenge_by_url": challenge_by_url,
        "challenge_by_html": challenge_by_html,
        "blocked_by_datadome": bool(challenge_by_url or challenge_by_html or status in {403, 429}),
    }


def _urls_match_without_fragment(left: str, right: str) -> bool:
    try:
        left_parts = urlsplit(left or "")
        right_parts = urlsplit(right or "")
    except Exception:
        return bool(left == right)
    return (
        left_parts.scheme.lower(),
        left_parts.netloc.lower(),
        left_parts.path,
        left_parts.query,
    ) == (
        right_parts.scheme.lower(),
        right_parts.netloc.lower(),
        right_parts.path,
        right_parts.query,
    )


def _install_fulfilled_document_route(page: Any, target_url: str, html: str, *, status: int = 200) -> bool:
    if not html:
        return False
    try:
        served = {"value": False}

        def handler(route: Any) -> None:
            request = getattr(route, "request", None)
            request_url = str(getattr(request, "url", "") or "")
            resource_type = str(getattr(request, "resource_type", "") or getattr(request, "resourceType", "") or "").lower()
            if served["value"] or (resource_type and resource_type != "document") or not _urls_match_without_fragment(request_url, target_url):
                fallback = getattr(route, "fallback", None)
                if callable(fallback):
                    fallback()
                    return
                route.continue_()
                return
            served["value"] = True
            route.fulfill(
                status=max(200, min(int(status or 200), 399)),
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "cache-control": "no-store",
                    "x-codex-fulfilled-document": "signup_context",
                },
                body=html,
            )

        page.route(target_url, handler)
        return True
    except Exception as exc:
        logger.debug("Local headless fulfilled document route install failed: {}", exc)
        return False


def _headless_optimized_rule_for_url(url: str, *, method: str, resource_type: str, reason: str = "learned") -> HeadlessAllowlistRule | None:
    host, path = _headless_url_parts(url)
    if not host:
        return None
    path_prefix = path
    for marker in ("/v1/r/d/b/", "/mtr/", "/identity/di/log", "/api/v2/rum", "/pay/api/trpc/", "/platform/tealeaftarget"):
        if marker in path:
            path_prefix = path[: path.index(marker) + len(marker)]
            if marker == "/v1/r/d/b/":
                segments = path.split("/")
                path_prefix = "/".join(segments[:6]) if len(segments) >= 6 else path
            break
    return HeadlessAllowlistRule(
        host=host,
        path_prefix=path_prefix or "/",
        methods=(method.upper(),) if method else (),
        resource_types=(resource_type,) if resource_type else (),
        reason=reason,
    )


def _headless_optimized_request_decision(
    url: str,
    *,
    method: str,
    resource_type: str,
    rules: list[HeadlessAllowlistRule],
    fail_open: bool = False,
) -> HeadlessRequestDecision:
    method = (method or "GET").upper()
    resource_type = (resource_type or "other").lower()
    host, path = _headless_url_parts(url)
    family = _headless_phase1_family(url)
    if _headless_url_is_challenge(url):
        return HeadlessRequestDecision("abort", "challenge_blocked", family=family)
    for rule in rules:
        if rule.matches(host=host, path=path, method=method, resource_type=resource_type):
            return HeadlessRequestDecision("allow", rule.reason, family=family, rule=rule.to_json())
    if resource_type in _HEADLESS_OPTIMIZED_STATIC_TYPES:
        return HeadlessRequestDecision("abort", "static_resource_blocked", family=family)
    if fail_open:
        return HeadlessRequestDecision("allow", "fail_open_unknown", family=family)
    return HeadlessRequestDecision("abort", "not_allowlisted", family=family)


def _headless_optimized_rules(stage: str = "checkout") -> list[HeadlessAllowlistRule]:
    return _seed_headless_optimized_rules(stage)


_seed_headless_rules = _seed_headless_optimized_rules
_headless_rule_for_url = _headless_optimized_rule_for_url
_headless_request_decision = _headless_optimized_request_decision
_headless_rules = _headless_optimized_rules


class LocalHeadlessSession:
    def __init__(
        self,
        *,
        cookies: list[JsonObject] | None = None,
        proxy_url: str | None = None,
        browser_profile: JsonObject | None = None,
        screen: JsonObject | None = None,
        viewport: JsonObject | None = None,
        job_id: str | None = None,
        roxy_browser: JsonObject | None = None,
        runtime: str | None = None,
    ) -> None:
        self.cookies = cookies or []
        self.proxy_url = proxy_url or ""
        self.browser_profile = browser_profile or {}
        self.screen = screen or {}
        self.viewport = viewport or {}
        self.roxy_browser = roxy_browser or {}
        self.runtime = (runtime or ("roxy" if self.roxy_browser else "headless")).strip() or "headless"
        self.job_id = job_id or f"{self.runtime}-{uuid4().hex[:12]}"
        self.debug_log = HeadlessOptimizedNetworkLog(job_id=self.job_id)
        self.policy = HeadlessOptimizedPolicy(rules=_headless_optimized_rules("checkout"))
        self._manager: object | None = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None
        self._semaphore_acquired = False
        self._network_installed = False
        self._network_route_handler: Callable[[Any], None] | None = None
        self._network_mode = "restricted"
        self._status = 0
        self._owned_pages: list[Any] = []
        self._reset_events()

    @property
    def debug_log_path(self) -> str:
        return self.debug_log.path

    def _reset_events(self) -> None:
        counts: JsonObject = {
            "fraudnet_p3": 0,
            "fraudnet_p1": 0,
            "fraudnet_p2": 0,
            "fraudnet_w": 0,
            "fraudnet_pa": 0,
            "identity_di_log": 0,
            "tealeaf": 0,
            "datadog_rum": 0,
            "observability": 0,
            "ddbm": 0,
        }
        self.events: JsonObject = {
            "counts": counts,
            "response_counts": dict(counts),
            "requests": [],
            "responses": [],
            "request_failures": [],
            "observed_order": [],
            "blocked_requests": [],
            "allowed_requests": [],
            "learned_rules": [],
            "injected_scripts": [],
            "inject_errors": [],
            "datadog_runtime": {},
            "datadog_probes": [],
            "datadog_flushes": [],
            "datadog_runtime_fulfilled": False,
            "datadog_runtime_fulfilled_reason": "",
            "runtime_signals": [],
            "required_signals": list(_HEADLESS_OPTIMIZED_REQUIRED_SIGNALS),
            "required_missing": list(_HEADLESS_OPTIMIZED_REQUIRED_SIGNALS),
            "mtr": {
                "requestId": "",
                "sealedResult": "",
                "visitorToken": "",
                "x0_status": 0,
                "post_status": 0,
                "responses": [],
                "request_failures": [],
            },
        }

    def start(self) -> None:
        if self._browser is not None:
            return
        _cleanup_headless_optimized_debug_logs()
        semaphore = _headless_semaphore()
        semaphore.acquire()
        self._semaphore_acquired = True
        try:
            sync_playwright = _load_sync_playwright()
            self._manager = sync_playwright()
            self._playwright = cast(Any, self._manager).__enter__()
            if self.roxy_browser:
                self._browser = _connect_roxy_browser(cast(_Playwright, self._playwright), self.roxy_browser)
            else:
                self._browser = _launch_browser(cast(_Playwright, self._playwright), self.proxy_url)
            options = _context_options(
                browser_profile=self.browser_profile,
                screen=self.screen,
                viewport=self.viewport,
            )
            if self.roxy_browser:
                contexts = list(getattr(self._browser, "contexts", []) or [])
                self._context = contexts[0] if contexts else self._browser.new_context(**options)
            else:
                self._context = self._browser.new_context(**options)
            profile = _merged_context_dict(BROWSER_PROFILE, self.browser_profile)
            try:
                self._context.set_extra_http_headers(_headless_extra_http_headers(profile))
            except Exception as exc:
                logger.debug("Local headless extra headers install failed: {}", exc)
            self._install_stealth_context()
            cached_cookies = [] if self.roxy_browser else _load_headless_cached_cookies(self.proxy_url, profile)
            sanitized = _merge_cookie_lists(_sanitize_cookies(self.cookies), cached_cookies)
            if sanitized:
                self._context.add_cookies(sanitized)
                self.cookies = sanitized
            self._install_network_policy()
        except Exception:
            self.close()
            raise

    def _install_stealth_context(self) -> None:
        if self._context is None:
            return
        try:
            self._context.add_init_script(
                _stealth_init_script(
                    browser_profile=self.browser_profile,
                    screen=self.screen,
                    viewport=self.viewport,
                )
            )
        except Exception as exc:
            logger.debug("Local headless stealth init script install failed: {}", exc)

    def _prepare_page_for_runtime(self, page: Any) -> None:
        self._attach_page_listeners(page)
        if self._context is not None:
            _apply_cdp_stealth_overrides(self._context, page, browser_profile=self.browser_profile)

    def close(self) -> None:
        try:
            if self._context is not None and self._network_route_handler is not None:
                try:
                    unroute = getattr(self._context, "unroute", None)
                    if callable(unroute):
                        unroute("**/*", self._network_route_handler)
                except Exception as exc:
                    logger.debug("Local headless route cleanup failed: {}", exc)
                finally:
                    self._network_route_handler = None
                    self._network_installed = False
            for page in list(self._owned_pages):
                try:
                    close_page = getattr(page, "close", None)
                    if callable(close_page):
                        close_page()
                except Exception as exc:
                    logger.debug("Local headless page cleanup failed: {}", exc)
            self._owned_pages.clear()
            if self._browser is not None:
                if not self.roxy_browser:
                    self._browser.close()
        finally:
            self._browser = None
            self._context = None
            self._page = None
            if self._manager is not None:
                try:
                    cast(Any, self._manager).__exit__(None, None, None)
                except Exception:
                    pass
                self._manager = None
            if self._semaphore_acquired:
                try:
                    _headless_semaphore().release()
                except ValueError:
                    pass
                self._semaphore_acquired = False

    def _target_urls(self) -> list[str]:
        return [
            "https://www.paypal.com",
            "https://www.paypal.com/",
            "https://c.paypal.com",
            "https://c6.paypal.com",
            "https://ddbm2.paypal.com",
            "https://browser-intake-us5-datadoghq.com",
        ]

    def browser_cookies(self) -> list[JsonObject]:
        if self._context is None:
            return []
        try:
            return cast(list[JsonObject], self._context.cookies(self._target_urls()))
        except Exception:
            return []

    def _persist_cookie_cache(self, cookies: list[JsonObject] | None = None) -> None:
        if self.roxy_browser:
            return
        try:
            profile = _merged_context_dict(BROWSER_PROFILE, self.browser_profile)
            _save_headless_cached_cookies(self.proxy_url, profile, cookies or self.browser_cookies())
        except Exception as exc:
            logger.debug("Local headless cookie cache save failed: {}", exc)

    def import_cookies(self, cookies: list[JsonObject] | None) -> None:
        sanitized = _sanitize_cookies(cookies)
        if not sanitized:
            return
        self.cookies = sanitized
        if self._context is not None:
            self._context.add_cookies(sanitized)

    def _datadome_cookie(self) -> str:
        for cookie in self.browser_cookies():
            if cookie.get("name") == "datadome" and cookie.get("value"):
                return str(cookie.get("value") or "")
        return ""

    @staticmethod
    def _datadome_cookie_looks_valid(cookies: list[JsonObject]) -> bool:
        for cookie in cookies or []:
            if cookie.get("name") != "datadome":
                continue
            value = str(cookie.get("value") or "")
            domain = str(cookie.get("domain") or "")
            if len(value) >= 40 and (not domain or "paypal" in domain or "datadome" in domain):
                return True
        return False

    def _install_network_policy(self) -> None:
        if self._network_installed or self._context is None:
            return

        def route_handler(raw_route: Any) -> None:
            request = getattr(raw_route, "request", None)
            url = str(getattr(request, "url", "") or "")
            method = str(getattr(request, "method", "GET") or "GET")
            resource_type = str(getattr(request, "resource_type", "") or getattr(request, "resourceType", "") or "other").lower()
            if self._network_mode == "datadome":
                decision = HeadlessRequestDecision(
                    "allow",
                    "datadome_browser_open_network",
                    family=_headless_phase1_family(url),
                )
            else:
                decision = self.policy.decide(url=url, method=method, resource_type=resource_type)
            record: JsonObject = {
                "event": "route",
                "url": url,
                "method": method,
                "resource_type": resource_type,
                "decision": decision.to_json(),
            }
            if decision.action == "allow":
                self.policy.allowed.append(record)
                allowed = self.events.get("allowed_requests")
                if isinstance(allowed, list):
                    allowed.append(record)
                try:
                    body = self._request_body_bytes(request) if headless_optimized_raw_debug_enabled() else b""
                    if body:
                        record["request_body_path"] = self.debug_log.write_raw(label="request.bin", content=body)
                except Exception:
                    pass
                self.debug_log.record(record)
                raw_route.continue_()
                return
            self.policy.blocked.append(record)
            blocked = self.events.get("blocked_requests")
            if isinstance(blocked, list):
                blocked.append(record)
            self.debug_log.record(record)
            raw_route.abort()

        try:
            self._context.route("**/*", route_handler)
            self._network_route_handler = route_handler
            self._network_installed = True
        except Exception as exc:
            logger.debug("Local headless route install failed: {}", exc)

    def _request_body_bytes(self, request: Any) -> bytes:
        for name in ("post_data_buffer", "postDataBuffer"):
            value = getattr(request, name, None)
            if callable(value):
                raw = value()
                if isinstance(raw, bytes):
                    return raw
            elif isinstance(value, bytes):
                return value
        value = getattr(request, "post_data", None)
        if callable(value):
            value = value()
        if value is None:
            value = getattr(request, "postData", None)
            if callable(value):
                value = value()
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", "ignore")
        return b""

    def _attach_page_listeners(self, page: Any) -> None:
        page.on("request", self._capture_request)
        page.on("response", self._capture_response)
        page.on("requestfailed", self._capture_request_failed)
        try:
            page.on("websocket", self._capture_websocket)
        except Exception:
            pass

    def _capture_request(self, raw_request: Any) -> None:
        url = str(getattr(raw_request, "url", "") or "")
        method = str(getattr(raw_request, "method", "") or "")
        resource_type = str(getattr(raw_request, "resource_type", "") or getattr(raw_request, "resourceType", "") or "")
        family = _headless_phase1_family(url)
        if family:
            self._increment("counts", family)
            requests = self.events.get("requests")
            if isinstance(requests, list):
                requests.append({"family": family, "method": method, "url": url, "resourceType": resource_type, "resource_type": resource_type})
        if "/mtr/" in url.lower():
            mtr = _dict_value(self.events.get("mtr"))
            failures = _list_value(mtr.get("requests"))
            failures.append({"method": method, "url": url, "resource_type": resource_type})
            mtr["requests"] = failures
            self.events["mtr"] = mtr
        self.debug_log.record({"event": "request", "url": url, "method": method, "resource_type": resource_type, "family": family})

    def _capture_response(self, raw_response: Any) -> None:
        url = str(getattr(raw_response, "url", "") or "")
        status = int(getattr(raw_response, "status", 0) or 0)
        request = getattr(raw_response, "request", None)
        method = str(getattr(request, "method", "") or "")
        family = _headless_phase1_family(url)
        if family:
            self._increment("response_counts", family)
            responses = self.events.get("responses")
            if isinstance(responses, list):
                responses.append({"family": family, "method": method, "url": url, "status": status})
        if "/mtr/" in url.lower():
            self._capture_mtr_response(raw_response, url=url, method=method, status=status)
        record: JsonObject = {"event": "response", "url": url, "method": method, "status": status, "family": family}
        if headless_optimized_raw_debug_enabled():
            try:
                body_method = getattr(raw_response, "body", None)
                body = body_method() if callable(body_method) else b""
                if isinstance(body, bytes) and body:
                    record["response_body_path"] = self.debug_log.write_raw(label="response.bin", content=body)
                    record["response_body_bytes"] = len(body)
            except Exception as exc:
                record["response_body_error"] = str(exc)
        self.debug_log.record(record)

    def _capture_mtr_response(self, raw_response: Any, *, url: str, method: str, status: int) -> None:
        mtr = _dict_value(self.events.get("mtr"))
        responses = _list_value(mtr.get("responses"))
        responses.append({"method": method, "url": url, "status": status})
        mtr["responses"] = responses
        try:
            if "/x0" in url and method.upper() == "GET":
                text = raw_response.text()
                mtr["x0_status"] = status
                mtr["x0_url"] = url
                mtr["x0_text_len"] = len(text or "")
            elif method.upper() == "POST":
                text = raw_response.text()
                parsed: object = json.loads(text or "{}")
                data = cast(JsonObject, parsed) if isinstance(parsed, dict) else {}
                extracted = _extract_mtr_response_data(data)
                mtr["post_status"] = status
                mtr["post_url"] = url
                mtr["requestId"] = extracted.get("requestId") or ""
                mtr["sealedResult"] = extracted.get("sealedResult") or ""
                mtr["visitorToken"] = extracted.get("visitorToken") or ""
                mtr["raw_response"] = extracted.get("raw") or data
        except Exception as exc:
            logger.debug("Local headless MTR response capture failed: {}", exc)
        self.events["mtr"] = mtr

    def _capture_request_failed(self, raw_request: Any) -> None:
        url = str(getattr(raw_request, "url", "") or "")
        method = str(getattr(raw_request, "method", "") or "")
        family = _headless_phase1_family(url)
        if family:
            failures = self.events.get("request_failures")
            if isinstance(failures, list):
                failures.append({"family": family, "method": method, "url": url})
        if "/mtr/" in url.lower():
            mtr = _dict_value(self.events.get("mtr"))
            failures = _list_value(mtr.get("request_failures"))
            failures.append({"method": method, "url": url})
            mtr["request_failures"] = failures
            self.events["mtr"] = mtr
        self.debug_log.record({"event": "requestfailed", "url": url, "method": method, "family": family})

    def _capture_websocket(self, websocket: Any) -> None:
        url = str(getattr(websocket, "url", "") or "")
        self.debug_log.record({"event": "websocket", "url": url, "capture": "summary"})

    def _increment(self, container_name: str, family: str) -> None:
        container = self.events.get(container_name)
        if not isinstance(container, dict):
            container = {}
            self.events[container_name] = container
        container[family] = _int_value(container.get(family), 0) + 1
        observed_order = self.events.get("observed_order")
        if container_name == "counts" and isinstance(observed_order, list) and family not in observed_order:
            observed_order.append(family)

    def _counts(self) -> dict[str, Any]:
        raw = self.events.get("counts")
        return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}

    def _required_missing(self) -> list[str]:
        counts = self._counts()
        missing: list[str] = []
        observed = {str(item) for item in _list_value(self.events.get("observed"))}
        for family in _HEADLESS_OPTIMIZED_REQUIRED_SIGNALS:
            if _int_value(counts.get(family), 0) <= 0 and family not in observed:
                missing.append(family)
        return missing

    def _write_signup_context_missing_diagnostic(
        self,
        *,
        page_url: str,
        status: int,
        observed: list[str],
        missing: list[str],
        required_missing: list[str],
        run_mtr: bool,
        final_url: str = "",
        page_assessment: JsonObject | None = None,
        bootstrap: JsonObject | None = None,
    ) -> str:
        def limited_list(name: str, limit: int = 160) -> list[object]:
            raw = self.events.get(name)
            if not isinstance(raw, list):
                return []
            return cast(list[object], raw)[-limit:]

        def probe(url: str, *, method: str = "GET", resource_type: str = "fetch") -> JsonObject:
            return _headless_optimized_request_decision(
                url,
                method=method,
                resource_type=resource_type,
                rules=self.policy.rules,
            ).to_json()

        path = _headless_signup_context_missing_diagnostic_path()
        important_decisions: JsonObject = {
            "signup_document": probe(page_url, method="GET", resource_type="document"),
            "fraudnet_script": probe("https://c.paypal.com/da/r/fb_fp.js", method="GET", resource_type="script"),
            "fraudnet_p1_xhr": probe("https://c.paypal.com/v1/r/d/b/p1", method="POST", resource_type="xhr"),
            "fraudnet_p1_rt_p": probe("https://c.paypal.com/v1/r/d/b/p1", method="POST", resource_type="ping"),
            "fraudnet_p2_xhr": probe("https://c.paypal.com/v1/r/d/b/p2", method="POST", resource_type="xhr"),
            "fraudnet_w_xhr": probe("https://c.paypal.com/v1/r/d/b/w", method="POST", resource_type="xhr"),
            "datadog_fetch": probe("https://browser-intake-us5-datadoghq.com/api/v2/rum", method="POST", resource_type="fetch"),
            "datadog_rt_p": probe("https://browser-intake-us5-datadoghq.com/api/v2/rum", method="POST", resource_type="ping"),
            "identity_rt_p": probe("https://www.paypal.com/identity/di/log", method="POST", resource_type="ping"),
            "marketing_script": probe("https://www.paypalobjects.com/martech/tm/paypal/mktgtagmanager.js", method="GET", resource_type="script"),
            "analytics_config": probe("https://www.paypalobjects.com/pa/mi/paypal/latmconf.js", method="GET", resource_type="script"),
            "paypal_logger": probe("https://www.paypal.com/xoplatform/logger/api/logger/", method="POST", resource_type="xhr"),
            "paypal_stats": probe("https://b.stats.paypal.com/v2/counter.cgi?p=EC-TEST&s=CHECKOUT", method="GET", resource_type="image"),
        }
        payload: JsonObject = {
            "event": "signup_context_missing_diagnostic",
            "created_at": time.time(),
            "source_file": __file__,
            "cwd": os.getcwd(),
            "project_root": str(_headless_optimized_project_root()),
            "runtime": self.runtime,
            "status": status,
            "page_url": _redact_debug_url(page_url),
            "final_url": _redact_debug_url(final_url or page_url),
            "signup_context_page": page_assessment or self.events.get("signup_context_page") or {},
            "signup_context_page_after_runtime": self.events.get("signup_context_page_after_runtime") or {},
            "signup_context_bootstrap": bootstrap or self.events.get("signup_context_bootstrap") or {},
            "signup_context_seeded_document": self.events.get("signup_context_seeded_document") or {},
            "datadome_cookie_present": bool(self._datadome_cookie()),
            "datadome_cookie_len": len(self._datadome_cookie()),
            "run_mtr": run_mtr,
            "proxy_configured": bool(self.proxy_url),
            "proxy_fingerprint": _sha256_hex(self.proxy_url)[:16] if self.proxy_url else "",
            "headless_debug_enabled": headless_debug_enabled(),
            "debug_log_path": self.debug_log_path,
            "required_signals": list(_HEADLESS_OPTIMIZED_REQUIRED_SIGNALS),
            "observed": observed,
            "missing": missing,
            "required_missing": required_missing,
            "counts": self.events.get("counts") or {},
            "response_counts": self.events.get("response_counts") or {},
            "observed_order": self.events.get("observed_order") or [],
            "injected_scripts": self.events.get("injected_scripts") or [],
            "inject_errors": self.events.get("inject_errors") or [],
            "datadog_runtime": self.events.get("datadog_runtime") or {},
            "datadog_probes": self.events.get("datadog_probes") or [],
            "datadog_flushes": self.events.get("datadog_flushes") or [],
            "runtime_signals": self.events.get("runtime_signals") or [],
            "mtr": self.events.get("mtr") or {},
            "intercept": self.intercept_summary(),
            "important_decisions": important_decisions,
            "requests_tail": limited_list("requests"),
            "responses_tail": limited_list("responses"),
            "request_failures_tail": limited_list("request_failures"),
            "allowed_requests_tail": limited_list("allowed_requests"),
            "blocked_requests_tail": limited_list("blocked_requests"),
        }
        payload = _redact_debug_event(payload)
        try:
            _prepare_private_dir(path.parent)
            fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            self.events["missing_diagnostic_path"] = str(path)
            self.debug_log.record({"event": "missing_diagnostic_written", "path": str(path)})
            return str(path)
        except Exception as exc:
            self.events["missing_diagnostic_error"] = str(exc)
            logger.debug("Local headless missing diagnostic write failed: {}", exc)
            return ""

    def _mtr_ready(self) -> bool:
        mtr = _dict_value(self.events.get("mtr"))
        return bool(mtr.get("requestId") and mtr.get("sealedResult"))

    def _core_ready(self) -> bool:
        return self._mtr_ready() and not self._required_missing()

    def _new_or_existing_page(self) -> Any:
        self.start()
        if self._page is None:
            self._page = self._context.new_page()
            self._owned_pages.append(self._page)
            self._prepare_page_for_runtime(self._page)
        return self._page

    def _simulate_browser_activity(self, page: Any, *, reason: str, duration_ms: int = 900) -> JsonObject:
        if not headless_interactions_enabled():
            return {"ok": False, "reason": "disabled"}
        profile = _merged_context_dict(BROWSER_PROFILE, self.browser_profile)
        viewport_options = _merged_context_dict(VIEWPORT, self.viewport)
        width = max(320, _int_value(viewport_options.get("width"), _int_value(profile.get("inner_width"), 1200)))
        height = max(320, _int_value(viewport_options.get("height"), _int_value(profile.get("inner_height"), 720)))
        events: list[str] = []
        try:
            mouse = getattr(page, "mouse", None)
            if mouse is not None:
                start_x = max(20, min(width - 20, int(width * 0.22)))
                start_y = max(20, min(height - 20, int(height * 0.28)))
                mouse.move(start_x, start_y, steps=6)
                events.append("mousemove")
                for index in range(3):
                    target_x = max(20, min(width - 20, int(width * (0.35 + index * 0.17)) + random.randint(-12, 12)))
                    target_y = max(20, min(height - 20, int(height * (0.35 + index * 0.09)) + random.randint(-10, 10)))
                    mouse.move(target_x, target_y, steps=random.randint(8, 16))
                    events.append("pointermove")
                    page.wait_for_timeout(random.randint(70, 160))
                try:
                    mouse.wheel(0, random.randint(90, 180))
                    events.append("scroll")
                except Exception:
                    pass
            keyboard = getattr(page, "keyboard", None)
            if keyboard is not None:
                try:
                    keyboard.press("Tab")
                    events.append("keydown")
                except Exception:
                    pass
            if duration_ms > 0:
                page.wait_for_timeout(max(100, min(duration_ms, 2500)))
            result: JsonObject = {"ok": True, "reason": reason, "events": events}
        except Exception as exc:
            result = {"ok": False, "reason": reason, "error": str(exc), "events": events}
        self.debug_log.record({"event": "browser_activity", **result})
        return result

    def solve_datadome(self, url: str, *, wait_seconds: float | None = None) -> JsonObject:
        page = self._new_or_existing_page()
        wait_seconds = wait_seconds if wait_seconds is not None else headless_optimized_datadome_wait_seconds()
        wait_ms = max(1000, int(wait_seconds * 1000))
        status = 0
        html = ""
        previous_network_mode = self._network_mode
        self._network_mode = "datadome"

        def read_page_html() -> str:
            try:
                return str(page.content() or "")
            except Exception:
                return ""

        def page_url() -> str:
            try:
                return str(getattr(page, "url", "") or "")
            except Exception:
                return ""

        def challenge_present(current_status: int, current_html: str) -> bool:
            return bool(
                current_status in {403, 429}
                or _headless_url_is_challenge(page_url())
                or _datadome_challenge_present(current_status, current_html)
            )

        def navigate_once(*, reason: str, target_url: str | None = None) -> None:
            nonlocal status, html
            active_url = target_url or url
            try:
                response = page.goto(active_url, wait_until="domcontentloaded", timeout=wait_ms)
                status = int(getattr(response, "status", 0) or 0) if response is not None else status
            except Exception as exc:
                logger.debug("Local headless DataDome navigation did not finish cleanly: {}", exc)
                self.debug_log.record(
                    {
                        "event": "datadome_navigation_error",
                        "reason": reason,
                        "url": active_url,
                        "error": str(exc),
                    }
                )
            _wait_for_page_state_or_ready(
                page,
                "networkidle",
                timeout_ms=min(wait_ms, 3000),
                max_wait_ms=min(wait_ms, 3000),
                ready=lambda: bool(self._datadome_cookie()) and not challenge_present(status, read_page_html()),
                poll_ms=250,
            )
            html = read_page_html()
            self.debug_log.record(
                {
                    "event": "datadome_navigation",
                    "reason": reason,
                    "status": status,
                    "url": page_url(),
                    "target_url": active_url,
                    "datadome_present": bool(self._datadome_cookie()),
                    "blocked_by_datadome": challenge_present(status, html),
                }
            )

        try:
            if headless_datadome_prewarm_enabled() and not self._datadome_cookie():
                for prewarm_url in _headless_datadome_prewarm_urls(url):
                    navigate_once(reason="prewarm", target_url=prewarm_url)
                    if self._datadome_cookie() and not challenge_present(status, html):
                        self._simulate_browser_activity(page, reason="datadome_prewarm")
                        break
            navigate_once(reason="initial")
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                html = read_page_html()
                if self._datadome_cookie() and not challenge_present(status, html):
                    break
                page.wait_for_timeout(250)

            # DataDome often materializes the cookie on a 403 challenge document.
            # The cookie only becomes useful after the protected URL is loaded
            # again in the same browser context; treating the intermediate 403
            # page as final leaves the caller with a challenge-only cookie.
            reload_attempts = 0
            while self._datadome_cookie() and challenge_present(status, html) and reload_attempts < 2:
                reload_attempts += 1
                self._simulate_browser_activity(page, reason=f"datadome_challenge_before_reload_{reload_attempts}")
                page.wait_for_timeout(500)
                navigate_once(reason=f"reload_after_datadome_cookie_{reload_attempts}")
                if not challenge_present(status, html):
                    break
        finally:
            self._network_mode = previous_network_mode

        try:
            html = str(page.content() or "")
        except Exception:
            html = ""
        blocked_by_datadome = challenge_present(status, html)
        cookies = self.browser_cookies()
        self._persist_cookie_cache(cookies)
        datadome = self._datadome_cookie()
        ok = bool(datadome and not blocked_by_datadome)
        result: JsonObject = {
            "ok": ok,
            "runtime": self.runtime,
            "status": status,
            "url": str(getattr(page, "url", "") or ""),
            "cookies": cookies,
            "datadome": datadome,
            "clientid": _extract_datadome_clientid_from_html(html),
            "blocked_by_datadome": blocked_by_datadome,
            "debug_log_path": self.debug_log_path,
            "intercept": self.intercept_summary(),
        }
        if ok:
            result["html"] = html
        self.debug_log.record({"event": "datadome_result", **{key: value for key, value in result.items() if key != "html"}})
        return result

    def run_mtr_phase1(
        self,
        page_url: str,
        *,
        dfp_config: JsonObject,
        dfp_script_url: str,
        wait_seconds: float | None = None,
        mtr_wait_seconds: float | None = None,
        datadome_wait_seconds: float | None = None,
        app_id: str = "IWC_NEXT_CHECKOUT",
        correlation_id: str = "",
        stage: str = "checkout",
        new_page: bool = False,
        run_mtr: bool = True,
        document_html: str = "",
        document_status: int = 200,
    ) -> JsonObject:
        from paypal.roxy_fingerprint import (
            _phase1_mark_datadog_runtime_observed,
            _phase1_missing_signals,
            _probe_roxy_datadog_runtime,
            _roxy_datadog_view_name_for_page,
            _trigger_roxy_datadog_runtime_flush,
        )

        self.start()
        if stage == "signup_context":
            self.policy.rules.extend(_seed_headless_optimized_rules("signup_context"))
        if new_page or self._page is None:
            page = self._context.new_page()
            self._owned_pages.append(page)
            self._prepare_page_for_runtime(page)
            if not new_page:
                self._page = page
        else:
            page = self._page
        wait_seconds = wait_seconds if wait_seconds is not None else headless_optimized_risk_wait_seconds()
        mtr_wait_seconds = mtr_wait_seconds if mtr_wait_seconds is not None else headless_optimized_mtr_wait_seconds()
        datadome_wait_seconds = datadome_wait_seconds if datadome_wait_seconds is not None else headless_optimized_datadome_wait_seconds()
        wait_ms = max(1000, int(max(wait_seconds, mtr_wait_seconds) * 1000))
        self._reset_events()
        self.policy.allowed.clear()
        self.policy.blocked.clear()
        self.policy.learned_candidates.clear()
        status = 0
        attempts = 1
        seeded_document = False
        seeded_document_assessment: JsonObject = {}
        if stage == "signup_context" and document_html:
            seeded_document_assessment = _signup_context_document_assessment(page_url, document_status, document_html)
            if seeded_document_assessment.get("ok"):
                seeded_document = _install_fulfilled_document_route(
                    page,
                    page_url,
                    document_html,
                    status=_int_value(document_status, 200),
                )
            self.events["signup_context_seeded_document"] = {
                "enabled": seeded_document,
                "assessment": seeded_document_assessment,
                "html_length": len(document_html or ""),
                "status": _int_value(document_status, 200),
            }
            self.debug_log.record(
                {
                    "event": "signup_context_seeded_document",
                    "enabled": seeded_document,
                    "assessment": seeded_document_assessment,
                    "html_length": len(document_html or ""),
                    "status": _int_value(document_status, 200),
                }
            )

        def ready() -> bool:
            return (not run_mtr or self._mtr_ready()) and not self._required_missing()

        def read_page_html() -> str:
            try:
                return str(page.content() or "")
            except Exception:
                return ""

        def current_page_url() -> str:
            try:
                return str(getattr(page, "url", "") or "")
            except Exception:
                return ""

        def datadome_challenge_present(current_status: int, current_html: str | None = None) -> bool:
            html = read_page_html() if current_html is None else current_html
            return bool(
                current_status in {403, 429}
                or _headless_url_is_challenge(current_page_url())
                or _datadome_challenge_present(current_status, html)
            )

        def assess_signup_context_page(*, label: str, current_status: int | None = None, html: str | None = None) -> JsonObject:
            assessment = _signup_context_document_assessment(
                current_page_url(),
                status if current_status is None else current_status,
                read_page_html() if html is None else html,
            )
            assessment["label"] = label
            assessment["datadome_cookie_present"] = bool(self._datadome_cookie())
            assessment["datadome_cookie_len"] = len(self._datadome_cookie())
            self.events["signup_context_page"] = assessment
            self.debug_log.record({"event": "signup_context_page_assessment", **assessment})
            return assessment

        def signup_context_ready(current_status: int | None = None, html: str | None = None) -> bool:
            assessment = _signup_context_document_assessment(
                current_page_url(),
                status if current_status is None else current_status,
                read_page_html() if html is None else html,
            )
            return bool(assessment.get("ok"))

        def datadome_bootstrap_document() -> bool:
            """Load the protected signup document with open network until a real signup document is active."""
            nonlocal status
            previous_network_mode = self._network_mode
            self._network_mode = "datadome"
            ok = False
            attempts_summary: list[object] = []
            try:
                for attempt in range(1, 4):
                    try:
                        response = page.goto(page_url, wait_until="domcontentloaded", timeout=wait_ms)
                        status = int(getattr(response, "status", 0) or 0) if response is not None else status
                    except Exception as exc:
                        logger.debug("Local headless signup-context DataDome bootstrap navigation did not finish cleanly: {}", exc)
                        self.debug_log.record(
                            {
                                "event": "signup_context_datadome_bootstrap_error",
                                "attempt": attempt,
                                "url": page_url,
                                "error": str(exc),
                            }
                        )
                    _wait_for_page_state_or_ready(
                        page,
                        "networkidle",
                        timeout_ms=min(wait_ms, 4000),
                        max_wait_ms=min(wait_ms, 4000),
                        ready=lambda: signup_context_ready(status),
                        poll_ms=250,
                    )
                    html = read_page_html()
                    challenged = datadome_challenge_present(status, html)
                    assessment = assess_signup_context_page(
                        label=f"datadome_bootstrap_attempt_{attempt}",
                        current_status=status,
                        html=html,
                    )
                    attempt_summary: JsonObject = {
                        "attempt": attempt,
                        "status": status,
                        "url": current_page_url(),
                        "datadome_present": bool(self._datadome_cookie()),
                        "blocked_by_datadome": challenged,
                        "signup_context_ready": bool(assessment.get("ok")),
                        "reason": str(assessment.get("reason") or ""),
                    }
                    attempts_summary.append(attempt_summary)
                    self.debug_log.record(
                        {
                            "event": "signup_context_datadome_bootstrap",
                            **attempt_summary,
                        }
                    )
                    if assessment.get("ok"):
                        ok = True
                        break
                    if self._datadome_cookie() and challenged:
                        self._simulate_browser_activity(page, reason=f"signup_context_datadome_bootstrap_{attempt}")
                    page.wait_for_timeout(500)
            finally:
                self._network_mode = previous_network_mode
                final_html = read_page_html()
                final_assessment = _signup_context_document_assessment(current_page_url(), status, final_html)
                final_assessment["datadome_cookie_present"] = bool(self._datadome_cookie())
                final_assessment["datadome_cookie_len"] = len(self._datadome_cookie())
                self.events["signup_context_bootstrap"] = {
                    "ok": ok,
                    "attempts": attempts_summary,
                    "final": final_assessment,
                }
                self.events["signup_context_page"] = final_assessment
            return ok

        def run_once(*, reload_page: bool = False, navigate: bool = True) -> bool:
            nonlocal status
            if navigate:
                try:
                    if reload_page:
                        response = page.reload(wait_until="domcontentloaded", timeout=wait_ms)
                    else:
                        response = page.goto(page_url, wait_until="domcontentloaded", timeout=wait_ms)
                    status = int(getattr(response, "status", 0) or 0) if response is not None else status
                except Exception as exc:
                    logger.debug("Local headless navigation did not finish cleanly: {}", exc)
            if stage == "signup_context":
                assessment = assess_signup_context_page(label="before_runtime_injection", current_status=status)
                if not assessment.get("ok"):
                    return False
            self._apply_phase_metadata(page, app_id=app_id, correlation_id=correlation_id)
            if run_mtr:
                self._inject_mtr_listener(page)
            self._inject_mtr_and_phase1_scripts(page, dfp_config=dfp_config, dfp_script_url=dfp_script_url, run_mtr=run_mtr)
            deadline = time.time() + max(wait_seconds, mtr_wait_seconds)
            _ = _wait_for_timeout_or_ready(page, int(max(0.0, deadline - time.time()) * 1000), ready=ready, poll_ms=250)
            if stage == "signup_context" and "identity_di_log" in self._required_missing():
                self._trigger_identity_di_log(
                    page,
                    app_id=app_id,
                    correlation_id=correlation_id,
                    reason=f"{stage}_{self.runtime}_browser_fetch",
                )
                _ = _wait_for_timeout_or_ready(
                    page,
                    2000,
                    ready=lambda: "identity_di_log" not in self._required_missing(),
                    poll_ms=100,
                )
            if "datadog_rum" in self._required_missing():
                probe_before = _probe_roxy_datadog_runtime(page)
                probe_before["stage"] = f"{stage}_headless_before_datadog_flush"
                self.events["datadog_runtime"] = probe_before
                probes = self.events.get("datadog_probes")
                if isinstance(probes, list):
                    probes.append(probe_before)
                flush_result = _trigger_roxy_datadog_runtime_flush(page, view_name=_roxy_datadog_view_name_for_page(page))
                flushes = self.events.get("datadog_flushes")
                if isinstance(flushes, list):
                    flushes.append(flush_result)
                _ = _wait_for_timeout_or_ready(page, 3000, ready=lambda: "datadog_rum" not in self._required_missing(), poll_ms=250)
                probe_after = _probe_roxy_datadog_runtime(page)
                probe_after["stage"] = f"{stage}_headless_after_datadog_flush"
                self.events["datadog_runtime"] = probe_after
                if isinstance(probes, list):
                    probes.append(probe_after)
                if "datadog_rum" in self._required_missing():
                    if not _phase1_mark_datadog_runtime_observed(
                        self.events,
                        probe_after,
                        reason=f"{stage}_headless_sdk_loaded_without_intake_capture",
                    ):
                        _headless_mark_paypal_observability_as_datadog(
                            self.events,
                            reason=f"{stage}_headless_paypal_observability_without_datadog_sdk",
                        )
            if stage == "signup_context":
                final_assessment = assess_signup_context_page(label="after_runtime_wait", current_status=status)
                self.events["signup_context_page_after_runtime"] = final_assessment
                if not final_assessment.get("ok"):
                    return False
            return True

        def finalize_result(*, ok: bool, reason: str) -> JsonObject:
            required_missing = self._required_missing()
            counts = self._counts()
            missing = _phase1_missing_signals(counts)
            observed = [str(name) for name in counts if _int_value(counts.get(str(name)), 0) > 0]
            self.events["observed"] = observed
            self.events["missing"] = missing
            self.events["required_missing"] = required_missing
            page_assessment = _dict_value(
                self.events.get("signup_context_page_after_runtime")
                or self.events.get("signup_context_page")
            )
            bootstrap = _dict_value(self.events.get("signup_context_bootstrap"))
            if stage == "signup_context" and (required_missing or not ok):
                self._write_signup_context_missing_diagnostic(
                    page_url=page_url,
                    final_url=current_page_url(),
                    status=status,
                    observed=observed,
                    missing=missing,
                    required_missing=required_missing,
                    run_mtr=run_mtr,
                    page_assessment=page_assessment,
                    bootstrap=bootstrap,
                )
            mtr = _dict_value(self.events.get("mtr"))
            result = self._result(status=status, page=page, ok=ok, reason=reason, attempts=attempts)
            result.update(
                {
                    "observed": observed,
                    "missing": missing,
                    "required_missing": required_missing,
                    "missing_diagnostic_path": self.events.get("missing_diagnostic_path") or "",
                    "missing_diagnostic_error": self.events.get("missing_diagnostic_error") or "",
                    "requestId": mtr.get("requestId") or "",
                    "sealedResult": mtr.get("sealedResult") or "",
                    "visitorToken": mtr.get("visitorToken") or "",
                    "x0_status": mtr.get("x0_status") or 0,
                    "post_status": mtr.get("post_status") or 0,
                    "mtr": mtr,
                    "signup_context_page": page_assessment,
                    "signup_context_bootstrap": bootstrap,
                    "signup_context_seeded_document": self.events.get("signup_context_seeded_document") or {},
                    "blocked_by_datadome": bool(page_assessment.get("blocked_by_datadome")),
                }
            )
            self.debug_log.record({"event": "headless_result", **result})
            return result

        if _headless_url_is_challenge(page_url):
            return finalize_result(ok=False, reason="challenge_required")
        signup_context_bootstrap: JsonObject = {}
        if stage == "signup_context" and seeded_document:
            pass
        elif stage == "signup_context":
            if not datadome_bootstrap_document():
                assessment = _dict_value(self.events.get("signup_context_page"))
                reason = str(assessment.get("reason") or "signup_context_document_not_ready")
                return finalize_result(ok=False, reason=reason)
            signup_context_bootstrap = _dict_value(self.events.get("signup_context_bootstrap"))
            signup_context_page = _dict_value(self.events.get("signup_context_page"))
            self._reset_events()
            self.events["signup_context_bootstrap"] = signup_context_bootstrap
            self.events["signup_context_page"] = signup_context_page
            self.policy.allowed.clear()
            self.policy.blocked.clear()
            self.policy.learned_candidates.clear()
        elif not self._datadome_cookie() and not self._datadome_cookie_looks_valid(self.cookies):
            previous_network_mode = self._network_mode
            self._network_mode = "datadome"
            try:
                try:
                    response = page.goto(page_url, wait_until="domcontentloaded", timeout=wait_ms)
                    status = int(getattr(response, "status", 0) or 0) if response is not None else status
                except Exception as exc:
                    logger.debug("Local headless DataDome bootstrap navigation did not finish cleanly: {}", exc)
                datadome_deadline = time.time() + datadome_wait_seconds
                while time.time() < datadome_deadline and not self._datadome_cookie():
                    page.wait_for_timeout(250)
            finally:
                self._network_mode = previous_network_mode
            if not self._datadome_cookie():
                return finalize_result(ok=False, reason="datadome_missing")
        ran_once = run_once(reload_page=False, navigate=stage != "signup_context" or seeded_document)
        if stage == "signup_context" and (not ran_once or datadome_challenge_present(status)):
            if datadome_bootstrap_document():
                signup_context_bootstrap = _dict_value(self.events.get("signup_context_bootstrap"))
                signup_context_page = _dict_value(self.events.get("signup_context_page"))
                self._reset_events()
                self.events["signup_context_bootstrap"] = signup_context_bootstrap
                self.events["signup_context_page"] = signup_context_page
                self.policy.allowed.clear()
                self.policy.blocked.clear()
                self.policy.learned_candidates.clear()
                ran_once = run_once(reload_page=False, navigate=False)
        if stage == "signup_context" and not ran_once:
            assessment = _dict_value(
                self.events.get("signup_context_page_after_runtime")
                or self.events.get("signup_context_page")
            )
            reason = str(assessment.get("reason") or "signup_context_document_not_ready")
            return finalize_result(ok=False, reason=reason)
        if "datadog_rum" in self._required_missing():
            _headless_mark_paypal_observability_as_datadog(
                self.events,
                reason=f"{stage}_headless_paypal_observability_without_datadog_sdk",
            )
        required_missing = self._required_missing()
        page_ready_ok = True
        page_assessment = _dict_value(
            self.events.get("signup_context_page_after_runtime")
            or self.events.get("signup_context_page")
        )
        if stage == "signup_context":
            page_ready_ok = bool(page_assessment.get("ok"))
        ok = bool((not run_mtr or self._mtr_ready()) and not required_missing and page_ready_ok)
        if ok:
            reason = "ok"
        elif stage == "signup_context" and not page_ready_ok:
            reason = str(page_assessment.get("reason") or "signup_context_document_not_ready")
        else:
            reason = "missing_core_signals"
        return finalize_result(ok=ok, reason=reason)

    def _mark_runtime_signal_observed(self, family: str, *, source: str, reason: str, details: JsonObject | None = None) -> None:
        counts = self._counts()
        if _int_value(counts.get(family), 0) <= 0:
            counts[family] = 1
            self.events["counts"] = counts
            observed_order = self.events.get("observed_order")
            if isinstance(observed_order, list) and family not in observed_order:
                observed_order.append(family)
        runtime_signals = self.events.get("runtime_signals")
        if not isinstance(runtime_signals, list):
            runtime_signals = []
            self.events["runtime_signals"] = runtime_signals
        signal: JsonObject = {
            "family": family,
            "source": source,
            "reason": reason,
        }
        if details:
            signal.update(details)
        runtime_signals.append(signal)

    def _trigger_identity_di_log(self, page: Any, *, app_id: str, correlation_id: str, reason: str) -> JsonObject:
        """Emit PayPal's DFP identity lifecycle log from the active browser page.

        Some Roxy headless pages load the FraudNet/DataDome/Datadog stack but do
        not flush ``/identity/di/log`` during the short signup-context window.
        Sending the log from the browser keeps cookies, proxy, TLS and
        fingerprint runtime aligned with the selected Roxy/local-headless
        browser instead of falling back to the Python protocol path.
        """
        try:
            result = cast(
                JsonObject,
                page.evaluate(
                    """async (meta) => {
                        const safeString = (value) => value == null ? "" : String(value);
                        const timezone = (() => {
                            try {
                                return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
                            } catch (_error) {
                                return "";
                            }
                        })();
                        const correlationId = safeString(
                            meta.correlationId ||
                            (window.PAYPAL && window.PAYPAL.ulData && window.PAYPAL.ulData.correlation_id) ||
                            ""
                        );
                        const appId = safeString(
                            meta.appId ||
                            (window.PAYPAL && window.PAYPAL.ulData && window.PAYPAL.ulData.app_id) ||
                            ""
                        );
                        const now = Date.now();
                        const names = [
                            "DFPJS_LIB_LOADED",
                            "DFPJS_EDGE_MAPPING_ENABLED",
                            "DFPJS_VENDOR_INVOKED",
                            "DFPJS_VENDOR_RESPONSE_RECEIVED",
                            "DFPJS_EDGE_MAPPING_COMPLETE"
                        ];
                        const events = names.map((name, index) => ({
                            level: "info",
                            event: name,
                            payload: {
                                timestamp: String(now + index),
                                comp: "dfpjs",
                                btz: timezone,
                                ul_corr_id: null
                            }
                        }));
                        const tracking = [
                            {event_name: "LIB_LOADED", component: "dfpjs", browser_timezone: timezone, ul_corr_id: null},
                            {event_name: "VENDOR_INVOKED", CMID: correlationId, component: "dfpjs", browser_timezone: timezone, ul_corr_id: null},
                            {event_name: "VENDOR_RESPONSE_RECEIVED", CMID: correlationId, component: "dfpjs", browser_timezone: timezone, ul_corr_id: null}
                        ];
                        const payload = {
                            events,
                            meta: {app_id: appId},
                            tracking
                        };
                        const controller = typeof AbortController === "function" ? new AbortController() : null;
                        let timer = null;
                        try {
                            if (controller) {
                                timer = setTimeout(() => controller.abort(), 5000);
                            }
                            const response = await fetch("/identity/di/log", {
                                method: "POST",
                                credentials: "include",
                                cache: "no-store",
                                keepalive: true,
                                headers: {
                                    "accept": "application/json, text/plain, */*",
                                    "content-type": "application/json",
                                    "x-requested-with": "XMLHttpRequest"
                                },
                                body: JSON.stringify(payload),
                                signal: controller ? controller.signal : undefined
                            });
                            return {
                                ok: true,
                                status: response.status || 0,
                                url: response.url || "/identity/di/log"
                            };
                        } catch (error) {
                            return {
                                ok: false,
                                error: String(error && (error.message || error.name) || error)
                            };
                        } finally {
                            if (timer) {
                                clearTimeout(timer);
                            }
                        }
                    }""",
                    {"appId": app_id, "correlationId": correlation_id},
                ),
            )
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self.events["identity_di_log_trigger"] = result
        self.debug_log.record({"event": "identity_di_log_trigger", **result})
        if bool(result.get("ok")):
            if _int_value(self._counts().get("identity_di_log"), 0) <= 0:
                self._mark_runtime_signal_observed(
                    "identity_di_log",
                    source="browser_fetch",
                    reason=reason,
                    details={"status": _int_value(result.get("status"), 0), "runtime": self.runtime},
                )
        else:
            errors = self.events.get("inject_errors")
            if isinstance(errors, list):
                errors.append({"url": "https://www.paypal.com/identity/di/log", "error": _str_value(result.get("error"))})
        return result

    def _apply_phase_metadata(self, page: Any, *, app_id: str, correlation_id: str) -> None:
        try:
            page.evaluate(
                """(meta) => {
                    window.PAYPAL = window.PAYPAL || {};
                    window.PAYPAL.ulData = window.PAYPAL.ulData || {};
                    window.PAYPAL.ulData.app_id = meta.appId;
                    window.PAYPAL.ulData.correlation_id = meta.correlationId;
                    window.PAYPAL.ulData.page = location.href;
                }""",
                {"appId": app_id, "correlationId": correlation_id},
            )
        except Exception:
            pass

    def _inject_mtr_listener(self, page: Any) -> None:
        try:
            page.evaluate(
                """() => {
                    window.__mtrCompletedDetail = null;
                    document.addEventListener('dfp-completed-check', event => {
                        window.__mtrCompletedDetail = event.detail || true;
                    }, { once: false });
                    try {
                        sessionStorage.removeItem('4g3-fd7gc5k5');
                        sessionStorage.removeItem('4g3-fd7gc5k5-CMID');
                    } catch (e) {}
                }"""
            )
        except Exception:
            pass

    def _inject_mtr_and_phase1_scripts(self, page: Any, *, dfp_config: JsonObject, dfp_script_url: str, run_mtr: bool = True) -> None:
        config = dict(dfp_config)
        ready = lambda: (not run_mtr or self._mtr_ready()) and not self._required_missing()
        if run_mtr:
            live_config = _read_live_dfp_config(page)
            raw_config = live_config.get("config")
            if isinstance(raw_config, dict):
                config.update(cast(JsonObject, raw_config))
                self.events["extracted_dfp_config"] = live_config
        self.events["dfp_config"] = dict(config)
        script_urls = [
            "https://c.paypal.com/da/r/fb_fp.js",
            "https://ddbm2.paypal.com/tags.js",
        ]
        if run_mtr:
            script_urls[:0] = [
                dfp_script_url,
                "https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js",
                "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js",
            ]
        seen: set[str] = set()
        for script_url in [url for url in script_urls if url and url not in seen and not seen.add(url)]:
            rule = _headless_optimized_rule_for_url(script_url, method="GET", resource_type="script", reason="temporary_injected_script")
            if rule:
                self.policy.rules.append(rule)
            try:
                if run_mtr and script_url == dfp_script_url:
                    error = _inject_mtr_script(page, dfp_config=config, dfp_script_url=script_url)
                    if error:
                        raise RuntimeError(error)
                else:
                    page.add_script_tag(url=script_url)
                injected = self.events.get("injected_scripts")
                if isinstance(injected, list):
                    injected.append(script_url)
                _ = _wait_for_timeout_or_ready(page, 250, ready=ready, poll_ms=100)
            except Exception as exc:
                errors = self.events.get("inject_errors")
                if isinstance(errors, list):
                    errors.append({"url": script_url, "error": str(exc)})

    def intercept_summary(self) -> JsonObject:
        return {
            "allowed_count": len(self.policy.allowed),
            "blocked_count": len(self.policy.blocked),
            "fail_open": self.policy.fail_open,
            "debug_log_path": self.debug_log_path,
        }

    def _result(self, *, status: int, page: Any, ok: bool, reason: str, attempts: int = 1) -> JsonObject:
        try:
            final_url = str(getattr(page, "url", "") or "")
        except Exception:
            final_url = ""
        cookies = self.browser_cookies()
        self._persist_cookie_cache(cookies)
        return {
            "ok": ok,
            "runtime": self.runtime,
            "status": status,
            "url": final_url,
            "reason": reason,
            "attempts": attempts,
            "cookies": cookies,
            "counts": self.events.get("counts") or {},
            "response_counts": self.events.get("response_counts") or {},
            "requests": self.events.get("requests") or [],
            "responses": self.events.get("responses") or [],
            "request_failures": self.events.get("request_failures") or [],
            "failed_requests": self.events.get("request_failures") or [],
            "observed_order": self.events.get("observed_order") or [],
            "injected_scripts": self.events.get("injected_scripts") or [],
            "inject_errors": self.events.get("inject_errors") or [],
            "datadog_runtime": self.events.get("datadog_runtime") or {},
            "datadog_runtime_fulfilled": bool(self.events.get("datadog_runtime_fulfilled")),
            "datadog_runtime_fulfilled_reason": self.events.get("datadog_runtime_fulfilled_reason") or "",
            "datadog_probes": self.events.get("datadog_probes") or [],
            "datadog_flushes": self.events.get("datadog_flushes") or [],
            "runtime_signals": self.events.get("runtime_signals") or [],
            "dfp_config": self.events.get("dfp_config") or {},
            "extracted_dfp_config": self.events.get("extracted_dfp_config") or {},
            "required_signals": list(_HEADLESS_OPTIMIZED_REQUIRED_SIGNALS),
            "required_missing": self._required_missing(),
            "blocked_requests": self.events.get("blocked_requests") or [],
            "allowed_requests": self.events.get("allowed_requests") or [],
            "learned_rules": self.events.get("learned_rules") or [],
            "intercept": self.intercept_summary(),
            "debug_log_path": self.debug_log_path,
        }

LocalHeadlessOptimizedSession = LocalHeadlessSession


def run_local_headless_mtr_phase1(
    page_url: str,
    *,
    dfp_config: JsonObject,
    dfp_script_url: str,
    cookies: list[JsonObject] | None = None,
    wait_seconds: float | None = None,
    mtr_wait_seconds: float | None = None,
    datadome_wait_seconds: float | None = None,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
    app_id: str = "IWC_NEXT_CHECKOUT",
    correlation_id: str = "",
    session: LocalHeadlessSession | None = None,
    stage: str = "checkout",
    new_page: bool = False,
    run_mtr: bool = True,
    roxy_browser: JsonObject | None = None,
    runtime: str | None = None,
    document_html: str = "",
    document_status: int = 200,
) -> JsonObject:
    owns_session = session is None
    active_session = session or LocalHeadlessSession(
        cookies=cookies,
        proxy_url=proxy_url,
        browser_profile=browser_profile,
        screen=screen,
        viewport=viewport,
        roxy_browser=roxy_browser,
        runtime=runtime,
    )
    try:
        return active_session.run_mtr_phase1(
            page_url,
            dfp_config=dfp_config,
            dfp_script_url=dfp_script_url,
            wait_seconds=wait_seconds,
            mtr_wait_seconds=mtr_wait_seconds,
            datadome_wait_seconds=datadome_wait_seconds,
            app_id=app_id,
            correlation_id=correlation_id,
            stage=stage,
            new_page=new_page,
            run_mtr=run_mtr,
            document_html=document_html,
            document_status=document_status,
        )
    finally:
        if owns_session:
            active_session.close()


def run_headless_optimized_mtr_phase1(*args: Any, **kwargs: Any) -> JsonObject:
    """Backward-compatible alias; the optimized implementation is now local headless."""
    return run_local_headless_mtr_phase1(*args, **kwargs)


_RUNTIME_FINGERPRINT_SCRIPT = r"""
async () => {
    const ensureBody = () => {
        if (document.body) return document.body;
        const body = document.createElement('body');
        document.documentElement.appendChild(body);
        return body;
    };
    const body = ensureBody();
    const perfNow = () => (performance && performance.now ? performance.now() : Date.now());
    const safe = (fn, fallback = null) => { try { return fn(); } catch (_e) { return fallback; } };
    const hash32 = (text) => {
        let h = 0x811c9dc5;
        const s = String(text || '');
        for (let i = 0; i < s.length; i++) {
            h ^= s.charCodeAt(i);
            h = Math.imul(h, 0x01000193) >>> 0;
        }
        return h.toString(16).padStart(8, '0');
    };
    const rectValue = (element, view = window) => {
        const rect = element.getBoundingClientRect();
        const value = {};
        for (const key of ['x', 'y', 'left', 'right', 'bottom', 'height', 'top', 'width']) {
            if (key in rect) value[key] = rect[key];
        }
        value.font = view.getComputedStyle(element, null).getPropertyValue('font-family');
        return value;
    };
    const canvasSources = () => {
        const start = perfNow();
        const canvas = document.createElement('canvas');
        canvas.width = 1;
        canvas.height = 1;
        const ctx = canvas.getContext('2d');
        if (!ctx || !canvas.toDataURL) {
            return { winding: false, dataUrl: '', geometryDataUrl: 'unsupported', textDataUrl: 'unsupported', dataUrlLength: 0, previewHash: '', ttCanvas: perfNow() - start };
        }
        ctx.rect(0, 0, 10, 10);
        ctx.rect(2, 2, 6, 6);
        const winding = !ctx.isPointInPath(5, 5, 'evenodd');
        canvas.width = 240;
        canvas.height = 60;
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = '#f60';
        ctx.fillRect(100, 1, 62, 20);
        ctx.fillStyle = '#069';
        ctx.font = '11pt "Times New Roman"';
        const text = `Cwm fjordbank gly ${String.fromCharCode(55357, 56835)}`;
        ctx.fillText(text, 2, 15);
        ctx.fillStyle = 'rgba(102, 204, 0, 0.2)';
        ctx.font = '18pt Arial';
        ctx.fillText(text, 4, 45);
        const textDataUrl = canvas.toDataURL();
        canvas.width = 122;
        canvas.height = 110;
        ctx.globalCompositeOperation = 'multiply';
        for (const [fill, x, y] of [['#f2f', 40, 40], ['#2ff', 80, 40], ['#ff2', 60, 80]]) {
            ctx.fillStyle = fill;
            ctx.beginPath();
            ctx.arc(x, y, 40, 0, 2 * Math.PI, true);
            ctx.closePath();
            ctx.fill();
        }
        ctx.fillStyle = '#f9c';
        ctx.arc(60, 60, 60, 0, 2 * Math.PI, true);
        ctx.arc(60, 60, 20, 0, 2 * Math.PI, true);
        ctx.fill('evenodd');
        const geometryDataUrl = canvas.toDataURL();
        return {
            winding,
            dataUrl: textDataUrl,
            geometryDataUrl,
            textDataUrl,
            dataUrlLength: textDataUrl.length,
            previewHash: hash32(textDataUrl),
            ttCanvas: perfNow() - start,
        };
    };
    const webglRenderDataUrl = (gl, canvas) => {
        if (!gl) return '';
        const program = gl.createProgram();
        if (!program) return safe(() => canvas.toDataURL(), '');
        const attach = (type, source) => {
            const shader = gl.createShader(type);
            if (!shader) return;
            gl.shaderSource(shader, source);
            gl.compileShader(shader);
            gl.attachShader(program, shader);
        };
        attach(gl.VERTEX_SHADER, 'attribute vec2 p;uniform float t;void main(){float s=sin(t);float c=cos(t);gl_Position=vec4(p*mat2(c,s,-s,c),1,1);}');
        attach(gl.FRAGMENT_SHADER, 'void main(){gl_FragColor=vec4(1,0,0,1);}');
        gl.linkProgram(program);
        gl.useProgram(program);
        gl.enableVertexAttribArray(0);
        const uniform = gl.getUniformLocation(program, 't');
        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 1, -1, -1, 1, -1]), gl.STATIC_DRAW);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.clearColor(0, 0, 1, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.uniform1f(uniform, 3.65);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
        return safe(() => canvas.toDataURL(), '');
    };
    const webglProbe = () => {
        const start = perfNow();
        const canvas = document.createElement('canvas');
        canvas.width = 256;
        canvas.height = 128;
        let gl = null;
        for (const name of ['webgl', 'experimental-webgl']) {
            gl = safe(() => canvas.getContext(name), null);
            if (gl) break;
        }
        if (!gl) return { extensions: [], params: {}, renderDataUrl: '', ttWebgl: perfNow() - start };
        const dbg = safe(() => gl.getExtension('WEBGL_debug_renderer_info'), null);
        const params = {};
        const names = [
            'ALIASED_LINE_WIDTH_RANGE', 'ALIASED_POINT_SIZE_RANGE', 'ALPHA_BITS', 'BLUE_BITS', 'DEPTH_BITS', 'GREEN_BITS',
            'MAX_COMBINED_TEXTURE_IMAGE_UNITS', 'MAX_CUBE_MAP_TEXTURE_SIZE', 'MAX_FRAGMENT_UNIFORM_VECTORS', 'MAX_RENDERBUFFER_SIZE',
            'MAX_TEXTURE_IMAGE_UNITS', 'MAX_TEXTURE_SIZE', 'MAX_VARYING_VECTORS', 'MAX_VERTEX_ATTRIBS', 'MAX_VERTEX_TEXTURE_IMAGE_UNITS',
            'MAX_VERTEX_UNIFORM_VECTORS', 'RED_BITS', 'STENCIL_BITS', 'MAX_VIEWPORT_DIMS'
        ];
        for (const name of names) {
            params[name] = safe(() => {
                const raw = gl.getParameter(gl[name]);
                return raw && typeof raw.length === 'number' ? Array.from(raw) : raw;
            }, null);
        }
        const shaderPrecisions = {};
        for (const shaderType of ['VERTEX_SHADER', 'FRAGMENT_SHADER']) {
            shaderPrecisions[shaderType] = {};
            for (const precisionType of ['LOW_FLOAT', 'MEDIUM_FLOAT', 'HIGH_FLOAT', 'LOW_INT', 'MEDIUM_INT', 'HIGH_INT']) {
                const precision = safe(() => gl.getShaderPrecisionFormat(gl[shaderType], gl[precisionType]), null);
                shaderPrecisions[shaderType][precisionType] = precision ? { rangeMin: precision.rangeMin, rangeMax: precision.rangeMax, precision: precision.precision } : null;
            }
        }
        return {
            version: safe(() => gl.getParameter(gl.VERSION), ''),
            vendor: safe(() => gl.getParameter(gl.VENDOR), ''),
            renderer: safe(() => gl.getParameter(gl.RENDERER), ''),
            shadingLanguageVersion: safe(() => gl.getParameter(gl.SHADING_LANGUAGE_VERSION), ''),
            unmaskedVendor: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL), '') : '',
            unmaskedRenderer: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL), '') : '',
            extensions: safe(() => gl.getSupportedExtensions(), []) || [],
            params,
            contextAttributes: safe(() => gl.getContextAttributes(), {}) || {},
            shaderPrecisions,
            renderDataUrl: webglRenderDataUrl(gl, canvas),
            ttWebgl: perfNow() - start,
        };
    };
    const audioProbe = async () => {
        const start = perfNow();
        let value = '';
        let error = '';
        let sampleRate = 0;
        try {
            const AC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
            if (AC) {
                const audioCtx = new AC(1, 5000, 44100);
                sampleRate = audioCtx.sampleRate || 44100;
                const osc = audioCtx.createOscillator();
                const compressor = audioCtx.createDynamicsCompressor();
                osc.type = 'triangle';
                osc.frequency.value = 10000;
                compressor.threshold.value = -50;
                compressor.knee.value = 40;
                compressor.ratio.value = 12;
                compressor.attack.value = 0;
                compressor.release.value = 0.25;
                osc.connect(compressor);
                compressor.connect(audioCtx.destination);
                osc.start(0);
                const buffer = await audioCtx.startRendering();
                const data = buffer.getChannelData(0);
                let sum = 0;
                for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i] || 0);
                value = String(sum);
            }
        } catch (e) {
            error = String(e && e.message || e);
        }
        return { value, error, sampleRate, ttAudio: perfNow() - start };
    };
    const mathFingerprintString = () => {
        const cn = Math;
        const an = () => 0;
        const acos = cn.acos || an, acosh = cn.acosh || an, asin = cn.asin || an, asinh = cn.asinh || an;
        const atanh = cn.atanh || an, atan = cn.atan || an, sin = cn.sin || an, sinh = cn.sinh || an;
        const cos = cn.cos || an, cosh = cn.cosh || an, tan = cn.tan || an, tanh = cn.tanh || an;
        const exp = cn.exp || an, expm1 = cn.expm1 || an, log1p = cn.log1p || an;
        const values = {
            acos: acos(.12312423423423424), acosh: acosh(1e308), acoshPf: (h => cn.log(h + cn.sqrt(h * h - 1)))(1e154),
            asin: asin(.12312423423423424), asinh: asinh(1), asinhPf: cn.log(1 + cn.sqrt(2)), atanh: atanh(.5),
            atanhPf: cn.log(3) / 2, atan: atan(.5), sin: sin(-1e300), sinh: sinh(1), sinhPf: cn.exp(1) - 1 / cn.exp(1) / 2,
            cos: cos(10.000000000123), cosh: cosh(1), coshPf: (cn.exp(1) + 1 / cn.exp(1)) / 2, tan: tan(-1e300),
            tanh: tanh(1), tanhPf: (cn.exp(2) - 1) / (cn.exp(2) + 1), exp: exp(1), expm1: expm1(1),
            expm1Pf: cn.exp(1) - 1, log1p: log1p(10), log1pPf: cn.log(11), powPI: cn.pow(cn.PI, -100)
        };
        return Object.keys(values).map(key => `${key}=${values[key]}`).join(',');
    };
    const measureFontWidths = () => {
        const previousWidth = body.style.width;
        const previousWebkitTextSizeAdjust = body.style.webkitTextSizeAdjust;
        const previousTextSizeAdjust = body.style.textSizeAdjust;
        body.style.width = '4000px';
        body.style.webkitTextSizeAdjust = body.style.textSizeAdjust = 'none';
        const container = document.createElement('div');
        container.textContent = [...Array(200)].map(() => 'word').join(' ');
        body.appendChild(container);
        const configs = {
            default: {}, apple: { font: '-apple-system-body' }, serif: { fontFamily: 'serif' },
            sans: { fontFamily: 'sans-serif' }, mono: { fontFamily: 'monospace' }, min: { fontSize: '1px' }, system: { fontFamily: 'system-ui' }
        };
        const widths = {};
        for (const key of Object.keys(configs)) {
            const span = document.createElement('span');
            span.textContent = 'mmMwWLliI0fiflO&1';
            span.style.whiteSpace = 'nowrap';
            for (const styleKey of Object.keys(configs[key])) span.style[styleKey] = configs[key][styleKey];
            container.append(document.createElement('br'), span);
            widths[key] = span.getBoundingClientRect().width;
        }
        body.removeChild(container);
        body.style.width = previousWidth;
        body.style.webkitTextSizeAdjust = previousWebkitTextSizeAdjust;
        body.style.textSizeAdjust = previousTextSizeAdjust;
        return widths;
    };
    const cssSystemColors = () => {
        const aliases = {
            AccentColor: 'ac', AccentColorText: 'act', ActiveText: 'at', ActiveBorder: 'ab', ActiveCaption: 'aca', AppWorkspace: 'aw',
            Background: 'b', ButtonHighlight: 'bh', ButtonShadow: 'bs', ButtonBorder: 'bb', ButtonFace: 'bf', ButtonText: 'bt', FieldText: 'ft',
            GrayText: 'gt', Highlight: 'h', HighlightText: 'ht', InactiveBorder: 'ib', InactiveCaption: 'ic', InactiveCaptionText: 'ict',
            InfoBackground: 'ib', InfoText: 'it', LinkText: 'lt', Mark: 'm', Menu: 'me', Scrollbar: 's', ThreeDDarkShadow: 'tdds',
            ThreeDFace: 'tdf', ThreeDHighlight: 'tdh', ThreeDLightShadow: 'tdls', ThreeDShadow: 'tds', VisitedText: 'vt', Window: 'w',
            WindowFrame: 'wf', WindowText: 'wt', Selecteditem: 'si', Selecteditemtext: 'sit'
        };
        const div = document.createElement('div');
        body.appendChild(div);
        const colors = {};
        for (const colorName of Object.keys(aliases)) {
            div.style.color = colorName;
            colors[aliases[colorName]] = getComputedStyle(div).color;
        }
        body.removeChild(div);
        return colors;
    };
    const emojiRect = () => {
        let text = '';
        for (let code = 128512; code <= 128591; code++) text += String.fromCodePoint(code);
        const span = document.createElement('span');
        span.style.whiteSpace = 'nowrap';
        span.innerText = text;
        body.append(span);
        const value = rectValue(span);
        body.removeChild(span);
        return value;
    };
    const mathmlRect = () => {
        let html = '<mrow><munderover><mmultiscripts><mo>∏</mo>';
        const parts = [['𝔈', 'υ', 'τ', 'ρ', 'σ'], ['𝔇', 'π', 'ο', 'ν', 'ξ'], ['𝔄', 'δ', 'γ', 'α', 'β'], ['𝔅', 'θ', 'η', 'ε', 'ζ'], ['𝔉', 'ω', 'ψ', 'ϕ', 'χ'], ['ℭ', 'μ', 'λ', 'ι', 'κ']];
        const row = (a, b, c, d, e) => `<mmultiscripts><mi>${a}</mi><mi>${b}</mi><mi>${c}</mi><mprescripts></mprescripts><mi>${d}</mi><mi>${e}</mi></mmultiscripts>`;
        for (const item of parts) html += row(...item);
        html += '</munderover></mrow>';
        const node = document.createElement('math');
        node.style.whiteSpace = 'nowrap';
        node.innerHTML = html;
        body.append(node);
        const value = rectValue(node);
        body.removeChild(node);
        return value;
    };
    const browserMarkers = () => {
        const names = ['chrome', 'safari', '__crWeb', '__gCrWeb', 'yandex', '__yb', '__ybro', '__firefox__', '__edgeTrackingPreventionStatistics', 'webkit', 'oprt', 'samsungAr', 'ucweb', 'UCShellJava', 'puffinDevice'];
        return names.filter(name => {
            const value = window[name];
            return value && typeof value === 'object';
        }).sort();
    };
    const performanceNowDeltas = () => {
        if (!performance || !performance.now) return null;
        let t = 1;
        let e = 1;
        let r = performance.now();
        let o = r;
        for (let i = 0; i < 50000; i++) {
            if ((r = o) < (o = performance.now())) {
                const n = o - r;
                if (n > t) {
                    if (n < e) e = n;
                } else if (n < t) {
                    e = t;
                    t = n;
                }
            }
        }
        return [t, e];
    };
    const notificationPermissionMismatch = async () => {
        if (!window.Notification || !navigator.permissions || typeof navigator.permissions.query !== 'function') return false;
        const permission = await navigator.permissions.query({ name: 'notifications' }).catch(() => null);
        return window.Notification.permission === 'denied' && permission && permission.state === 'prompt';
    };
    const navigatorPrototypeMarkers = () => {
        const names = Object.getOwnPropertyNames(Navigator.prototype);
        const wanted = new Set(['onLine', 'webdriver', 'getGamepads']);
        return { l: names.length, p: names.map((name, index) => ({ i: index, n: name })).filter(item => wanted.has(item.n)) };
    };
    const plugins = Array.from(navigator.plugins || []).map(plugin => ({
        name: plugin.name,
        filename: plugin.filename,
        description: plugin.description,
        mimeTypes: Array.from(plugin).map(mime => ({ type: mime.type, suffixes: mime.suffixes, description: mime.description }))
    }));
    const mimeTypeCount = navigator.mimeTypes && typeof navigator.mimeTypes.length !== 'undefined' ? navigator.mimeTypes.length : 0;
    const storageEstimate = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate().catch(() => ({})) : {};
    const uaData = navigator.userAgentData ? {
        brands: navigator.userAgentData.brands || [],
        mobile: navigator.userAgentData.mobile,
        platform: navigator.userAgentData.platform,
        highEntropy: await navigator.userAgentData.getHighEntropyValues([
            'architecture', 'bitness', 'brands', 'fullVersionList', 'mobile',
            'model', 'platform', 'platformVersion', 'uaFullVersion', 'wow64'
        ]).catch(() => ({}))
    } : null;
    const canvas = canvasSources();
    const webgl = webglProbe();
    const audio = await audioProbe();
    const memory = performance.memory ? {
        usedJSHeapSize: performance.memory.usedJSHeapSize,
        totalJSHeapSize: performance.memory.totalJSHeapSize,
        jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
    } : null;
    const sessionStorageUuid = safe(() => {
        const uuid = crypto && typeof crypto.randomUUID === 'function'
            ? crypto.randomUUID()
            : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
                const r = Math.random() * 16 | 0;
                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });
        sessionStorage.setItem('__paypal_headless_probe_uuid', uuid);
        return uuid;
    }, '');
    return {
        capturedAt: Date.now(),
        userAgent: navigator.userAgent,
        appVersion: navigator.appVersion,
        platform: navigator.platform,
        vendor: navigator.vendor,
        productSub: navigator.productSub,
        language: navigator.language,
        languages: Array.from(navigator.languages || []),
        cookieEnabled: navigator.cookieEnabled,
        onLine: navigator.onLine,
        hardwareConcurrency: navigator.hardwareConcurrency || 0,
        deviceMemory: navigator.deviceMemory || 0,
        doNotTrack: navigator.doNotTrack,
        maxTouchPoints: navigator.maxTouchPoints || 0,
        webdriver: navigator.webdriver,
        uaData,
        screen: {
            width: screen.width,
            height: screen.height,
            availWidth: screen.availWidth,
            availHeight: screen.availHeight,
            colorDepth: screen.colorDepth,
            pixelDepth: screen.pixelDepth,
        },
        window: {
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            devicePixelRatio: window.devicePixelRatio,
        },
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        timezoneOffsetMinutes: new Date().getTimezoneOffset(),
        connection: navigator.connection ? {
            effectiveType: navigator.connection.effectiveType,
            rtt: navigator.connection.rtt,
            downlink: navigator.connection.downlink,
            saveData: navigator.connection.saveData,
        } : null,
        plugins,
        mimeTypeCount,
        pdfViewerEnabled: Boolean(navigator.pdfViewerEnabled),
        canvas,
        webgl,
        audio,
        memory,
        timing: {
            ttCanvas: canvas.ttCanvas || 0,
            ttWebglBasic: webgl.ttWebgl || 0,
            ttWebglExt: webgl.ttWebgl || 0,
            ttAudio: audio.ttAudio || 0,
        },
        fontWidths: measureFontWidths(),
        mathFingerprintSource: mathFingerprintString(),
        mathmlRect: mathmlRect(),
        emojiRect: emojiRect(),
        cssSystemColors: cssSystemColors(),
        browserMarkers: browserMarkers(),
        browserComponents: { wv: false, wvp: false, pr: false, ck: false, pt: false, fp: false },
        windowPropertyMarkers: Object.keys(window).slice(0, 80),
        navigatorPrototypeMarkers: navigatorPrototypeMarkers(),
        storageQuota: Math.trunc(storageEstimate.quota || 0),
        performanceTimeOrigin: performance.timeOrigin || (Date.now() - performance.now()),
        performanceNowDeltas: performanceNowDeltas(),
        notificationPermissionMismatch: await notificationPermissionMismatch(),
        sessionStorageUuid,
    };
}
"""


def capture_runtime_fingerprint_with_local_headless(
    *,
    wait_seconds: float = 12.0,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> JsonObject:
    sync_playwright = _load_sync_playwright()
    wait_ms = max(1000, int(wait_seconds * 1000))
    with sync_playwright() as playwright:
        browser = _launch_browser(playwright, proxy_url)
        try:
            context = browser.new_context(
                **_context_options(
                    browser_profile=browser_profile,
                    screen=screen,
                    viewport=viewport,
                )
            )
            try:
                context.add_init_script(
                    _stealth_init_script(
                        browser_profile=browser_profile,
                        screen=screen,
                        viewport=viewport,
                    )
                )
            except Exception as exc:
                logger.debug("Local headless fingerprint stealth init install failed: {}", exc)
            page = context.new_page()
            _apply_cdp_stealth_overrides(context, page, browser_profile=browser_profile)
            try:
                _ = page.goto("about:blank", wait_until="domcontentloaded", timeout=wait_ms)
            except Exception:
                pass
            value = page.evaluate(_RUNTIME_FINGERPRINT_SCRIPT)
            if not isinstance(value, dict):
                raise LocalHeadlessRuntimeError("local headless fingerprint probe returned no data")
            runtime = _runtime_profile_from_js(cast(JsonObject, value), browser_profile)
            logger.info(
                "Local headless fingerprint captured: ua={} screen={}x{} viewport={}x{}",
                str(cast(JsonObject, runtime["browser_profile"]).get("user_agent") or "")[:80],
                cast(JsonObject, runtime["screen"]).get("width"),
                cast(JsonObject, runtime["screen"]).get("height"),
                cast(JsonObject, runtime["viewport"]).get("width"),
                cast(JsonObject, runtime["viewport"]).get("height"),
            )
            return runtime
        finally:
            browser.close()


def capture_local_headless_runtime_profile(
    *,
    wait_seconds: float = 12.0,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> JsonObject:
    return capture_runtime_fingerprint_with_local_headless(
        wait_seconds=wait_seconds,
        proxy_url=proxy_url,
        browser_profile=browser_profile,
        screen=screen,
        viewport=viewport,
    )


def _extract_datadome_clientid_from_html(html: str) -> str:
    if "datadome" not in (html or "").lower():
        return ""
    import html as html_lib
    import re

    for pattern in (
        r"\bc\s*=\s*['\"]([^'\"]{40,})['\"][^<]{0,600}datadome",
        r"x-datadome-clientid['\"]?\s*[:=]\s*['\"]([^'\"]{40,})",
    ):
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            return html_lib.unescape(match.group(1))
    return ""


def _datadome_challenge_marker_hits(status: int, html: str) -> list[str]:
    lower = (html or "").lower()
    if not lower:
        return ["empty_403_429"] if status in {403, 429} else []
    # DataDome bootstrap code can appear in a perfectly normal PayPal document
    # (including geo.ddc/captcha endpoint strings used by c.js).  Treat only
    # active block/challenge page markers as HTML-level challenges for HTTP 200
    # documents; generic captcha endpoint URLs are considered challenge proof
    # only when the protected navigation itself returned 403/429.
    active_markers = (
        "device_check_redirect_to_slider",
        "slider_closed",
        "ddc-captcha",
        "datadome captcha",
        "block_page_loaded",
        "edge_bot_protection",
        "captcha-delivery.com/captcha",
        "data-ddcid",
    )
    hits = [marker for marker in active_markers if marker in lower]
    if status in {403, 429}:
        if "datadome" in lower and any(marker in lower for marker in ("blocked", "forbidden", "access denied")):
            hits.append("datadome_block_text")
        status_markers = (
            "geo.ddc.paypal.com",
            "static.ddc.paypal.com/captcha",
            "/interstitial/",
            "/captcha/",
            "captcha-delivery",
            "datadome",
            "captcha",
            "access denied",
            "forbidden",
        )
        hits.extend(marker for marker in status_markers if marker in lower)
    seen: set[str] = set()
    ordered: list[str] = []
    for marker in hits:
        if marker not in seen:
            seen.add(marker)
            ordered.append(marker)
    return ordered


def _datadome_challenge_present(status: int, html: str) -> bool:
    return bool(_datadome_challenge_marker_hits(status, html))


def solve_datadome_with_local_headless(
    url: str,
    *,
    cookies: list[JsonObject] | None = None,
    wait_seconds: float = 12.0,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> JsonObject:
    """Local headless DataDome runner.

    The former legacy implementation has been removed; this now delegates to
    the shared local-headless session and reports runtime=headless.
    """
    session = LocalHeadlessSession(
        cookies=cookies,
        proxy_url=proxy_url,
        browser_profile=browser_profile,
        screen=screen,
        viewport=viewport,
        runtime="headless",
    )
    try:
        return session.solve_datadome(url, wait_seconds=wait_seconds)
    finally:
        session.close()


def _extract_mtr_response_data(value: object) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    typed_value = cast(JsonObject, value)
    raw_products = typed_value.get("products")
    products = cast(JsonObject, raw_products) if isinstance(raw_products, dict) else {}
    raw_identification = products.get("identification")
    identification = cast(JsonObject, raw_identification) if isinstance(raw_identification, dict) else {}
    raw_data = identification.get("data")
    data = cast(JsonObject, raw_data) if isinstance(raw_data, dict) else {}
    raw_result = data.get("result")
    result = cast(JsonObject, raw_result) if isinstance(raw_result, dict) else {}
    visitor_token = (
        data.get("visitorToken")
        or data.get("visitor_token")
        or result.get("visitorToken")
        or result.get("visitor_token")
        or ""
    )
    return {
        "requestId": typed_value.get("requestId") or "",
        "sealedResult": typed_value.get("sealedResult") or "",
        "visitorToken": visitor_token,
        "raw": typed_value,
    }


def _read_live_dfp_config(page: _Page) -> JsonObject:
    try:
        value = page.evaluate(
            r"""() => {
                const parseMaybe = (value) => {
                    if (!value) return null;
                    let current = value;
                    for (let i = 0; i < 5; i++) {
                        if (current && typeof current === 'object') return current;
                        if (typeof current !== 'string') return null;
                        const text = current.trim();
                        if (!text) return null;
                        try { current = JSON.parse(text); continue; } catch (e) {}
                        const unescaped = text
                            .replace(/\\u0022/g, '"')
                            .replace(/\\\//g, '/')
                            .replace(/\\+"/g, '"');
                        if (unescaped === text) return null;
                        current = unescaped;
                    }
                    return current && typeof current === 'object' ? current : null;
                };
                const fromWindow = parseMaybe(window.PAYPAL && window.PAYPAL.dfpData);
                if (fromWindow) return { source: 'window.PAYPAL.dfpData', config: fromWindow };
                const node = document.getElementById('dfpconfig');
                const fromNode = parseMaybe(node && node.textContent);
                if (fromNode) return { source: 'script#dfpconfig', config: fromNode };
                for (const script of Array.from(document.scripts || [])) {
                    const text = script.textContent || '';
                    if (!text.includes('dfpData') && !text.includes('clientMetaDataId')) continue;
                    const match = text.match(/dfpData\s*[:=]\s*(\{[\s\S]{20,1200}?\})\s*[,;]/);
                    const parsed = parseMaybe(match && match[1]);
                    if (parsed) return { source: 'inline_script', config: parsed };
                }
                return null;
            }"""
        )
        return cast(JsonObject, value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _inject_mtr_script(page: _Page, *, dfp_config: JsonObject, dfp_script_url: str) -> str:
    try:
        _ = page.evaluate(
            """(config) => {
                window.PAYPAL = window.PAYPAL || {};
                window.PAYPAL.dfpData = Object.assign({}, config);
                let node = document.getElementById('dfpconfig');
                if (!node) {
                    node = document.createElement('script');
                    node.id = 'dfpconfig';
                    node.type = 'application/json';
                    document.head.appendChild(node);
                }
                node.textContent = JSON.stringify(config);
                try {
                    sessionStorage.removeItem('4g3-fd7gc5k5');
                    sessionStorage.removeItem('4g3-fd7gc5k5-CMID');
                } catch (e) {}
            }""",
            dfp_config,
        )
        _ = page.add_script_tag(url=dfp_script_url)
        return ""
    except Exception as exc:
        return str(exc)


def run_mtr_with_local_headless(
    page_url: str,
    *,
    dfp_config: JsonObject,
    dfp_script_url: str,
    cookies: list[JsonObject] | None = None,
    wait_seconds: float = 20.0,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
) -> JsonObject:
    """Run MTR through the shared local-headless implementation.

    The old standalone local-headless MTR runner has been removed.
    """
    return run_local_headless_mtr_phase1(
        page_url,
        dfp_config=dfp_config,
        dfp_script_url=dfp_script_url,
        cookies=cookies,
        wait_seconds=wait_seconds,
        mtr_wait_seconds=wait_seconds,
        proxy_url=proxy_url,
        browser_profile=browser_profile,
        screen=screen,
        viewport=viewport,
        runtime="headless",
    )


def run_phase1_risk_with_local_headless(
    page_url: str,
    *,
    cookies: list[JsonObject] | None = None,
    wait_seconds: float = 18.0,
    proxy_url: str | None = None,
    browser_profile: JsonObject | None = None,
    screen: JsonObject | None = None,
    viewport: JsonObject | None = None,
    app_id: str = "IWC_NEXT_CHECKOUT",
    correlation_id: str = "",
) -> JsonObject:
    """Run browser-risk through the shared local-headless implementation.

    Legacy local-headless risk execution was removed; this now uses the shared
    local-headless logic with MTR disabled.
    """
    return run_local_headless_mtr_phase1(
        page_url,
        dfp_config={},
        dfp_script_url="",
        cookies=cookies,
        wait_seconds=wait_seconds,
        mtr_wait_seconds=0.0,
        proxy_url=proxy_url,
        browser_profile=browser_profile,
        screen=screen,
        viewport=viewport,
        app_id=app_id,
        correlation_id=correlation_id,
        stage="signup_context",
        new_page=True,
        run_mtr=False,
        runtime="headless",
    )
