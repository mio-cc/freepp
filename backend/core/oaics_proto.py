# -*- coding: utf-8 -*-
"""oaics_ custom Checkout 纯 HTTP 协议层 (对齐 link-pp handoff/protocol/stripe_checkout.py)。

checkout(custom) -> fetch state -> taxes -> 严格 0 元轮询 -> elements/sessions
(amount=0 + manual approval betas) -> confirmation_tokens (内联 paypal, 可选 P1)
-> checkout/confirm (可选 sentinel 头) -> 直出 redirect, 否则 Stripe intent
confirm (seti_/pi_, 复用 ctoken) -> redirect。不经过 cs_live_ 的 update/approve/poll。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import settings

STRIPE_API = "https://api.stripe.com"
STRIPE_VERSION_BASE = "2025-03-31.basil"
STRIPE_VERSION_FULL = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
DEFAULT_STRIPE_RUNTIME_VERSION = settings.stripe.get("runtime_version", "6f8494a281")
OPENAI_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"

# OpenAI Web 前端版本头 (对齐 link-pp chatgpt_context_headers)
_CHATGPT_CLIENT_VERSION = "prod-db390ebea64862bf1899c420a4c736e0cf639747"
_CHATGPT_CLIENT_BUILD_NUMBER = "7904904"

# Stripe.js 浏览器 TLS/client 头 (对齐 link-pp _stripe_headers)
_STRIPE_SEC_CH_UA = '"Chromium";v="146", "Google Chrome";v="146", "Not.A/Brand";v="99"'
_STRIPE_SEC_CH_UA_FULL = (
    '"Chromium";v="146.0.7423.118", '
    '"Google Chrome";v="146.0.7423.118", '
    '"Not.A/Brand";v="99.0.0.0"'
)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.7423.118 Safari/537.36"
)


def _stripe_headers() -> dict[str, str]:
    """对齐 link-pp _stripe_headers: 浏览器上下文 + UA + sec-ch-ua 家族。"""
    return {
        "User-Agent": _CHROME_UA,
        "Accept": "application/json",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
        "sec-ch-ua": _STRIPE_SEC_CH_UA,
        "sec-ch-ua-full-version-list": _STRIPE_SEC_CH_UA_FULL,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }


def _cookie_header_for(device_id: str, session_token: str) -> str:
    """构造与 chatgpt_session 一致的 oai-did + session token Cookie 头。

    参考 link-pp _session_cookie_header: sentinel /req 必须携带与后续
    ChatGPT 请求同一会话的 cookie。
    """
    device_id = str(device_id or "").strip()
    session_token = str(session_token or "").strip()
    if not device_id:
        device_id = str(uuid.uuid4())
    parts = [f"oai-did={device_id}"]
    if session_token:
        parts += [
            f"__Secure-next-auth.session-token={session_token}",
            f"next-auth.session-token={session_token}",
        ]
    return "; ".join(parts)


def _merge_set_cookie(cookie_jar: Optional[dict], resp) -> None:
    """把响应 Set-Cookie 合并进链级 cookie 罐 (仅保留名=值, 忽略过期/域属性)。

    对齐 link-pp CheckoutTransport.claim 的会话保持: 服务器下发的
    __cf_bm / CSRF 类 cookie 需要透传给后续请求, 否则 confirm/sentinel 被拒。
    """
    if not cookie_jar or resp is None:
        return
    try:
        headers = getattr(resp, "headers", None) or {}
        raw = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    except Exception:
        return
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, (list, tuple)):
        text = "\n".join(str(item) for item in raw)
    else:
        return
    for line in text.splitlines():
        name, _, rest = str(line).partition("=")
        name = name.strip()
        if not name:
            continue
        value = rest.split(";", 1)[0].strip()
        cookie_jar[name] = value


def _cookie_header(device_id: str, session_token: str,
                   cookie_jar: Optional[dict] = None) -> str:
    """组装 Cookie 头: 基础 oai-did/session token + 服务器下发 cookie 透传。"""
    parts = [_cookie_header_for(device_id, session_token)]
    if cookie_jar:
        reserved = {"oai-did", "__Secure-next-auth.session-token",
                    "next-auth.session-token"}
        for name, value in cookie_jar.items():
            if name in reserved or not value:
                continue
            parts.append(f"{name}={value}")
    return "; ".join(part for part in parts if part)

_OAICS_WRAPPER_KEYS = (
    "checkout_session", "checkoutSession", "session", "checkout", "data",
    "result", "payload", "response", "checkout_state", "checkoutState",
    "checkout_snapshot", "checkoutSnapshot",
)

# 每 device 稳定的 oai-session-id (对齐 link-pp oai_session_id_for_device)
_OAI_SESSION_IDS: dict[str, str] = {}
_OAI_SESSION_IDS_LOCK = threading.Lock()

# 各国家浏览器档案 (locale/timezone/language), 未收录国家回退 en-US
_LOCALE_PROFILES = {
    "US": {"browser_locale": "en-US", "browser_timezone": "America/Chicago", "browser_language": "en-US"},
    "BR": {"browser_locale": "pt-BR", "browser_timezone": "America/Sao_Paulo", "browser_language": "pt-BR"},
    "GB": {"browser_locale": "en-GB", "browser_timezone": "Europe/London", "browser_language": "en-GB"},
    "DE": {"browser_locale": "de-DE", "browser_timezone": "Europe/Berlin", "browser_language": "de-DE"},
    "FR": {"browser_locale": "fr-FR", "browser_timezone": "Europe/Paris", "browser_language": "fr-FR"},
    "JP": {"browser_locale": "ja-JP", "browser_timezone": "Asia/Tokyo", "browser_language": "ja-JP"},
    "AU": {"browser_locale": "en-AU", "browser_timezone": "Australia/Sydney", "browser_language": "en-AU"},
    "CA": {"browser_locale": "en-CA", "browser_timezone": "America/Toronto", "browser_language": "en-CA"},
    "TH": {"browser_locale": "th-TH", "browser_timezone": "Asia/Bangkok", "browser_language": "th-TH"},
    "PH": {"browser_locale": "en-PH", "browser_timezone": "Asia/Manila", "browser_language": "en-PH"},
    "VN": {"browser_locale": "vi-VN", "browser_timezone": "Asia/Ho_Chi_Minh", "browser_language": "vi-VN"},
    "KR": {"browser_locale": "ko-KR", "browser_timezone": "Asia/Seoul", "browser_language": "ko-KR"},
    "IN": {"browser_locale": "en-IN", "browser_timezone": "Asia/Kolkata", "browser_language": "en-IN"},
    "ID": {"browser_locale": "id-ID", "browser_timezone": "Asia/Jakarta", "browser_language": "id-ID"},
    "NL": {"browser_locale": "nl-NL", "browser_timezone": "Europe/Amsterdam", "browser_language": "nl-NL"},
    "ES": {"browser_locale": "es-ES", "browser_timezone": "Europe/Madrid", "browser_language": "es-ES"},
    "IT": {"browser_locale": "it-IT", "browser_timezone": "Europe/Rome", "browser_language": "it-IT"},
    "PL": {"browser_locale": "pl-PL", "browser_timezone": "Europe/Warsaw", "browser_language": "pl-PL"},
    "SE": {"browser_locale": "sv-SE", "browser_timezone": "Europe/Stockholm", "browser_language": "sv-SE"},
    "NO": {"browser_locale": "nb-NO", "browser_timezone": "Europe/Oslo", "browser_language": "nb-NO"},
    "DK": {"browser_locale": "da-DK", "browser_timezone": "Europe/Copenhagen", "browser_language": "da-DK"},
    "FI": {"browser_locale": "fi-FI", "browser_timezone": "Europe/Helsinki", "browser_language": "fi-FI"},
    "SG": {"browser_locale": "en-SG", "browser_timezone": "Asia/Singapore", "browser_language": "en-SG"},
    "MY": {"browser_locale": "ms-MY", "browser_timezone": "Asia/Kuala_Lumpur", "browser_language": "ms-MY"},
    "MX": {"browser_locale": "es-MX", "browser_timezone": "America/Mexico_City", "browser_language": "es-MX"},
    "AR": {"browser_locale": "es-AR", "browser_timezone": "America/Argentina/Buenos_Aires", "browser_language": "es-AR"},
    "CL": {"browser_locale": "es-CL", "browser_timezone": "America/Santiago", "browser_language": "es-CL"},
    "CO": {"browser_locale": "es-CO", "browser_timezone": "America/Bogota", "browser_language": "es-CO"},
    "PE": {"browser_locale": "es-PE", "browser_timezone": "America/Lima", "browser_language": "es-PE"},
    "AE": {"browser_locale": "en-AE", "browser_timezone": "Asia/Dubai", "browser_language": "en-AE"},
    "ZA": {"browser_locale": "en-ZA", "browser_timezone": "Africa/Johannesburg", "browser_language": "en-ZA"},
    "IL": {"browser_locale": "he-IL", "browser_timezone": "Asia/Jerusalem", "browser_language": "he-IL"},
    "CH": {"browser_locale": "de-CH", "browser_timezone": "Europe/Zurich", "browser_language": "de-CH"},
}
_OAICS_AMOUNT_PATHS = (
    ("checkout_amount_minor",),
    ("total_summary", "due"),
    ("totalSummary", "due"),
    ("invoice", "amount_due"),
    ("invoice", "amountDue"),
    ("amount_due",),
    ("amountDue",),
    ("amount_total",),
    ("amountTotal",),
    ("total", "total"),
    ("total", "due"),
    ("total", "taxInclusive"),
    ("total", "taxInclusiveAmount"),
)


class OaicsPromoNotApplied(RuntimeError):
    def __init__(self, session_id: str, amount: Any, currency: str, detail: str = ""):
        self.session_id = session_id
        self.amount = amount
        self.currency = str(currency or "").upper()
        suffix = f"（{detail}）" if detail else ""
        super().__init__(
            "免费促销未实际生效 "
            f"(session={session_id}, due={amount} {self.currency or '?'}){suffix}"
        )


class OaicsPaypalUnsupported(RuntimeError):
    def __init__(self, session_id: str, methods: list[str], detail: str = ""):
        self.session_id = session_id
        self.methods = list(methods)
        suffix = f"; {detail}" if detail else ""
        super().__init__(
            f"Checkout 不支持 PayPal (session={session_id}, pm={self.methods}){suffix}"
        )


class OaicsConfirmBlocked(RuntimeError):
    pass


class OaicsAuthError(RuntimeError):
    pass


class PayPalRiskDeclined(RuntimeError):
    """Stripe Radar 拒绝了当前 PayPal 支付方式 (对齐 link-pp PayPalRiskDeclinedError)。"""

    def __init__(
        self,
        *,
        decline_code: str = "generic_decline",
        error_code: str = "",
        payment_method_id: str = "",
    ):
        self.decline_code = str(decline_code or "generic_decline")
        self.error_code = str(error_code or "")
        self.payment_method_id = str(payment_method_id or "")
        detail = f"; error_code={self.error_code}" if self.error_code else ""
        super().__init__(
            f"PayPal 风控拒绝: decline_code={self.decline_code}{detail}"
        )


class PayPalFundingUnavailable(RuntimeError):
    """当前 Checkout 不接受 PayPal (对齐 link-pp PayPalFundingUnavailableError)。"""

    def __init__(self, session_id: str, payment_method_types: list[str], detail: str = ""):
        self.session_id = str(session_id or "")
        self.payment_method_types = list(payment_method_types)
        suffix = f"; {detail}" if detail else ""
        super().__init__(
            f"Checkout 不支持 PayPal "
            f"(session={self.session_id}, pm={self.payment_method_types}){suffix}"
        )


def _is_payment_method_types_mismatch(resp) -> bool:
    """confirm 返回 payment_method_types_mismatch 时判定当前 Checkout 不支持 PayPal。"""
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    extra = error.get("extra_fields") if isinstance(error, dict) else None
    reason = extra.get("confirm_error_reason") if isinstance(extra, dict) else ""
    if str(reason or "").lower() == "payment_method_types_mismatch":
        return True
    text = (getattr(resp, "text", "") or "").lower()
    return "payment_method_types_mismatch" in text


def _raise_for_current_paypal_risk_decline(payload: Any, payment_method_id: str = "") -> None:
    """仅当 generic_decline 属于本次 provider 使用的 PM 时抛出 (对齐 link-pp)。"""
    if isinstance(payload, dict):
        decline_code = str(payload.get("decline_code") or "").strip().lower()
        if decline_code == "generic_decline":
            error_payment_method = payload.get("payment_method")
            declined_pm_id = (
                str(error_payment_method.get("id") or "").strip()
                if isinstance(error_payment_method, dict)
                else ""
            )
            if not payment_method_id or not declined_pm_id or declined_pm_id == payment_method_id:
                raise PayPalRiskDeclined(
                    decline_code=decline_code,
                    error_code=str(payload.get("code") or ""),
                    payment_method_id=declined_pm_id or payment_method_id,
                )
        for value in payload.values():
            _raise_for_current_paypal_risk_decline(value, payment_method_id)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _raise_for_current_paypal_risk_decline(value, payment_method_id)


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def common_headers(*, country: str, device_id: str, referer: str, route: str = "") -> dict[str, str]:
    base = str((country or "US").upper())
    profile = _LOCALE_PROFILES.get(base) or _LOCALE_PROFILES["US"]
    browser_language = profile["browser_language"]
    language = browser_language.split("-", 1)[0]
    headers = {
        "Accept": "application/json",
        "Accept-Language": f"{browser_language},{language};q=0.9,en;q=0.8",
        "Origin": "https://chatgpt.com",
        "Referer": referer,
        "User-Agent": _CHROME_UA,
        "OAI-Language": browser_language,
        "oai-client-version": _CHATGPT_CLIENT_VERSION,
        "oai-client-build-number": _CHATGPT_CLIENT_BUILD_NUMBER,
        # 2026-08-14: 对齐 link-pp chatgpt_context_headers 补 sec-ch-ua 家族 + sec-fetch
        "sec-ch-ua": _STRIPE_SEC_CH_UA,
        "sec-ch-ua-full-version-list": _STRIPE_SEC_CH_UA_FULL,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if device_id:
        headers["oai-device-id"] = device_id
        headers["oai-session-id"] = oai_session_id_for_device(device_id)
    if route:
        headers["x-openai-target-path"] = route
        headers["x-openai-target-route"] = route
    return headers


def warmup_chatgpt_page(session, *, country: str, device_id: str, timeout: float = 30) -> None:
    """同会话 GET chatgpt.com 预热页面, 种 __cf_bm/_cfuvid cookie (对齐 link-pp)。

    建单前调用, 让 CF/OAI cookie 与后续 Checkout POST 同一 HTTP 会话;
    失败静默忽略 (不影响建单)。
    """
    try:
        session.get("https://chatgpt.com/", headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "upgrade-insecure-requests": "1",
            **common_headers(country=country, device_id=device_id,
                             referer="https://chatgpt.com/"),
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
        }, timeout=timeout)
    except Exception:
        pass


def oai_session_id_for_device(device_id: str) -> str:
    """返回与 device 绑定的稳定 session UUID (对齐 link-pp)。"""
    key = str(device_id or "").strip()
    if not key:
        return ""
    with _OAI_SESSION_IDS_LOCK:
        value = _OAI_SESSION_IDS.get(key)
        if value:
            return value
        if len(_OAI_SESSION_IDS) >= 4096:
            _OAI_SESSION_IDS.pop(next(iter(_OAI_SESSION_IDS)))
        value = str(uuid.uuid4())
        _OAI_SESSION_IDS[key] = value
        return value


def _profile(country: str) -> dict:
    return _LOCALE_PROFILES.get((country or "US").upper()) or _LOCALE_PROFILES["US"]


def _jwt_profile(access_token: str) -> dict[str, str]:
    import base64

    try:
        part = str(access_token).split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}
    prof = payload.get("https://api.openai.com/profile") or {}
    prof = prof if isinstance(prof, dict) else {}
    return {
        "email": str(prof.get("email") or payload.get("email") or ""),
        "name": str(prof.get("name") or payload.get("name") or ""),
    }


_BOOTSTRAP_ATTESTATION_RE = re.compile(r'"webDeploymentAttestation"\s*:\s*"([^"]+)"')
_BOOTSTRAP_ATTESTATION_ALT_RE = re.compile(
    r"[^A-Za-z0-9]webDeploymentAttestation[=:]\s*[\"']?([A-Za-z0-9._-]+)")
_BOOTSTRAP_BUILD_RE = re.compile(r'<html[^>]*\bdata-build="([^"]+)"')
_BOOTSTRAP_SEQ_RE = re.compile(r'<html[^>]*\bdata-seq="([^"]+)"')
_BOOTSTRAP_SESSION_ID_RE = re.compile(r'"sessionId"\s*:\s*"([^"]+)"')


def bootstrap_oaics_checkout_context(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, *, country: str, device_id: str,
    cookie_jar: Optional[dict] = None,
) -> dict[str, str]:
    """预热 checkout 页面抓前端部署上下文 (对齐 kakao _bootstrap_oaics_kakao_http_context)。

    返回 webDeploymentAttestation / sessionId / data-build / data-seq,
    confirm 用 oai-web-deployment-attestation + x-oai-is-client-observation 带出。
    MIN_OAICS_ATTESTATION 可直注入 (跳过抓取); 任何失败返回空 dict 降级。
    """
    env_att = str(os.environ.get("MIN_OAICS_ATTESTATION") or "").strip()
    out = {"attestation": env_att, "session_id": "", "build_id": "", "seq": "",
           "source": "env" if env_att else ""}
    if env_att:
        return out
    from .chain import chatgpt_session, _req

    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = f"/backend-api/payments/checkout/{processor_entity}/{session_id}"
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        r = _req(s, "GET", checkout_url,
                 headers={
                     **common_headers(country=country, device_id=device_id,
                                      referer="https://chatgpt.com/", route=route),
                     "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                 },
                 timeout=30)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code != 200:
            return out
        html = r.text or ""
    except Exception:
        return out
    finally:
        s.close()
    for pattern, key in (
        (_BOOTSTRAP_ATTESTATION_RE, "attestation"),
        (_BOOTSTRAP_ATTESTATION_ALT_RE, "attestation"),
        (_BOOTSTRAP_SESSION_ID_RE, "session_id"),
        (_BOOTSTRAP_BUILD_RE, "build_id"),
        (_BOOTSTRAP_SEQ_RE, "seq"),
    ):
        m = pattern.search(html)
        if m and not out.get(key):
            out[key] = m.group(1).strip()
    if out.get("attestation"):
        out["source"] = "html"
    return out


def fetch_oaics_checkout_state(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, *, country: str, device_id: str,
    cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    from .chain import chatgpt_session, _req

    if not str(session_id or "").startswith("oaics_"):
        raise OaicsPaypalUnsupported(session_id, [], "非 oaics_ 会话")
    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = f"/backend-api/payments/checkout/{processor_entity}/{session_id}"
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        r = _req(s, "GET", f"{OPENAI_CHECKOUT_URL}/{processor_entity}/{session_id}",
                 headers=common_headers(country=country, device_id=device_id,
                                        referer=checkout_url, route=route),
                 timeout=45)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code == 401:
            raise OaicsAuthError("读取 OAICS Checkout HTTP 401")
        if r.status_code != 200:
            raise RuntimeError(f"读取 OAICS Checkout HTTP {r.status_code}: {(r.text or '')[:300]}")
        d = r.json() if r.text else {}
        return d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
    finally:
        s.close()


def submit_oaics_checkout_taxes(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, *, billing: dict[str, Any], country: str, currency: str,
    device_id: str, cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    from .chain import chatgpt_session, _req

    cc = str(country or "").upper()
    cur = str(currency or "").upper()
    address = billing.get("address") if isinstance(billing, dict) else {}
    address = address if isinstance(address, dict) else {}
    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = "/backend-api/payments/checkout/taxes"
    body = {
        "checkout_session_id": session_id,
        "checkout_email": str(billing.get("email") or ""),
        "billing_country": cc,
        "billing_name": str(billing.get("name") or ""),
        "currency": cur,
        "tax_id": str(billing.get("tax_id") or "") or None,
        "processor_entity": processor_entity,
        "billing_address": {
            "country": cc,
            "line1": str(address.get("line1") or ""),
            "line2": str(address.get("line2") or ""),
            "city": str(address.get("city") or ""),
            "state": str(address.get("state") or ""),
            "postal_code": str(address.get("postal_code") or ""),
        },
    }
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        r = _req(s, "POST", f"{OPENAI_CHECKOUT_URL}/taxes", json=body,
                 headers={
                     **common_headers(country=cc, device_id=device_id,
                                      referer=checkout_url, route=route),
                     "Content-Type": "application/json",
                 },
                 timeout=50)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code == 401:
            raise OaicsAuthError("提交 OAICS 账单 HTTP 401")
        if r.status_code != 200:
            raise RuntimeError(f"提交 OAICS 账单 HTTP {r.status_code}: {(r.text or '')[:300]}")
        d = r.json() if r.text else {}
        return d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
    finally:
        s.close()


def _nested_value(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _minor_amount(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    # 仅接受整数字面量 (允许小数点为 .0), 拒绝 1.5 等会被截断成整数造成
    # 0 元校验误判的值 (对齐 link-pp _minor_amount)
    if not re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        return None
    return int(text.split(".", 1)[0])


def _oaics_money_minor(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        for key in ("minorUnitsAmount", "minor_units_amount", "amount"):
            if value.get(key) is not None:
                found = _oaics_money_minor(value.get(key))
                if found is not None:
                    return found
        return None
    return _minor_amount(value)


def oaics_amount_observations(payload: Any) -> list[tuple[str, int]]:
    observations: list[tuple[str, int]] = []
    visited: set[int] = set()

    def visit(value: Any, prefix: str = "") -> None:
        if not isinstance(value, dict) or id(value) in visited:
            return
        visited.add(id(value))
        for path in _OAICS_AMOUNT_PATHS:
            raw = _nested_value(value, path)
            amount = _oaics_money_minor(raw)
            if amount is not None:
                label = ".".join(path)
                observations.append((f"{prefix}{label}", amount))
        for key in _OAICS_WRAPPER_KEYS:
            nested = value.get(key)
            if isinstance(nested, dict):
                visit(nested, f"{prefix}{key}.")

    visit(payload)
    return list(dict.fromkeys(observations))


def oaics_checkout_currency(payload: Any) -> str:
    visited: set[int] = set()

    def find(value: Any) -> str:
        if not isinstance(value, dict) or id(value) in visited:
            return ""
        visited.add(id(value))
        for key in ("currency", "currency_code", "currencyCode"):
            candidate = str(value.get(key) or "").strip().upper()
            if re.fullmatch(r"[A-Z]{3}", candidate):
                return candidate
        for key in (*_OAICS_WRAPPER_KEYS, "total", "total_summary", "totalSummary"):
            candidate = find(value.get(key))
            if candidate:
                return candidate
        return ""

    return find(payload)


def verify_oaics_zero_snapshot(payload: Any, *, session_id: str, currency: str) -> int:
    observations = oaics_amount_observations(payload)
    if not observations:
        raise OaicsPromoNotApplied(
            session_id, "unknown", oaics_checkout_currency(payload) or currency,
            "OAICS 未返回可核验的应付金额",
        )
    nonzero = [(label, amount) for label, amount in observations if amount != 0]
    if nonzero:
        detail = ", ".join(f"{label}={amount}" for label, amount in nonzero)
        raise OaicsPromoNotApplied(
            session_id, nonzero[0][1],
            oaics_checkout_currency(payload) or currency, detail,
        )
    return 0


def wait_for_oaics_zero(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, *, country: str, currency: str, device_id: str,
    initial_payload: Optional[dict[str, Any]] = None,
    attempts: int = 4, delay: float = 0.8,
    cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    payload = initial_payload or {}
    last_error: OaicsPromoNotApplied | None = None
    for attempt in range(1, max(1, attempts) + 1):
        if not payload or attempt > 1:
            payload = fetch_oaics_checkout_state(
                proxy, access_token, session_token, session_id, processor_entity,
                country=country, device_id=device_id, cookie_jar=cookie_jar,
            )
        try:
            verify_oaics_zero_snapshot(payload, session_id=session_id, currency=currency)
            return payload
        except OaicsPromoNotApplied as exc:
            last_error = exc
            if attempt < max(1, attempts):
                time.sleep(delay)
    if last_error is not None:
        raise last_error
    raise OaicsPromoNotApplied(session_id, "unknown", currency, "无状态")


def oaics_payment_method_types(payload: Any) -> list[str]:
    methods: list[str] = []
    seen: set[str] = set()
    for item in _walk_dicts(payload):
        candidates = item.get("payment_method_types")
        if candidates is None:
            candidates = item.get("paymentMethodTypes")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("type")
            method = str(candidate or "").strip().lower()
            if method and method not in seen:
                seen.add(method)
                methods.append(method)
    return methods


def oaics_custom_payment_methods(payload: Any) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk_dicts(payload):
        candidates = item.get("custom_payment_methods")
        if candidates is None:
            candidates = item.get("customPaymentMethods")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            method_id = str(candidate.get("id") or "").strip()
            if not method_id.startswith("cpmt_") or method_id in seen:
                continue
            seen.add(method_id)
            methods.append(candidate)
    methods.sort(
        key=lambda item: (
            0 if "paypal" in json.dumps(item, ensure_ascii=True).lower() else 1
        )
    )
    return methods


def create_oaics_elements_session(
    proxy: str, publishable_key: str, customer_secret: str, *,
    country: str, currency: str, methods: list[str],
) -> dict[str, Any]:
    from .chain import make_session, _req

    if not publishable_key.startswith("pk_live_"):
        raise RuntimeError("OAICS 缺少 Stripe publishable_key")
    if not customer_secret:
        raise RuntimeError("OAICS 缺少 customer_session_client_secret")
    stripe_js_id = str(uuid.uuid4())
    params = {
        "customer_session_client_secret": customer_secret,
        "client_betas[0]": "custom_checkout_server_updates_1",
        "client_betas[1]": "custom_checkout_manual_approval_1",
        "deferred_intent[mode]": "subscription",
        "deferred_intent[amount]": "0",
        "deferred_intent[currency]": str(currency or "").lower(),
        "deferred_intent[setup_future_usage]": "off_session",
        "currency": str(currency or "").lower(),
        "key": publishable_key,
        "_stripe_version": STRIPE_VERSION_FULL,
        "elements_init_source": "stripe.elements",
        "referrer_host": "chatgpt.com",
        "stripe_js_id": stripe_js_id,
        "locale": _profile(country)["browser_locale"],
        "type": "deferred_intent",
    }
    for index, method in enumerate(methods):
        params[f"deferred_intent[payment_method_types][{index}]"] = method
    s = make_session(proxy)
    try:
        r = _req(s, "GET", f"{STRIPE_API}/v1/elements/sessions", params=params,
                 headers=_stripe_headers(), timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f"OAICS Elements Session 失败 HTTP {r.status_code}")
        d = r.json() if r.text else {}
        d = d if isinstance(d, dict) else {}
        d["_pk"] = publishable_key
        d["_stripe_js_id"] = stripe_js_id
        d["_methods"] = methods
        return d
    finally:
        s.close()


def _oaics_find_string(payload: Any, names: tuple[str, ...], *, prefixes: tuple[str, ...] = ()) -> str:
    for item in _walk_dicts(payload):
        for name in names:
            candidate = item.get(name)
            if not isinstance(candidate, str):
                continue
            value = candidate.strip()
            if value and (not prefixes or value.startswith(prefixes)):
                return value
    return ""


def create_oaics_paypal_confirmation_token(
    proxy: str, publishable_key: str, elements: dict[str, Any], *,
    billing: dict[str, Any], currency: str, p1_token: str = "",
) -> str:
    from .chain import make_session, _req

    stripe_js_id = str(elements.get("_stripe_js_id") or uuid.uuid4())
    elements_session_id = _oaics_find_string(
        elements, ("session_id", "sessionId", "id"), prefixes=("elements_session_",))
    elements_config_id = _oaics_find_string(
        elements, ("config_id", "elements_session_config_id", "elementsSessionConfigId"))
    customer = _oaics_find_string(elements, ("customer", "customer_id", "customerId"), prefixes=("cus_",))
    methods = list(elements.get("_methods") or [])
    address = billing.get("address") if isinstance(billing, dict) else {}
    address = address if isinstance(address, dict) else {}
    body: dict[str, Any] = {
        "payment_method_data[type]": "paypal",
        "payment_method_data[billing_details][name]": str(billing.get("name") or ""),
        "payment_method_data[billing_details][email]": str(billing.get("email") or ""),
        "payment_method_data[guid]": uuid.uuid4().hex,
        "payment_method_data[muid]": uuid.uuid4().hex,
        "payment_method_data[sid]": uuid.uuid4().hex,
        "payment_method_data[payment_user_agent]": (
            f"stripe.js/{DEFAULT_STRIPE_RUNTIME_VERSION}; "
            f"stripe-js-v3/{DEFAULT_STRIPE_RUNTIME_VERSION}; "
            "payment-element; deferred-intent"
        ),
        "payment_method_data[referrer]": "https://chatgpt.com",
        "payment_method_data[time_on_page]": str(25000 + (uuid.uuid4().int % 30000)),
        "setup_future_usage": "off_session",
        "set_as_default_payment_method": "false",
        "mandate_data[customer_acceptance][type]": "online",
        "mandate_data[customer_acceptance][online][infer_from_client]": "true",
        "client_context[currency]": str(currency or "").lower(),
        "client_context[mode]": "subscription",
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "expressCheckout",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][2]": "address",
        "key": publishable_key,
    }
    for field in ("line1", "line2", "city", "state", "postal_code", "country"):
        value = str(address.get(field) or "")
        if value:
            body[f"payment_method_data[billing_details][address][{field}]"] = value
    for index, method in enumerate(methods):
        body[f"client_context[payment_method_types][{index}]"] = method
    if customer:
        body["client_context[customer]"] = customer
    for prefix in ("client_attribution_metadata", "payment_method_data[client_attribution_metadata]"):
        if elements_session_id:
            body[f"{prefix}[elements_session_id]"] = elements_session_id
        if elements_config_id:
            body[f"{prefix}[elements_session_config_id]"] = elements_config_id
    if p1_token:
        body["payment_method_data[radar_options][hcaptcha_token]"] = p1_token
    s = make_session(proxy)
    try:
        r = _req(s, "POST", f"{STRIPE_API}/v1/confirmation_tokens", data=body,
                 headers={**_stripe_headers(),
                          "Content-Type": "application/x-www-form-urlencoded",
                          "Authorization": f"Bearer {publishable_key}",
                          "Stripe-Version": STRIPE_VERSION_FULL},
                 timeout=40)
        if r.status_code != 200:
            d = r.json() if r.text else {}
            code = ""
            err = d.get("error") if isinstance(d, dict) else {}
            if isinstance(err, dict):
                code = str(err.get("code") or err.get("type") or "")
            suffix = f" ({code})" if code else ""
            raise RuntimeError(f"OAICS ConfirmationToken 失败 HTTP {r.status_code}{suffix}")
        d = r.json() if r.text else {}
        confirmation_token = _oaics_find_string(
            d if isinstance(d, dict) else {},
            ("id", "confirmation_token", "confirmationToken"), prefixes=("ctoken_", "ct_"))
        if not confirmation_token:
            raise RuntimeError("OAICS ConfirmationToken 响应缺少 token")
        return confirmation_token
    finally:
        s.close()


def confirm_oaics_standard_paypal(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, confirmation_token: str, *,
    country: str, device_id: str,
    sentinel_headers: Optional[dict[str, str]] = None,
    attestation: str = "",
    cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    from .chain import chatgpt_session, _req

    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = "/backend-api/payments/checkout/confirm"
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        headers = {
            **common_headers(country=country, device_id=device_id,
                             referer=checkout_url, route=route),
            "Content-Type": "application/json",
            **(sentinel_headers or {}),
        }
        if attestation:
            headers["oai-web-deployment-attestation"] = attestation
            headers["x-oai-is-client-observation"] = "true"
        r = _req(s, "POST", f"{OPENAI_CHECKOUT_URL}/confirm",
                 json={
                     "checkout_session_id": session_id,
                     "confirm_token": confirmation_token,
                     "selected_payment_method_type": "paypal",
                 },
                 headers=headers,
                 timeout=50)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code == 401:
            raise OaicsAuthError("OAICS confirm HTTP 401")
        if r.status_code != 200:
            raise RuntimeError(f"OAICS confirm HTTP {r.status_code}")
        d = r.json() if r.text else {}
        d = d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
        status = str(d.get("status") or "").strip().lower()
        if status == "blocked":
            raise OaicsConfirmBlocked("OAICS PayPal confirm blocked")
        if status in {"declined", "failed", "error", "expired"}:
            raise RuntimeError(f"OAICS confirm status={status}")
        return d
    finally:
        s.close()


def confirm_oaics_paypal_intent(
    proxy: str, confirmation_token: str, app_confirm: dict[str, Any],
    elements: dict[str, Any],
) -> dict[str, Any]:
    from .chain import make_session, _req

    publishable_key = str(elements.get("_pk") or "").strip()
    intent_type = str(app_confirm.get("type") or "").strip().lower()
    intent_id_raw = str(app_confirm.get("client_secret") or "").strip()
    if "_secret_" not in intent_id_raw:
        raise RuntimeError("OAICS confirm 未返回 Intent client_secret")
    intent_id = intent_id_raw.split("_secret_", 1)[0]
    if intent_id.startswith("pi_"):
        expected_type, collection = "payment_intent", "payment_intents"
    elif intent_id.startswith("seti_"):
        expected_type, collection = "setup_intent", "setup_intents"
    else:
        raise RuntimeError(f"OAICS confirm 返回未知 Intent: {intent_id}")
    if intent_type and intent_type != expected_type:
        raise RuntimeError(f"OAICS confirm 返回的 Intent 类型不一致: {intent_type} != {expected_type}")
    body = {
        "confirmation_token": confirmation_token,
        "client_secret": intent_id_raw,
        "use_stripe_sdk": "true",
        "key": publishable_key,
    }
    return_url = str(app_confirm.get("confirm_return_url") or "").strip()
    if return_url:
        body["return_url"] = return_url
    s = make_session(proxy)
    try:
        r = _req(s, "POST", f"{STRIPE_API}/v1/{collection}/{intent_id}/confirm",
                 data=body,
                 headers={**_stripe_headers(),
                          "Content-Type": "application/x-www-form-urlencoded",
                          "Authorization": f"Bearer {publishable_key}",
                          "Stripe-Version": STRIPE_VERSION_FULL},
                 timeout=50)
        if r.status_code != 200:
            d = r.json() if r.text else {}
            err = d.get("error") if isinstance(d, dict) else {}
            code = str(err.get("code") or "") if isinstance(err, dict) else ""
            # 2026-08-15: 附带完整 error JSON (param/decline_code/message) 便于定位
            # setup_intent_invalid_parameter 等 400 的具体错配参数
            import json as _json
            _dbg = _json.dumps(err, ensure_ascii=False) if err else (r.text or "")[:400]
            if _is_payment_method_types_mismatch(r):
                raise PayPalFundingUnavailable(
                    intent_id, [], "intent confirm 返回 payment_method_types_mismatch")
            _raise_for_current_paypal_risk_decline(d, "")
            suffix = f" ({code})" if code else ""
            raise RuntimeError(f"OAICS Intent confirm 失败 HTTP {r.status_code}{suffix}: {_dbg}")
        d = r.json() if r.text else {}
        _raise_for_current_paypal_risk_decline(d, "")
        return d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
    finally:
        s.close()


def oaics_standard_paypal_redirect(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, publishable_key: str, customer_secret: str,
    billing: dict[str, Any], *, country: str, currency: str, device_id: str,
    p1_token: str = "",
    sentinel_headers: Optional[dict[str, str]] = None,
    cookie_header: str = "",
    cookie_jar: Optional[dict] = None,
    mint_sentinel: Optional[Callable[[str, str, str, str], dict[str, str]]] = None,
) -> tuple[str, dict[str, Any]]:
    elements = create_oaics_elements_session(
        proxy, publishable_key, customer_secret,
        country=country, currency=currency,
        methods=["paypal", "link", "card"],
    )
    confirmation_token = create_oaics_paypal_confirmation_token(
        proxy, publishable_key, elements,
        billing=billing, currency=currency, p1_token=p1_token,
    )
    page_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    headers = dict(sentinel_headers or {})
    if mint_sentinel is not None and not headers:
        headers = mint_sentinel("checkout_session_approval", device_id,
                                page_url, cookie_header)
    try:
        app_confirm = confirm_oaics_standard_paypal(
            proxy, access_token, session_token, session_id, processor_entity,
            confirmation_token, country=country, device_id=device_id,
            sentinel_headers=headers or None, cookie_jar=cookie_jar,
        )
    except OaicsConfirmBlocked:
        if mint_sentinel is not None:
            headers = mint_sentinel("checkout_session_approval", device_id,
                                    page_url, cookie_header)
        app_confirm = confirm_oaics_standard_paypal(
            proxy, access_token, session_token, session_id, processor_entity,
            confirmation_token, country=country, device_id=device_id,
            sentinel_headers=headers or None, cookie_jar=cookie_jar,
        )
    from .link_helpers import extract_redirect_url

    redirect_url = extract_redirect_url(app_confirm)
    intent_confirm: dict[str, Any] = {}
    if not redirect_url:
        intent_confirm = confirm_oaics_paypal_intent(
            proxy, confirmation_token, app_confirm, elements)
        redirect_url = extract_redirect_url(intent_confirm)
    if not redirect_url:
        raise RuntimeError("OAICS PayPal confirm 未返回跳转地址")
    return redirect_url, {
        "elements": elements,
        "app_confirm": app_confirm,
        "intent_confirm": intent_confirm,
    }


def oaics_to_paypal_redirect(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, publishable_key: str, customer_secret: str,
    billing: dict[str, Any], *, country: str, currency: str, device_id: str,
    p1_token: str = "",
    cookie_header: str = "",
    cookie_jar: Optional[dict] = None,
    mint_sentinel: Optional[Callable[[str, str, str, str], dict[str, str]]] = None,
) -> tuple[str, dict[str, Any]]:
    state: dict[str, Any] = {}
    methods: list[dict[str, Any]] = []
    for attempt in range(1, 4):
        state = fetch_oaics_checkout_state(
            proxy, access_token, session_token, session_id, processor_entity,
            country=country, device_id=device_id, cookie_jar=cookie_jar,
        )
        verify_oaics_zero_snapshot(state, session_id=session_id, currency=currency)
        payment_method_types = oaics_payment_method_types(state)
        if "paypal" in payment_method_types:
            return oaics_standard_paypal_redirect(
                proxy, access_token, session_token, session_id, processor_entity,
                publishable_key, customer_secret, billing,
                country=country, currency=currency, device_id=device_id,
                p1_token=p1_token, cookie_header=cookie_header,
                cookie_jar=cookie_jar, mint_sentinel=mint_sentinel,
            )
        methods = oaics_custom_payment_methods(state)
        if methods:
            break
        if payment_method_types:
            raise PayPalFundingUnavailable(
                session_id, payment_method_types, "OAICS payment_method_types 未包含 paypal")
        if attempt < 3:
            time.sleep(0.8 * attempt)
    if not methods:
        raise RuntimeError("OAICS 未返回可判定的 payment_method_types 或 cpmt_ 支付方式")
    raise PayPalFundingUnavailable(
        session_id, [str(m.get("id") or "") for m in methods],
        "自定义支付方式未包含 PayPal",
    )


def oaics_checkout_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "payment_method_types": oaics_payment_method_types(state),
        "custom_payment_methods": [
            {k: v for k, v in m.items() if k in ("id", "name", "type")}
            for m in oaics_custom_payment_methods(state)
        ],
        "currency": oaics_checkout_currency(state),
    }


def start_oaics_custom_payment_method(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, method_type: str, *, country: str, device_id: str,
    cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    """自定义支付方式开始 (对齐 link-pp start_oaics_custom_payment_method)。

    POST /backend-api/payments/checkout/custom_payment_method
    → 返回更新后的状态 (含 cpmt_ 入口 / 确认所需字段)。
    """
    from .chain import chatgpt_session, _req

    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = "/backend-api/payments/checkout/custom_payment_method"
    body = {
        "checkout_session_id": session_id,
        "processor_entity": processor_entity,
        "payment_method_type": str(method_type or "") or "paypal",
    }
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        r = _req(s, "POST", f"{OPENAI_CHECKOUT_URL}/custom_payment_method", json=body,
                 headers={
                     **common_headers(country=country, device_id=device_id,
                                      referer=checkout_url, route=route),
                     "Content-Type": "application/json",
                 },
                 timeout=50)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code == 401:
            raise OaicsAuthError("OAICS custom_payment_method HTTP 401")
        if r.status_code != 200:
            raise RuntimeError(
                f"OAICS custom_payment_method HTTP {r.status_code}: {(r.text or '')[:300]}")
        d = r.json() if r.text else {}
        return d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
    finally:
        s.close()


def confirm_oaics_custom_payment_method(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, custom_method: dict[str, Any], *,
    country: str, device_id: str,
    sentinel_headers: Optional[dict[str, str]] = None,
    attestation: str = "",
    cookie_jar: Optional[dict] = None,
) -> dict[str, Any]:
    """自定义支付方式继续/确认 (对齐 link-pp confirm_oaics_custom_payment_method)。

    POST /backend-api/payments/checkout/custom_payment_method/continue
    → 直出 redirect; blocked/declined 分类抛异常。
    """
    from .chain import chatgpt_session, _req

    checkout_url = f"https://chatgpt.com/checkout/{processor_entity}/{session_id}"
    route = "/backend-api/payments/checkout/custom_payment_method/continue"
    body = {
        "checkout_session_id": session_id,
        "custom_payment_method_id": str(custom_method.get("id") or ""),
    }
    headers = {
        **common_headers(country=country, device_id=device_id,
                         referer=checkout_url, route=route),
        "Content-Type": "application/json",
        **(sentinel_headers or {}),
    }
    if attestation:
        headers["oai-web-deployment-attestation"] = attestation
        headers["x-oai-is-client-observation"] = "true"
    s = chatgpt_session(proxy, access_token, session_token, device_id=device_id)
    if cookie_jar:
        s.headers["Cookie"] = _cookie_header(device_id, session_token, cookie_jar)
    try:
        r = _req(s, "POST", f"{OPENAI_CHECKOUT_URL}/custom_payment_method/continue",
                 json=body, headers=headers, timeout=50)
        _merge_set_cookie(cookie_jar, r)
        if r.status_code == 401:
            raise OaicsAuthError("OAICS custom_payment_method continue HTTP 401")
        if r.status_code != 200:
            raise RuntimeError(
                f"OAICS custom_payment_method continue HTTP {r.status_code}: {(r.text or '')[:300]}")
        d = r.json() if r.text else {}
        d = d if isinstance(d, dict) else {"raw": (r.text or "")[:400]}
        status = str(d.get("status") or "").strip().lower()
        if status == "blocked":
            raise OaicsConfirmBlocked("OAICS cpmt continue blocked")
        if status in {"declined", "failed", "error", "expired"}:
            raise RuntimeError(f"OAICS cpmt continue status={status}")
        return d
    finally:
        s.close()


def oaics_custom_paypal_redirect(
    proxy: str, access_token: str, session_token: str, session_id: str,
    processor_entity: str, *, country: str, currency: str, device_id: str,
    sentinel_headers: Optional[dict[str, str]] = None,
    attestation: str = "",
    cookie_jar: Optional[dict] = None,
) -> tuple[str, dict[str, Any]]:
    """cpmt_ 自定义支付方式 (paypal) 提链: start -> continue -> redirect。

    仅当 payment_method_types 未直接暴露 paypal 但存在 cpmt_ 方法时使用;
    start/continue 任一失败降级抛 PayPalFundingUnavailable (由调用方分类)。
    """
    state: dict[str, Any] = {}
    for attempt in range(1, 3):
        state = fetch_oaics_checkout_state(
            proxy, access_token, session_token, session_id, processor_entity,
            country=country, device_id=device_id, cookie_jar=cookie_jar,
        )
        verify_oaics_zero_snapshot(state, session_id=session_id, currency=currency)
        methods = oaics_payment_method_types(state)
        if "paypal" in methods:
            raise PayPalFundingUnavailable(session_id, methods, "付款方式已直接暴露, 不应走 cpmt")
        cpmt = [
            m for m in oaics_custom_payment_methods(state)
            if m.get("type") is None or "paypal" in str(m.get("type") or "").lower()
        ]
        if not cpmt:
            raise PayPalFundingUnavailable(
                session_id, [], "cpmt_ 自定义支付方式未包含 PayPal")
        method_type = str(cpmt[0].get("type") or "").lower() or "paypal"
        started = start_oaics_custom_payment_method(
            proxy, access_token, session_token, session_id, processor_entity,
            method_type, country=country, device_id=device_id, cookie_jar=cookie_jar)
        nested_methods = oaics_custom_payment_methods(started)
        use_method = nested_methods[0] if nested_methods else cpmt[0]
        try:
            confirmed = confirm_oaics_custom_payment_method(
                proxy, access_token, session_token, session_id, processor_entity,
                use_method, country=country, device_id=device_id,
                sentinel_headers=sentinel_headers, attestation=attestation,
                cookie_jar=cookie_jar)
        except OaicsConfirmBlocked:
            if attempt >= 2:
                raise
            time.sleep(0.8)
            continue
        from .link_helpers import extract_redirect_url

        redirect_url = extract_redirect_url(confirmed)
        if redirect_url:
            return redirect_url, {"cpmt_method": use_method, "app_confirm": confirmed,
                                  "elements": {}, "intent_confirm": {}}
        if attempt < 2:
            time.sleep(0.8)
    raise RuntimeError("OAICS cpmt continue 未返回跳转地址")