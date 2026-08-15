# -*- coding: utf-8 -*-
"""直卡纯协议支付段: 提链 → 绑卡 → 支付确认 → 订阅验证 (零浏览器)。

流程 (纯协议, 参考 zkky):
  1. 提链: checkout(US) → update(TR 压0) → oaics 短链
  2. 绑卡: POST /backend-api/payments/payment_method → SetupIntent
     → POST /v1/setup_intents/{seti}/confirm (内联卡数据) → pm_id
  3. 支付确认: POST /v1/confirmation_tokens → ctoken
     → POST /backend-api/payments/checkout/confirm → final seti
     → POST /v1/setup_intents/{final}/confirm → succeeded
  4. 订阅验证: GET /backend-api/subscriptions → plan_type == "plus"

卡片来源: core/card_store (内置测试卡 4000 0000 0000 0002 / 12/30 / 123)
免税地址: core/taxfree_store (DE/NH/MT/OR/AK)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .bind_card import bind_card, bind_and_pay, list_payment_methods
from .card_store import card_store
from .chain import chatgpt_session, make_session, _req, stage_checkout_live, stage_update_live
from .config import settings
from .taxfree_store import taxfree_store

log = logging.getLogger(__name__)


class DirectPayError(RuntimeError):
    pass


def _pick_proxy(stage: str, country: str) -> str:
    from .proxy_pool import proxy_pool
    try:
        return proxy_pool.pick_for_stage(stage, country)
    except Exception as e:
        raise DirectPayError(f"代理获取失败: {e}") from e


def _run_stage(fn, *args, tries: int = 3, **kw) -> Any:
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kw)
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def direct_checkout(
    access_token: str,
    session_token: str = "",
    *,
    billing_country: str = "PH",
    currency: str = "PHP",
) -> dict[str, Any]:
    """直卡提链核心: checkout(US) → update(TR 压 0) → 产出短链。"""
    co_px = _pick_proxy("checkout", "US")
    co = _run_stage(stage_checkout_live, co_px, access_token, session_token,
                    billing_country, currency, "direct")
    if not co.get("ok"):
        raise DirectPayError(f"checkout 失败: status={co.get('status')} {co.get('detail')}")
    cs = co["checkout_session_id"]
    entity = co.get("processor_entity") or "openai_llc"

    upd_px = _pick_proxy("update", "TR")
    upd = _run_stage(stage_update_live, upd_px, access_token, session_token,
                     cs, entity, "US", "USD", "direct")
    if not upd.get("ok"):
        raise DirectPayError(f"update 压0失败: status={upd.get('status')}")

    # 金额验证 (从 update body 提取)
    from .chain import _extract_amount_minor
    amount = _extract_amount_minor(upd.get("body"))
    if amount not in (0, None):
        raise DirectPayError(f"压0失败: amount={amount}")
    short_link = f"https://chatgpt.com/checkout/{entity}/{cs}"
    return {"short_link": short_link, "cs": cs, "entity": entity,
            "amount": amount or 0, "billing_country": billing_country}


def bind_and_subscribe(
    access_token: str,
    session_token: str = "",
    *,
    account_id: str = "",
    card_id: int | None = None,
    taxfree_state: str = "DE",
    rebind_recheckout: bool = True,
    use_cdp_fallback: bool = False,
    hcaptcha_token: str = "",
) -> dict[str, Any]:
    """完整直卡纯协议支付: 提链 → 绑卡 → 支付确认 → 订阅验证。

    Args:
        use_cdp_fallback: True=纯协议失败时回退到 CDP 浏览器绑卡 (默认 False, 纯协议优先)
        hcaptcha_token: hCaptcha passive token (可选, Stripe radar_options)
    """
    out: dict[str, Any] = {"ok": False, "step": ""}
    proxy = _pick_proxy("checkout", "US")

    # 0. 取卡 (卡片库)
    card = None
    if card_id:
        card = card_store.get_card(card_id)
    if not card:
        card = card_store.pickup_card()
    if not card:
        out.update(step="card", error="卡片库无可用卡")
        return out
    out["card_last4"] = str(card.get("number", ""))[-4:]

    # 1. 提链 (短链)
    try:
        ch1 = direct_checkout(access_token, session_token)
    except DirectPayError as e:
        out.update(step="checkout1", error=str(e))
        return out
    out["short_link_1"] = ch1["short_link"]
    cs1 = ch1["cs"]
    entity = ch1["entity"]

    # 2. account_id (从 JWT 提取)
    if not account_id:
        import base64
        try:
            payload_b64 = access_token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            jwt = json.loads(base64.urlsafe_b64decode(payload_b64))
            auth_claims = jwt.get("https://api.openai.com/auth") or {}
            account_id = str(auth_claims.get("chatgpt_account_id") or "")
        except Exception:
            account_id = ""
    if not account_id:
        out.update(step="bind", error="无法确定 account_id (需提供 account_id)")
        return out

    # 3. 免税地址 (账单)
    addr = taxfree_store["generate"](state=taxfree_state)
    billing = {
        "name": card.get("name") or "Test User",
        "line1": addr.get("street", ""),
        "city": addr.get("city", ""),
        "state": addr.get("state", ""),
        "postal_code": addr.get("zip", ""),
        "country": "US",  # 绑卡用 US 地址 (免税州)
    }

    # 4. 纯协议完整流程: 绑卡 + 支付确认 + 订阅验证
    log.info("Starting pure-protocol bind_and_pay...")
    result = bind_and_pay(
        proxy, access_token, account_id, card, session_token,
        checkout_id=cs1, processor=entity,
        billing=billing, currency="USD",
        fast_verify=True, timeout=30,
        hcaptcha_token=hcaptcha_token,
    )
    out["bind_and_pay"] = result

    if not result.get("ok"):
        # 纯协议失败, 可选回退 CDP
        if use_cdp_fallback:
            log.warning(f"Pure protocol failed at {result.get('step')}, falling back to CDP...")
            try:
                from .cdp_bind import cdp_bind_card
                import asyncio
                bind = asyncio.new_event_loop().run_until_complete(
                    cdp_bind_card(access_token, account_id, card, session_token)
                )
            except Exception as e:
                bind = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            out["cdp_fallback"] = bind
            if not bind.get("ok"):
                out.update(step="bind", error=f"pure: {result.get('error')} | cdp: {bind.get('error')}")
                return out
            # CDP 只绑卡, 不走支付确认
            out["step"] = "done_bind_only"
            out["pm_id"] = bind.get("pm_id", "")
            out["method"] = "cdp_fallback"
        else:
            out.update(step=result.get("step", "bind"),
                       error=result.get("error", "pure protocol failed"))
            return out
    else:
        out["method"] = "pure_protocol"
        out["pm_id"] = result.get("pm_id", "")
        out["subscription_plan"] = result.get("subscription_plan", "")

    # 5. 重新提链 (session 刷新, 可选)
    if rebind_recheckout:
        try:
            ch2 = direct_checkout(access_token, session_token)
            out["short_link_2"] = ch2["short_link"]
            out["cs_2"] = ch2["cs"]
        except DirectPayError as e:
            out["recheckout_error"] = str(e)

    out["taxfree_address"] = addr
    out["taxfree_state"] = taxfree_state
    out["ok"] = True
    out["step"] = "done"
    out["subscribe_hint"] = (
        f"纯协议绑卡+支付{'成功' if result.get('ok') else '完成(绑卡)'}, "
        f"subscription={result.get('subscription_plan', 'N/A')}"
    )
    return out
