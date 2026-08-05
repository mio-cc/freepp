"""异步 7 段链路引擎：$0 ChatGPT Plus -> PayPal BA Approve 提链。

链路顺序固定: checkout -> init -> [update 金额守卫] -> provider(PM+confirm)
              -> approve -> poll -> resolve

成功判定三条件：
1. init.invoice.amount_due == 0
2. redirect 匹配 ^https://pm-redirects\.stripe\.com/authorize/
3. 最终 URL 匹配 ^https://www\.paypal\.com/agreements/approve\?ba_token=

双模式：
- mock: 模拟各段耗时与成败，无需网络/代理/真实 Token，供前端展示
- live: 通过 ThreadPoolExecutor 包装 curl_cffi 同步调用，执行真实 HTTP 请求

curl_cffi 使用 impersonate="chrome" 进行 TLS 指纹伪装。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .billing import billing_for, billing_currency, CHECKOUT_MATRIX, PAYPAL_BLOCKED
from .config import settings
from .proxy_pool import proxy_pool
from .branch_profile import branch_profile

# =============================================================================
# 常量与正则
# =============================================================================
STRIPE_INIT_VERSION = settings.stripe.get(
    "init_version",
    "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
)
STRIPE_RUNTIME_VERSION = settings.stripe.get("runtime_version", "6f8494a281")
USER_AGENT = settings.tls.get(
    "user_agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
)
CURL_IMPERSONATE = settings.tls.get("impersonate", "chrome")

# 假链/真链判定正则
RE_PM_AUTHORIZE = re.compile(r"^https://pm-redirects\.stripe\.com/authorize/")
RE_PAYPAL_BA = re.compile(r"^https://www\.paypal\.com/agreements/approve\?ba_token=[A-Za-z0-9-]+$")
RE_PAYPAL_BA_SEARCH = re.compile(r"https://www\.paypal\.com/agreements/approve\?ba_token=[A-Za-z0-9-]+")

# 7 段全部展示: checkout -> init -> update -> provider -> approve -> poll -> resolve
DISPLAY_STAGES = ["checkout", "init", "update", "provider", "approve", "poll", "resolve"]
ALL_STAGES = ["checkout", "init", "update", "provider", "approve", "poll", "resolve"]

# 失败原因码 (14 种)
REASON_CODES = {
    "checkout_failed": "checkout 段失败",
    "init_failed": "init 段失败",
    "non_zero_amount": "金额非 0 (守卫拦截)",
    "paypal_unsupported": "当前 checkout 不支持 paypal",
    "provider_failed": "provider 段失败 (PM/confirm)",
    "pm_creation_failed": "payment_method 创建失败",
    "confirm_failed": "confirm 段失败",
    "approve_failed": "approve 段失败",
    "no_redirect": "未获取到 pm-redirects 跳转",
    "poll_timeout": "poll 轮询超时",
    "resolve_failed": "resolve 解析失败",
    "network_error": "网络错误",
    "tls_error": "TLS 指纹错误",
    "proxy_error": "代理错误",
}

# 事件回调类型: async (event_dict) -> None
Emitter = Callable[[dict[str, Any]], Awaitable[None]]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# curl_cffi 封装 (可选: 缺失时降级 mock)
# =============================================================================
try:
    from curl_cffi import requests as _curl  # type: ignore
    _HAS_CURL = True
except Exception:  # pragma: no cover
    _curl = None
    _HAS_CURL = False


def make_session(proxy: str):
    """创建 curl_cffi Session (chrome TLS 指纹)。"""
    s = _curl.Session(impersonate=CURL_IMPERSONATE)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _req(session, method: str, url: str, **kw):
    """带 TLS 失败重试(关 verify)的请求。"""
    verify = kw.pop("verify", True)
    attempts = [verify]
    if verify and url.startswith("https://"):
        attempts.append(False)
    last = None
    for v in attempts:
        try:
            return session.request(method, url, verify=v, **kw)
        except Exception as exc:
            last = exc
            low = f"{type(exc).__name__}:{exc}".lower()
            if v and any(m in low for m in ("certificate", "ssl", "curl: (60)")):
                continue
            raise
    if last:
        raise last
    raise RuntimeError("request_failed")


def chatgpt_session(proxy: str, access_token: str, session_token: str = ""):
    s = make_session(proxy)
    device_id = str(uuid.uuid4())
    cookie = [f"oai-did={device_id}"]
    if session_token:
        cookie += [f"__Secure-next-auth.session-token={session_token}",
                   f"next-auth.session-token={session_token}"]
    s.headers.update({
        "Authorization": f"Bearer {access_token}",
        "Accept": "*/*", "Content-Type": "application/json",
        "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/",
        "oai-device-id": device_id, "oai-language": "en-US",
        "Cookie": "; ".join(cookie),
    })
    return s


# =============================================================================
# 真实 HTTP 链路各段 (对照原 chain.py, 通过线程池异步执行)
# =============================================================================
def _checkout_detail(raw) -> str:
    if not isinstance(raw, dict):
        return ""
    for k in ("detail", "message", "error"):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict) and v.get("message"):
            return str(v["message"]).strip()
    return ""


def stage_checkout_live(proxy, access_token, session_token, country, currency, branch="paypal"):
    from .branch_profile import branch_profile

    prof = branch_profile(branch)
    s = chatgpt_session(proxy, access_token, session_token)
    path = "/backend-api/payments/checkout"
    payload = {
        "entry_point": "all_plans_pricing_modal", "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency},
        "checkout_ui_mode": "hosted",
    }
    if prof.get("checkout_promo", True):
        payload["promo_campaign"] = {
            "promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False,
        }
    try:
        r = _req(s, "POST", "https://chatgpt.com" + path, json=payload,
                 headers={"Referer": "https://chatgpt.com/",
                          "x-openai-target-path": path, "x-openai-target-route": path},
                 timeout=settings.branch_stage(branch, "checkout").timeout)
        try:
            d = r.json()
        except Exception:
            d = {"raw": (r.text or "")[:800]}
        d = d if isinstance(d, dict) else {}
        return {"status": r.status_code, "ok": 200 <= r.status_code < 300,
                "checkout_session_id": d.get("checkout_session_id") or d.get("id") or "",
                "publishable_key": d.get("publishable_key") or "",
                "processor_entity": d.get("processor_entity") or d.get("processorEntity") or "",
                "detail": _checkout_detail(d), "raw": d}
    finally:
        s.close()


def stage_update_live(proxy, access_token, session_token, cs, entity, bill, cur, branch="paypal"):
    """双出口第二段：在 update_region 出口对已建 checkout session 注入 promo (压 0 元)。

    照抄 v1 已验证请求体: POST /backend-api/payments/checkout/update
    """
    s = chatgpt_session(proxy, access_token, session_token)
    path = "/backend-api/payments/checkout/update"
    body = {
        "checkout_session_id": cs,
        "processor_entity": entity,
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
    }
    if branch == "direct":
        # 直卡 (ph_short): TR 出口 + US 账单压 0 (pay.153 配方)
        body["billing_details"] = {"country": "US", "currency": "USD"}
    else:
        body["billing_details"] = {"country": bill, "currency": cur}
    body["checkout_ui_mode"] = "hosted"
    try:
        r = _req(s, "POST", "https://chatgpt.com" + path, json=body,
                 headers={
                     "Referer": f"https://chatgpt.com/checkout/{entity}/{cs}",
                     "x-openai-target-path": path,
                     "x-openai-target-route": path,
                 },
                 timeout=settings.branch_stage(branch, "update").timeout)
        try:
            d = r.json()
        except Exception:
            d = {"raw": (r.text or "")[:800]}
        d = d if isinstance(d, dict) else {}
        return {"status": r.status_code, "ok": 200 <= r.status_code < 300, "body": d}
    finally:
        s.close()


def stage_init_live(proxy, pk, cs, branch="paypal"):
    s = make_session(proxy)
    body = {
        "browser_locale": "en-US", "browser_timezone": "Asia/Shanghai",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": str(uuid.uuid4()),
        "elements_session_client[locale]": "en",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": pk, "_stripe_version": STRIPE_INIT_VERSION,
    }
    try:
        r = _req(s, "POST", f"https://api.stripe.com/v1/payment_pages/{cs}/init", data=body,
                 headers={"Origin": "https://pay.openai.com", "Referer": "https://pay.openai.com/",
                          "Accept": "application/json",
                          "Content-Type": "application/x-www-form-urlencoded"},
                 timeout=settings.branch_stage(branch, "init").timeout)
        try:
            d = r.json()
        except Exception:
            d = {"raw": (r.text or "")[:500]}
        return {"status": r.status_code, "ok": r.status_code < 400, "init": d}
    finally:
        s.close()


def verify_zero(init: dict, require_zero: bool = True, channel_check: bool = True,
                channel: str = "paypal") -> dict:
    """金额守卫 + 支付渠道校验。

    - channel_check=True: 校验 init.payment_method_types 含目标渠道 (paypal/momo/card/link)
      → 无渠道校验时跳过 (如 momo 链路的 init 只返回 card/momo)
    - require_zero=True: amount_due 须为 0 (fail-closed)
    """
    invoice = init.get("invoice") if isinstance(init.get("invoice"), dict) else {}
    if "amount_due" not in invoice:
        raise RuntimeError("无法确认 Stripe 发票应付金额")
    ad = int(invoice.get("amount_due"))
    currency = str(init.get("currency") or invoice.get("currency") or "").lower()
    if channel_check:
        methods = init.get("payment_method_types")
        if not isinstance(methods, list) or channel not in {str(m).lower() for m in methods}:
            raise ChannelMismatch(channel, methods or [])
    if require_zero and ad != 0:
        raise NonZeroAmount(ad, currency)
    return {"amount_due": ad, "currency": currency, "zero_verified": (ad == 0)}


class ChannelMismatch(Exception):
    """支付渠道校验失败: init 返回的 payment_method_types 不含目标渠道。"""

    def __init__(self, channel: str, methods: list):
        super().__init__(f"init 不含目标渠道 {channel}: {methods}")
        self.channel = channel
        self.methods = methods


class NonZeroAmount(Exception):
    def __init__(self, amount: int, currency: str):
        super().__init__(f"amount_due={amount} {currency} 非 0")
        self.amount = amount
        self.currency = currency


def _extract_amount_minor(payload: Any) -> int | None:
    """从 checkout/update 响应递归提取金额 (直卡提链: checkout_amount_minor 兼容)。

    优先字段: checkout_amount_minor / invoice.amount_due / total.taxInclusive / total.total
    / minorUnitsAmount dict / amount_due / amount_total。
    """
    if isinstance(payload, dict):
        for key in ("checkout_amount_minor", "amount_due", "amountDue", "amount_total", "amountTotal"):
            v = payload.get(key)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().lstrip("-").isdigit():
                return int(v.strip())
        invoice = payload.get("invoice")
        if isinstance(invoice, dict) and isinstance(invoice.get("amount_due"), int):
            return int(invoice["amount_due"])
        # minorUnitsAmount dict: {minorUnitsAmount: int}
        if "minorUnitsAmount" in payload and isinstance(payload.get("minorUnitsAmount"), int):
            return int(payload["minorUnitsAmount"])
        if "minor_units_amount" in payload and isinstance(payload.get("minor_units_amount"), int):
            return int(payload["minor_units_amount"])
        # total 结构优先: taxInclusive > total > subtotal
        total = payload.get("total")
        if isinstance(total, dict):
            for k in ("taxInclusive", "total", "due"):
                tv = total.get(k)
                if isinstance(tv, dict) and isinstance(tv.get("minorUnitsAmount"), int):
                    return int(tv["minorUnitsAmount"])
                if isinstance(tv, int):
                    return tv
        for v in payload.values():
            r = _extract_amount_minor(v)
            if r is not None:
                return r
    elif isinstance(payload, list):
        for v in payload:
            r = _extract_amount_minor(v)
            if r is not None:
                return r
    return None


def build_ctx(init: dict) -> dict:
    ctx = {"stripe_js_id": str(uuid.uuid4()),
           "elements_session_id": f"elements_session_{uuid.uuid4().hex[:11]}"}
    config_id = str(init.get("config_id") or "").strip() if isinstance(init, dict) else ""
    ctx["config_id"] = config_id
    ctx["elements_session_config_id"] = config_id or str(uuid.uuid4())
    return ctx


def extract_redirect(payload: Any) -> str:
    if isinstance(payload, dict):
        na = payload.get("next_action")
        if isinstance(na, dict) and na.get("type") == "redirect_to_url":
            ru = na.get("redirect_to_url")
            if isinstance(ru, dict) and ru.get("url"):
                return str(ru["url"]).strip()
        for k in ("setup_intent", "payment_intent"):
            n = payload.get(k)
            if isinstance(n, dict):
                found = extract_redirect(n)
                if found:
                    return found
        m = RE_PAYPAL_BA_SEARCH.search(json.dumps(payload, ensure_ascii=False))
        if m:
            return m.group(0)
        m2 = re.search(r"https://pm-redirects\.stripe\.com/authorize/[^\"'\s<>]+",
                       json.dumps(payload, ensure_ascii=False))
        if m2:
            return m2.group(0)
    return ""


def stage_payment_method_live(proxy, pk, cs, init, country, ctx, branch="paypal"):
    from .branch_profile import branch_profile

    prof = branch_profile(branch)
    b = billing_for(country)
    body = {
        "billing_details[name]": b["name"],
        "billing_details[email]": f"{re.sub(r'[^a-z0-9]+', '.', b['name'].lower()).strip('.')}.{uuid.uuid4().hex[:6]}@example.com",
        "billing_details[address][country]": b["country"],
        "billing_details[address][line1]": b["line1"],
        "billing_details[address][city]": b["city"],
        "billing_details[address][state]": b["state"],
        "billing_details[address][postal_code]": b["postal_code"],
        "type": prof["pm_type"],
        "payment_user_agent": f"stripe.js/{STRIPE_RUNTIME_VERSION}; stripe-js-v3/{STRIPE_RUNTIME_VERSION}; payment-element; deferred-intent",
        "referrer": prof["referrer"],
        "time_on_page": str(25000 + (uuid.uuid4().int % 30000)),
        "client_attribution_metadata[checkout_session_id]": cs,
        "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
        "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
        "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
        "client_attribution_metadata[elements_session_config_id]": ctx.get("elements_session_config_id") or "",
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
        "key": pk, "_stripe_version": STRIPE_INIT_VERSION,
    }
    body.update(prof["pm_extra"])
    s = make_session(proxy)
    try:
        r = _req(s, "POST", "https://api.stripe.com/v1/payment_methods", data=body,
                 timeout=settings.branch_stage(branch, "provider").timeout)
        d = r.json() if r.text else {}
        pm = str(d.get("id") or "")
        if not pm.startswith("pm_"):
            raise RuntimeError("payment_method 创建失败")
        return pm
    finally:
        s.close()


def stage_confirm_live(proxy, pk, cs, init, pm, ctx, country, entity, require_zero=True,
                       channel_check=True, channel="paypal", branch="paypal"):
    from .branch_profile import branch_profile

    gate = verify_zero(init, require_zero=require_zero, channel_check=channel_check, channel=channel)
    hosted = str(init.get("url") or "")
    if hosted:
        return_url = hosted
    else:
        ent = entity or ("openai_llc" if country == "US" else "openai_ie")
        success = f"https://chatgpt.com/checkout/verify?stripe_session_id={cs}&processor_entity={ent}&plan_type=plus"
        return_url = (f"https://checkout.stripe.com/c/pay/{cs}?returned_from_redirect=true"
                      f"&ui_mode=custom&return_url={success}")
    body = {
        "guid": uuid.uuid4().hex, "muid": uuid.uuid4().hex, "sid": uuid.uuid4().hex,
        "payment_method": pm, "init_checksum": str(init.get("init_checksum") or ""),
        "version": STRIPE_RUNTIME_VERSION, "expected_amount": str(gate["amount_due"]),
        "expected_payment_method_type": branch_profile(channel or branch)["confirm_type"],
        "return_url": return_url,
        "elements_session_client[session_id]": ctx["elements_session_id"],
        "elements_session_client[locale]": "en",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[stripe_js_id]": ctx["stripe_js_id"],
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "client_attribution_metadata[client_session_id]": ctx["stripe_js_id"],
        "client_attribution_metadata[checkout_session_id]": cs,
        "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
        "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
        "client_attribution_metadata[elements_session_config_id]": ctx.get("elements_session_config_id") or "",
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "custom",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
        "consent[terms_of_service]": "accepted",
        "key": pk, "_stripe_version": STRIPE_INIT_VERSION,
    }
    s = make_session(proxy)
    try:
        url = f"https://api.stripe.com/v1/payment_pages/{cs}/confirm"
        hdrs = {"Origin": "https://pay.openai.com", "Referer": return_url.split("#", 1)[0],
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"}
        timeout = settings.branch_stage(branch, "provider").timeout
        r = _req(s, "POST", url, data=body, headers=hdrs, timeout=timeout)
        if r.status_code == 400 and "terms of service" in (r.text or "").lower():
            r = _req(s, "POST", url, data=body, headers=hdrs, timeout=timeout)
        if r.status_code == 400 and "checkout_amount_mismatch" in (r.text or ""):
            new_init = stage_init_live(proxy, pk, cs, branch).get("init") or {}
            if new_init.get("init_checksum"):
                body["init_checksum"] = str(new_init.get("init_checksum"))
            try:
                ngate = verify_zero(new_init, require_zero=require_zero,
                                    channel_check=channel_check, channel=channel)
                body["expected_amount"] = str(ngate["amount_due"])
            except Exception:
                pass
            r = _req(s, "POST", url, data=body, headers=hdrs, timeout=timeout)
        if r.status_code in {400, 429, 500, 502, 503}:
            time.sleep(2)
            r = _req(s, "POST", url, data=body, headers=hdrs, timeout=timeout)
        d = r.json() if r.text else {}
        redirect = extract_redirect(d)
        state = ""
        sub = d.get("submission_attempt") if isinstance(d, dict) else None
        if isinstance(sub, dict):
            state = str(sub.get("state") or "")
        return {"redirect": redirect, "confirm_state": state}
    finally:
        s.close()


def stage_approve_live(proxy, access_token, session_token, cs, entity, branch="paypal"):
    s = chatgpt_session(proxy, access_token, session_token)
    try:
        path = "/backend-api/payments/checkout/approve"
        payload = {"checkout_session_id": cs, "processor_entity": entity}
        last_result = ""
        d: dict = {}
        r = None
        for attempt in range(1, 4):
            if attempt > 1:
                time.sleep(3)
            r = _req(s, "POST", "https://chatgpt.com" + path, json=payload,
                     headers={"Referer": f"https://chatgpt.com/checkout/{entity}/{cs}",
                              "x-openai-target-path": path, "x-openai-target-route": path},
                     timeout=settings.branch_stage(branch, "approve").timeout)
            if r.status_code >= 400:
                break
            d = r.json() if r.text else {}
            last_result = str(d.get("result") or "") if isinstance(d, dict) else ""
            if last_result == "approved" or (last_result and last_result != "blocked"):
                break
        return {"ok": r is not None and r.status_code < 400, "result": last_result,
                "redirect": extract_redirect(d)}
    finally:
        s.close()


def stage_poll_live(proxy, pk, cs, timeout=None, branch="paypal"):
    if timeout is None:
        timeout = settings.branch_stage(branch, "poll").timeout
    s = make_session(proxy)
    deadline = time.monotonic() + max(1.0, timeout)
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": f"elements_session_{uuid.uuid4().hex[:11]}",
        "elements_session_client[stripe_js_id]": str(uuid.uuid4()),
        "elements_session_client[locale]": "en",
        "key": pk, "_stripe_version": STRIPE_INIT_VERSION,
    }
    try:
        interval = settings.branch_stage(branch, "poll").poll_interval
        while time.monotonic() < deadline:
            r = _req(s, "GET", f"https://api.stripe.com/v1/payment_pages/{cs}",
                     params=params, timeout=5)
            if r.status_code == 200:
                d = r.json() if r.text else {}
                url = extract_redirect(d)
                if url:
                    return url
            time.sleep(interval)
        return ""
    finally:
        s.close()


def stage_resolve_live(proxy, intermediate, timeout=None, branch="paypal"):
    from .branch_profile import branch_profile

    prof = branch_profile(branch)
    success_re = prof["resolve_re"]
    search_re = prof["resolve_search_re"]
    if timeout is None:
        timeout = settings.branch_stage(branch, "resolve").timeout
    url = (intermediate or "").strip()
    if success_re and success_re.match(url):
        return url
    if not RE_PM_AUTHORIZE.match(url) and not (search_re and search_re.search(url)):
        return ""
    s = make_session(proxy)
    deadline = time.monotonic() + max(3.0, timeout)
    current = url
    seen: set[str] = set()
    try:
        from urllib.parse import urljoin
        while current and time.monotonic() < deadline and current not in seen:
            seen.add(current)
            remain = max(2.0, min(6.0, deadline - time.monotonic()))
            r = _req(s, "GET", current, timeout=remain, allow_redirects=False)
            loc = str(r.headers.get("Location") or r.headers.get("location") or "").strip()
            if success_re and success_re.match(loc):
                return loc
            if search_re:
                m = search_re.search(loc or "")
                if m:
                    return m.group(0)
                m2 = search_re.search(r.text or "")
                if m2:
                    return m2.group(0)
            if r.status_code in {301, 302, 303, 307, 308} and loc:
                current = urljoin(current, loc)
                continue
            break
        return ""
    finally:
        s.close()


# =============================================================================
# 国家选择
# =============================================================================
def pick_countries(attempt_idx: int, branch: str = "paypal") -> dict[str, str]:
    """第 attempt_idx(0-based) 次尝试时，各段选哪个国家。

    按提链分支独立七段配置取国家 (互不混用)。
    - follow_checkout=True: 除 update 外所有段跟随 checkout 段 (分段跟随)
    - 双 init: init 段走 init0(借道出口) -> init1(验真出口) -> init_t(过渡)

    国家选择规则:
    - countries 为 ["auto"] 或空 → auto 模式: 全量 ALL_COUNTRIES 轮换 (动态优先国 + 长尾)
    - countries 为 ["US"] 等单值 → 手动锁定该国家 (override 优先)
    """
    cfg = settings.branch(branch)
    st = lambda name: settings.branch_stage(branch, name)  # noqa: E731

    def _auto_pool() -> list[str]:
        from .billing import ALL_COUNTRIES
        return [c for c in ALL_COUNTRIES if c not in PAYPAL_BLOCKED] or ["US"]

    def _pick(name: str, countries: list[str]) -> str:
        lst = countries or []
        if not lst or lst == ["auto"]:
            lst = _auto_pool()
        offset = attempt_idx + (sum(ord(c) for c in name) % max(1, len(lst)))
        return lst[offset % len(lst)]

    co = st("checkout")
    countries_co = co.countries or ["auto"]
    checkout_cc = _pick("checkout", countries_co)
    # 币种跟随账单国 (billing_country: auto -> 跟随 checkout 出口国)
    billing_cc = (cfg.billing_country or "auto").upper()
    if billing_cc in ("AUTO", ""):
        billing_cc = checkout_cc
    currency = billing_currency(billing_cc)
    pick: dict[str, str] = {
        "checkout": checkout_cc,
        "_billing": billing_cc,
        "_currency": currency,
    }

    follow = cfg.follow_checkout
    for stage in ("init", "update", "provider", "approve", "poll", "resolve"):
        if follow and stage != "update":
            pick[stage] = checkout_cc
        elif stage == "update":
            # update 段: 出口代理国家取配置首选 (TH 优先, 用于注入 promo)
            upd_list = st("update").countries or ["TH"]
            pick[stage] = upd_list[0] if upd_list and upd_list != ["auto"] else "TH"
        else:
            pick[stage] = _pick(stage, st(stage).countries)

    # 双 init: 覆盖 init 段为三轮 (init0/init1/init_t 各取优先列表)
    if cfg.dual_init:
        pick["init0"] = _pick("init0", cfg.init0_ccs)
        pick["init1"] = _pick("init1", cfg.init1_ccs)
        pick["init_t"] = _pick("init_t", cfg.init_t_ccs or [pick["init1"]])
    return pick


# =============================================================================
# 异步链路引擎
# =============================================================================
class ChainResult:
    def __init__(self) -> None:
        self.success: bool = False
        self.paypal_approve_url: str = ""
        self.pm_authorize_url: str = ""
        self.amount_due: int = 0
        self.currency: str = ""
        self.country: str = ""
        self.billing_country: str = ""
        self.reason_code: str = ""
        self.reason_text: str = ""
        self.stage_reached: str = ""
        self.elapsed: float = 0.0
        self.ba_token: str = ""
        self.email: str = ""
        self.token_id: str = ""


class AsyncChain:
    """单条异步链路。execute() 跑完整 7 段，通过 emitter 推送事件。"""

    def __init__(self, chain_id: str, token: dict[str, Any], attempt: int,
                 options: dict[str, Any], emitter: Emitter,
                 executor: Any | None = None) -> None:
        self.chain_id = chain_id
        self.token = token
        self.attempt = attempt
        self.options = options
        self.emit = emitter
        self.executor = executor
        self.branch_name = str(options.get("branch") or "paypal")
        self.branch_cfg = settings.branch(self.branch_name)
        self.pick = pick_countries(attempt - 1, self.branch_name)
        self.access_token = token.get("access_token") or token.get("raw") or ""
        self.session_token = token.get("session_token") or ""
        self.email = token.get("email") or token.get("sub") or ""
        self.result = ChainResult()
        self.result.email = self.email
        self.result.token_id = token.get("id", "")
        self._cancelled = False
        self._stage_states: dict[str, dict] = {}

    def cancel(self) -> None:
        self._cancelled = True

    async def _emit(self, evt: dict[str, Any]) -> None:
        evt.setdefault("chain_id", self.chain_id)
        await self.emit(evt)

    async def _run_in_executor(self, fn, *args):
        loop = asyncio.get_event_loop()
        if self.executor:
            return await loop.run_in_executor(self.executor, lambda: fn(*args))
        return await loop.run_in_executor(None, lambda: fn(*args))

    def _pick_proxy(self, stage: str, country: str | None = None) -> str:
        """为某段链路选代理：live 模式下经代理池选 711/节点/QG，mock 返回空。"""
        if settings.chain_mode != "live" or not _HAS_CURL:
            return ""
        try:
            return proxy_pool.pick_for_stage(stage, country)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 单段执行（mock + live 统一入口）
    # ------------------------------------------------------------------
    async def _run_stage(self, stage: str, fn_live, *args, fail_reason: str = "network_error") -> Any:
        sc = settings.branch_stage(self.branch_name, stage)
        display = stage  # 7段全部独立展示
        max_try = sc.retry
        country = self.pick.get(stage, "US")
        last_err = ""
        for try_n in range(1, max_try + 1):
            if self._cancelled:
                raise asyncio.CancelledError()
            await self._emit({"type": "stage_try", "stage": display, "country": country,
                              "try_n": try_n, "max_try": max_try})
            try:
                if settings.chain_mode == "live" and _HAS_CURL:
                    live_args = list(args)
                    if live_args and live_args[0] is None:
                        live_args[0] = self._pick_proxy(stage, country)
                    res = await self._run_in_executor(fn_live, *live_args)
                else:
                    res = await self._mock_stage(stage)
                await self._emit({"type": "stage_ok", "stage": display, "country": country})
                self._stage_states[stage] = {"state": "ok", "country": country}
                return res
            except asyncio.CancelledError:
                raise
            except NonZeroAmount as e:
                # 金额守卫失败：fail-closed，不重试
                await self._emit({"type": "stage_fail", "stage": display, "country": country})
                self._stage_states[stage] = {"state": "fail", "country": country}
                raise
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if try_n < max_try:
                    await self._emit({"type": "stage_retry", "stage": display, "country": country,
                                      "error": last_err, "try_n": try_n, "max_try": max_try})
                    await asyncio.sleep(min(2.0, 0.5 * try_n))
                else:
                    await self._emit({"type": "stage_fail", "stage": display, "country": country})
                    self._stage_states[stage] = {"state": "fail", "country": country}
        raise ChainStageError(fail_reason, last_err)

    async def _mock_stage(self, stage: str) -> dict:
        """模拟单段：随机耗时 + 按成功率判定。按分支渠道类型模拟。"""
        lo = settings.mock_stage_min
        hi = settings.mock_stage_max
        await asyncio.sleep(random.uniform(lo, hi))
        # poll 段模拟轮询多步
        if stage == "poll":
            steps = random.randint(2, 6)
            for _ in range(steps):
                await asyncio.sleep(settings.branch_stage(self.branch_name, "poll").poll_interval)
        rate = settings.mock_success_rate
        if random.random() > rate:
            raise RuntimeError(f"mock_{stage}_random_fail")
        channel = self.branch_cfg.channel
        # 返回模拟数据
        if stage == "checkout":
            return {"checkout_session_id": f"cs_mock_{uuid.uuid4().hex[:12]}",
                    "publishable_key": "pk_live_mock", "processor_entity": "openai_llc"}
        if stage == "init":
            # 双 init 时: init0 借道出口拿渠道类型, init1 本地验真 (此处统一返回)
            return {"init": {"invoice": {"amount_due": 0}, "currency": "usd",
                             "payment_method_types": [channel], "init_checksum": "ic_mock"}}
        if stage == "update":
            return {"amount_due": 0, "currency": "usd", "zero_verified": True}
        if stage == "provider":
            return {"pm_id": f"pm_mock_{uuid.uuid4().hex[:8]}",
                    "redirect": f"https://pm-redirects.stripe.com/authorize/mock_{uuid.uuid4().hex[:10]}",
                    "confirm_state": "requires_approval"}
        if stage == "approve":
            return {"ok": True, "result": "approved"}
        if stage == "poll":
            return {"redirect": f"https://pm-redirects.stripe.com/authorize/mock_{uuid.uuid4().hex[:10]}"}
        if stage == "resolve":
            ba = f"BA-{uuid.uuid4().hex[:16].upper()}"
            return {"url": f"https://www.paypal.com/agreements/approve?ba_token={ba}"}
        return {}

    # ------------------------------------------------------------------
    # 完整链路
    # ------------------------------------------------------------------
    async def execute(self) -> ChainResult:
        t0 = time.monotonic()
        try:
            await self._emit({"type": "chain_start", "email": self.email,
                              "token_sub": self.token.get("sub", ""), "attempt": self.attempt})
            country = self.pick["checkout"]
            # 账单国/币种: 跟随 config billing_country (auto -> 出口国), 币种 EUR 等按账单国
            billing_cc = self.pick.get("_billing", country)
            currency = self.pick.get("_currency", "USD")

            # S1 checkout (billing_details 用账单国+币种; 出口代理按 pick 国家)
            co = await self._run_stage("checkout", stage_checkout_live,
                                       None, self.access_token, self.session_token, billing_cc, currency,
                                       self.branch_name,
                                       fail_reason="checkout_failed")
            if settings.chain_mode == "live" and _HAS_CURL:
                cs = co["checkout_session_id"]
                pk = co["publishable_key"]
                entity = co.get("processor_entity") or ("openai_llc" if country == "US" else "openai_ie")
            else:
                cs = co["checkout_session_id"]
                pk = co["publishable_key"]
                entity = "openai_llc"
            self.result.country = country
            self.result.stage_reached = "checkout"

            # S2 init (双 init: init0 借道出口拿渠道类型 -> init1 回本地验真 -> init_t 过渡)
            # direct 直卡: 无 Stripe init, 标记 ok 跳过
            init = {}
            if self.branch_name == "direct":
                await self._emit({"type": "stage_try", "stage": "init",
                                  "country": self.pick.get("init", country), "try_n": 1, "max_try": 1})
                await self._emit({"type": "stage_ok", "stage": "init",
                                  "country": self.pick.get("init", country)})
                self._stage_states["init"] = {"state": "ok", "country": self.pick.get("init", country)}
                self.result.stage_reached = "init"
            elif self.branch_cfg.dual_init:
                # init0: 借道出口 (init0_ccs) 拿 payment_method_types
                initr0 = await self._run_stage("init", stage_init_live, None, pk, cs,
                                               self.branch_name,
                                               fail_reason="init_failed")
                init0 = initr0.get("init", {}) if isinstance(initr0, dict) else {}
                # init1: 回本地 (init1_ccs) 验真, 取最后一轮结果
                initr1 = await self._run_stage("init", stage_init_live, None, pk, cs,
                                               self.branch_name,
                                               fail_reason="init_failed")
                init1 = initr1.get("init", {}) if isinstance(initr1, dict) else {}
                # 合并: init1 优先, 缺字段回退 init0
                init = {**init0, **init1}
                self.result.stage_reached = "init"
                self._init_rounds = {"init0": init0, "init1": init1}
            else:
                initr = await self._run_stage("init", stage_init_live, None, pk, cs,
                                              self.branch_name,
                                              fail_reason="init_failed")
                init = initr.get("init", {}) if isinstance(initr, dict) else {}
                self.result.stage_reached = "init"

            # S2.5 渠道探测 (先探测后压 0):
            # checkout 不带 promo 拿到真实 payment_method_types, 校验目标渠道是否存在。
            # 探测到渠道才继续 update 压 0; 探测不到直接失败 (链路列表 init 阶段显示)。
            # direct 直卡提链跳过探测 (不走 Stripe init)。
            chk_channel = self.branch_cfg.channel
            chk_check = self.branch_cfg.channel_check and self.options.get("channel_check", True)
            if chk_check and self.branch_name != "direct":
                methods = init.get("payment_method_types")
                methods_l = [str(m).lower() for m in methods] if isinstance(methods, list) else []
                await self._emit({"type": "channel_detect", "stage": "init",
                                  "channel": chk_channel, "methods": methods_l,
                                  "present": chk_channel in methods_l,
                                  "country": self.pick.get("init", country)})
                if chk_channel not in methods_l:
                    self.result.reason_code = "paypal_unsupported"
                    self.result.reason_text = f"渠道探测失败: init 无 {chk_channel} 渠道, payment_method_types={methods_l}"
                    self.result.stage_reached = "init"
                    self._stage_states["init"] = {"state": "fail", "country": self.pick.get("init", country)}
                    await self._emit({"type": "stage_fail", "stage": "init",
                                      "country": self.pick.get("init", country)})
                    await self._finish_failure()
                    return self.result

            # S3 update / 双出口注入 promo 压 0 元 + 金额守卫 + 支付渠道校验
            chk_zero = self.branch_cfg.require_zero and self.options.get("require_zero", True)
            try:
                if settings.chain_mode == "live" and _HAS_CURL:
                    # update 段: 在 update_region 出口对已建 checkout session 注入 promo
                    upd_country = self.pick.get("update", country)
                    upd_proxy = self._pick_proxy("update", upd_country)
                    await self._emit({"type": "stage_try", "stage": "update",
                                      "country": upd_country, "try_n": 1, "max_try": 1})
                    upd = await self._run_in_executor(
                        stage_update_live, upd_proxy, self.access_token, self.session_token,
                        cs, entity, self.pick.get("_billing", country),
                        self.pick.get("_currency", "USD"), self.branch_name)
                    if not upd.get("ok"):
                        await self._emit({"type": "stage_fail", "stage": "update",
                                          "country": upd_country})
                        self._stage_states["update"] = {"state": "fail", "country": upd_country}
                        raise ChainStageError(
                            "non_zero_amount",
                            f"update status={upd.get('status')} body={json.dumps(upd.get('body'), ensure_ascii=False)[:300]}")
                    await self._emit({"type": "stage_ok", "stage": "update",
                                      "country": upd_country})
                    self._stage_states["update"] = {"state": "ok", "country": upd_country}
                    if self.branch_name == "direct":
                        # 直卡: 从 update body 提取金额验证 (checkout_state.total), 无需 Stripe init
                        amount = _extract_amount_minor(upd.get("body"))
                        gate = {"amount_due": amount, "currency": self.pick.get("_currency", "USD"),
                                "zero_verified": (amount == 0)}
                        if chk_zero and amount != 0:
                            raise NonZeroAmount(amount or -1, str(gate["currency"]))
                        self.result.stage_reached = "update"
                    else:
                        # update 注册 promo 成功后重跑 init 拿新 amount_due
                        initr = await self._run_stage("init", stage_init_live, None, pk, cs,
                                                      self.branch_name,
                                                      fail_reason="init_failed")
                        init = initr.get("init", {}) if isinstance(initr, dict) else {}
                        self.result.stage_reached = "init"
                        gate = await self._run_stage("update", lambda *_: verify_zero(
                            init, require_zero=chk_zero, channel_check=chk_check, channel=chk_channel),
                                                     fail_reason="non_zero_amount")
            except NonZeroAmount as e:
                self.result.reason_code = "non_zero_amount"
                self.result.reason_text = f"amount_due={e.amount} {e.currency} 非 0"
                self.result.stage_reached = "init"
                await self._finish_failure()
                return self.result
            except ChannelMismatch as e:
                self.result.reason_code = "paypal_unsupported"
                self.result.reason_text = str(e)
                self.result.stage_reached = "init"
                await self._finish_failure()
                return self.result
            except ChainStageError as e:
                if "paypal" in str(e) or "渠道" in str(e) or "channel" in str(e).lower():
                    self.result.reason_code = "paypal_unsupported"
                else:
                    self.result.reason_code = "non_zero_amount"
                self.result.reason_text = str(e)
                await self._finish_failure()
                return self.result
            self.result.amount_due = gate.get("amount_due", 0)
            self.result.currency = gate.get("currency", "")
            self.result.stage_reached = "update"

            # 直卡提链: 截断路径 (checkout 无 promo -> update 压 0 -> 验证 -> 产出短链接)
            # 产出: https://chatgpt.com/checkout/{entity}/{cs_id}
            prof2 = branch_profile(self.branch_name)
            if prof2.get("truncate_after_update"):
                # 跳过 init/provider/approve/poll/resolve, 直接成功
                ent = entity or ("openai_llc" if country == "US" else "openai_ie")
                final_url = f"https://chatgpt.com/checkout/{ent}/{cs}"
                self.result.success = True
                self.result.paypal_approve_url = final_url
                self.result.stage_reached = "resolve"
                self.result.elapsed = time.monotonic() - t0
                # 标记后续段为 ok (链路监控显示)
                for _s in ("provider", "approve", "poll", "resolve"):
                    await self._emit({"type": "stage_try", "stage": _s,
                                      "country": self.pick.get(_s, country), "try_n": 1, "max_try": 1})
                    await self._emit({"type": "stage_ok", "stage": _s,
                                      "country": self.pick.get(_s, country)})
                    self._stage_states[_s] = {"state": "ok", "country": self.pick.get(_s, country)}
                await self._emit({
                    "type": "chain_success",
                    "paypal_approve_url": final_url,
                    "pm_authorize_url": "",
                    "country": self.result.country,
                    "email": self.email,
                    "amount": self.result.amount_due,
                    "currency": self.result.currency,
                    "ba_token": "",
                    "branch": self.branch_name,
                    "elapsed": round(self.result.elapsed, 2),
                })
                return self.result

            ctx = build_ctx(init)

            # S4 provider (PM + confirm)
            provider_country = self.pick["provider"]
            # 账单国: 配置了固定国家则用之, 否则跟随 provider 段出口国
            bc = (self.branch_cfg.billing_country or "auto").upper()
            billing_country = provider_country if bc in ("AUTO", "") else bc
            self.result.billing_country = billing_country
            try:
                if settings.chain_mode == "live" and _HAS_CURL:
                    pm_proxy = self._pick_proxy("provider", provider_country)
                    pm = await self._run_in_executor(
                        stage_payment_method_live, pm_proxy, pk, cs, init, billing_country, ctx,
                        self.branch_name)
                    await self._emit({"type": "stage_try", "stage": "provider",
                                      "country": provider_country, "try_n": 1, "max_try": 1})
                    cf = await self._run_in_executor(
                        stage_confirm_live, pm_proxy, pk, cs, init, pm, ctx, provider_country, entity,
                        chk_zero, chk_check, chk_channel, self.branch_name)
                    await self._emit({"type": "stage_ok", "stage": "provider", "country": provider_country})
                    self._stage_states["provider"] = {"state": "ok", "country": provider_country}
                    redirect = cf.get("redirect", "")
                    state = cf.get("confirm_state", "")
                else:
                    pr = await self._mock_stage("provider")
                    await self._emit({"type": "stage_try", "stage": "provider",
                                      "country": provider_country, "try_n": 1, "max_try": 1})
                    await self._emit({"type": "stage_ok", "stage": "provider", "country": provider_country})
                    self._stage_states["provider"] = {"state": "ok", "country": provider_country}
                    redirect = pr.get("redirect", "")
                    state = pr.get("confirm_state", "")
            except Exception as e:
                self._stage_states["provider"] = {"state": "fail", "country": provider_country}
                await self._emit({"type": "stage_fail", "stage": "provider", "country": provider_country})
                self.result.reason_code = "provider_failed"
                self.result.reason_text = str(e)
                self.result.stage_reached = "provider"
                await self._finish_failure()
                return self.result
            self.result.stage_reached = "provider"

            # S5 approve (仅当无 redirect 且 requires_approval 时)
            approve_country = self.pick["approve"]
            if not redirect and state == "requires_approval":
                try:
                    ap = await self._run_stage("approve", stage_approve_live,
                                               None, self.access_token, self.session_token, cs, entity,
                                               self.branch_name,
                                               fail_reason="approve_failed")
                    if isinstance(ap, dict):
                        redirect = ap.get("redirect", "") or redirect
                except ChainStageError as e:
                    self.result.reason_code = "approve_failed"
                    self.result.reason_text = str(e)
                    self.result.stage_reached = "approve"
                    await self._finish_failure()
                    return self.result
            else:
                # 跳过 approve 段，直接标记 ok
                await self._emit({"type": "stage_try", "stage": "approve",
                                  "country": approve_country, "try_n": 1, "max_try": 1})
                await self._emit({"type": "stage_ok", "stage": "approve", "country": approve_country})
                self._stage_states["approve"] = {"state": "ok", "country": approve_country}
            self.result.stage_reached = "approve"

            # S6 poll
            if not redirect:
                try:
                    poll_res = await self._run_stage("poll", stage_poll_live, None, pk, cs,
                                                     None, self.branch_name, fail_reason="poll_timeout")
                    redirect = poll_res.get("redirect", "") if isinstance(poll_res, dict) else ""
                except ChainStageError as e:
                    self.result.reason_code = "poll_timeout"
                    self.result.reason_text = str(e)
                    self.result.stage_reached = "poll"
                    await self._finish_failure()
                    return self.result
            else:
                await self._emit({"type": "stage_try", "stage": "poll",
                                  "country": self.pick["poll"], "try_n": 1, "max_try": 1})
                await self._emit({"type": "stage_ok", "stage": "poll", "country": self.pick["poll"]})
                self._stage_states["poll"] = {"state": "ok", "country": self.pick["poll"]}
            # 校验 pm-redirects
            prof = branch_profile(self.branch_name)
            resolve_re = prof["resolve_re"]
            search_re = prof["resolve_search_re"]
            if not redirect or not (RE_PM_AUTHORIZE.match(redirect) or (search_re and search_re.search(redirect))):
                self.result.reason_code = "no_redirect"
                self.result.reason_text = f"redirect 不匹配 pm-redirects: {redirect[:80]}"
                self.result.stage_reached = "poll"
                self._stage_states["poll"] = {"state": "fail", "country": self.pick["poll"]}
                await self._emit({"type": "stage_fail", "stage": "poll", "country": self.pick["poll"]})
                await self._finish_failure()
                return self.result
            self.result.pm_authorize_url = redirect
            self.result.stage_reached = "poll"

            # S7 resolve
            try:
                rs = await self._run_stage("resolve", stage_resolve_live,
                                           None, redirect, None, self.branch_name, fail_reason="resolve_failed")
                final_url = rs.get("url", "") if isinstance(rs, dict) else ""
            except ChainStageError as e:
                self.result.reason_code = "resolve_failed"
                self.result.reason_text = str(e)
                self.result.stage_reached = "resolve"
                await self._finish_failure()
                return self.result
            # 校验最终 URL（按分支正则）
            if not final_url or not (resolve_re and resolve_re.match(final_url)):
                self.result.reason_code = "resolve_failed"
                self.result.reason_text = f"最终 URL 不匹配 {self.branch_name}: {final_url[:80]}"
                self.result.stage_reached = "resolve"
                self._stage_states["resolve"] = {"state": "fail", "country": self.pick["resolve"]}
                await self._emit({"type": "stage_fail", "stage": "resolve", "country": self.pick["resolve"]})
                await self._finish_failure()
                return self.result

            # 成功
            self.result.success = True
            self.result.paypal_approve_url = final_url
            m = re.search(r"ba_token=([A-Za-z0-9-]+)", final_url)
            self.result.ba_token = m.group(1) if m else ""
            self.result.stage_reached = "resolve"
            self.result.elapsed = time.monotonic() - t0
            await self._emit({
                "type": "chain_success",
                "paypal_approve_url": final_url,
                "pm_authorize_url": redirect,
                "country": self.result.country,
                "email": self.email,
                "amount": self.result.amount_due,
                "currency": self.result.currency,
                "ba_token": self.result.ba_token,
                "branch": self.branch_name,
                "elapsed": round(self.result.elapsed, 2),
            })
            return self.result

        except asyncio.CancelledError:
            self.result.reason_code = "network_error"
            self.result.reason_text = "链路被取消"
            await self._finish_failure()
            raise
        except Exception as e:
            self.result.reason_code = "network_error"
            self.result.reason_text = f"{type(e).__name__}: {e}"
            await self._finish_failure()
            return self.result

    async def _finish_failure(self) -> None:
        self.result.elapsed = time.monotonic() - 0  # 由调用方覆盖
        await self._emit({
            "type": "chain_failure",
            "reason_code": self.result.reason_code,
            "reason_text": self.result.reason_text,
            "country": self.result.country,
            "stage_reached": self.result.stage_reached,
            "email": self.email,
        })


class ChainStageError(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
