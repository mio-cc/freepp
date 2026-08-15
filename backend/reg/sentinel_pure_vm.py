# -*- coding: utf-8 -*-
"""
sentinel_pure_vm.py — 纯无浏览器 Turnstile / Collector VM

基于官方 sdk.js@20260219f9f6 反编译的 Map-based VM：
  - Turnstile: _n(challenge, dx) → t   (bn Map)
  - Collector: jt(collector_dx, p) 副作用采集
  - Snapshot:  jt(snapshot_dx)     → so  (复用 St 状态)

算法与 codebai.cn `_solve_turnstile_token` / realasfngl opcode 表一致：
  atob(dx) → XOR(p) → JSON 指令队列 → opcode 0..35 解释执行 → btoa 结果

auth 路径与 chat 路径共用同一套 obt 字节码格式；XOR 密钥为 /req body 的 p。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# XOR / base helpers
# ---------------------------------------------------------------------------

def xor_string(text: str, key: str) -> str:
    if not key:
        return text
    kl = len(key)
    return "".join(chr(ord(ch) ^ ord(key[i % kl])) for i, ch in enumerate(text))


def decode_dx(dx: str, seed: str) -> List[Any]:
    """atob(dx) XOR seed → JSON instruction list."""
    raw = base64.b64decode(dx).decode("latin-1")
    plain = xor_string(raw, seed)
    data = json.loads(plain)
    if not isinstance(data, list):
        raise ValueError("dx decode did not yield instruction list")
    return data


def b64_utf8(s: str) -> str:
    return base64.b64encode(s.encode("utf-8", errors="surrogatepass")).decode("ascii")


def b64_latin1(s: str) -> str:
    return base64.b64encode(s.encode("latin-1", errors="replace")).decode("ascii")


def atob_str(s: str) -> str:
    pad = (-len(s)) % 4
    raw = base64.b64decode(s + ("=" * pad))
    return raw.decode("latin-1")


def btoa_str(s: str) -> str:
    return base64.b64encode(str(s).encode("latin-1", errors="replace")).decode("ascii")


# ---------------------------------------------------------------------------
# Pseudo browser environment (fingerprint sources for VM probes)
# ---------------------------------------------------------------------------

class OrderedMap:
    """Mimics Object.create(null) + Reflect.set ordered map used by bytecode."""

    def __init__(self) -> None:
        self._keys: List[str] = []
        self._values: Dict[str, Any] = {}

    def __setitem__(self, key: Any, value: Any) -> None:
        k = str(key)
        if k not in self._values:
            self._keys.append(k)
        self._values[k] = value

    def __getitem__(self, key: Any) -> Any:
        return self._values[str(key)]

    def get(self, key: Any, default: Any = None) -> Any:
        return self._values.get(str(key), default)

    def keys(self) -> List[str]:
        return list(self._keys)

    def values(self) -> List[Any]:
        return [self._values[k] for k in self._keys]

    def items(self) -> List[Tuple[str, Any]]:
        return [(k, self._values[k]) for k in self._keys]

    def __contains__(self, key: Any) -> bool:
        return str(key) in self._values

    def __repr__(self) -> str:
        return f"OrderedMap({self._values!r})"


def _native(name: str) -> str:
    return f"function {name}() {{ [native code] }}"


class PseudoElement:
    def __init__(
        self,
        tag: str = "div",
        src: str = "",
        *,
        canvas_seed: str = "default",
        webgl_vendor: str = "Google Inc. (NVIDIA)",
        webgl_renderer: str = (
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)"
        ),
    ) -> None:
        self.tagName = tag.upper()
        self.nodeName = self.tagName
        self.nodeType = 1
        self.src = src
        self.style: Dict[str, str] = {}
        self.children: List[Any] = []
        self.id = ""
        self.className = ""
        self.innerHTML = ""
        self.textContent = ""
        self.offsetWidth = 1440
        self.offsetHeight = 40
        self.clientWidth = 1440
        self.clientHeight = 40
        self._canvas_seed = canvas_seed
        self._webgl_vendor = webgl_vendor
        self._webgl_renderer = webgl_renderer

    def setAttribute(self, *_a: Any, **_k: Any) -> None:
        return None

    def getAttribute(self, name: str) -> Optional[str]:
        if name == "src":
            return self.src or None
        if name == "data-build":
            return None
        return None

    def appendChild(self, child: Any) -> Any:
        self.children.append(child)
        return child

    def removeChild(self, child: Any) -> Any:
        if child in self.children:
            self.children.remove(child)
        return child

    def addEventListener(self, *_a: Any, **_k: Any) -> None:
        return None

    def removeEventListener(self, *_a: Any, **_k: Any) -> None:
        return None

    def getBoundingClientRect(self) -> Dict[str, float]:
        return {
            "x": 0.0, "y": 0.0, "width": 37.8125, "height": 30.0,
            "top": 0.0, "left": 0.0, "right": 37.8125, "bottom": 30.0,
        }

    def getContext(self, kind: str = "2d", *_a: Any, **_k: Any) -> Any:
        if kind in ("2d", "webgl", "experimental-webgl", "webgl2"):
            return CanvasContext(
                kind,
                webgl_vendor=self._webgl_vendor,
                webgl_renderer=self._webgl_renderer,
            )
        return None

    def toDataURL(self, *_a: Any, **_k: Any) -> str:
        # Stable synthetic canvas fingerprint（按 canvas_seed 区分设备）
        digest = hashlib.sha256(
            f"{self._canvas_seed}|canvas".encode("utf-8")
        ).digest()
        return "data:image/png;base64," + base64.b64encode(digest).decode("ascii")


class CanvasContext:
    def __init__(
        self,
        kind: str,
        webgl_vendor: str = "Google Inc. (NVIDIA)",
        webgl_renderer: str = (
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)"
        ),
    ) -> None:
        self.kind = kind
        self.fillStyle = "#000"
        self.font = "10px sans-serif"
        self.webgl_vendor = webgl_vendor
        self.webgl_renderer = webgl_renderer

    def fillRect(self, *_a: Any, **_k: Any) -> None:
        return None

    def fillText(self, *_a: Any, **_k: Any) -> None:
        return None

    def beginPath(self, *_a: Any, **_k: Any) -> None:
        return None

    def arc(self, *_a: Any, **_k: Any) -> None:
        return None

    def fill(self, *_a: Any, **_k: Any) -> None:
        return None

    def getImageData(self, *_a: Any, **_k: Any) -> Any:
        class _ID:
            data = [0, 0, 0, 255] * 16
        return _ID()

    def getParameter(self, param: Any) -> Any:
        # Common WebGL params — vendor/renderer 来自配套指纹 profile
        table = {
            37445: self.webgl_vendor,
            37446: self.webgl_renderer,
            7936: "WebKit",
            7937: "WebKit WebGL",
            7938: "WebGL 1.0",
        }
        return table.get(param, "WebGL")

    def getExtension(self, name: str) -> Any:
        if name in ("WEBGL_debug_renderer_info", "EXT_texture_filter_anisotropic"):
            return self
        return None

    def getSupportedExtensions(self) -> List[str]:
        return [
            "ANGLE_instanced_arrays", "EXT_blend_minmax",
            "WEBGL_debug_renderer_info", "OES_texture_float",
        ]


class PseudoStorage:
    def __init__(self, initial: Optional[Dict[str, str]] = None) -> None:
        self._d: Dict[str, str] = dict(initial or {})

    def getItem(self, key: Any) -> Optional[str]:
        return self._d.get(str(key))

    def setItem(self, key: Any, value: Any) -> None:
        self._d[str(key)] = str(value)

    def removeItem(self, key: Any) -> None:
        self._d.pop(str(key), None)

    def clear(self) -> None:
        self._d.clear()

    def key(self, index: int) -> Optional[str]:
        keys = list(self._d.keys())
        return keys[index] if 0 <= index < len(keys) else None

    @property
    def length(self) -> int:
        return len(self._d)

    def keys(self) -> List[str]:
        return list(self._d.keys())

    def __iter__(self):
        return iter(self._d)

    def __contains__(self, key: Any) -> bool:
        return str(key) in self._d

    def __getitem__(self, key: Any) -> str:
        return self._d[str(key)]


@dataclass
class FingerprintProfile:
    """Synthetic but realistic browser fingerprint for VM probes.

    与 chatgpt.choose_fp() 配套时，HTTP 头 UA / sec-ch-ua 与此处 navigator/screen/webgl
    必须同一套，避免 oai-did 信封与 TLS/UA 矛盾（见 ANTI_FUZZING.md §3.3）。
    """
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    language: str = "en-US"
    languages: Tuple[str, ...] = ("en-US", "en")
    platform: str = "Win32"
    vendor: str = "Google Inc."
    hardware_concurrency: int = 8
    device_memory: float = 8
    max_touch_points: int = 0
    screen_width: int = 1920
    screen_height: int = 1080
    color_depth: int = 24
    device_pixel_ratio: float = 1.0
    timezone_offset: int = -480
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sdk_version: str = "20260219f9f6"
    href: str = "https://auth.openai.com/"
    origin: str = "https://auth.openai.com"
    # 与 HTTP 配套的 WebGL / canvas 种子
    webgl_vendor: str = "Google Inc. (NVIDIA)"
    webgl_renderer: str = (
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)"
    )
    canvas_seed: str = "default"
    # auth 页常见 script src 形态
    script_sources: Tuple[str, ...] = ()
    local_storage_keys: Tuple[str, ...] = (
        "oai-did",
        "client-correlated-secret",
        "oai/apps/capExpiresAt",
        "STATSIG_LOCAL_STORAGE_STABLE_ID",
        "STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
        "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
        "oai/apps/hasSeenNoAuthImagegenNux",
        "oai/apps/lastPageLoadDate",
    )
    # CDP 导出的指纹快照（可选，覆盖合成值）
    cdp_snapshot: Dict[str, Any] = field(default_factory=dict)


def fingerprint_from_dict(
    fp: Optional[Dict[str, Any]] = None,
    *,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    cdp_snapshot: Optional[Dict[str, Any]] = None,
) -> FingerprintProfile:
    """从 chatgpt.choose_fp() 的 dict 构造 FingerprintProfile；fp=None 时默认随机感合成值。"""
    if not fp:
        return FingerprintProfile(
            device_id=device_id or str(uuid.uuid4()),
            user_agent=user_agent or FingerprintProfile.user_agent,
            cdp_snapshot=cdp_snapshot or {},
        )
    screen = fp.get("screen") or {}
    langs = fp.get("languages") or ("en-US", "en")
    if isinstance(langs, list):
        langs_t: Tuple[str, ...] = tuple(str(x) for x in langs)
    elif isinstance(langs, tuple):
        langs_t = langs  # type: ignore[assignment]
    else:
        langs_t = (str(langs),)
    lang0 = langs_t[0] if langs_t else "en-US"
    ua = user_agent or fp.get("ua") or FingerprintProfile.user_agent
    # 由配套字段合成 cdp 覆盖，与 profile 字段一致
    snap = dict(cdp_snapshot or {})
    snap.setdefault("userAgent", ua)
    snap.setdefault("webgl_vendor", fp.get("webgl_vendor"))
    snap.setdefault("webgl_renderer", fp.get("webgl_renderer"))
    if fp.get("canvas_seed") and "canvas_hash" not in snap:
        digest = hashlib.sha256(
            f"{fp.get('canvas_seed')}|canvas".encode("utf-8")
        ).digest()
        snap["canvas_hash"] = "data:image/png;base64," + base64.b64encode(digest).decode("ascii")
    return FingerprintProfile(
        user_agent=ua,
        language=lang0,
        languages=langs_t,
        platform=str(fp.get("platform") or "Win32"),
        hardware_concurrency=int(fp.get("hardware_concurrency") or 8),
        device_memory=float(fp.get("device_memory") or 8),
        screen_width=int(screen.get("width") or 1920),
        screen_height=int(screen.get("height") or 1080),
        device_pixel_ratio=float(screen.get("px_ratio") or 1.0),
        device_id=device_id or str(uuid.uuid4()),
        webgl_vendor=str(fp.get("webgl_vendor") or FingerprintProfile.webgl_vendor),
        webgl_renderer=str(fp.get("webgl_renderer") or FingerprintProfile.webgl_renderer),
        canvas_seed=str(fp.get("canvas_seed") or "default"),
        cdp_snapshot=snap,
    )


def default_script_sources(sdk_version: str) -> List[str]:
    return [
        f"https://sentinel.openai.com/sentinel/{sdk_version}/sdk.js",
        "https://auth.openai.com/cdn-cgi/challenge-platform/scripts/jsd/main.js",
        "https://auth.openai.com/assets/index.js",
        "https://auth.openai.com/assets/vendor.js",
        "https://cdn.oaistatic.com/assets/root.js",
        "https://auth.openai.com/api/accounts/csrf",
    ]


class JsEvent:
    """Minimal DOM Event / MouseEvent / KeyboardEvent for collector listeners."""

    def __init__(self, typ: str, **kwargs: Any) -> None:
        self.type = typ
        self.timeStamp = time.time() * 1000
        self.bubbles = True
        self.cancelable = True
        self.defaultPrevented = False
        self.isTrusted = True
        self.target = kwargs.pop("target", None)
        self.currentTarget = kwargs.pop("currentTarget", None)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def preventDefault(self) -> None:
        self.defaultPrevented = True

    def stopPropagation(self) -> None:
        return None


class EventTargetMixin:
    """addEventListener / dispatchEvent used by collector_dx session observer."""

    def __init__(self) -> None:
        self._listeners: Dict[str, List[Any]] = {}

    def addEventListener(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        self._listeners.setdefault(str(typ), []).append(fn)

    def removeEventListener(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        lst = self._listeners.get(str(typ), [])
        self._listeners[str(typ)] = [x for x in lst if x is not fn]

    def dispatchEvent(self, ev: Any) -> bool:
        typ = getattr(ev, "type", None)
        if typ is None and isinstance(ev, dict):
            typ = ev.get("type")
        for fn in list(self._listeners.get(str(typ), [])):
            try:
                fn(ev)
            except Exception:
                pass
        return True


class PseudoBrowser:
    """Minimal object graph so VM property walks succeed with non-empty values."""

    def __init__(self, profile: Optional[FingerprintProfile] = None) -> None:
        self.profile = profile or FingerprintProfile()
        if not self.profile.script_sources:
            self.profile.script_sources = tuple(
                default_script_sources(self.profile.sdk_version)
            )
        self._t0 = time.time()
        self._perf_origin = time.time() * 1000 - random.uniform(2000, 8000)
        self._win_listeners: Dict[str, List[Any]] = {}
        self._doc_listeners: Dict[str, List[Any]] = {}
        self._build()

    # ── event bus (collector session observer) ────────────────────
    def _win_add_event(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        self._win_listeners.setdefault(str(typ), []).append(fn)

    def _win_remove_event(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        lst = self._win_listeners.get(str(typ), [])
        self._win_listeners[str(typ)] = [x for x in lst if x is not fn]

    def _doc_add_event(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        self._doc_listeners.setdefault(str(typ), []).append(fn)

    def _doc_remove_event(self, typ: Any, fn: Any = None, *a: Any, **k: Any) -> None:
        if fn is None:
            return
        lst = self._doc_listeners.get(str(typ), [])
        self._doc_listeners[str(typ)] = [x for x in lst if x is not fn]

    def _fire(self, typ: str, ev: Any, where: str = "window") -> None:
        bag = self._win_listeners if where == "window" else self._doc_listeners
        for fn in list(bag.get(typ, [])):
            try:
                fn(ev)
            except Exception:
                pass
        # also mirror to the other target (some handlers bind both)
        other = self._doc_listeners if where == "window" else self._win_listeners
        for fn in list(other.get(typ, [])):
            try:
                fn(ev)
            except Exception:
                pass

    def simulate_user_activity(self, duration_ms: float = 1200.0) -> Dict[str, int]:
        """
        P3: feed synthetic pointer/key/scroll/wheel/click/paste into collector hooks.
        Official waits Xn=5000ms; we compress realistic samples into ~duration_ms wall time
        (VM is sync — events are immediate; duration only spaces timestamps).
        """
        w = self.profile.screen_width
        h = self.profile.screen_height
        x = float(random.randint(80, max(120, w - 80)))
        y = float(random.randint(80, max(120, h - 80)))
        keys = list("abcdefghijklmnopqrstuvwxyz0123456789")
        special = ["Shift", "Control", "Alt", "Meta", "CapsLock", "Backspace", "Enter", "Tab"]
        stats = {"pointermove": 0, "keydown": 0, "click": 0, "scroll": 0, "wheel": 0, "paste": 0}

        n_move = random.randint(25, 55)
        n_key = random.randint(8, 20)
        n_click = random.randint(2, 6)
        n_scroll = random.randint(3, 10)
        n_wheel = random.randint(2, 8)
        n_paste = random.randint(0, 2)

        def _micro_pause() -> None:
            """步间微抖动（ms 级），打散 obt 采集的机械节奏（ANTI_FUZZING §3.1/§3.2）。"""
            time.sleep(random.uniform(0.001, 0.012))

        # interleaved human-ish path
        for i in range(n_move):
            x += random.uniform(-40, 40)
            y += random.uniform(-30, 30)
            x = max(0, min(w - 1, x))
            y = max(0, min(h - 1, y))
            ev = JsEvent(
                "pointermove",
                clientX=x, clientY=y, pageX=x, pageY=y,
                screenX=x, screenY=y, movementX=random.uniform(-5, 5),
                movementY=random.uniform(-5, 5),
                pointerType="mouse", buttons=0, button=-1,
                altKey=False, ctrlKey=False, metaKey=False, shiftKey=False,
            )
            self._fire("pointermove", ev)
            stats["pointermove"] += 1
            if i % 3 == 0:
                _micro_pause()

        for i in range(n_key):
            if random.random() < 0.15:
                key = random.choice(special)
            else:
                key = random.choice(keys)
            ev = JsEvent(
                "keydown",
                key=key, code=f"Key{key.upper()}" if len(key) == 1 else key,
                keyCode=ord(key) if len(key) == 1 else 0,
                which=ord(key) if len(key) == 1 else 0,
                altKey=key == "Alt", ctrlKey=key == "Control",
                metaKey=key == "Meta", shiftKey=key == "Shift",
                repeat=False, isComposing=False,
                clientX=x, clientY=y,
            )
            self._fire("keydown", ev)
            stats["keydown"] += 1
            _micro_pause()

        for i in range(n_click):
            x += random.uniform(-20, 20)
            y += random.uniform(-20, 20)
            x = max(0, min(w - 1, x))
            y = max(0, min(h - 1, y))
            ev = JsEvent(
                "click",
                clientX=x, clientY=y, pageX=x, pageY=y,
                screenX=x, screenY=y, button=0, buttons=1,
                detail=1, altKey=False, ctrlKey=False, metaKey=False, shiftKey=False,
            )
            self._fire("click", ev)
            stats["click"] += 1
            _micro_pause()

        scroll_y = 0
        for i in range(n_scroll):
            scroll_y += random.randint(40, 200)
            # update window scroll positions
            try:
                self.window["scrollY"] = scroll_y
                self.window["pageYOffset"] = scroll_y
                self.window["scrollX"] = random.randint(0, 20)
                self.window["pageXOffset"] = self.window["scrollX"]
            except Exception:
                pass
            ev = JsEvent("scroll", clientX=x, clientY=y)
            self._fire("scroll", ev)
            stats["scroll"] += 1
            if i % 2 == 0:
                _micro_pause()

        for i in range(n_wheel):
            ev = JsEvent(
                "wheel",
                clientX=x, clientY=y,
                deltaX=random.uniform(-10, 10),
                deltaY=random.uniform(30, 120),
                deltaZ=0, deltaMode=0,
                altKey=False, ctrlKey=False, metaKey=False, shiftKey=False,
            )
            self._fire("wheel", ev)
            stats["wheel"] += 1
            _micro_pause()

        for i in range(n_paste):
            ev = JsEvent(
                "paste",
                clipboardData=type("CD", (), {
                    "getData": lambda *_a, **_k: "pasted text sample",
                })(),
            )
            self._fire("paste", ev)
            stats["paste"] += 1
            _micro_pause()

        # tiny wall sleep so performance.now advances a bit
        time.sleep(min(0.05, duration_ms / 1000.0) + random.uniform(0.0, 0.02))
        return stats

    def _build(self) -> None:
        p = self.profile
        scripts = [PseudoElement("script", src=s) for s in p.script_sources]
        document_el = PseudoElement("html")
        document_el.clientWidth = p.screen_width
        document_el.clientHeight = p.screen_height

        ls_init = {k: "" for k in p.local_storage_keys}
        ls_init["oai-did"] = p.device_id
        ls_init["client-correlated-secret"] = base64.b64encode(
            uuid.uuid4().bytes
        ).decode("ascii")
        self.localStorage = PseudoStorage(ls_init)
        self.sessionStorage = PseudoStorage()

        class _Doc:
            pass

        doc = _Doc()
        doc.readyState = "complete"
        doc.hidden = False
        doc.visibilityState = "visible"
        doc.referrer = "https://chatgpt.com/"
        doc.URL = p.href
        doc.documentURI = p.href
        doc.compatMode = "CSS1Compat"
        doc.cookie = f"oai-did={p.device_id}"
        doc.scripts = scripts
        doc.currentScript = scripts[0] if scripts else PseudoElement("script")
        doc.documentElement = document_el
        doc.body = PseudoElement("body")
        doc.head = PseudoElement("head")
        doc.title = "Auth"
        doc.location = None  # set below
        def _mk_el(tag: Any) -> PseudoElement:
            return PseudoElement(
                str(tag),
                canvas_seed=p.canvas_seed,
                webgl_vendor=p.webgl_vendor,
                webgl_renderer=p.webgl_renderer,
            )

        doc.createElement = _mk_el
        doc.createElementNS = lambda _ns, tag: _mk_el(tag)
        doc.querySelector = lambda *_a, **_k: None
        doc.querySelectorAll = lambda *_a, **_k: []
        doc.getElementById = lambda *_a, **_k: None
        doc.getElementsByTagName = lambda tag: (
            scripts if str(tag).lower() == "script" else []
        )
        doc.getElementsByClassName = lambda *_a, **_k: []
        doc.addEventListener = self._doc_add_event
        doc.removeEventListener = self._doc_remove_event
        doc.dispatchEvent = lambda ev: (
            self._fire(getattr(ev, "type", ""), ev, "document") or True
        )
        doc.hasFocus = lambda: True
        doc.implementation = type("Impl", (), {"hasFeature": lambda *a: True})()
        # Object.keys(document) noise (React-like keys)
        setattr(doc, "__reactContainer$auth", True)
        setattr(doc, "_reactListeningAuth", True)

        class _Loc:
            pass

        loc = _Loc()
        loc.href = p.href
        loc.origin = p.origin
        loc.protocol = "https:"
        loc.host = "auth.openai.com"
        loc.hostname = "auth.openai.com"
        loc.pathname = "/"
        loc.search = ""
        loc.hash = ""
        loc.port = ""
        loc.toString = lambda: p.href
        doc.location = loc

        class _Nav:
            pass

        nav = _Nav()
        nav.userAgent = p.user_agent
        nav.appVersion = p.user_agent.replace("Mozilla/", "")
        nav.appName = "Netscape"
        nav.appCodeName = "Mozilla"
        nav.product = "Gecko"
        nav.productSub = "20030107"
        nav.vendor = p.vendor
        nav.vendorSub = ""
        nav.language = p.language
        nav.languages = list(p.languages)
        nav.platform = p.platform
        nav.hardwareConcurrency = p.hardware_concurrency
        nav.deviceMemory = p.device_memory
        nav.maxTouchPoints = p.max_touch_points
        nav.cookieEnabled = True
        nav.onLine = True
        nav.webdriver = False
        nav.pdfViewerEnabled = True
        nav.doNotTrack = None
        nav.plugins = [
            type("Plugin", (), {"name": "PDF Viewer", "filename": "internal-pdf-viewer",
                                "description": "Portable Document Format"})(),
            type("Plugin", (), {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer",
                                "description": ""})(),
            type("Plugin", (), {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer",
                                "description": ""})(),
        ]
        nav.mimeTypes = []
        # 从 UA 推断 Chrome major，避免硬编码 131 与配套 fp 矛盾
        _chrome_major = "131"
        try:
            import re as _re
            _m = _re.search(r"Chrome/(\d+)", p.user_agent or "")
            if _m:
                _chrome_major = _m.group(1)
        except Exception:
            pass
        _uad_platform = (
            "macOS" if "Mac" in (p.platform or "") or "Mac" in (p.user_agent or "")
            else "Windows"
        )
        nav.userAgentData = type("UAD", (), {
            "brands": [
                {"brand": "Google Chrome", "version": _chrome_major},
                {"brand": "Chromium", "version": _chrome_major},
                {"brand": "Not_A Brand", "version": "24"},
            ],
            "mobile": False,
            "platform": _uad_platform,
            "getHighEntropyValues": lambda *_a, **_k: {
                "architecture": "x86", "bitness": "64",
                "model": "", "platformVersion": "15.0.0",
            },
        })()
        nav.connection = type("Conn", (), {
            "effectiveType": "4g", "rtt": 50, "downlink": 10, "saveData": False,
        })()
        nav.mediaDevices = type("MD", (), {
            "enumerateDevices": lambda: [],
        })()
        nav.permissions = type("Perm", (), {
            "query": lambda *_a, **_k: type("PS", (), {"state": "prompt"})(),
        })()
        nav.credentials = object()
        nav.locks = object()
        nav.storage = object()
        nav.serviceWorker = object()
        nav.geolocation = object()
        nav.mediaCapabilities = object()
        nav.clipboard = object()
        nav.keyboard = object()
        nav.usb = object()
        nav.hid = object()
        nav.serial = object()
        nav.gpu = object()
        nav.scheduling = object()
        nav.userActivation = type("UA", (), {"hasBeenActive": True, "isActive": True})()
        nav.getBattery = lambda: None
        nav.sendBeacon = lambda *_a, **_k: True
        nav.javaEnabled = lambda: False
        nav.vibrate = lambda *_a, **_k: False

        class _Screen:
            pass

        scr = _Screen()
        scr.width = p.screen_width
        scr.height = p.screen_height
        scr.availWidth = p.screen_width
        scr.availHeight = p.screen_height - 40
        scr.colorDepth = p.color_depth
        scr.pixelDepth = p.color_depth
        scr.orientation = type("SO", (), {"type": "landscape-primary", "angle": 0})()

        class _Perf:
            pass

        perf = _Perf()
        t0 = self._t0

        def _now() -> float:
            return (time.time() - t0) * 1000.0 + random.random()

        perf.now = _now
        perf.timeOrigin = self._perf_origin
        perf.memory = type("Mem", (), {
            "jsHeapSizeLimit": 4294705152,
            "totalJSHeapSize": 30000000,
            "usedJSHeapSize": 20000000,
        })()
        perf.getEntries = lambda: []
        perf.getEntriesByType = lambda *_a, **_k: []
        perf.getEntriesByName = lambda *_a, **_k: []
        perf.timing = type("Timing", (), {
            "navigationStart": int(self._perf_origin),
            "domContentLoadedEventEnd": int(self._perf_origin + 400),
            "loadEventEnd": int(self._perf_origin + 800),
        })()
        perf.navigation = type("NavT", (), {"type": 0, "redirectCount": 0})()

        # Math / Object / Reflect / JSON / Array as real Python callables where needed
        class _Math:
            abs = staticmethod(math.fabs)
            floor = staticmethod(math.floor)
            ceil = staticmethod(math.ceil)
            round = staticmethod(round)
            max = staticmethod(max)
            min = staticmethod(min)
            random = staticmethod(random.random)
            pow = staticmethod(math.pow)
            sqrt = staticmethod(math.sqrt)
            PI = math.pi
            E = math.e

        class _Object:
            @staticmethod
            def create(proto=None, *_a, **_k):
                if proto is None:
                    return OrderedMap()
                return OrderedMap()

            @staticmethod
            def keys(obj):
                if isinstance(obj, PseudoStorage):
                    return obj.keys()
                if isinstance(obj, OrderedMap):
                    return obj.keys()
                if isinstance(obj, dict):
                    return list(obj.keys())
                if hasattr(obj, "__dict__"):
                    return [k for k in vars(obj).keys() if not k.startswith("_")]
                try:
                    return list(obj.keys())  # type: ignore
                except Exception:
                    return []

            @staticmethod
            def values(obj):
                return [getattr(obj, k) for k in _Object.keys(obj)]

            @staticmethod
            def assign(target, *sources):
                for s in sources:
                    if isinstance(s, dict):
                        for k, v in s.items():
                            if isinstance(target, OrderedMap):
                                target[k] = v
                            elif isinstance(target, dict):
                                target[k] = v
                            else:
                                setattr(target, k, v)
                return target

            @staticmethod
            def getOwnPropertyNames(obj):
                return _Object.keys(obj)

            @staticmethod
            def getPrototypeOf(obj):
                return type(obj)

            @staticmethod
            def freeze(obj):
                return obj

            @staticmethod
            def is_(a, b):
                return a is b

        class _Reflect:
            @staticmethod
            def set(obj, key, value):
                if isinstance(obj, OrderedMap):
                    obj[key] = value
                    return True
                if isinstance(obj, dict):
                    obj[key] = value
                    return True
                if isinstance(obj, PseudoStorage):
                    obj.setItem(key, value)
                    return True
                try:
                    setattr(obj, str(key), value)
                    return True
                except Exception:
                    return False

            @staticmethod
            def get(obj, key):
                return _prop_get(obj, key)

            @staticmethod
            def has(obj, key):
                try:
                    _prop_get(obj, key)
                    return True
                except Exception:
                    return False

            @staticmethod
            def apply(fn, this, args):
                return fn(*list(args or []))

            @staticmethod
            def construct(cls, args):
                return cls(*list(args or []))

        class _JSON:
            @staticmethod
            def parse(s):
                return json.loads(s)

            @staticmethod
            def stringify(v, *a, **k):
                return json.dumps(v, separators=(",", ":"), ensure_ascii=False, default=str)

        class _Array:
            isArray = staticmethod(lambda x: isinstance(x, list))
            from_ = staticmethod(lambda x: list(x) if x is not None else [])
            of = staticmethod(lambda *a: list(a))

        class _Date:
            def __init__(self, *a):
                self._t = time.time() * 1000 if not a else a[0]

            def getTime(self):
                return self._t

            def getTimezoneOffset(self):
                return p.timezone_offset

            def toString(self):
                return time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
                                    time.gmtime(self._t / 1000))

            @staticmethod
            def now():
                return time.time() * 1000

        class _Promise:
            @staticmethod
            def resolve(v=None):
                return _Resolved(v)

            @staticmethod
            def reject(v=None):
                return _Rejected(v)

        class _Resolved:
            def __init__(self, v):
                self.v = v

            def then(self, fn=None, *_a, **_k):
                try:
                    return _Resolved(fn(self.v) if fn else self.v)
                except Exception as e:
                    return _Rejected(e)

            def catch(self, fn=None, *_a, **_k):
                return self

            def finally_(self, fn=None):
                if fn:
                    fn()
                return self

        class _Rejected:
            def __init__(self, v):
                self.v = v

            def then(self, _fn=None, err=None, *_a, **_k):
                if err:
                    try:
                        return _Resolved(err(self.v))
                    except Exception as e:
                        return _Rejected(e)
                return self

            def catch(self, fn=None, *_a, **_k):
                if fn:
                    try:
                        return _Resolved(fn(self.v))
                    except Exception as e:
                        return _Rejected(e)
                return self

            def finally_(self, fn=None):
                if fn:
                    fn()
                return self

        # chrome runtime stub
        chrome = type("Chrome", (), {
            "runtime": type("RT", (), {"id": None, "OnInstalledReason": {}})(),
            "app": type("App", (), {"isInstalled": False})(),
            "csi": lambda: {},
            "loadTimes": lambda: {},
        })()

        win: Dict[str, Any] = {
            "window": None,  # filled below
            "self": None,
            "top": None,
            "parent": None,
            "document": doc,
            "navigator": nav,
            "location": loc,
            "screen": scr,
            "performance": perf,
            "localStorage": self.localStorage,
            "sessionStorage": self.sessionStorage,
            "Math": _Math,
            "Object": _Object,
            "Reflect": _Reflect,
            "JSON": _JSON,
            "Array": _Array,
            "Date": _Date,
            "Promise": _Promise,
            "Number": float,
            "String": str,
            "Boolean": bool,
            "parseInt": lambda s, b=10: int(str(s), b),
            "parseFloat": float,
            "isNaN": lambda x: x != x if isinstance(x, float) else False,
            "undefined": None,
            "NaN": float("nan"),
            "Infinity": float("inf"),
            "chrome": chrome,
            "devicePixelRatio": p.device_pixel_ratio,
            "innerWidth": p.screen_width,
            "innerHeight": p.screen_height - 100,
            "outerWidth": p.screen_width,
            "outerHeight": p.screen_height,
            "screenX": 0,
            "screenY": 0,
            "pageXOffset": 0,
            "pageYOffset": 0,
            "scrollX": 0,
            "scrollY": 0,
            "history": type("Hist", (), {"length": random.randint(2, 6), "state": None})(),
            "crypto": type("Crypto", (), {
                "getRandomValues": lambda arr: _fill_random(arr),
                "randomUUID": lambda: str(uuid.uuid4()),
                "subtle": object(),
            })(),
            "atob": atob_str,
            "btoa": btoa_str,
            "addEventListener": self._win_add_event,
            "removeEventListener": self._win_remove_event,
            "dispatchEvent": lambda ev: (self._fire(getattr(ev, "type", ""), ev) or True),
            "setTimeout": lambda fn, ms=0, *a: fn(*a) if callable(fn) else None,
            "setInterval": lambda *_a, **_k: 1,
            "clearTimeout": lambda *_a, **_k: None,
            "clearInterval": lambda *_a, **_k: None,
            "requestAnimationFrame": lambda fn: fn(time.time() * 1000) if callable(fn) else 1,
            "matchMedia": lambda q: type("MQL", (), {
                "matches": "prefers-color-scheme" not in str(q),
                "media": str(q),
                "addListener": lambda *_a: None,
                "addEventListener": lambda *_a: None,
            })(),
            "getComputedStyle": lambda *_a, **_k: type("CSS", (), {
                "getPropertyValue": lambda *_a, **_k: "",
            })(),
            "HTMLCanvasElement": PseudoElement,
            "Image": lambda: PseudoElement("img"),
            "XMLHttpRequest": object,
            "fetch": lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fetch blocked")),
            "console": type("Con", (), {
                "log": lambda *a: None, "warn": lambda *a: None, "error": lambda *a: None,
            })(),
            "isSecureContext": True,
            "origin": p.origin,
            "name": "",
            "closed": False,
            "length": 0,
            "frames": [],
            "speechSynthesis": object(),
            "indexedDB": object(),
            "caches": object(),
            "scheduler": object(),
            "trustedTypes": object(),
            "visualViewport": type("VV", (), {
                "width": float(p.screen_width),
                "height": float(p.screen_height - 100),
                "scale": 1.0,
            })(),
            "WebGLRenderingContext": object,
            "WebGL2RenderingContext": object,
            "AudioContext": object,
            "webkitAudioContext": object,
            "RTCPeerConnection": object,
            "Intl": type("Intl", (), {
                "DateTimeFormat": lambda *a, **k: type("DTF", (), {
                    "resolvedOptions": lambda: {"timeZone": "America/Los_Angeles"},
                })(),
            })(),
        }
        # self-refs
        win["window"] = win
        win["self"] = win
        win["top"] = win
        win["parent"] = win
        win["globalThis"] = win

        # Apply CDP snapshot overrides if present
        snap = p.cdp_snapshot or {}
        if snap.get("userAgent"):
            nav.userAgent = snap["userAgent"]
        if snap.get("webgl_vendor"):
            CanvasContext.getParameter = (  # type: ignore
                lambda self, param, _v=snap.get("webgl_vendor"), _r=snap.get("webgl_renderer"):
                {37445: _v, 37446: _r or _v}.get(param, "WebGL")
            )
        if snap.get("localStorage"):
            for k, v in snap["localStorage"].items():
                self.localStorage.setItem(k, v)

        self.window = win
        self.document = doc
        self.navigator = nav
        self.location = loc
        self.screen = scr
        self.performance = perf


def _fill_random(arr: Any) -> Any:
    try:
        n = len(arr)
        for i in range(n):
            arr[i] = random.randint(0, 255)
    except Exception:
        pass
    return arr


def _prop_get(obj: Any, key: Any) -> Any:
    """JS-like property access."""
    if obj is None:
        raise TypeError("Cannot read property of null/undefined")
    k = key
    sk = str(key) if not isinstance(key, (int, float)) else key

    # dict-like window
    if isinstance(obj, dict):
        if k in obj:
            return obj[k]
        if sk in obj:
            return obj[sk]
        # Array.from special
        if sk == "from" and "from_" in obj:
            return obj["from_"]
        raise KeyError(key)

    if isinstance(obj, OrderedMap):
        return obj[k]

    if isinstance(obj, PseudoStorage):
        if sk in ("length",):
            return obj.length
        if sk in ("getItem", "setItem", "removeItem", "clear", "key", "keys"):
            return getattr(obj, sk)
        return obj.getItem(sk)

    if isinstance(obj, list):
        if isinstance(k, (int, float)) and int(k) == k:
            return obj[int(k)]
        if sk == "length":
            return len(obj)
        if sk == "push":
            return obj.append
        if sk == "pop":
            return obj.pop
        if sk == "slice":
            return lambda *a: obj[slice(*a)] if a else list(obj)
        if sk == "map":
            return lambda fn: [fn(x) for x in obj]
        if sk == "filter":
            return lambda fn: [x for x in obj if fn(x)]
        if sk == "join":
            return lambda sep=",": sep.join(str(x) for x in obj)
        if sk == "indexOf":
            return lambda x: obj.index(x) if x in obj else -1
        if sk == "includes":
            return lambda x: x in obj
        if sk == "shift":
            return lambda: obj.pop(0) if obj else None
        if sk == "splice":
            def _sp(start, delete_count=None, *items):
                start = int(start)
                if delete_count is None:
                    del obj[start:]
                    return []
                end = start + int(delete_count)
                deleted = obj[start:end]
                obj[start:end] = list(items)
                return deleted
            return _sp
        if sk == "concat":
            return lambda other: list(obj) + list(other or [])
        if sk == "forEach":
            return lambda fn: [fn(x) for x in obj]
        if sk == "find":
            return lambda fn: next((x for x in obj if fn(x)), None)

    if isinstance(obj, str):
        if isinstance(k, (int, float)) and int(k) == k:
            i = int(k)
            return obj[i] if 0 <= i < len(obj) else undefined
        if sk == "length":
            return len(obj)
        if sk == "charCodeAt":
            return lambda i: ord(obj[int(i)]) if 0 <= int(i) < len(obj) else None
        if sk == "charAt":
            return lambda i: obj[int(i)] if 0 <= int(i) < len(obj) else ""
        if sk == "indexOf":
            return lambda sub, start=0: obj.find(str(sub), int(start))
        if sk == "slice":
            return lambda *a: obj[slice(*[int(x) for x in a])]
        if sk == "substring":
            return lambda a, b=None: obj[int(a): int(b) if b is not None else None]
        if sk == "substr":
            return lambda a, n=None: obj[int(a): int(a) + int(n) if n is not None else None]
        if sk == "split":
            return lambda sep: obj.split(str(sep) if sep is not None else "")
        if sk == "replace":
            return lambda a, b: obj.replace(str(a), str(b), 1)
        if sk == "toLowerCase":
            return lambda: obj.lower()
        if sk == "toUpperCase":
            return lambda: obj.upper()
        if sk == "trim":
            return lambda: obj.strip()
        if sk == "match":
            return lambda pat: None  # simplified
        if sk == "startsWith":
            return lambda s: obj.startswith(str(s))
        if sk == "endsWith":
            return lambda s: obj.endswith(str(s))
        if sk == "includes":
            return lambda s: str(s) in obj
        if sk == "padStart":
            return lambda n, ch=" ": obj.rjust(int(n), str(ch))
        if sk == "concat":
            return lambda *a: obj + "".join(str(x) for x in a)

    # Python keyword method aliases (Object.is / Array.from)
    if sk == "is" and hasattr(obj, "is_"):
        return getattr(obj, "is_")
    if sk == "from" and hasattr(obj, "from_"):
        return getattr(obj, "from_")

    # generic attribute
    name = sk if isinstance(sk, str) else str(sk)
    if hasattr(obj, name):
        return getattr(obj, name)
    if hasattr(obj, str(k)):
        return getattr(obj, str(k))
    if isinstance(obj, type) and hasattr(obj, str(sk)):
        return getattr(obj, str(sk))
    raise KeyError(key)


# sentinel for JS undefined
class _Undefined:
    def __repr__(self):
        return "undefined"

    def __str__(self):
        return "undefined"

    def __bool__(self):
        return False


undefined = _Undefined()


def js_to_str(value: Any) -> str:
    if value is None or isinstance(value, _Undefined):
        return "undefined"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        # JS number stringification
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    if callable(value):
        name = getattr(value, "__name__", "")
        if name in ("abs", "random", "create", "keys", "set", "now", "parse", "stringify"):
            return _native(name if name != "now" else "")
        return _native(name or "")
    if isinstance(value, list):
        return ",".join(js_to_str(x) for x in value)
    if isinstance(value, dict) and value.get("window") is value:
        return "[object Window]"
    # type tags
    tn = type(value).__name__
    tags = {
        "_Math": "[object Math]",
        "_Reflect": "[object Reflect]",
        "_Perf": "[object Performance]",
        "PseudoStorage": "[object Storage]",
        "_Object": _native("Object"),
        "_JSON": "[object JSON]",
        "_Array": _native("Array"),
        "_Date": _native("Date"),
        "_Nav": "[object Navigator]",
        "_Doc": "[object HTMLDocument]",
        "_Screen": "[object Screen]",
        "_Loc": "[object Location]",
    }
    if tn in tags:
        return tags[tn]
    if hasattr(value, "tagName"):
        return f"[object HTML{getattr(value, 'tagName', 'Element')}Element]"
    return str(value)


# ---------------------------------------------------------------------------
# Core VM (shared by turnstile + collector + snapshot)
# ---------------------------------------------------------------------------

class SentinelVM:
    """
    Official Map-based bytecode VM.

    Opcode map (sdk.js 20260219f9f6):
      0  nested On/jt runner
      1  XOR_STR
      2  SET_VALUE
      3  success → btoa (result)
      4  error → btoa reject
      5  ADD_OR_PUSH
      6  ARRAY_ACCESS  m[n]=m[e][m[r]]
      7  CALL
      8  COPY
      9  instruction queue
      10 window
      11 GET_SCRIPT_SRC
      12 GET_MAP (self)
      13 TRY_CALL (no result)
      14 JSON_PARSE
      15 JSON_STRINGIFY
      16 seed (XOR key / p)
      17 CALL_AND_SET (+ promise)
      18 ATOB
      19 BTOA
      20 IF_EQUAL_CALL
      21 IF_ABS_DIFF_CALL
      22 RUN_NESTED_INSTRS
      23 IF_DEFINED_CALL
      24 BIND_METHOD
      25-28 noops / subtract variants
      29 LESS_THAN
      30 DEFINE_FN (async callable)
      33 MULTIPLY
      34 PROMISE_SET
      35 DIVIDE
    """

    def __init__(
        self,
        browser: Optional[PseudoBrowser] = None,
        seed: str = "",
        mode: str = "turnstile",
    ) -> None:
        self.browser = browser or PseudoBrowser()
        self.seed = seed
        self.mode = mode  # turnstile | collector
        self.m: Dict[Any, Any] = {}
        self.steps = 0
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self._done = False
        self._start = time.time()
        self._install_opcodes()

    def _install_opcodes(self) -> None:
        m = self.m
        m.clear()

        # 0: nested VM entry (On) — re-run a dx blob with current seed
        def op_nested(dx_blob: Any) -> Any:
            # used as factory for nested solve; rare on auth path
            try:
                nested = SentinelVM(self.browser, seed=self.seed, mode=self.mode)
                return nested.run_instructions(
                    decode_dx(str(dx_blob), self.seed) if isinstance(dx_blob, str) and len(str(dx_blob)) > 40
                    else (dx_blob if isinstance(dx_blob, list) else [])
                )
            except Exception:
                return None

        def op_xor(e: Any, t: Any) -> None:
            m[e] = xor_string(js_to_str(m.get(e)), js_to_str(m.get(t)))

        def op_set(e: Any, t: Any) -> None:
            m[e] = t

        def op_success(val: Any) -> None:
            if self._done:
                return
            self._done = True
            # official: btoa("" + t)
            self.result = btoa_str(js_to_str(val) if not isinstance(val, str) else val)
            # If val is already binary-ish latin1 from fingerprint packing, preserve
            if isinstance(val, str):
                self.result = btoa_str(val)

        def op_error(val: Any) -> None:
            if self._done:
                return
            self._done = True
            self.error = btoa_str(js_to_str(val))

        def op_add_push(e: Any, t: Any) -> None:
            cur = m.get(e)
            incoming = m.get(t)
            if isinstance(cur, list):
                cur.append(incoming)
                m[e] = cur
            else:
                m[e] = js_to_str(cur) + js_to_str(incoming) if not (
                    isinstance(cur, (int, float)) and isinstance(incoming, (int, float))
                    and not isinstance(cur, bool) and not isinstance(incoming, bool)
                ) else cur + incoming  # type: ignore
                # Prefer string concat when either side is str (JS +)
                if isinstance(cur, str) or isinstance(incoming, str):
                    m[e] = js_to_str(cur) + js_to_str(incoming)
                elif isinstance(cur, list):
                    pass
                elif isinstance(cur, (int, float)) and isinstance(incoming, (int, float)):
                    m[e] = cur + incoming  # type: ignore
                else:
                    m[e] = js_to_str(cur) + js_to_str(incoming)

        def op_sub_splice(e: Any, t: Any) -> None:
            cur = m.get(e)
            incoming = m.get(t)
            if isinstance(cur, list):
                try:
                    idx = cur.index(incoming)
                    cur.pop(idx)
                except ValueError:
                    pass
                m[e] = cur
            else:
                try:
                    m[e] = (cur or 0) - (incoming or 0)  # type: ignore
                except Exception:
                    m[e] = float("nan")

        def op_lt(e: Any, t: Any, n: Any) -> None:
            try:
                m[e] = m.get(t) < m.get(n)  # type: ignore
            except Exception:
                m[e] = False

        def op_mul(e: Any, t: Any, n: Any) -> None:
            try:
                m[e] = float(m.get(t) or 0) * float(m.get(n) or 0)
            except Exception:
                m[e] = 0

        def op_div(e: Any, t: Any, n: Any) -> None:
            try:
                c = float(m.get(n) or 0)
                m[e] = 0 if c == 0 else float(m.get(t) or 0) / c
            except Exception:
                m[e] = 0

        def op_index(e: Any, t: Any, n: Any) -> None:
            base = m.get(t)
            key = m.get(n)
            try:
                m[e] = _prop_get(base, key)
            except Exception as ex:
                m[e] = undefined

        def op_call(e: Any, *args: Any) -> None:
            fn = m.get(e)
            call_args = [m.get(a) for a in args]
            if fn is None or isinstance(fn, _Undefined):
                return
            # Reflect.set special
            if fn is self.browser.window.get("Reflect") or (
                callable(fn) and getattr(fn, "__name__", "") == "set"
                and len(call_args) >= 3
            ):
                # detect Reflect.set by identity
                pass
            try:
                if callable(fn):
                    fn(*call_args)
                else:
                    # maybe bound method stored as tuple (fn, this)
                    if isinstance(fn, tuple) and callable(fn[0]):
                        fn[0](*call_args)
            except Exception:
                pass

        def op_copy(e: Any, t: Any) -> None:
            m[e] = m.get(t)

        def op_script_src(e: Any, t: Any) -> None:
            pat = js_to_str(m.get(t))
            found = None
            try:
                scripts = getattr(self.browser.document, "scripts", []) or []
                for sc in scripts:
                    src = getattr(sc, "src", "") or ""
                    if pat and pat in src:
                        # match groups like c/xxx/_
                        import re
                        mm = re.search(pat, src)
                        if mm:
                            found = mm.group(0) if mm.lastindex is None else (mm.group(1) or mm.group(0))
                            break
                        found = src
                        break
            except Exception:
                found = None
            m[e] = found

        def op_get_map(e: Any) -> None:
            m[e] = m  # self-ref; careful

        def op_try_call(e: Any, t: Any, *args: Any) -> None:
            """Official et/Xt: try { St.get(fn)(...rawArgs) } catch { St.set(err, ""+e) }

            Args are intentionally NOT map-resolved. When fn is op_index/ARRAY_ACCESS,
            raw args are (destSlot, baseSlot, keySlot) and op_index does m.get itself.
            Resolving first breaks event.property reads in collector handlers.
            """
            fn = m.get(t)
            try:
                if callable(fn):
                    fn(*args)
            except Exception as ex:
                m[e] = str(ex)

        def op_json_parse(e: Any, t: Any) -> None:
            try:
                m[e] = json.loads(js_to_str(m.get(t)))
            except Exception as ex:
                m[e] = str(ex)

        def op_json_stringify(e: Any, t: Any) -> None:
            try:
                val = m.get(t)
                if isinstance(val, OrderedMap):
                    # ordered object
                    d = {k: val._values[k] for k in val._keys}
                    m[e] = json.dumps(d, separators=(",", ":"), ensure_ascii=False, default=str)
                elif isinstance(val, dict) and val is not m and val.get("window") is not val:
                    m[e] = json.dumps(val, separators=(",", ":"), ensure_ascii=False, default=str)
                else:
                    m[e] = json.dumps(val, separators=(",", ":"), ensure_ascii=False, default=str)
            except Exception:
                m[e] = "null"

        def op_call_set(e: Any, t: Any, *args: Any) -> None:
            fn = m.get(t)
            call_args = [m.get(a) for a in args]
            try:
                if fn is undefined or fn is None:
                    m[e] = undefined
                    return
                # string special paths from codebai-compat layer
                if fn == "window.performance.now" or (
                    callable(fn) and getattr(fn, "__name__", "") == "_now"
                ):
                    m[e] = (time.time() - self._start) * 1000.0 + random.random()
                    return
                if callable(fn):
                    res = fn(*call_args)
                    # promise-like
                    if hasattr(res, "then") and callable(res.then):
                        res.then(lambda v: m.__setitem__(e, v))
                        return
                    m[e] = res
                    return
                # Reflect.set as callable stored
                m[e] = undefined
            except Exception as ex:
                m[e] = str(ex)

        def op_atob(e: Any) -> None:
            try:
                m[e] = atob_str(js_to_str(m.get(e)))
            except Exception as ex:
                m[e] = str(ex)

        def op_btoa(e: Any) -> None:
            try:
                m[e] = btoa_str(js_to_str(m.get(e)))
            except Exception as ex:
                m[e] = str(ex)

        def op_if_eq(e: Any, t: Any, n: Any, *args: Any) -> None:
            if m.get(e) == m.get(t):
                fn = m.get(n)
                if callable(fn):
                    fn(*args)  # args are raw slots in official: St.get(r)(...o) where o are slots not values!
                    # official: St.get(n)===St.get(e)?St.get(r)(...o):null
                    # where o are the raw instruction args (slot ids), and CALL resolves them
                    # Actually: (...o)=> and then St.get(r)(...o) — o NOT resolved!
                    # Wait: ft: (n,e,r,...o)=>St.get(n)===St.get(e)?St.get(r)(...o):null
                    # The ...o are passed as-is to the function. If r is success callback (3),
                    # it receives slot ids? Looking at obt/codebai func_20:
                    # if process_map[e]==process_map[t]: target(*[process_map[arg] for arg in args])
                    # codebai RESOLVES. But official does NOT resolve for ft!
                    # However success fn expects the value, so the instruction usually passes
                    # a slot that is used by a wrapper. We'll resolve like codebai (works in practice).

        def op_if_eq_fixed(e: Any, t: Any, n: Any, *args: Any) -> None:
            # Official ft: St.get(n)===St.get(e)?St.get(r)(...rawArgs):null
            if m.get(e) == m.get(t):
                fn = m.get(n)
                if callable(fn):
                    fn(*args)

        def op_if_abs(e: Any, t: Any, n: Any, o: Any, *args: Any) -> None:
            # Official lt: abs(a-b)>c ? St.get(o)(...rawArgs) : null
            try:
                if abs(float(m.get(e) or 0) - float(m.get(t) or 0)) > float(m.get(n) or 0):
                    fn = m.get(o)
                    if callable(fn):
                        fn(*args)
            except Exception:
                pass

        def op_if_def(e: Any, t: Any, *args: Any) -> None:
            # Official at: void 0!==St.get(n)?St.get(e)(...rawArgs):null
            val = m.get(e)
            if val is not None and not isinstance(val, _Undefined):
                fn = m.get(t)
                if callable(fn):
                    fn(*args)

        def op_bind(e: Any, t: Any, n: Any) -> None:
            base = m.get(t)
            key = m.get(n)
            try:
                method = _prop_get(base, key)
                if callable(method):
                    # bind: wrap to ignore this
                    def bound(*a, _fn=method, _this=base):
                        return _fn(*a)
                    try:
                        bound.__name__ = getattr(method, "__name__", "bound")
                    except Exception:
                        pass
                    m[e] = bound
                else:
                    m[e] = method
            except Exception:
                m[e] = undefined

        def op_promise_set(e: Any, t: Any) -> None:
            val = m.get(t)
            if hasattr(val, "then") and callable(val.then):
                val.then(lambda v: m.__setitem__(e, v))
            else:
                m[e] = val

        def op_nested_run(e: Any, instrs: Any) -> None:
            """Replace instruction queue with nested list, restore after."""
            # handled specially in run loop
            pass

        def op_define_fn(e: Any, ret_slot: Any, arg_slots: Any, body: Any) -> None:
            """
            Create a callable stored at e.
            Official: if body is array of instr (when arg_slots is array),
            bind args then run body, return m[ret_slot].
            """
            use_args = isinstance(arg_slots, list)
            params = arg_slots if use_args else []
            body_instr = body if use_args else (arg_slots or [])
            if not use_args:
                body_instr = arg_slots if isinstance(arg_slots, list) else (body or [])
                params = []
            # re-read official:
            # (t,n,e,r) => { c=Array.isArray(r); s=c?e:[]; u=(c?r:e)||[];
            #   St.set(t, (...args) => { save queue; if c bind params; set queue to u; run; return get(n) })
            # }
            # So: e=ret_slot? Actually: t=fn_slot, n=return_slot, e=param_slots or body, r=body or param
            # c = Array.isArray(r) → if r is array, then params=e, body=r; else params=[], body=e

            fn_slot = e
            ret = ret_slot
            if isinstance(body, list):
                param_list = arg_slots if isinstance(arg_slots, list) else []
                body_list = body
            else:
                param_list = []
                body_list = arg_slots if isinstance(arg_slots, list) else []

            def _fn(*call_args, _params=param_list, _body=body_list, _ret=ret):
                # Collector handlers are DEFINE_FN closures over the St map. Official
                # jt done-flag would skip them after collector promise settles; we clear
                # _done for the nested body so simulate_user_activity can populate
                # __oai_so_* before snapshot_dx runs.
                saved = list(m.get(9) or [])
                saved_done = self._done
                saved_result = self.result
                saved_error = self.error
                try:
                    for i, pslot in enumerate(_params):
                        if i < len(call_args):
                            m[pslot] = call_args[i]
                    m[9] = list(_body)
                    self._done = False
                    self._run_queue()
                    return m.get(_ret)
                finally:
                    m[9] = saved
                    self._done = saved_done
                    # keep outer t/so result unless nested body intentionally produced one
                    # and outer had none
                    if saved_result is not None:
                        self.result = saved_result
                    if saved_error is not None:
                        self.error = saved_error

            m[fn_slot] = _fn

        def op_noop(*_a: Any) -> None:
            return None

        # Install base opcodes
        m[0] = op_nested
        m[1] = op_xor
        m[2] = op_set
        m[3] = op_success
        m[4] = op_error
        m[5] = op_add_push
        m[6] = op_index
        m[7] = op_call
        m[8] = op_copy
        m[9] = []  # queue placeholder
        m[10] = self.browser.window
        m[11] = op_script_src
        m[12] = op_get_map
        m[13] = op_try_call
        m[14] = op_json_parse
        m[15] = op_json_stringify
        m[16] = self.seed
        m[17] = op_call_set
        m[18] = op_atob
        m[19] = op_btoa
        m[20] = op_if_eq_fixed
        m[21] = op_if_abs
        m[22] = op_nested_run  # special-cased
        m[23] = op_if_def
        m[24] = op_bind
        m[25] = op_noop
        m[26] = op_noop
        m[27] = op_sub_splice
        m[28] = op_noop
        m[29] = op_lt
        m[30] = op_define_fn
        m[31] = op_noop  # INCREMENT sometimes
        m[32] = op_noop
        m[33] = op_mul
        m[34] = op_promise_set
        m[35] = op_div

        # codebai-compat: also expose common string tags some bytecode expects via SET
        # (not required if index/bind work)

    def _exec_one(self, token: Sequence[Any]) -> None:
        if not token:
            return
        op = token[0]
        args = list(token[1:])
        fn = self.m.get(op)

        # opcode 22: nested instruction list
        if op == 22 or (callable(fn) and getattr(fn, "__name__", "") == "op_nested_run"):
            # (n, e) where e is list of instructions or slot holding list
            if len(args) >= 2:
                dest, body = args[0], args[1]
                instrs = body if isinstance(body, list) else self.m.get(body)
                if isinstance(instrs, list):
                    saved = list(self.m.get(9) or [])
                    self.m[9] = list(instrs)
                    try:
                        self._run_queue()
                    except Exception as ex:
                        self.m[dest] = str(ex)
                    finally:
                        self.m[9] = saved
                return
            return

        # opcode 30 define_fn needs special arg handling (lists as literals)
        if op == 30 and callable(fn):
            # args: fn_slot, ret_slot, params_or_body, body?
            fn(*args)
            self.steps += 1
            return

        if not callable(fn):
            return
        try:
            fn(*args)
        except TypeError:
            # some ops receive fewer args
            try:
                fn(*args[:4])
            except Exception:
                pass
        except Exception:
            pass
        self.steps += 1

    def _run_queue(self) -> None:
        """Execute instruction queue at slot 9.

        Must re-read m[9] each step: DEFINE_FN / nested ops replace the list
        in-place on the map (official An() always does bn.get(Zt).shift()).
        """
        guard = 0
        while not self._done and guard < 200000:
            guard += 1
            q = self.m.get(9)
            if not isinstance(q, list) or not q:
                break
            token = q.pop(0)
            if not isinstance(token, (list, tuple)):
                continue
            self._exec_one(token)

    def run_instructions(self, instructions: List[Any]) -> Optional[str]:
        self.result = None
        self.error = None
        self._done = False
        self.steps = 0
        self.m[9] = list(instructions)
        self.m[16] = self.seed
        self._run_queue()
        return self.result

    def run_dx(self, dx: str, seed: Optional[str] = None, reinit: bool = True) -> Optional[str]:
        if seed is not None:
            self.seed = seed
        if reinit:
            self._install_opcodes()
        else:
            # snapshot path: keep map state, only reset result flags
            self.result = None
            self.error = None
            self._done = False
            self.m[16] = self.seed
        instr = decode_dx(dx, self.seed)
        return self.run_instructions(instr)


# ---------------------------------------------------------------------------
# High-level: solve t + so for auth path
# ---------------------------------------------------------------------------

@dataclass
class PureSolveResult:
    t: Optional[str] = None
    so: Optional[str] = None
    c: Optional[str] = None
    p_pow: Optional[str] = None
    t_len: int = 0
    so_len: int = 0
    t_prefix: str = ""
    so_prefix: str = ""
    steps_t: int = 0
    steps_collector: int = 0
    steps_snapshot: int = 0
    error: Optional[str] = None
    mode: str = "pure-vm"

    def morph_ok(self) -> bool:
        # thresholds from NOBROWSER_TSO_REVERSE.md
        return (self.t_len >= 1150) and (self.so_len >= 460 or not self.so)


def solve_turnstile_t(
    dx: str,
    p: str,
    browser: Optional[PseudoBrowser] = None,
) -> Tuple[Optional[str], int]:
    """P2: pure-Python obt VM → turnstile token t."""
    vm = SentinelVM(browser=browser or PseudoBrowser(), seed=p, mode="turnstile")
    t = vm.run_dx(dx, seed=p, reinit=True)
    return t, vm.steps


def solve_collector_so(
    collector_dx: str,
    snapshot_dx: str,
    p: str,
    browser: Optional[PseudoBrowser] = None,
    simulate_ms: float = 1200.0,
) -> Tuple[Optional[str], int, int, Dict[str, int]]:
    """P3: collector_dx → simulate user events → snapshot_dx → so.

    Auth so is NOT a pure crypto transform of collector_dx. Official flow:
      Et(challenge) runs collector_dx which registers window listeners
      (keydown/pointermove/click/scroll/paste/wheel) and seeds __oai_so_* counters.
      After Xn=5000ms observer window, sessionObserverToken runs snapshot_dx
      which packages the counters into `so`.

    We compress the observer window into simulate_ms of synthetic events.
    """
    browser = browser or PseudoBrowser()
    vm = SentinelVM(browser=browser, seed=p, mode="collector")
    # 1) collector — install listeners + init __oai_so_* (discard short result)
    try:
        vm.run_dx(collector_dx, seed=p, reinit=True)
    except Exception:
        pass
    steps_c = vm.steps
    # 2) simulate human input into the registered listeners
    activity: Dict[str, int] = {}
    try:
        activity = browser.simulate_user_activity(duration_ms=simulate_ms)
    except Exception:
        activity = {}
    # 3) snapshot — reuse St-equivalent map state (reinit=False)
    so = None
    try:
        so = vm.run_dx(snapshot_dx, seed=p, reinit=False)
    except Exception:
        so = None
    steps_s = vm.steps
    return so, steps_c, steps_s, activity


def solve_auth_tso(
    challenge: Dict[str, Any],
    request_p: str,
    *,
    device_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    cdp_snapshot: Optional[Dict[str, Any]] = None,
    profile: Optional[FingerprintProfile] = None,
    fp: Optional[Dict[str, Any]] = None,
) -> PureSolveResult:
    """
    Full pure path: challenge from POST /req + request_p → t + so.

    fp: 可选，chatgpt.choose_fp() 返回的配套指纹 dict；与 profile 二选一，
        profile 优先。未传时保持向后兼容（合成默认指纹）。
    """
    out = PureSolveResult()
    try:
        out.c = str(challenge.get("token") or "")
        if profile is not None:
            fp_prof = profile
        elif fp:
            fp_prof = fingerprint_from_dict(
                fp, device_id=device_id, user_agent=user_agent, cdp_snapshot=cdp_snapshot,
            )
        else:
            fp_prof = FingerprintProfile(
                device_id=device_id or str(uuid.uuid4()),
                user_agent=user_agent or FingerprintProfile.user_agent,
                cdp_snapshot=cdp_snapshot or {},
            )
        if device_id:
            fp_prof.device_id = device_id
        if user_agent:
            fp_prof.user_agent = user_agent
        if cdp_snapshot:
            fp_prof.cdp_snapshot = {**(fp_prof.cdp_snapshot or {}), **cdp_snapshot}
        browser = PseudoBrowser(fp_prof)

        turnstile = challenge.get("turnstile") or {}
        dx = turnstile.get("dx")
        if turnstile.get("required") and dx:
            t, steps = solve_turnstile_t(str(dx), request_p, browser=browser)
            out.t = t
            out.steps_t = steps
            out.t_len = len(t or "")
            out.t_prefix = (t or "")[:16]

        so_info = challenge.get("so") or {}
        cdx = so_info.get("collector_dx")
        sdx = so_info.get("snapshot_dx")
        if so_info.get("required") and cdx and sdx:
            so, sc, ss, _act = solve_collector_so(
                str(cdx), str(sdx), request_p, browser=browser,
            )
            out.so = so
            out.steps_collector = sc
            out.steps_snapshot = ss
            out.so_len = len(so or "")
            out.so_prefix = (so or "")[:16]

        # PoW p for envelope
        pow_info = challenge.get("proofofwork") or {}
        if pow_info.get("required") and pow_info.get("seed"):
            try:
                from sentinel_sdk import SentinelTokenGenerator
                gen = SentinelTokenGenerator(
                    device_id=fp_prof.device_id,
                    user_agent=fp_prof.user_agent,
                    screen=f"{fp_prof.screen_width}x{fp_prof.screen_height}",
                    languages=fp_prof.languages,
                    hardware_concurrency=fp_prof.hardware_concurrency,
                )
                out.p_pow = gen.generate_token(
                    seed=str(pow_info.get("seed")),
                    difficulty=str(pow_info.get("difficulty") or "0"),
                )
            except Exception as e:
                out.error = f"pow: {e}"
    except Exception as e:
        out.error = f"{type(e).__name__}: {e}"
    return out


def build_pure_envelopes(
    challenge: Dict[str, Any],
    request_p: str,
    *,
    device_id: str,
    flow: str,
    user_agent: Optional[str] = None,
    cdp_snapshot: Optional[Dict[str, Any]] = None,
    fp: Optional[Dict[str, Any]] = None,
    profile: Optional[FingerprintProfile] = None,
) -> Dict[str, Any]:
    """Build openai-sentinel-token + openai-sentinel-so-token JSON strings.

    fp / profile 可选：传入后与 HTTP 会话配套对齐（向后兼容默认随机合成）。
    """
    r = solve_auth_tso(
        challenge, request_p,
        device_id=device_id, user_agent=user_agent, cdp_snapshot=cdp_snapshot,
        profile=profile, fp=fp,
    )
    sentinel_token = None
    so_token = None
    if r.t and r.c:
        env = {
            "p": r.p_pow or "",
            "t": r.t,
            "c": r.c,
            "id": device_id,
            "flow": flow,
        }
        sentinel_token = json.dumps(env, separators=(",", ":"), ensure_ascii=False)
    if r.so and r.c:
        so_env = {"so": r.so, "c": r.c, "id": device_id, "flow": flow}
        so_token = json.dumps(so_env, separators=(",", ":"), ensure_ascii=False)
    return {
        "ok": bool(sentinel_token and (so_token or flow not in (
            "oauth_create_account", "username_password_create",
        ))),
        "mode": "pure-vm",
        "sentinel_token": sentinel_token,
        "so_token": so_token,
        "t_len": r.t_len,
        "so_len": r.so_len,
        "t_prefix": r.t_prefix,
        "so_prefix": r.so_prefix,
        "t_morph_ok": r.t_len >= 1150,
        "so_morph_ok": r.so_len >= 460 if r.so else False,
        "steps_t": r.steps_t,
        "steps_collector": r.steps_collector,
        "steps_snapshot": r.steps_snapshot,
        "error": r.error,
        "solve": r,
    }


# ---------------------------------------------------------------------------
# CDP fingerprint snapshot helper (P4)
# ---------------------------------------------------------------------------

CDP_SNAPSHOT_JS = r"""
() => {
  const scripts = Array.from(document.scripts || []).map(s => s.src).filter(Boolean);
  let webgl_vendor = null, webgl_renderer = null;
  try {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if (dbg) {
        webgl_vendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
        webgl_renderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
      }
    }
  } catch (e) {}
  let canvas_hash = null;
  try {
    const c = document.createElement('canvas');
    c.width = 240; c.height = 60;
    const ctx = c.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(0,0,240,60);
    ctx.fillStyle = '#069';
    ctx.fillText('sentinel-fp', 2, 15);
    canvas_hash = c.toDataURL();
  } catch (e) {}
  const ls = {};
  try { for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i); ls[k]=localStorage.getItem(k);} } catch(e){}
  return {
    userAgent: navigator.userAgent,
    language: navigator.language,
    languages: Array.from(navigator.languages || []),
    platform: navigator.platform,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    maxTouchPoints: navigator.maxTouchPoints,
    vendor: navigator.vendor,
    screen: {width: screen.width, height: screen.height, colorDepth: screen.colorDepth},
    devicePixelRatio: devicePixelRatio,
    scripts,
    webgl_vendor, webgl_renderer,
    canvas_hash,
    localStorage: ls,
    timeOrigin: performance.timeOrigin,
    href: location.href,
  };
}
"""


def export_cdp_fingerprint_snapshot(timeout_ms: int = 30000) -> Dict[str, Any]:
    """
    P4 optional: one-shot Chrome export of fingerprint fields for injection.
    Requires Playwright + local Chrome. Result is pure data — VM stays browserless.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return {"error": f"playwright missing: {e}"}
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    import os
    if not os.path.isfile(chrome):
        return {"error": f"chrome missing: {chrome}"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, executable_path=chrome,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            page.goto(
                "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                timeout=timeout_ms, wait_until="domcontentloaded",
            )
            snap = page.evaluate(CDP_SNAPSHOT_JS)
            browser.close()
            return snap if isinstance(snap, dict) else {"error": "bad snapshot"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# CLI self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("sentinel_pure_vm self-test: live /req + solve t/so")
    try:
        from curl_cffi import requests as crequests
    except Exception:
        print("curl_cffi required")
        sys.exit(1)

    from sentinel_sdk import SentinelTokenGenerator, fetch_sentinel_challenge, SENTINEL_VERSION

    did = str(uuid.uuid4())
    gen = SentinelTokenGenerator(device_id=did)
    p = gen.generate_requirements_token()
    ch = fetch_sentinel_challenge(did, "oauth_create_account", None, request_p=p)
    if not ch:
        print("FAIL: /req returned nothing")
        sys.exit(2)
    print("challenge keys:", list(ch.keys()))
    print("dx lens:", len((ch.get("turnstile") or {}).get("dx") or ""),
          len((ch.get("so") or {}).get("collector_dx") or ""),
          len((ch.get("so") or {}).get("snapshot_dx") or ""))

    # decode smoke
    dx = (ch.get("turnstile") or {}).get("dx")
    if dx:
        instr = decode_dx(dx, p)
        print(f"turnstile instr count={len(instr)} first={instr[:3]}")

    r = build_pure_envelopes(ch, p, device_id=did, flow="oauth_create_account")
    print(json.dumps({k: v for k, v in r.items() if k not in ("sentinel_token", "so_token", "solve")},
                     ensure_ascii=False, indent=2))
    if r.get("t_len"):
        print("t morph target ~1332, got", r["t_len"], "prefix", r.get("t_prefix"))
    if r.get("so_len"):
        print("so morph target ~520, got", r["so_len"], "prefix", r.get("so_prefix"))
