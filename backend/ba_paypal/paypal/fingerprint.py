"""Device fingerprint generation for PayPal anti-fraud system."""
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import base64
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Iterable
from typing import TypeVar
from typing import cast

from paypal.models import generate_eteid
from config import SCREEN, USER_AGENT, VIEWPORT, BROWSER_PROFILE, FINGERPRINT_SOURCE, DATADOME_MODE


PAYPAL_RISK_BASE = "https://c.paypal.com/v1/r/d/b"
PAYPAL_RISK_P3 = "https://c6.paypal.com/v1/r/d/b/p3"
PAYPAL_DA_FB_FP_JS = "https://c.paypal.com/da/r/fb_fp.js"
PAYPAL_DFP_JS_LEGACY = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
PAYPAL_DFP_JS_RDA = "https://www.paypalobjects.com/rdaAssets/fraudnet/ext/dfp.js"
PAYPAL_DDBM_TAGS_JS = "https://ddbm2.paypal.com/tags.js"
PAYPAL_DI_LOG = "https://www.paypal.com/identity/di/log"

_T = TypeVar("_T")
JsonDict = dict[str, Any]

PDF_PLUGINS: list[JsonDict] = [
    {
        "mT": [{"t": "application/pdf", "s": "pdf"}, {"t": "text/pdf", "s": "pdf"}],
        "n": name,
        "v": "",
        "fn": "internal-pdf-viewer",
        "d": "Portable Document Format",
    }
    for name in [
        "Chrome PDF Viewer",
        "Chromium PDF Viewer",
        "Microsoft Edge PDF Viewer",
        "PDF Viewer",
        "WebKit built-in PDF",
    ]
]


def _compact_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _load_dotenv_value(name: str) -> str:
    """Read one value from local .env without adding a runtime dependency."""
    if os.getenv(name):
        return os.getenv(name, "").strip()
    from pathlib import Path

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


def _cookie_value(session, name: str) -> str | None:
    try:
        for cookie in session.client.cookies.jar:
            if getattr(cookie, "name", "") == name and getattr(cookie, "value", ""):
                return cookie.value
    except Exception:
        return None
    return None


def _risk_headers(referer: str, *, content_type: str | None = "application/json") -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Origin": "https://www.paypal.com",
        "Referer": referer or "https://www.paypal.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _random_hex(length: int = 64) -> str:
    seed = f"{time.time_ns()}:{random.random()}:{generate_eteid()}".encode()
    digest = hashlib.sha256(seed).hexdigest()
    while len(digest) < length:
        digest += hashlib.sha256(digest.encode()).hexdigest()
    return digest[:length]


_GPU_PROFILES: list[dict[str, str]] = [
    {
        "gpu_vendor": "Google Inc. (Intel)",
        "gpu_renderer": "ANGLE (Intel, Intel(R) UHD Graphics 620, OpenGL 4.1)",
        "webgl_vendor": "WebKit",
        "webgl_renderer": "WebKit WebGL",
    },
    {
        "gpu_vendor": "Google Inc. (AMD)",
        "gpu_renderer": "ANGLE (AMD, AMD Radeon RX 580, OpenGL 4.1)",
        "webgl_vendor": "WebKit",
        "webgl_renderer": "WebKit WebGL",
    },
    {
        "gpu_vendor": "Google Inc. (NVIDIA Corporation)",
        "gpu_renderer": "ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1650/PCIe/SSE2, OpenGL ES 3.2)",
        "webgl_vendor": "WebKit",
        "webgl_renderer": "WebKit WebGL",
    },
    {
        "gpu_vendor": "Google Inc. (NVIDIA Corporation)",
        "gpu_renderer": "ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3060/PCIe/SSE2, OpenGL ES 3.2)",
        "webgl_vendor": "WebKit",
        "webgl_renderer": "WebKit WebGL",
    },
]

_CHROME_VERSION_CHOICES: list[tuple[str, int]] = [
    ("150.0.7871.46", 35),
    ("150.0.7871.42", 25),
    ("149.0.7834.83", 20),
    ("149.0.7834.62", 12),
    ("148.0.7772.122", 8),
]

_MAINSTREAM_SCREEN_CHOICES: list[tuple[JsonDict, int]] = [
    ({"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040}, 30),
    ({"width": 1366, "height": 768, "availWidth": 1366, "availHeight": 728}, 22),
    ({"width": 1536, "height": 864, "availWidth": 1536, "availHeight": 824}, 20),
    ({"width": 1440, "height": 900, "availWidth": 1440, "availHeight": 860}, 12),
    ({"width": 1600, "height": 900, "availWidth": 1600, "availHeight": 860}, 10),
    ({"width": 1280, "height": 720, "availWidth": 1280, "availHeight": 680}, 8),
    ({"width": 1280, "height": 800, "availWidth": 1280, "availHeight": 760}, 7),
    ({"width": 1680, "height": 1050, "availWidth": 1680, "availHeight": 1010}, 6),
    ({"width": 1920, "height": 1200, "availWidth": 1920, "availHeight": 1160}, 5),
    ({"width": 2560, "height": 1440, "availWidth": 2560, "availHeight": 1400}, 5),
]


def _random_mainstream_screen() -> JsonDict:
    screens = [screen for screen, _weight in _MAINSTREAM_SCREEN_CHOICES]
    weights = [weight for _screen, weight in _MAINSTREAM_SCREEN_CHOICES]
    return dict(random.choices(screens, weights=weights, k=1)[0])


_WEBGL_EXTENSION_SETS: dict[str, list[str]] = {
    "intel": [
        "ANGLE_instanced_arrays",
        "EXT_blend_minmax",
        "EXT_color_buffer_half_float",
        "EXT_disjoint_timer_query",
        "EXT_float_blend",
        "EXT_frag_depth",
        "EXT_shader_texture_lod",
        "EXT_texture_compression_bptc",
        "EXT_texture_compression_rgtc",
        "EXT_texture_filter_anisotropic",
        "KHR_parallel_shader_compile",
        "OES_element_index_uint",
        "OES_fbo_render_mipmap",
        "OES_standard_derivatives",
        "OES_texture_float",
        "OES_texture_float_linear",
        "OES_texture_half_float",
        "OES_texture_half_float_linear",
        "OES_vertex_array_object",
        "WEBGL_color_buffer_float",
        "WEBGL_compressed_texture_s3tc",
        "WEBGL_compressed_texture_s3tc_srgb",
        "WEBGL_debug_renderer_info",
        "WEBGL_debug_shaders",
        "WEBGL_depth_texture",
        "WEBGL_draw_buffers",
        "WEBGL_lose_context",
        "WEBGL_multi_draw",
    ],
    "amd": [
        "ANGLE_instanced_arrays",
        "EXT_blend_minmax",
        "EXT_color_buffer_float",
        "EXT_color_buffer_half_float",
        "EXT_disjoint_timer_query",
        "EXT_float_blend",
        "EXT_frag_depth",
        "EXT_shader_texture_lod",
        "EXT_texture_compression_bptc",
        "EXT_texture_compression_rgtc",
        "EXT_texture_filter_anisotropic",
        "KHR_parallel_shader_compile",
        "OES_element_index_uint",
        "OES_fbo_render_mipmap",
        "OES_standard_derivatives",
        "OES_texture_float",
        "OES_texture_float_linear",
        "OES_texture_half_float",
        "OES_texture_half_float_linear",
        "OES_vertex_array_object",
        "WEBGL_color_buffer_float",
        "WEBGL_compressed_texture_s3tc",
        "WEBGL_compressed_texture_s3tc_srgb",
        "WEBGL_debug_renderer_info",
        "WEBGL_debug_shaders",
        "WEBGL_depth_texture",
        "WEBGL_draw_buffers",
        "WEBGL_lose_context",
        "WEBGL_multi_draw",
    ],
    "nvidia": [
        "ANGLE_instanced_arrays",
        "EXT_blend_minmax",
        "EXT_color_buffer_float",
        "EXT_color_buffer_half_float",
        "EXT_disjoint_timer_query",
        "EXT_float_blend",
        "EXT_frag_depth",
        "EXT_shader_texture_lod",
        "EXT_texture_compression_bptc",
        "EXT_texture_compression_rgtc",
        "EXT_texture_filter_anisotropic",
        "KHR_parallel_shader_compile",
        "OES_element_index_uint",
        "OES_fbo_render_mipmap",
        "OES_standard_derivatives",
        "OES_texture_float",
        "OES_texture_float_linear",
        "OES_texture_half_float",
        "OES_texture_half_float_linear",
        "OES_vertex_array_object",
        "WEBGL_color_buffer_float",
        "WEBGL_compressed_texture_s3tc",
        "WEBGL_compressed_texture_s3tc_srgb",
        "WEBGL_debug_renderer_info",
        "WEBGL_debug_shaders",
        "WEBGL_depth_texture",
        "WEBGL_draw_buffers",
        "WEBGL_lose_context",
        "WEBGL_multi_draw",
    ],
}


_WINDOWS_FONT_STACK = [
    "Arial",
    "Calibri",
    "Cambria",
    "Cambria Math",
    "Comic Sans MS",
    "Consolas",
    "Courier New",
    "Georgia",
    "Impact",
    "Lucida Console",
    "Malgun Gothic",
    "Microsoft JhengHei",
    "Microsoft YaHei",
    "Segoe UI",
    "Segoe UI Emoji",
    "Segoe UI Symbol",
    "Tahoma",
    "Times New Roman",
    "Trebuchet MS",
    "Verdana",
]

_LINUX_FONT_STACK = [
    "Arial",
    "Courier New",
    "DejaVu Sans",
    "DejaVu Sans Mono",
    "DejaVu Serif",
    "Liberation Mono",
    "Liberation Sans",
    "Liberation Serif",
    "Noto Color Emoji",
    "Noto Sans",
    "Noto Serif",
    "Roboto",
    "Ubuntu",
]

_MAC_FONT_STACK = [
    "Arial",
    "Courier New",
    "Georgia",
    "Helvetica Neue",
    "Menlo",
    "Monaco",
    "San Francisco",
    "Times New Roman",
    "Verdana",
]


def _digest_bytes(*parts: object, length: int = 32) -> bytes:
    material = "|".join(_compact_json(part) if isinstance(part, (dict, list, tuple)) else str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    output = bytearray(digest)
    while len(output) < length:
        digest = hashlib.sha256(digest + material.encode("utf-8")).digest()
        output.extend(digest)
    return bytes(output[:length])


def _stable_hex(*parts: object, length: int = 64) -> str:
    raw = _digest_bytes(*parts, length=(length + 1) // 2)
    return raw.hex()[:length]


def _stable_ratio(*parts: object) -> float:
    raw = _digest_bytes(*parts, length=8)
    return int.from_bytes(raw, "big") / float(2**64 - 1)


def _weighted_choice(seed_parts: tuple[object, ...], choices: Sequence[tuple[_T, int]]) -> _T:
    total = sum(max(0, weight) for _, weight in choices)
    if total <= 0:
        return choices[0][0]
    point = _stable_ratio(*seed_parts) * total
    upto = 0.0
    for value, weight in choices:
        upto += max(0, weight)
        if point <= upto:
            return value
    return choices[-1][0]


def _gpu_family(profile: Mapping[str, Any]) -> str:
    text = f"{profile.get('gpu_vendor', '')} {profile.get('gpu_renderer', '')}".lower()
    if "nvidia" in text or "geforce" in text or "rtx" in text or "gtx" in text:
        return "nvidia"
    if "amd" in text or "radeon" in text:
        return "amd"
    return "intel"


def _font_stack_for_profile(profile: Mapping[str, Any]) -> list[str]:
    platform = str(profile.get("platform") or "").lower()
    if "win" in platform:
        return list(_WINDOWS_FONT_STACK)
    if "mac" in platform:
        return list(_MAC_FONT_STACK)
    return list(_LINUX_FONT_STACK)


def _random_chrome_full_version() -> str:
    total = sum(weight for _version, weight in _CHROME_VERSION_CHOICES)
    point = random.randint(1, total)
    upto = 0
    for version, weight in _CHROME_VERSION_CHOICES:
        upto += weight
        if point <= upto:
            return version
    return _CHROME_VERSION_CHOICES[-1][0]


def _chrome_major_from_version(version: str, default: int = 150) -> int:
    try:
        return int(str(version).split(".", 1)[0])
    except Exception:
        return default


def _user_agent_with_chrome_version(base_user_agent: str, chrome_full_version: str) -> str:
    if not base_user_agent:
        base_user_agent = USER_AGENT
    return re.sub(
        r"((?:Chrome|Chromium|HeadlessChrome)/)[0-9.]+",
        rf"\g<1>{chrome_full_version}",
        base_user_agent,
    )


def _value_is_present(value: object) -> bool:
    return value is not None and value != ""


def _float_or(value: object, fallback: float) -> float:
    if isinstance(value, (str, int, float)) and _value_is_present(value):
        try:
            return float(value)
        except Exception:
            pass
    return fallback


def _bool_or(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _js_heap_limit_for_profile(profile: Mapping[str, Any], salt: str) -> int:
    device_memory = int(profile.get("device_memory") or 8)
    # 64-bit desktop Chromium commonly exposes values around 4.29-4.49GB via
    # performance.memory.jsHeapSizeLimit.  Pick a stable bucket for the whole
    # virtual device instead of choosing independently for every request.
    if device_memory >= 8:
        choices = [
            (4_294_705_152, 35),
            (4_395_630_592, 45),
            (4_496_293_888, 20),
        ]
    else:
        choices = [
            (2_147_352_576, 60),
            (3_221_225_472, 30),
            (4_294_705_152, 10),
        ]
    return int(_weighted_choice(("jsHeapSizeLimit", salt, profile.get("chrome_major")), choices))


def _build_device_fingerprint(profile: Mapping[str, Any], screen: Mapping[str, Any], viewport: Mapping[str, Any]) -> JsonDict:
    """Build coherent synthetic values for the fields sent by FraudNet.

    These values still come from Python, but they are now derived from one
    per-session device salt plus the same browser/GPU/screen/font material.
    Real browser values are deterministic for a given runtime; independent
    random bytes per field are less plausible because canvas, WebGL, audio and
    heap limits all depend on the same OS/browser/GPU stack.
    """
    salt = _random_hex(32)
    fonts = _font_stack_for_profile(profile)
    family = _gpu_family(profile)
    extensions = _WEBGL_EXTENSION_SETS.get(family, _WEBGL_EXTENSION_SETS["intel"])
    render_material = {
        "ua": profile.get("user_agent"),
        "platform": profile.get("platform"),
        "chrome": profile.get("chrome_full_version"),
        "language": profile.get("language"),
        "screen": screen,
        "viewport": viewport,
        "dpr": profile.get("device_pixel_ratio"),
        "gpu_vendor": profile.get("gpu_vendor"),
        "gpu_renderer": profile.get("gpu_renderer"),
        "fonts": fonts,
        "salt": salt,
    }
    canvas_h = base64.b64encode(
        _digest_bytes("canvas:dataURL:fingerprintjs-like", render_material, length=42)
    ).decode("ascii")
    cv_sig = _stable_hex("canvas:cvSig:webgl-canvas", render_material, length=64)
    webgl_ext_hash = _stable_hex(
        "webgl:supportedExtensions",
        {
            "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
            "vendor": profile.get("webgl_vendor"),
            "renderer": profile.get("webgl_renderer"),
            "unmaskedVendor": profile.get("gpu_vendor"),
            "unmaskedRenderer": profile.get("gpu_renderer"),
            "extensions": extensions,
            "chrome": profile.get("chrome_full_version"),
            "salt": salt,
        },
        length=64,
    )
    # OfflineAudioContext + oscillator + DynamicsCompressor outputs a small
    # stable numeric signature.  Keep it clustered around Chromium desktop
    # values, with a per-device offset instead of unconstrained randomness.
    audio_center = 0.000095
    audio_spread = 0.000026
    audio_ratio = _stable_ratio("audio", render_material)
    audio_val = audio_center + ((audio_ratio * 2.0) - 1.0) * audio_spread
    audio_val = max(0.000055, min(0.000135, audio_val))

    hardware = max(2, int(profile.get("hardware_concurrency") or 8))
    perf_factor = max(0.55, min(1.45, 8 / hardware))
    tt_dfp = 24.0 + _stable_ratio("timing:dfp", salt) * 20.0 * perf_factor
    tt_canvas = max(6.0, tt_dfp - (0.6 + _stable_ratio("timing:canvas", salt) * 2.6))
    tt_webgl_basic = 7.5 + _stable_ratio("timing:webglBasic", salt) * 13.0 * perf_factor
    tt_webgl_ext = 10.0 + _stable_ratio("timing:webglExt", salt) * 15.0 * perf_factor
    tt_storage = 0.0 if _stable_ratio("timing:storage", salt) < 0.55 else 0.09999999776482582
    tt_math = 0.10000000149011612 if _stable_ratio("timing:math", salt) < 0.8 else 0.19999999925494194
    used_heap = int(18_000_000 + _stable_ratio("heap:used", salt) * 24_000_000)
    total_heap = int(max(used_heap + 18_000_000, 56_000_000 + _stable_ratio("heap:total", salt) * 42_000_000))

    return {
        "source": str(profile.get("fingerprint_source") or "random"),
        "device_salt": salt,
        "canvas_h": canvas_h,
        "cv_sig": cv_sig,
        "webgl_ext_hash": webgl_ext_hash,
        "audio_val": f"{audio_val:.10f}",
        "js_heap_size_limit": _js_heap_limit_for_profile(profile, salt),
        "webgl_extensions": extensions,
        "font_hash": _stable_hex("fonts", fonts, profile.get("platform"), salt, length=32),
        "timings": {
            "tt_dfp": tt_dfp,
            "tt_canvas": tt_canvas,
            "tt_webgl_basic": tt_webgl_basic,
            "tt_webgl_ext": tt_webgl_ext,
            "tt_storage": tt_storage,
            "tt_math": tt_math,
        },
        "js_memory": {
            "used": used_heap,
            "total": total_heap,
        },
    }


def _normalize_fingerprint_source(source: str | None = None) -> str:
    value = (
        (source or "").strip()
        or _load_dotenv_value("PAYPAL_FINGERPRINT_SOURCE")
        or _load_dotenv_value("FINGERPRINT_SOURCE")
        or str(FINGERPRINT_SOURCE or "")
    ).strip().lower().replace("-", "_")
    aliases = {
        "": "random",
        "program": "random",
        "python": "random",
        "synthetic": "random",
        "random": "random",
        "roxy": "roxy",
        "roxy_browser": "roxy",
        "roxybrowser": "roxy",
        "browser": "roxy",
        "headless": "headless",
        "headless_optimized": "headless",
        "optimized_headless": "headless",
        "local_headless": "headless",
        "playwright": "headless",
        "local_playwright": "headless",
        "auto": "auto",
    }
    return aliases.get(value, "random")


def _normalize_datadome_mode() -> str:
    value = (
        _load_dotenv_value("PAYPAL_DATADOME_MODE")
        or _load_dotenv_value("DATADOME_MODE")
        or str(DATADOME_MODE or "")
    ).strip().lower().replace("-", "_")
    aliases = {
        "": "protocol",
        "protocol": "protocol",
        "edge": "protocol",
        "header": "protocol",
        "headers": "protocol",
        "clientid": "protocol",
        "roxy": "roxy",
        "browser": "roxy",
        "real_browser": "roxy",
        "auto": "auto",
        "off": "off",
        "none": "off",
        "disabled": "off",
        "disable": "off",
        "0": "off",
    }
    return aliases.get(value, "protocol")


def _roxy_fallback_to_random_enabled() -> bool:
    value = (
        _load_dotenv_value("PAYPAL_ROXY_FINGERPRINT_FALLBACK")
        or _load_dotenv_value("PAYPAL_FINGERPRINT_FALLBACK")
        or "random"
    ).strip().lower()
    strict = _load_dotenv_value("PAYPAL_ROXY_FINGERPRINT_STRICT").strip().lower()
    if strict in {"1", "true", "yes", "on"}:
        return False
    return value in {"", "1", "true", "yes", "on", "random", "program", "python", "synthetic"}


def _headless_fallback_to_random_enabled() -> bool:
    value = (
        _load_dotenv_value("PAYPAL_HEADLESS_FINGERPRINT_FALLBACK")
        or _load_dotenv_value("PAYPAL_LOCAL_HEADLESS_FINGERPRINT_FALLBACK")
        or _load_dotenv_value("PAYPAL_FINGERPRINT_FALLBACK")
        or "random"
    ).strip().lower()
    strict = _load_dotenv_value("PAYPAL_HEADLESS_FINGERPRINT_STRICT").strip().lower()
    if strict in {"1", "true", "yes", "on"}:
        return False
    return value in {"", "1", "true", "yes", "on", "random", "program", "python", "synthetic"}


def _generate_synthetic_runtime_profile() -> JsonDict:
    """Generate one stable synthetic browser/device profile for a single protocol run."""
    randomize = os.getenv(
        "PAYPAL_RANDOMIZE_BROWSER_PROFILE",
        "1",
    ).strip().lower() not in {"0", "false", "no", "off", "fixed", "disabled", "disable"}
    if randomize:
        chrome_full_version = _random_chrome_full_version()
        chrome_major = _chrome_major_from_version(chrome_full_version)
    else:
        chrome_major = int(BROWSER_PROFILE.get("chrome_major") or 150)
        chrome_full_version = str(
            BROWSER_PROFILE.get("chrome_full_version")
            or f"{chrome_major}.0.0.0"
        )
    if randomize:
        screen = _random_mainstream_screen()
        screen.update({"colorDepth": 24, "pixelDepth": 24})
        screen_width = cast(int, screen["width"])
        screen_height = cast(int, screen["height"])
        avail_height = cast(int, screen.get("availHeight") or screen_height)
        viewport: JsonDict = {
            "width": max(980, min(screen_width - 16, screen_width - random.randint(120, 360))),
            "height": max(560, min(avail_height - 40, screen_height - 96, avail_height - random.randint(70, 170))),
        }
        gpu: JsonDict = random.choice(_GPU_PROFILES).copy()
        hardware_concurrency = random.choice([4, 6, 8, 8, 12])
        device_pixel_ratio = random.choice([1, 1.25, 1.5, 2])
        connection_rtt = str(random.choice([100, 125, 150, 175, 200]))
        connection_downlink = str(random.choice([8, 10, 12, 15]))
    else:
        screen = dict(SCREEN)
        viewport = dict(VIEWPORT)
        gpu = {
            "gpu_vendor": BROWSER_PROFILE.get("gpu_vendor"),
            "gpu_renderer": BROWSER_PROFILE.get("gpu_renderer"),
            "webgl_vendor": BROWSER_PROFILE.get("webgl_vendor"),
            "webgl_renderer": BROWSER_PROFILE.get("webgl_renderer"),
        }
        hardware_concurrency = int(BROWSER_PROFILE.get("hardware_concurrency") or 8)
        device_pixel_ratio = BROWSER_PROFILE.get("device_pixel_ratio", 1)
        connection_rtt = str(BROWSER_PROFILE.get("connection_rtt") or "150")
        connection_downlink = str(BROWSER_PROFILE.get("connection_downlink") or "10")
    device_memory = int(BROWSER_PROFILE.get("device_memory") or 8)
    profile: JsonDict = dict(BROWSER_PROFILE)
    profile.update(gpu)
    user_agent = _user_agent_with_chrome_version(str(BROWSER_PROFILE.get("user_agent") or USER_AGENT), chrome_full_version)
    profile.update(
        {
            "fingerprint_source": "random",
            "chrome_major": chrome_major,
            "chrome_full_version": chrome_full_version,
            "user_agent": user_agent,
            "device_memory": device_memory,
            "hardware_concurrency": hardware_concurrency,
            "device_pixel_ratio": device_pixel_ratio,
            "connection_rtt": connection_rtt,
            "connection_downlink": connection_downlink,
        }
    )
    fp = _build_device_fingerprint(profile, screen, viewport)
    return {
        "browser_profile": profile,
        "screen": screen,
        "viewport": viewport,
        "device_fingerprint": fp,
    }


def generate_runtime_profile(
    source: str | None = None,
    *,
    roxy_proxy_url: str | None = None,
    keep_roxy_browser: bool = False,
) -> JsonDict:
    """Generate one stable browser/device profile for a single protocol run.

    `source` can be:
      - random/program/python/synthetic: local Python generator;
      - roxy/browser: RoxyBrowser Local API + CDP runtime capture;
      - auto: Roxy when API key is configured, otherwise random.
    """
    requested = _normalize_fingerprint_source(source)
    selected = requested
    if selected == "auto":
        try:
            from paypal.roxy_fingerprint import configured_roxy_api_key

            selected = "roxy" if configured_roxy_api_key() else "random"
        except Exception:
            selected = "random"

    if selected == "roxy":
        try:
            from loguru import logger
            from paypal.roxy_fingerprint import capture_roxy_runtime_profile

            logger.info("Generating browser fingerprint from Roxy runtime...")
            keep_browser = bool(keep_roxy_browser) or _normalize_datadome_mode() in {"roxy", "auto"}
            runtime = capture_roxy_runtime_profile(
                keep_browser=keep_browser,
                proxy_url=roxy_proxy_url,
            )
            runtime["browser_profile"]["fingerprint_source"] = "roxy"
            runtime["device_fingerprint"]["source"] = "roxy"
            logger.info(
                "Roxy fingerprint captured: ua={} screen={}x{} viewport={}x{}",
                (runtime["browser_profile"].get("user_agent") or "")[:80],
                runtime["screen"].get("width"),
                runtime["screen"].get("height"),
                runtime["viewport"].get("width"),
                runtime["viewport"].get("height"),
            )
            return runtime
        except Exception as exc:
            fallback_configured = bool(
                _load_dotenv_value("PAYPAL_ROXY_FINGERPRINT_FALLBACK")
                or _load_dotenv_value("PAYPAL_FINGERPRINT_FALLBACK")
            )
            if requested == "roxy" and not fallback_configured:
                raise
            if not _roxy_fallback_to_random_enabled():
                raise
            try:
                from loguru import logger

                logger.warning("Roxy fingerprint unavailable; falling back to program random: {}", exc)
            except Exception:
                pass

    if selected == "headless":
        try:
            from loguru import logger
            from paypal.local_headless import capture_runtime_fingerprint_with_local_headless

            logger.info("Generating browser fingerprint from local headless runtime...")
            headless_seed = _generate_synthetic_runtime_profile()
            runtime = capture_runtime_fingerprint_with_local_headless(
                proxy_url=roxy_proxy_url or "",
                browser_profile=cast(JsonDict, headless_seed["browser_profile"]),
                screen=cast(JsonDict, headless_seed["screen"]),
                viewport=cast(JsonDict, headless_seed["viewport"]),
            )
            browser_profile = cast(dict[str, object], runtime["browser_profile"])
            screen = cast(dict[str, object], runtime["screen"])
            viewport = cast(dict[str, object], runtime["viewport"])
            device_fingerprint = cast(dict[str, object], runtime["device_fingerprint"])
            browser_profile["fingerprint_source"] = "headless"
            device_fingerprint["source"] = "headless"
            logger.info(
                "Local headless fingerprint captured: ua={} screen={}x{} viewport={}x{}",
                str(browser_profile.get("user_agent") or "")[:80],
                screen.get("width"),
                screen.get("height"),
                viewport.get("width"),
                viewport.get("height"),
            )
            return runtime
        except Exception as exc:
            fallback_configured = bool(
                _load_dotenv_value("PAYPAL_HEADLESS_FINGERPRINT_FALLBACK")
                or _load_dotenv_value("PAYPAL_LOCAL_HEADLESS_FINGERPRINT_FALLBACK")
                or _load_dotenv_value("PAYPAL_FINGERPRINT_FALLBACK")
            )
            if requested == "headless" and not fallback_configured:
                raise
            if not _headless_fallback_to_random_enabled():
                raise
            try:
                from loguru import logger

                logger.warning("Local headless fingerprint unavailable; falling back to program random: {}", exc)
            except Exception:
                pass

    return _generate_synthetic_runtime_profile()


def ensure_runtime_profile(
    state,
    source: str | None = None,
    *,
    roxy_proxy_url: str | None = None,
    keep_roxy_browser: bool = False,
) -> None:
    if not state:
        return
    if getattr(state, "browser_profile", None) and getattr(state, "device_fingerprint", None):
        return
    runtime = generate_runtime_profile(
        source,
        roxy_proxy_url=roxy_proxy_url,
        keep_roxy_browser=keep_roxy_browser,
    )
    state.browser_profile = runtime["browser_profile"]
    state.screen = runtime["screen"]
    state.viewport = runtime["viewport"]
    state.device_fingerprint = runtime["device_fingerprint"]
    if "roxy_browser" in runtime and hasattr(state, "roxy_browser"):
        state.roxy_browser = runtime["roxy_browser"]
    if hasattr(state, "fingerprint_source"):
        state.fingerprint_source = str(runtime["browser_profile"].get("fingerprint_source") or source or "")


def _state(session: object | None) -> object | None:
    return getattr(session, "state", None)


def _profile(session: object | None = None) -> JsonDict:
    state = _state(session)
    profile = (getattr(state, "browser_profile", None) if state else None) or BROWSER_PROFILE
    return cast(JsonDict, profile)


def _screen(session: object | None = None) -> JsonDict:
    state = _state(session)
    screen = (getattr(state, "screen", None) if state else None) or SCREEN
    return cast(JsonDict, screen)


def _viewport(session: object | None = None) -> JsonDict:
    state = _state(session)
    viewport = (getattr(state, "viewport", None) if state else None) or VIEWPORT
    return cast(JsonDict, viewport)


def _dfp(session: object | None = None) -> JsonDict:
    state = _state(session)
    dfp = (getattr(state, "device_fingerprint", None) if state else None) or {}
    return cast(JsonDict, dfp)


def _user_agent(session: object | None = None) -> str:
    return str(_profile(session).get("user_agent") or USER_AGENT)


def _rdt_string(chunks: int | None = None) -> str:
    chunks = chunks or random.randint(9, 16)
    anchors = [5128, 10252, 15375, 20498, 25620, 30744, 35867, 40989, 46113, 51236]
    values = []
    for _ in range(chunks):
        c = random.choice(anchors) + random.randint(-6, 6)
        b = c + random.uniform(120, 380)
        a = b + random.uniform(80, 380)
        values.append(f"{a:.6f},{b:.6f},{c}")
    values.append(f"{random.randint(9_000, 18_000)},{random.randint(40, 80)}")
    return ":".join(values)


def _risk_sequence(app_id: str) -> str:
    if app_id == "IWC_NEXT_CHECKOUT":
        return "1"
    if app_id == "CHECKOUTUINODEWEB_ONBOARDING_LITE":
        return "2"
    return "3"


def _browser_timezone(session: object | None = None) -> tuple[int, str, bool]:
    # Keep DA/FraudNet timezone aligned with the BR checkout locale/IP profile.
    profile = _profile(session)
    return (
        int(profile["timezone_offset_ms"]),
        str(profile["timezone"]),
        bool(profile["dst"]),
    )


def _nav_timing(now_ms: int) -> JsonDict:
    nav_start = now_ms - random.randint(3_500, 9_500)
    fetch_start = nav_start + random.randint(1, 6)
    dns_start = fetch_start + random.randint(1, 8)
    dns_end = dns_start + random.randint(0, 15)
    connect_start = dns_end
    connect_end = connect_start + random.randint(25, 220)
    request_start = connect_end + random.randint(0, 25)
    response_start = request_start + random.randint(250, 1900)
    response_end = response_start + random.randint(35, 900)
    dom_loading = response_end + random.randint(1, 70)
    dom_interactive = dom_loading + random.randint(350, 2300)
    dom_complete = dom_interactive + random.randint(0, 1200)
    return {
        "connectStart": connect_start,
        "secureConnectionStart": connect_start + random.randint(0, 80),
        "unloadEventEnd": 0,
        "domainLookupStart": dns_start,
        "domainLookupEnd": dns_end,
        "responseStart": response_start,
        "connectEnd": connect_end,
        "responseEnd": response_end,
        "requestStart": request_start,
        "domLoading": dom_loading,
        "redirectStart": 0,
        "loadEventEnd": 0,
        "domComplete": dom_complete,
        "navigationStart": nav_start,
        "loadEventStart": 0,
        "domContentLoadedEventEnd": dom_interactive,
        "unloadEventStart": 0,
        "redirectEnd": 0,
        "domInteractive": dom_interactive,
        "fetchStart": fetch_start,
        "domContentLoadedEventStart": dom_interactive - random.randint(0, 25),
    }


def _window_payload(session: object | None = None) -> JsonDict:
    # Keep viewport stable across all FraudNet appIds in one protocol run.
    # A real browser does not materially change window geometry between the
    # ModXO, signup and Hermes pages.
    viewport = _viewport(session)
    profile = _profile(session)
    inner_width = max(320, int(viewport.get("width", 1324) or 1324))
    inner_height = max(320, int(viewport.get("height", 842) or 842))
    return {
        "outerHeight": inner_height + 88,
        "outerWidth": inner_width + 16,
        "innerHeight": inner_height,
        "innerWidth": inner_width,
        "devicePixelRatio": profile["device_pixel_ratio"],
    }


def _build_p1_payload(session: object, correlation_id: str, app_id: str, page_url: str, page_referer: str) -> JsonDict:
    now_ms = int(time.time() * 1000)
    profile = _profile(session)
    screen = _screen(session)
    user_agent = _user_agent(session)
    tz, tz_name, dst = _browser_timezone(session)
    rtt = str(profile["connection_rtt"])
    webdriver = _bool_or(_dfp(session).get("navigator_webdriver"), False)
    return {
        "trt": False,
        "connectionData": {
            "effectiveType": profile["connection_effective_type"],
            "rtt": rtt,
            "downlink": str(profile["connection_downlink"]),
        },
        "navigator": {
            "appName": "Netscape",
            "appVersion": user_agent.removeprefix("Mozilla/"),
            "cookieEnabled": True,
            "language": profile["language"],
            "onLine": True,
            "platform": profile["platform"],
            "product": "Gecko",
            "productSub": "20030107",
            "userAgent": user_agent,
            "vendor": "Google Inc.",
            "vendorSub": "",
            "hardwareConcurrency": profile["hardware_concurrency"],
            "deviceMemory": profile["device_memory"],
        },
        "screen": {
            "colorDepth": screen.get("colorDepth", 24),
            "pixelDepth": screen.get("pixelDepth", 24),
            "height": screen.get("height", 1152),
            "width": screen.get("width", 2048),
            "availHeight": screen.get("availHeight", screen.get("height", 1152)),
            "availWidth": screen.get("availWidth", screen.get("width", 2048)),
        },
        "window": _window_payload(session),
        "referer": page_referer or "",
        "URL": page_url,
        "rvr": "3.15.1-FP",
        "tnt": "PP",
        "activeXDefined": False,
        "flashVersion": {"major": 0, "minor": 0, "release": 0},
        "lst": {
            "ddiLst": True,
            "ddi": _cookie_value(session, "ddi"),
            "v": None,
            "vf": _cookie_value(session, "KHcl0EuY7AKSMgfvHl7J5E7hPtK"),
        },
        "tz": tz,
        "tzName": tz_name,
        "dst": dst,
        "wit": 2,
        "time": now_ms,
        "pt1": {
            "i": "NaN",
            "pp1": f"{random.randint(5, 24)}.00",
            "cd1": f"{random.randint(1, 18)}.00",
            "tb": 1,
            "sf": "0000",
            "ph1": f"{random.randint(9580, 9660)}.00",
        },
        "asynchk": {
            "ph2": _random_hex(64),
            "o": ["ua", "colorDepth", "width", "tz", "time", "appId", "correlationId", _risk_sequence(app_id)],
        },
        "hlb": {"wd": webdriver, "chromeWSRT": False, "plgSize": len(PDF_PLUGINS), "lgSize": 2, "rtt": rtt},
        "pkc": {"uvpa": 3, "cma": 3, "cc": 3, "ht": 3, "pkp": 3},
    }


def _build_p2_payload(session: object, page_url: str) -> JsonDict:
    now_ms = int(time.time() * 1000)
    profile = _profile(session)
    dfp = _dfp(session)
    raw_js_mem = dfp.get("js_memory")
    js_mem_base = cast(JsonDict, raw_js_mem) if isinstance(raw_js_mem, dict) else {}
    used_heap_value = js_mem_base.get("used")
    total_heap_value = js_mem_base.get("total")
    has_used_heap = _value_is_present(used_heap_value)
    has_total_heap = _value_is_present(total_heap_value)
    used_heap = int(cast(str | int | float, used_heap_value)) if has_used_heap else random.randint(18_000_000, 42_000_000)
    total_heap = int(cast(str | int | float, total_heap_value)) if has_total_heap else random.randint(56_000_000, 98_000_000)
    if not has_used_heap:
        used_heap += random.randint(0, 3_000_000)
    if not has_total_heap:
        total_heap = max(total_heap, used_heap + random.randint(14_000_000, 38_000_000))
    raw_timings = dfp.get("timings")
    timings = cast(JsonDict, raw_timings) if isinstance(raw_timings, dict) else {}
    return {
        "URL": page_url,
        "tnt": "PP",
        "data": {
            "plugins": PDF_PLUGINS,
            "cv": {
                "h": dfp.get("canvas_h") or "D//3CNWpwAAAAGSURBVAMAvazvNp9EI5cAAAAASUVORK5CYII=",
                "f": 1,
                "t": f"{int(_float_or(timings.get('tt_canvas'), float(random.randint(2, 29)))):.2f}",
            },
            "vm": {
                "cores": profile["hardware_concurrency"],
                "gpu": {
                    "vendor": profile["gpu_vendor"],
                    "renderer": profile["gpu_renderer"],
                },
                "jsMem": {
                    "usedJSHeapSize": used_heap,
                    "totalJSHeapSize": total_heap,
                    "jsHeapSizeLimit": int(dfp.get("js_heap_size_limit") or 4_395_630_592),
                },
                "perfNav": _nav_timing(now_ms),
            },
            "timing": {
                "cores": "0.00",
                "gpu": f"{random.randint(8, 220)}.00",
                "jsMem": "0.00",
                "perfNav": "0.00",
                "total": f"{random.randint(8, 235)}.00",
            },
        },
    }


def _build_w_payload() -> JsonDict:
    slt = random.randint(70, 330)
    return {
        "pkc": {"uvpa": 2, "cma": 1, "cc": 3, "ht": 3, "pkp": 3},
        "slt": slt,
        "uvpat": max(0, slt - random.randint(0, 24)),
        "cmat": slt,
        "capt": 0,
    }


def _build_pa_payload(session: object, correlation_id: str, app_id: str) -> list[JsonDict]:
    profile = _profile(session)
    dfp = _dfp(session)
    raw_timings = dfp.get("timings")
    timings = cast(JsonDict, raw_timings) if isinstance(raw_timings, dict) else {}
    tt_dfp = _float_or(timings.get("tt_dfp"), random.uniform(20.0, 42.0))
    tt_canvas = _float_or(timings.get("tt_canvas"), max(6.0, tt_dfp - random.uniform(0.3, 2.5)))
    tt_webgl_basic = _float_or(timings.get("tt_webgl_basic"), random.uniform(7.5, 20.5))
    tt_webgl_ext = _float_or(timings.get("tt_webgl_ext"), random.uniform(10.0, 25.0))
    storage_value = timings.get("tt_storage") if "tt_storage" in timings else random.choice([0, 0.09999999776482582])
    math_value = timings.get("tt_math") if "tt_math" in timings else random.choice([0.10000000149011612, 0.19999999925494194])
    tt_storage = float(storage_value or 0)
    tt_math = float(math_value or 0)
    return [{
        "dfp": [{
            "d": {
                "ttDfp": tt_dfp,
                "pdVer": "1.0.0",
                "strg": {"attr": {"lStrg": True, "iDb": True, "sStrg": True, "oDb": False}, "ttStrg": tt_storage},
                "wGlCnv": {"attr": {
                    "wbCnv": {"data": {"cvSig": dfp.get("cv_sig") or _random_hex(64), "ttCvSig": tt_canvas, "wndg": True}},
                    "wbBsc": {"data": {
                        "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
                        "vendor": profile["webgl_vendor"],
                        "vendorUnmasked": profile["gpu_vendor"],
                        "renderer": profile["webgl_renderer"],
                        "rendererUnmasked": profile["gpu_renderer"],
                        "shadingLanguageVersion": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
                    }, "ttWbBsc": tt_webgl_basic},
                    "wbExt": {"data": {"hash": dfp.get("webgl_ext_hash") or _random_hex(64), "ttWbExt": tt_webgl_ext}},
                    "ttWebCnv": tt_canvas,
                }},
                "auCtx": {"attr": {"auVal": dfp.get("audio_val") or "0.0000832115", "ttAuCtx": tt_dfp}},
                "devMd": {"attr": {"auInp": [{"deviceId": "", "kind": "audioinput", "label": "", "groupId": ""}], "vInp": []}, "ttDevMd": 0.09999999776482582},
                "mth": {"attr": {
                    "acos": "1.4473588658278522", "acosh": "709.889355822726", "acoshPf": "355.291251501643",
                    "asin": "0.12343746096704435", "asinh": "0.881373587019543", "asinhPf": "0.8813735870195429",
                    "atanh": "0.5493061443340549", "atanhPf": "0.5493061443340549", "atan": "0.4636476090008061",
                    "sin": "0.8178819121159085", "sinh": "1.1752011936438014", "sinhPf": "2.534342107873324",
                    "cos": "-0.8390715290095377", "cosh": "1.5430806348152437", "coshPf": "1.5430806348152437",
                    "tan": "-1.4214488238747245", "tanh": "0.7615941559557649", "tanhPf": "0.7615941559557649",
                    "exp": "2.718281828459045", "expm1": "1.7182818284590453", "expm1Pf": "1.718281828459045",
                    "log1p": "2.3978952727983707", "log1pPf": "2.3978952727983707", "powPI": "1.9275814160560206e-50",
                }, "ttMth": tt_math},
                "pClkMsr": {"attr": {"value": 0}, "ttPClkMsr": 0},
                "fpTrck": {"data": {"data": {"ttfb": f"{random.uniform(650.0, 1900.0)}", "ttfb_attr": _compact_json({"connectionTime": random.uniform(5.0, 12.0), "dnsTime": 0, "requestTime": random.uniform(600.0, 1850.0), "waitingTime": random.uniform(5.0, 20.0), "rating": "poor"}), "e": "cwv"}}, "ttFpti": random.uniform(8.0, 24.0)},
            },
            "corrId": correlation_id,
            "sourceId": app_id,
            "slt": random.uniform(115.0, 1900.0),
            "clt": tt_dfp,
        }]
    }]


def _wrap(app_id: str, correlation_id: str, payload: object, wrapped: bool) -> object:
    return {"appId": app_id, "correlationId": correlation_id, "payload": payload} if wrapped else payload


def build_fn_sync_data(
    correlation_id: str,
    *,
    source: str = "IWC_NEXT_CHECKOUT",
    include_d: bool = False,
    session=None,
) -> str:
    """Build the FraudNet fn_sync_data hidden field."""
    now_ms = int(time.time() * 1000)
    screen = _screen(session)
    user_agent = _user_agent(session)
    data: JsonDict = {
        "SC_VERSION": "0.1.13" if source.startswith("IWC_NEXT_CHECKOUT") else "2.0.4",
        "syncStatus": "data",
        "f": correlation_id,
        "s": source,
        "chk": {"ts": now_ms, "eteid": generate_eteid(), "tts": random.randint(1, 80)},
        "dc": _compact_json({"screen": screen, "ua": user_agent}),
        "wv": False,
        "web_integration_type": "WEB_REDIRECT",
        "cookie_enabled": True,
    }
    if include_d:
        data["d"] = {
            "ts2": f"Dk17:{random.randint(40000, 56000)}Di0:{random.randint(120, 260)}Ui0:{random.randint(80, 130)}Uk17:{random.randint(90, 140)}Uh:{random.randint(1800, 2600)}",
            "rDT": _rdt_string(13),
        }
    return urllib.parse.quote(_compact_json(data), safe="")


def build_signup_fn_sync_data(ec_token: str, session=None) -> str:
    """Build SignUpNewMember fn_sync_data using the EC token."""
    return build_fn_sync_data(ec_token, source="IWC_LOGIN_APP", include_d=True, session=session)


def send_da_bootstrap(session, *, referer: str = "https://www.paypal.com/", include_ddbm: bool = True):
    """Load DA/FraudNet bootstrap scripts observed in captured HTML."""
    from loguru import logger

    urls = [PAYPAL_DA_FB_FP_JS, PAYPAL_DFP_JS_LEGACY, PAYPAL_DFP_JS_RDA]
    if include_ddbm:
        urls.append(PAYPAL_DDBM_TAGS_JS)
    headers = {
        "Accept": "*/*",
        "Referer": referer,
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "script",
    }
    for url in urls:
        try:
            logger.debug(f"Loading DA bootstrap script: {url}")
            session.get(url, headers=headers)
        except Exception as e:
            logger.debug(f"DA bootstrap script failed {url}: {e}")


def send_identity_di_log(session, correlation_id: str, *, referer: str = "https://www.paypal.com/", eligible: bool = True):
    """Emit the DFP JS lifecycle log that appears with DA/FraudNet loads."""
    from loguru import logger

    ts = int(time.time() * 1000)
    timezone_name = str(_profile(session)["timezone"])
    if eligible:
        event_names = ["DFPJS_LIB_LOADED", "DFPJS_EDGE_MAPPING_ENABLED", "DFPJS_VENDOR_INVOKED", "DFPJS_VENDOR_RESPONSE_RECEIVED", "DFPJS_EDGE_MAPPING_COMPLETE"]
        tracking = [
            {"event_name": "LIB_LOADED", "component": "dfpjs", "browser_timezone": timezone_name, "ul_corr_id": None},
            {"event_name": "VENDOR_INVOKED", "CMID": correlation_id, "component": "dfpjs", "browser_timezone": timezone_name, "ul_corr_id": None},
            {"event_name": "VENDOR_RESPONSE_RECEIVED", "CMID": correlation_id, "component": "dfpjs", "browser_timezone": timezone_name, "ul_corr_id": None},
        ]
    else:
        event_names = ["DFPJS_LIB_LOADED", "DFPJS_EDGE_MAPPING_ENABLED", "DFPJS_INELIGIBlE:DFP_COMPLETE", "DFPJS_NOT_ELIGIBLE"]
        tracking = [
            {"event_name": "LIB_LOADED", "component": "dfpjs", "browser_timezone": timezone_name, "ul_corr_id": None},
            {"event_name": "INELIGIBlE:DFP_COMPLETE", "CMID": correlation_id, "component": "dfpjs", "browser_timezone": timezone_name, "ul_corr_id": None},
        ]
    events = [{"level": "info", "event": name, "payload": {"timestamp": str(ts + i), "comp": "dfpjs", "btz": timezone_name, "ul_corr_id": None}} for i, name in enumerate(event_names)]
    try:
        logger.info("Sending DFP identity log app_corr_id={}...", correlation_id[:8] + "..." if correlation_id else "")
        session.post(PAYPAL_DI_LOG, json={"events": events, "meta": {}, "tracking": tracking}, headers={**_risk_headers(referer), "Sec-Fetch-Site": "same-origin"})
    except Exception as e:
        logger.debug(f"DFP identity log failed: {e}")


def send_fraudnet_rdt(session, correlation_id: str, *, app_id: str, referer: str = "https://www.paypal.com/"):
    """Send the lightweight GET /w rDT beacon observed after DA bootstrap."""
    from loguru import logger

    try:
        session.get(
            f"{PAYPAL_RISK_BASE}/w",
            params={"f": correlation_id, "s": app_id, "d": urllib.parse.quote(_compact_json({"rDT": _rdt_string()}), safe="")},
            headers=_risk_headers(referer, content_type=None),
        )
    except Exception as e:
        logger.debug(f"FraudNet rDT beacon failed: {e}")


def send_device_fingerprint(
    session,
    correlation_id: str,
    *,
    app_id: str = "IWC_NEXT_CHECKOUT",
    referer: str = "https://www.paypal.com/",
    wrapped: bool = True,
    page_url: str | None = None,
    page_referer: str | None = None,
    include_pa: bool = False,
    include_p3: bool = True,
):
    """Send browser-like p3/p1/p2/w and optional /pa FraudNet signals."""
    from loguru import logger
    ensure_runtime_profile(getattr(session, "state", None))

    page_url = page_url or referer or "https://www.paypal.com/"
    page_referer = "" if page_referer is None else page_referer
    headers = _risk_headers(referer)

    if include_p3:
        try:
            logger.info(f"Sending device fingerprint p3 app_id={app_id}...")
            session.get(PAYPAL_RISK_P3, params={"f": correlation_id, "s": app_id}, headers=_risk_headers(referer, content_type=None))
        except Exception as e:
            logger.debug(f"Fingerprint p3 failed: {e}")

    endpoints: list[tuple[str, object]] = [
        ("p1", _build_p1_payload(session, correlation_id, app_id, page_url, page_referer)),
        ("p2", _build_p2_payload(session, page_url)),
        ("w", _build_w_payload()),
    ]
    if include_pa:
        endpoints.append(("pa", _build_pa_payload(session, correlation_id, app_id)))

    for endpoint, payload in endpoints:
        try:
            logger.info(f"Sending device fingerprint {endpoint} app_id={app_id}...")
            session.post(f"{PAYPAL_RISK_BASE}/{endpoint}", json=_wrap(app_id, correlation_id, payload, wrapped), headers=headers)
        except Exception as e:
            logger.warning(f"Fingerprint {endpoint} failed: {e}")


def _field_ts(field_id: str, elapsed: int) -> str:
    if field_id in {"password", "cardCvv"}:
        return f"Di0:{elapsed}Ui0:{random.randint(70, 140)}Di1:{random.randint(4, 190)}Ui1:{random.randint(20, 110)}Di2:{random.randint(7, 190)}Uh:{random.randint(1800, 4300)}"
    if field_id in {"cardNumber", "cardExpiry", "phone", "lastName"}:
        return f"Dk17:{elapsed}Di0:{random.randint(120, 220)}Ui0:{random.randint(70, 130)}Uk17:{random.randint(80, 140)}Uh:{random.randint(1500, 2300)}"
    if field_id in {"firstName", "login_email", "email"}:
        return f"Di0:{elapsed}Ui0:{random.randint(70, 140)}Di1:{random.randint(20, 300)}Ui1:{random.randint(20, 150)}Di2:{random.randint(7, 180)}Uh:{random.randint(1500, 4300)}"
    return f"Dk000:{elapsed}Uk000:{random.randint(4, 13)}Uh:{random.randint(850, 1800)}"


def send_signup_field_events(
    session,
    ec_token: str,
    field_ids: Iterable[str],
    *,
    app_id: str = "CHECKOUTUINODEWEB_ONBOARDING_LITE",
    referer: str | None = None,
):
    """Emit FraudNet field timing beacons for a form."""
    from loguru import logger

    state = getattr(session, "state", None)
    resolved_referer = referer or getattr(state, "signup_url", "") or "https://www.paypal.com/"
    headers = _risk_headers(str(resolved_referer), content_type=None)
    elapsed = random.randint(650, 1600)
    for field_id in field_ids:
        payload = {
            "tsobj": {
                "elid": field_id,
                "sid": app_id,
                "tst": "UL" if app_id.startswith("IWC_NEXT_CHECKOUT") else app_id,
                "wsps": False,
                "ts": _field_ts(field_id, elapsed),
                "pf": {"psu": False, "val": False},
            }
        }
        try:
            session.get(
                f"{PAYPAL_RISK_BASE}/w",
                params={"f": ec_token, "s": app_id, "d": urllib.parse.quote(_compact_json(payload), safe="")},
                headers=headers,
            )
        except Exception as e:
            logger.debug(f"Signup field event {field_id} failed: {e}")
        elapsed += random.randint(120, 380)
