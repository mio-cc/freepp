"""出口 IP 真实国家多源探测模块。

每次探测走代理链路（curl_cffi chrome TLS 指纹），从公开 IP 地理库源拉取出口 IP。
三源交叉验证:
    1. ip-api.com  (HTTP 免费, 45/min/IP, 无需 token, 精度高)
    2. ipwho.is    (HTTPS 免费, 无需 token)
    3. ipinfo.io   (HTTPS 免费 Lite 有限量, 无需 token; 高精度城市)

策略:
    - 任一源成功即可返回（记录来源），速度优先。
    - 多个源都成功时取「多数一致」的国家码；冲突时回退置信度最高的单一源。
    - 结果带 confidence(0~1) 与 sources 明细，供前端显示/落库复盘。

因为探测请求本身走代理出口，rate limit 按出口 IP 计：每段探测都是不同出口，
天然避开了 ip-api 45/min/IP 限制。同一 proxy_url(同一 sticky session) 短窗口
内按 TTL 缓存，避免重试时重复探测。
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time
from typing import Any, Callable

try:
    from curl_cffi import requests as _curl  # type: ignore
except Exception:  # pragma: no cover
    _curl = None

PROBE_TIMEOUT = 10.0
_CACHE_TTL = float(os.environ.get("PROBE_GEO_CACHE_TTL", "5") or "5")

_cfg: Any = None  # 延迟注入 settings (避免循环 import)


def bind_settings(settings: Any) -> None:
    """注入全局 settings 以便读取 geo 探测开关/超时/源列表。"""
    global _cfg
    _cfg = settings


# ---- 各源解析 ---
def _parse_ip_api(d: dict[str, Any]) -> tuple[str, str, str, str]:
    if d.get("status") != "success":
        raise ValueError(f"ip-api status={d.get('status')}")
    return (str(d.get("query") or ""),
            str(d.get("countryCode") or "").upper(),
            str(d.get("city") or ""),
            str(d.get("regionName") or ""))


def _parse_ipwhois(d: dict[str, Any]) -> tuple[str, str, str, str]:
    if not d.get("success"):
        raise ValueError(f"ipwhois success={d.get('success')}")
    return (str(d.get("ip") or ""),
            str(d.get("country_code") or "").upper(),
            str(d.get("city") or ""),
            str(d.get("region") or ""))


def _parse_ipinfo(d: dict[str, Any]) -> tuple[str, str, str, str]:
    ip = str(d.get("ip") or "")
    cc = str(d.get("country") or "").upper()
    if not ip or not cc:
        raise ValueError("ipinfo 缺 ip/country")
    return (ip, cc, str(d.get("city") or ""), str(d.get("region") or ""))


PROVIDERS: dict[str, tuple[str, Callable[[dict[str, Any]], tuple[str, str, str, str]]]] = {
    "ip-api": ("http://ip-api.com/json/", _parse_ip_api),
    "ipwhois": ("https://ipwho.is/", _parse_ipwhois),
    "ipinfo": ("https://ipinfo.io/json", _parse_ipinfo),
}


def _default_sources() -> list[str]:
    if _cfg is not None:
        try:
            opts = (_cfg.raw or {}).get("geo") or {}
            src = opts.get("sources") or ["ip-api", "ipwhois", "ipinfo"]
            return [s for s in src if s in PROVIDERS]
        except Exception:
            pass
    return ["ip-api", "ipwhois", "ipinfo"]


def _default_timeout() -> float:
    if _cfg is not None:
        try:
            return float(((_cfg.raw or {}).get("geo") or {}).get("timeout", PROBE_TIMEOUT))
        except Exception:
            pass
    return PROBE_TIMEOUT


def _default_enabled() -> bool:
    if _cfg is not None:
        try:
            return bool(((_cfg.raw or {}).get("geo") or {}).get("enabled", True))
        except Exception:
            pass
    return True


# ---- 缓存 (TTL) ---
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def probe_country(proxy: str = "", timeout: float | None = None,
                  sources: list[str] | None = None) -> dict[str, Any]:
    """经代理探测出口真实国家。

    proxy 为空/直连时走本机出口。返回:
        {"ok", "ip", "country", "city", "confidence", "sources":
            [{"provider","ip","country","city","region"}...], "error", "ts"}
    """
    if _curl is None:
        return {"ok": False, "ip": "", "country": "", "city": "",
                "confidence": 0.0, "sources": [], "error": "curl_cffi 不可用"}
    timeout = timeout or _default_timeout()
    srcs = sources or _default_sources()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(proxy)
        if hit and hit[0] > now:
            return dict(hit[1])

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    ip_primary = ""
    if not _default_enabled():
        return {"ok": False, "ip": "", "country": "", "city": "",
                "confidence": 0.0, "sources": [], "error": "geo 探测已禁用"}
    for name in srcs:
        url, parse = PROVIDERS.get(name) or (None, None)
        if url is None:
            continue
        try:
            ip, cc, city, region = _probe(url, proxy, parse, timeout)
            results.append({"provider": name, "ip": ip, "country": cc,
                            "city": city, "region": region})
            if not ip_primary:
                ip_primary = ip
        except Exception as e:  # 跳过失败源
            errors.append(f"{name}: {type(e).__name__}: {e}")
    codes = [r["country"] for r in results if r["country"]]
    out: dict[str, Any] = {"ok": False, "ip": ip_primary, "country": "",
                           "city": "", "confidence": 0.0,
                           "sources": results, "error": "；".join(errors),
                           "ts": _utc_now()}
    if codes:
        out["ok"] = True
        out["country"] = majority_code(codes) or codes[0]
        matched = sum(1 for c in codes if c == out["country"])
        out["confidence"] = round(matched / len(codes), 2)
        # 主类国家的 city/region 优先
        for r in results:
            if r["country"] == out["country"] and r["city"]:
                out["city"] = r["city"]
                break
    with _cache_lock:
        _cache[proxy] = (now + _CACHE_TTL, out)
    return out


def _probe(url: str, proxy: str, parse: Callable[[dict[str, Any]], tuple[str, str, str, str]],
           timeout: float) -> tuple[str, str, str, str]:
    s = _curl.Session(impersonate="chrome131")
    try:
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        r = s.get(url, timeout=timeout, verify=False)
        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                d = {}
            return parse(d)
        raise ValueError(f"HTTP {r.status_code}")
    finally:
        try:
            s.close()
        except Exception:
            pass


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def majority_code(codes: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for c in codes:
        if c:
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda x: counts[x])


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()