# -*- coding: utf-8 -*-
"""直卡纯协议绑卡 + 支付确认 (零浏览器)。

核心突破 (参考 zkky):
  Stripe 卡 tokenization 纯 HTTP 的关键不在于 POST /v1/payment_methods (该端点
  对 pk 直连返回 400 "integration surface unsupported"), 而是把卡数据内联到
  SetupIntent confirm 调用里 — 这正是 Stripe.js confirmCardSetup 内部做的事。

流程 (strong_bind_direct 模式):
  1. (已有) 提链: checkout(PH/US) → update(TR 压0) → oaics 短链
  2. (HTTP) GET  /backend-api/payments/checkout/{processor}/{cs} — checkout context
  3. (HTTP) POST /backend-api/payments/payment_method {account_id} — SetupIntent (seti + secret)
  4. (HTTP) POST /v1/setup_intents/{seti}/confirm — 内联卡数据 → pm_id + succeeded
  5. (HTTP) GET  /backend-api/payments/payment_methods — 验证绑卡
  6. (HTTP) POST /v1/confirmation_tokens — ctoken
  7. (HTTP) POST /backend-api/payments/checkout/confirm — final seti
  8. (HTTP) POST /v1/setup_intents/{final}/confirm — final succeeded
  9. (HTTP) GET  /backend-api/subscriptions — plan_type == "plus"

全部纯 HTTP, 零浏览器, 零 CDP。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from typing import Any
from urllib.parse import urlencode

from .chain import chatgpt_session, make_session, _req

log = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================
APP_BASE = "https://chatgpt.com"
STRIPE_BASE = "https://api.stripe.com"
CHECKOUT_URL = f"{APP_BASE}/backend-api/payments/checkout"

STRIPE_VERSION = "2025-03-31.basil"
STRIPE_BETAS = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
STRIPE_INIT_VERSION = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)

# hCaptcha (Stripe invisible, 可选)
STRIPE_HCAPTCHA_SITE_KEY = "463b917e-e264-403f-ad34-34af0ee10294"
STRIPE_HCAPTCHA_URL = (
    "https://b.stripecdn.com/stripethirdparty-srv/assets/"
    "v33.5/HCaptchaInvisible.html"
)

# Stripe 公钥 (公开配置, 双商户分片)
KNOWN_PUBLISHABLE_KEYS = {
    "KslHRdbaPg": "pk_live_51Pj377KslHRdbaPgTJYjThzH3f5dt1N1vK7LUp0qh0yNSarhfZ6nfbG7FFlh8KLxVkvdMWN5o6Mc4Vda6NHaSnaV00C2Sbl8Zs",
    "C6h1nxGoI3": "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRacViovU3kLKvpkjh7IqkW00iXQsjo3n",
}

SETUP_INTENT_RE = re.compile(r"seti_[A-Za-z0-9]+")
CS_RE = re.compile(r"cs_[A-Za-z0-9]+")


# =============================================================================
# 辅助
# =============================================================================
def _pick_pk(client_secret: str) -> str:
    """按 SetupIntent 的商户分片选 pk。"""
    hinted = [k for frag, k in KNOWN_PUBLISHABLE_KEYS.items() if frag in (client_secret or "")]
    candidates = hinted + list(KNOWN_PUBLISHABLE_KEYS.values())
    seen = set()
    out = []
    for k in candidates:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[0] if out else list(KNOWN_PUBLISHABLE_KEYS.values())[1]


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v).strip()


def _mask(v: str, head: int = 8, tail: int = 4) -> str:
    s = _text(v)
    if len(s) <= head + tail + 3:
        return s[:4] + "…" if len(s) > 4 else s
    return s[:head] + "…" + s[-tail:]


def _walk(value: Any):
    """递归 yield 所有 dict 值和 list 元素。"""
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def _find_key(payload: Any, names: tuple[str, ...]) -> str:
    """在嵌套结构里搜第一个匹配 key 的值。"""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in names and isinstance(v, (str, int)):
                return _text(v)
            found = _find_key(v, names)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_key(item, names)
            if found:
                return found
    return ""


def _find_identifier(payload: Any, prefixes: tuple[str, ...]) -> str:
    """在嵌套结构里搜第一个以某前缀开头的字符串值。"""
    for v in _walk(payload):
        if isinstance(v, str):
            for p in prefixes:
                if v.startswith(p):
                    return v
    return ""


def _find_client_secret(payload: Any) -> str:
    """从 payload 中提取 SetupIntent client_secret。"""
    # 直接 key
    for k in ("client_secret", "clientSecret", "setup_intent_client_secret"):
        v = _find_key(payload, (k,))
        if v.startswith("seti_"):
            return v
    # 嵌套 seti_xxx_secret_yyy
    for v in _walk(payload):
        if isinstance(v, str) and re.match(r"seti_\w+_secret_\w+", v):
            return v
    return ""


def _setup_intent_id(payload: Any, client_secret: str = "") -> str:
    """从 payload 或 client_secret 中提取 setup_intent_id。"""
    # 从 client_secret 提取
    m = SETUP_INTENT_RE.search(client_secret or "")
    if m:
        return m.group(0)
    # 从 payload 搜索
    for v in _walk(payload):
        if isinstance(v, str) and re.match(r"^seti_[A-Za-z0-9]+$", v):
            return v
    return ""


def _publishable_key_for_setup(client_secret: str, fallback: str = "") -> str:
    """从 client_secret 商户分片推断 pk。"""
    if not client_secret:
        return fallback
    for frag, pk in KNOWN_PUBLISHABLE_KEYS.items():
        if frag in client_secret:
            return pk
    return fallback


def _card_fields(card: dict[str, Any]) -> dict[str, str]:
    """规范化卡字段。"""
    number = re.sub(r"\D", "", _text(card.get("number")))
    cvc = re.sub(r"\D", "", _text(card.get("cvc") or card.get("cvv")))
    month = re.sub(r"\D", "", _text(card.get("exp_month") or card.get("month"))).zfill(2)
    year = re.sub(r"\D", "", _text(card.get("exp_year") or card.get("year")))
    if len(year) == 2:
        year = f"20{year}"
    if not number or not cvc or len(month) != 2 or len(year) != 4:
        raise ValueError(f"invalid card fields: number_len={len(number)}, cvc_len={len(cvc)}, month={month}, year={year}")
    return {"number": number, "cvc": cvc, "exp_month": month, "exp_year": year}


def _billing_fields(billing: dict[str, Any] | None) -> dict[str, str]:
    """规范化账单字段。"""
    src = {str(k): _text(v) for k, v in (billing or {}).items()}
    return {
        "name": src.get("name") or "",
        "email": src.get("email") or "",
        "line1": src.get("line1") or src.get("address") or "",
        "line2": src.get("line2") or "",
        "city": src.get("city") or "",
        "state": src.get("state") or "",
        "postal_code": src.get("postal_code") or src.get("zip") or "",
        "country": src.get("country") or "",
        "phone": src.get("phone") or "",
    }


def _app_headers(referer: str = "", route: str = "") -> dict[str, str]:
    """ChatGPT API 请求头。"""
    h = {
        "Origin": APP_BASE,
        "Referer": referer or f"{APP_BASE}/",
        "Content-Type": "application/json",
    }
    if route:
        h["x-openai-target-path"] = route
        h["x-openai-target-route"] = route
    return h


def _stripe_headers(
    pk: str,
    referer: str,
    *,
    stripe_version: str = "",
) -> dict[str, str]:
    """Stripe API 请求头 (关键: Origin=js.stripe.com)。"""
    h = {
        "Authorization": f"Bearer {pk}",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if stripe_version:
        h["Stripe-Version"] = stripe_version
    return h


def _post_form(session, url, body, *, pk, referer, timeout, stripe_version=""):
    """Stripe form POST 辅助。"""
    r = session.post(
        url,
        data=urlencode(body, doseq=True),
        headers=_stripe_headers(pk, referer, stripe_version=stripe_version),
        timeout=timeout,
    )
    try:
        d = r.json()
    except Exception:
        d = {"raw": (r.text or "")[:500]}
    return r, d if isinstance(d, dict) else {}


def _post_json(session, url, body, *, timeout, referer="", route=""):
    """ChatGPT JSON POST 辅助。"""
    r = session.post(url, json=body, headers=_app_headers(referer, route), timeout=timeout)
    try:
        d = r.json()
    except Exception:
        d = {"raw": (r.text or "")[:500]}
    return r, d if isinstance(d, dict) else {}


def _require_setup_succeeded(payload: dict, stage: str) -> str:
    status = _text(payload.get("status"))
    if status != "succeeded":
        raise RuntimeError(f"{stage}: status={status or 'unknown'} body={str(payload)[:200]}")
    return status


# =============================================================================
# Step 1: 创建 SetupIntent (ChatGPT API)
# (保留原 create_setup_intent, 它已经能跑通)
# =============================================================================
def create_setup_intent(
    proxy: str,
    access_token: str,
    account_id: str,
    session_token: str = "",
) -> dict[str, Any]:
    """POST /backend-api/payments/payment_method → SetupIntent (client_secret)。"""
    s = chatgpt_session(proxy, access_token, session_token)
    path = "/backend-api/payments/payment_method"
    try:
        r = _req(s, "POST", APP_BASE + path,
                 json={"account_id": account_id},
                 headers={
                     "Authorization": f"Bearer {access_token}",
                     "chatgpt-account-id": account_id,
                     "x-openai-target-path": path,
                     "x-openai-target-route": path,
                     "Referer": f"{APP_BASE}/",
                 },
                 timeout=20)
        try:
            d = r.json()
        except Exception:
            d = {"raw": (r.text or "")[:500]}
        d = d if isinstance(d, dict) else {}
        out = {"status": r.status_code, "ok": r.status_code < 300, "body": d}
        if out["ok"]:
            cs = _find_client_secret(d) or str(d.get("client_secret") or d.get("clientSecret") or "")
            si = _setup_intent_id(d, cs) or (SETUP_INTENT_RE.search(cs).group(0) if SETUP_INTENT_RE.search(cs) else "")
            out["client_secret"] = cs
            out["setup_intent_id"] = si
            out["pk"] = _publishable_key_for_setup(cs, _pick_pk(cs))
        return out
    finally:
        s.close()


# =============================================================================
# Step 2: SetupIntent confirm — 内联卡数据 (核心突破!)
# 取代旧 create_payment_method + confirm_setup_intent 两步
# =============================================================================
def setup_confirm_inline(
    session,
    *,
    pk: str,
    setup_id: str,
    client_secret: str,
    card: dict[str, Any],
    billing: dict[str, Any] | None = None,
    checkout_id: str = "",
    processor: str = "",
    stripe_js_id: str = "",
    hcaptcha_token: str = "",
    init_payload: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[Any, dict[str, Any]]:
    """POST /v1/setup_intents/{seti}/confirm — 内联 payment_method_data。

    这是纯协议的核心: 把卡号/CVC/有效期作为 payment_method_data[card][...] 字段
    直接发送到 confirm 端点, Stripe 服务端在 confirm 过程中自动创建 PaymentMethod,
    不需要先调 /v1/payment_methods (该端点对 pk 直连返回 400)。
    """
    cf = _card_fields(card)
    bl = _billing_fields(billing)
    sid = stripe_js_id or str(uuid.uuid4())

    # Stripe 反欺诈字段 (正常由 Stripe.js 在浏览器里生成 cookie)
    guid = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"
    muid = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"
    sid_val = f"{uuid.uuid4()}{uuid.uuid4().hex[:6]}"

    # 从 init_payload 提取 elements_session_id / wallet_config_id (如有)
    elements_session_id = ""
    wallet_config_id = ""
    if init_payload and isinstance(init_payload, dict):
        elements_session_id = _find_identifier(init_payload, ("elements_session_",))
        wallet_config_id = _find_key(init_payload, ("wallet_config_id", "walletConfigId"))

    body: dict[str, Any] = {
        # 基础字段
        "set_as_default_payment_method": "true",
        "expected_payment_method_type": "card",
        "use_stripe_sdk": "true",
        "key": pk,
        "_stripe_version": STRIPE_VERSION,
        "client_secret": client_secret,
        # client_attribution
        "client_attribution_metadata[client_session_id]": sid,
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "card-element",
        "client_attribution_metadata[merchant_integration_version]": "2017",
        # 内联卡数据 (核心!)
        "payment_method_data[type]": "card",
        "payment_method_data[card][number]": cf["number"],
        "payment_method_data[card][cvc]": cf["cvc"],
        "payment_method_data[card][exp_month]": cf["exp_month"],
        "payment_method_data[card][exp_year]": cf["exp_year"],
        "payment_method_data[billing_details][name]": bl["name"] or "Test User",
        "payment_method_data[allow_redisplay]": "always",
        # 反欺诈
        "payment_method_data[guid]": guid,
        "payment_method_data[muid]": muid,
        "payment_method_data[sid]": sid_val,
        "payment_method_data[pasted_fields]": "number,exp,cvc",
        "payment_method_data[payment_user_agent]": (
            "stripe.js/3704557c13; stripe-js-v3/3704557c13; card-element"
        ),
        "payment_method_data[referrer]": APP_BASE,
        "payment_method_data[time_on_page]": str(random.randint(300_000, 750_000)),
        # payment_method_data client_attribution
        "payment_method_data[client_attribution_metadata][client_session_id]": sid,
        "payment_method_data[client_attribution_metadata][merchant_integration_source]": "elements",
        "payment_method_data[client_attribution_metadata][merchant_integration_subtype]": "card-element",
        "payment_method_data[client_attribution_metadata][merchant_integration_version]": "2017",
    }

    # 账单地址字段
    if bl["email"]:
        body["payment_method_data[billing_details][email]"] = bl["email"]
    for field in ("line1", "line2", "city", "state", "postal_code", "country"):
        val = bl.get(field, "")
        if val:
            body[f"payment_method_data[billing_details][address][{field}]"] = (
                val.upper() if field == "country" else val
            )
    if bl["phone"]:
        body["payment_method_data[billing_details][phone]"] = bl["phone"]
    if bl["postal_code"]:
        body["payment_method_data[pasted_fields]"] = "number,exp,cvc,zip"

    # hCaptcha (可选)
    if hcaptcha_token:
        body["radar_options[hcaptcha_token]"] = hcaptcha_token

    # elements_session_id / wallet_config_id
    if elements_session_id:
        body["payment_method_data[client_attribution_metadata][elements_session_id]"] = elements_session_id
    if wallet_config_id:
        body["payment_method_data[client_attribution_metadata][wallet_config_id]"] = wallet_config_id
        body["client_attribution_metadata[wallet_config_id]"] = wallet_config_id

    referer = f"{APP_BASE}/checkout/{processor}/{checkout_id}" if checkout_id and processor else APP_BASE
    return _post_form(
        session,
        f"{STRIPE_BASE}/v1/setup_intents/{setup_id}/confirm",
        body,
        key=pk,
        referer=referer,
        timeout=timeout,
        stripe_version=STRIPE_VERSION,
    )


# =============================================================================
# Step 3: 列卡验证 (ChatGPT API + Stripe API)
# =============================================================================
def list_payment_methods(
    proxy: str,
    access_token: str,
    account_id: str,
    session_token: str = "",
) -> dict[str, Any]:
    """GET /backend-api/payments/payment_methods — 确认绑卡。"""
    s = chatgpt_session(proxy, access_token, session_token)
    path = "/backend-api/payments/payment_methods"
    try:
        r = _req(s, "GET", f"{APP_BASE}{path}?account_id={account_id}",
                 headers={
                     "Authorization": f"Bearer {access_token}",
                     "chatgpt-account-id": account_id,
                     "x-openai-target-path": path,
                     "x-openai-target-route": path,
                     "Referer": f"{APP_BASE}/",
                 },
                 timeout=20)
        try:
            d = r.json()
        except Exception:
            d = {"raw": (r.text or "")[:500]}
        d = d if isinstance(d, dict) else {}
        cards = []
        for item in d.get("payment_methods") or []:
            if not isinstance(item, dict):
                continue
            card = item.get("card") or {}
            cards.append({
                "id": item.get("id"),
                "brand": card.get("brand") or item.get("type"),
                "last4": card.get("last4"),
                "exp": f"{card.get('exp_month')}/{card.get('exp_year')}",
                "default": item.get("id") == d.get("default_payment_method_id"),
            })
        return {"status": r.status_code, "ok": r.status_code < 300, "cards": cards, "body": d}
    finally:
        s.close()


def list_stripe_payment_methods(
    session, *, pk: str, customer: str, referer: str, timeout: int = 20,
) -> dict[str, Any]:
    """GET /v1/payment_methods?customer=cus_xxx — Stripe 侧列卡。"""
    r = session.get(
        f"{STRIPE_BASE}/v1/payment_methods",
        params={"customer": customer, "type": "card", "limit": 30},
        headers=_stripe_headers(pk, referer),
        timeout=timeout,
    )
    try:
        d = r.json()
    except Exception:
        d = {"raw": (r.text or "")[:500]}
    return {"status": r.status_code, "ok": r.status_code < 300, "body": d if isinstance(d, dict) else {}}


# =============================================================================
# Step 4-6: 支付确认段 (confirmation_tokens → checkout/confirm → final confirm)
# =============================================================================
def create_confirmation_token(
    session, *, pk: str, pm_id: str, customer: str = "",
    currency: str = "usd", stripe_js_id: str = "",
    elements_session_id: str = "", elements_session_config_id: str = "",
    referer: str = "", timeout: int = 30,
) -> tuple[Any, dict[str, Any]]:
    """POST /v1/confirmation_tokens — 用 pm_id 换 ctoken。"""
    body: dict[str, Any] = {
        "payment_method": pm_id,
        "setup_future_usage": "off_session",
        "set_as_default_payment_method": "true",
        "client_context[currency]": currency.lower(),
        "client_context[mode]": "subscription",
        "client_context[payment_method_types][0]": "card",
        "client_context[payment_method_types][1]": "link",
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "expressCheckout",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][2]": "address",
        "key": pk,
    }
    if customer:
        body["client_context[customer]"] = customer
    if elements_session_id:
        body["client_attribution_metadata[elements_session_id]"] = elements_session_id
    if elements_session_config_id:
        body["client_attribution_metadata[elements_session_config_id]"] = elements_session_config_id
    return _post_form(
        session,
        f"{STRIPE_BASE}/v1/confirmation_tokens",
        body,
        key=pk,
        referer=referer or APP_BASE,
        timeout=timeout,
        stripe_version=STRIPE_BETAS,
    )


def checkout_confirm(
    session, *, access_token: str, account_id: str,
    checkout_id: str, processor: str, timeout: int = 30,
) -> dict[str, Any]:
    """POST /backend-api/payments/checkout/confirm — 拿 final SetupIntent。"""
    referer = f"{APP_BASE}/checkout/{processor}/{checkout_id}"
    route = "/backend-api/payments/checkout/confirm"
    r, d = _post_json(
        session,
        f"{APP_BASE}/backend-api/payments/checkout/confirm",
        {},
        timeout=timeout,
        referer=referer,
        route=route,
    )
    d = d if isinstance(d, dict) else {}
    out = {"status": r.status_code, "ok": r.status_code < 300, "body": d}
    if out["ok"]:
        out["client_secret"] = _find_client_secret(d)
        out["setup_intent_id"] = _setup_intent_id(d, out["client_secret"])
    return out


def final_setup_confirm(
    session, *, pk: str, setup_id: str, client_secret: str,
    confirmation_token: str, checkout_id: str, processor: str,
    stripe_js_id: str = "", timeout: int = 30,
) -> tuple[Any, dict[str, Any]]:
    """POST /v1/setup_intents/{final}/confirm — 用 ctoken 确认最终支付。"""
    return_url = (
        f"{APP_BASE}/checkout/verify?stripe_session_id={checkout_id}"
        f"&processor_entity={processor}&plan_type=plus"
    )
    body = {
        "client_secret": client_secret,
        "confirmation_token": confirmation_token,
        "key": pk,
        "return_url": return_url,
        "use_stripe_sdk": "true",
        "_stripe_version": STRIPE_BETAS,
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[merchant_integration_source]": "l1",
    }
    referer = f"{APP_BASE}/checkout/{processor}/{checkout_id}"
    return _post_form(
        session,
        f"{STRIPE_BASE}/v1/setup_intents/{setup_id}/confirm",
        body,
        key=pk,
        referer=referer,
        timeout=timeout,
    )


# =============================================================================
# Step 7: 订阅验证
# =============================================================================
def verify_subscription(
    session, *, access_token: str, account_id: str,
    max_attempts: int = 3, interval_sec: float = 0.35, timeout: int = 20,
) -> dict[str, Any]:
    """轮询 GET /backend-api/subscriptions 确认 plus 激活。"""
    path = "/backend-api/subscriptions"
    for attempt in range(1, max_attempts + 1):
        r = session.get(
            f"{APP_BASE}{path}",
            params={"account_id": account_id},
            headers=_app_headers(f"{APP_BASE}/", path),
            timeout=timeout,
        )
        if int(getattr(r, "status_code", 0) or 0) >= 400:
            return {"ok": False, "status": r.status_code, "attempt": attempt}
        try:
            d = r.json()
        except Exception:
            d = {}
        plan = _text(d.get("plan_type")).lower()
        if plan == "plus":
            return {"ok": True, "status": r.status_code, "plan": plan,
                    "attempt": attempt, "body": d}
        if attempt < max_attempts:
            time.sleep(interval_sec)
    return {"ok": False, "status": r.status_code, "plan": plan,
            "attempt": max_attempts, "error": "plus not activated"}


# =============================================================================
# 完整纯协议绑卡 (Step 1-3: bind only)
# =============================================================================
def bind_card(
    proxy: str,
    access_token: str,
    account_id: str,
    card: dict[str, Any],
    session_token: str = "",
    *,
    checkout_id: str = "",
    processor: str = "",
    billing: dict[str, Any] | None = None,
    hcaptcha_token: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """纯协议绑卡: SetupIntent → inline confirm → 列卡验证。

    Args:
        proxy: 代理 URL
        access_token: ChatGPT access token (JWT)
        account_id: ChatGPT account_id (UUID)
        card: {"number", "exp_month", "exp_year", "cvc", "name"}
        session_token: __Secure-next-auth.session-token
        checkout_id: oaics_xxx (来自提链)
        processor: openai_llc / openai_phoenix_llc
        billing: 账单地址 (可选, 缺省用卡片 name)
        hcaptcha_token: hCaptcha passive token (可选)

    Returns:
        {ok, step, pm_id, setup_status, cards, ...}
    """
    out: dict[str, Any] = {"ok": False, "step": ""}
    s = chatgpt_session(proxy, access_token, session_token)
    stripe_js_id = str(uuid.uuid4())
    try:
        # --- Step 1: checkout context (如有 checkout_id) ---
        if checkout_id and processor:
            r_ctx = s.get(
                f"{APP_BASE}/backend-api/payments/checkout/{processor}/{checkout_id}",
                headers=_app_headers(
                    f"{APP_BASE}/checkout/{processor}/{checkout_id}",
                    "/backend-api/payments/checkout/{processor_entity}/{checkout_session_id}",
                ),
                timeout=timeout,
            )
            if int(getattr(r_ctx, "status_code", 0) or 0) >= 400:
                out.update(step="checkout_context",
                           error=f"status={r_ctx.status_code}: {(r_ctx.text or '')[:200]}")
                return out

        # --- Step 2: SetupIntent (ChatGPT API) ---
        r_si, d_si = _post_json(
            s,
            f"{APP_BASE}/backend-api/payments/payment_method",
            {"account_id": account_id},
            timeout=timeout,
            referer=f"{APP_BASE}/",
            route="/backend-api/payments/payment_method",
        )
        if int(getattr(r_si, "status_code", 0) or 0) >= 400:
            out.update(step="setup_intent",
                       error=f"status={r_si.status_code}: {str(d_si)[:200]}")
            return out

        client_secret = _find_client_secret(d_si) or _text(d_si.get("client_secret"))
        setup_id = _setup_intent_id(d_si, client_secret)
        pk = _publishable_key_for_setup(client_secret, _pick_pk(client_secret))
        if not setup_id or not client_secret:
            out.update(step="setup_intent",
                       error=f"missing seti/secret: body={str(d_si)[:200]}")
            return out
        out["setup_intent_id"] = setup_id
        out["pk"] = pk
        log.info(f"SetupIntent ready: {_mask(setup_id)}")

        # --- Step 3: SetupIntent confirm — 内联卡数据 (核心!) ---
        r_cf, d_cf = setup_confirm_inline(
            s,
            pk=pk,
            setup_id=setup_id,
            client_secret=client_secret,
            card=card,
            billing=billing,
            checkout_id=checkout_id,
            processor=processor,
            stripe_js_id=stripe_js_id,
            hcaptcha_token=hcaptcha_token,
            timeout=timeout,
        )
        cf_status = int(getattr(r_cf, "status_code", 0) or 0)
        if cf_status == 402:
            # 402 可重试一次
            log.warning("setup confirm returned 402, retrying...")
            r_cf, d_cf = setup_confirm_inline(
                s,
                pk=pk, setup_id=setup_id, client_secret=client_secret,
                card=card, billing=billing,
                checkout_id=checkout_id, processor=processor,
                stripe_js_id=stripe_js_id, hcaptcha_token=hcaptcha_token,
                timeout=timeout,
            )
            cf_status = int(getattr(r_cf, "status_code", 0) or 0)
        if cf_status >= 400:
            out.update(step="setup_confirm",
                       error=f"status={cf_status}: {str(d_cf)[:250]}")
            return out

        # 校验 succeeded
        si_status = _text(d_cf.get("status"))
        if si_status != "succeeded":
            out.update(step="setup_confirm",
                       si_status=si_status,
                       error=f"status={si_status or 'unknown'}: {str(d_cf)[:200]}")
            return out

        pm_id = _find_key(d_cf, ("payment_method", "payment_method_id")) or \
                _find_identifier(d_cf, ("pm_",))
        if not pm_id:
            out.update(step="setup_confirm",
                       error=f"missing pm_id in confirm response: {str(d_cf)[:200]}")
            return out
        out["pm_id"] = pm_id
        out["setup_status"] = si_status
        log.info(f"PaymentMethod created: {_mask(pm_id)}, status={si_status}")

        # --- Step 4: 列卡验证 ---
        # ChatGPT 侧
        pm_list = list_payment_methods(proxy, access_token, account_id, session_token)
        out["cards"] = pm_list.get("cards") if pm_list.get("ok") else []

        # Stripe 侧 (如有 customer)
        customer = _find_identifier(d_cf, ("cus_",)) or _find_identifier(d_si, ("cus_,"))
        out["customer"] = customer
        if customer:
            r_sp, d_sp = list_stripe_payment_methods(
                s, pk=pk, customer=customer,
                referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}" if checkout_id else APP_BASE,
                timeout=timeout,
            )
            out["stripe_pm_status"] = int(getattr(r_sp, "status_code", 0) or 0)

        out["ok"] = True
        out["step"] = "done"
        out["stripe_js_id"] = stripe_js_id
        return out

    except Exception as e:
        out.update(step="exception", error=f"{type(e).__name__}: {e}")
        log.exception("bind_card failed")
        return out
    finally:
        s.close()


# =============================================================================
# 完整纯协议支付 (Step 1-7: bind + pay + verify)
# =============================================================================
def bind_and_pay(
    proxy: str,
    access_token: str,
    account_id: str,
    card: dict[str, Any],
    session_token: str = "",
    *,
    checkout_id: str = "",
    processor: str = "openai_llc",
    billing: dict[str, Any] | None = None,
    currency: str = "USD",
    hcaptcha_token: str = "",
    fast_verify: bool = True,
    timeout: int = 30,
) -> dict[str, Any]:
    """纯协议完整流程: 绑卡 → confirmation_token → checkout/confirm → final confirm → 订阅验证。

    Args:
        checkout_id: 来自提链的 oaics_xxx
        processor: openai_llc / openai_phoenix_llc
        billing: 账单地址 (含 name/line1/city/state/postal_code/country)
        currency: USD / EUR 等
        fast_verify: True=只查 subscriptions; False=额外查 verify/success.data/auth/session

    Returns:
        {ok, pm_id, subscription_plan, ...}
    """
    out: dict[str, Any] = {"ok": False, "step": ""}

    # --- Phase 1: 绑卡 (Step 1-4) ---
    bind = bind_card(
        proxy, access_token, account_id, card, session_token,
        checkout_id=checkout_id, processor=processor,
        billing=billing, hcaptcha_token=hcaptcha_token, timeout=timeout,
    )
    out["bind"] = bind
    if not bind.get("ok"):
        out.update(step="bind", error=bind.get("error"))
        return out

    pm_id = bind["pm_id"]
    pk = bind["pk"]
    stripe_js_id = bind.get("stripe_js_id", str(uuid.uuid4()))
    customer = bind.get("customer", "")
    out["pm_id"] = pm_id
    out["customer"] = customer

    s = chatgpt_session(proxy, access_token, session_token)
    try:
        # --- Phase 2: 支付确认 (Step 5-6) ---
        # Step 5: confirmation_token
        r_ct, d_ct = create_confirmation_token(
            s, pk=pk, pm_id=pm_id, customer=customer,
            currency=currency, stripe_js_id=stripe_js_id,
            referer=f"{APP_BASE}/checkout/{processor}/{checkout_id}" if checkout_id else APP_BASE,
            timeout=timeout,
        )
        if int(getattr(r_ct, "status_code", 0) or 0) >= 400:
            out.update(step="confirmation_token",
                       error=f"status={r_ct.status_code}: {str(d_ct)[:200]}")
            return out
        ctoken = _find_key(d_ct, ("confirmation_token", "confirmationToken")) or \
                 _find_identifier(d_ct, ("ctoken_", "ct_"))
        if not ctoken:
            out.update(step="confirmation_token",
                       error=f"missing ctoken: {str(d_ct)[:200]}")
            return out
        out["ctoken"] = ctoken
        log.info(f"Confirmation token: {_mask(ctoken)}")

        # Step 6: checkout/confirm (ChatGPT API → final SetupIntent)
        cc = checkout_confirm(
            s, access_token=access_token, account_id=account_id,
            checkout_id=checkout_id, processor=processor, timeout=timeout,
        )
        if not cc.get("ok"):
            out.update(step="checkout_confirm",
                       error=f"status={cc.get('status')}: {str(cc.get('body'))[:200]}")
            return out

        final_secret = cc.get("client_secret") or _find_client_secret(cc.get("body", {}))
        final_seti = cc.get("setup_intent_id") or _setup_intent_id(cc.get("body", {}), final_secret)
        if not final_seti or not final_secret:
            out.update(step="checkout_confirm",
                       error="missing final SetupIntent credentials")
            return out
        out["final_setup_intent_id"] = final_seti
        log.info(f"Final SetupIntent: {_mask(final_seti)}")

        # Step 6b: final setup confirm
        r_fc, d_fc = final_setup_confirm(
            s, pk=pk, setup_id=final_seti, client_secret=final_secret,
            confirmation_token=ctoken, checkout_id=checkout_id,
            processor=processor, stripe_js_id=stripe_js_id, timeout=timeout,
        )
        if int(getattr(r_fc, "status_code", 0) or 0) >= 400:
            out.update(step="final_confirm",
                       error=f"status={r_fc.status_code}: {str(d_fc)[:200]}")
            return out
        final_status = _text(d_fc.get("status"))
        if final_status != "succeeded":
            out.update(step="final_confirm",
                       error=f"final status={final_status}: {str(d_fc)[:200]}")
            return out
        out["final_status"] = final_status
        log.info(f"Final confirm succeeded: {final_status}")

        # --- Phase 3: 订阅验证 (Step 7) ---
        # checkout context verify
        if checkout_id and processor:
            s.get(
                f"{APP_BASE}/backend-api/payments/checkout/{processor}/{checkout_id}",
                headers=_app_headers(
                    f"{APP_BASE}/checkout/verify",
                    "/backend-api/payments/checkout/{processor_entity}/{checkout_session_id}",
                ),
                timeout=timeout,
            )
        if not fast_verify:
            s.get(f"{APP_BASE}/checkout/verify",
                  params={"stripe_session_id": checkout_id,
                          "processor_entity": processor, "plan_type": "plus"},
                  headers=_app_headers(f"{APP_BASE}/checkout/{processor}/{checkout_id}"),
                  timeout=timeout)
            s.get(f"{APP_BASE}/payments/success.data",
                  params={"stripe_session_id": checkout_id},
                  headers=_app_headers(f"{APP_BASE}/checkout/verify"),
                  timeout=timeout)
            s.get(f"{APP_BASE}/api/auth/session",
                  params={"reason": "checkout_success"},
                  headers=_app_headers(f"{APP_BASE}/checkout/verify"),
                  timeout=timeout)

        # 轮询 subscriptions
        sub = verify_subscription(
            s, access_token=access_token, account_id=account_id,
            max_attempts=3, interval_sec=0.35, timeout=timeout,
        )
        out["subscription"] = sub
        if not sub.get("ok"):
            out.update(step="verify",
                       error=f"plus not activated: plan={sub.get('plan')}")
            return out

        out["ok"] = True
        out["step"] = "done"
        out["subscription_plan"] = sub.get("plan")
        out["card_last4"] = str(card.get("number", ""))[-4:]
        log.info(f"Payment succeeded, subscription plan={sub.get('plan')}")
        return out

    except Exception as e:
        out.update(step="exception", error=f"{type(e).__name__}: {e}")
        log.exception("bind_and_pay failed")
        return out
    finally:
        s.close()
