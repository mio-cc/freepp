"""MoMo 提链：五层 Patch 实现。

五层 Patch：
1. CONNECT 拦截: 拦截 api.stripe.com CONNECT, 直连绕过代理 TLS 拦截
2. DNS 修复: Clash fake-ip 198.18.x.x 检测, DoH 重解析真实 IP
3. payment_method 注入: 注入 momo payment type (替代 paypal)
4. confirm payload 构造: MoMo 专用 confirm body
5. resolve regex: MoMo 支付 URL 格式匹配 (momo.com/payment/...)

MoMo 链路复用 7 段框架，但在 provider/confirm/resolve 段注入 MoMo 专用逻辑。
"""
from __future__ import annotations

import asyncio
import random
import re
import uuid
from typing import Any, Awaitable, Callable

from .billing import billing_for
from .config import settings

# MoMo 支付 URL 正则 (越南 MoMo 钱包)
RE_MOMO_PAY = re.compile(r"^https://payment\.momo\.vn/pay/app/[A-Za-z0-9]+")
RE_MOMO_PAY_SEARCH = re.compile(r"https://payment\.momo\.vn/pay/app/[A-Za-z0-9]+")
# Clash fake-ip 段 198.18.0.0/15
RE_FAKE_IP = re.compile(r"^198\.(1[89]|2[0-9]|3[01])\.")

# DoH 服务器
DOH_SERVERS = ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"]

Emitter = Callable[[dict[str, Any]], Awaitable[None]]


class MomoPatches:
    """五层 Patch 开关状态。"""

    def __init__(self) -> None:
        cfg = settings.momo_cfg
        self.connect_intercept: bool = cfg.get("connect_intercept", True)
        self.dns_fix: bool = cfg.get("dns_fix", True)
        self.pm_inject: bool = cfg.get("pm_inject", True)
        self.confirm_build: bool = cfg.get("confirm_build", True)
        self.resolve_regex: bool = cfg.get("resolve_regex", True)

    def to_dict(self) -> dict[str, bool]:
        return {
            "connect_intercept": self.connect_intercept,
            "dns_fix": self.dns_fix,
            "pm_inject": self.pm_inject,
            "confirm_build": self.confirm_build,
            "resolve_regex": self.resolve_regex,
        }

    def update(self, patches: dict[str, bool] | None) -> None:
        if not patches:
            return
        for k, v in patches.items():
            if hasattr(self, k):
                setattr(self, k, bool(v))


# =============================================================================
# 五层 Patch 实现
# =============================================================================
def layer1_connect_intercept(host: str) -> dict[str, Any]:
    """L1: CONNECT 拦截 — 拦截 api.stripe.com CONNECT, 直连。

    返回拦截决策。实际生产中通过 mitmproxy/CONNECT 钩子实现；
    这里返回决策元数据供链路层使用。
    """
    if "api.stripe.com" in host:
        return {"intercepted": True, "host": host, "action": "direct_connect",
                "reason": "stripe API 直连绕过代理 TLS 拦截"}
    return {"intercepted": False, "host": host, "action": "proxy"}


def layer2_dns_fix(ip: str) -> dict[str, Any]:
    """L2: DNS 修复 — 检测 Clash fake-ip 198.18.x.x, 标记需 DoH 重解析。"""
    if RE_FAKE_IP.match(ip or ""):
        return {"is_fake_ip": True, "original_ip": ip,
                "doh_servers": DOH_SERVERS, "action": "doh_reresolve"}
    return {"is_fake_ip": False, "original_ip": ip, "action": "passthrough"}


def layer3_pm_inject(country: str = "VN") -> dict[str, Any]:
    """L3: payment_method 注入 — 注入 momo payment type。

    构造 MoMo 专用 payment_method body (type=momo, 越南账单)。
    """
    b = billing_for(country, fallback="VN")
    body = {
        "billing_details[name]": b["name"],
        "billing_details[email]": f"momo.{uuid.uuid4().hex[:6]}@example.com",
        "billing_details[address][country]": b["country"],
        "billing_details[address][line1]": b["line1"],
        "billing_details[address][city]": b["city"],
        "billing_details[address][state]": b["state"],
        "billing_details[address][postal_code]": b["postal_code"],
        "type": "momo",  # MoMo 支付类型
        "payment_user_agent": "stripe.js/momo; payment-element; deferred-intent",
        "referrer": "https://chatgpt.com",
        "time_on_page": str(25000 + (uuid.uuid4().int % 30000)),
    }
    return {"pm_type": "momo", "billing_country": country, "body": body,
            "pm_id": f"pm_momo_{uuid.uuid4().hex[:10]}"}


def layer4_confirm_build(cs: str, pm_id: str, country: str = "VN") -> dict[str, Any]:
    """L4: confirm payload 构造 — MoMo 专用 confirm body。"""
    return_url = (f"https://checkout.stripe.com/c/pay/{cs}?returned_from_redirect=true"
                  f"&ui_mode=custom&return_url=https://chatgpt.com/checkout/verify"
                  f"?stripe_session_id={cs}&plan_type=plus")
    body = {
        "guid": uuid.uuid4().hex, "muid": uuid.uuid4().hex, "sid": uuid.uuid4().hex,
        "payment_method": pm_id,
        "expected_payment_method_type": "momo",
        "return_url": return_url,
        "consent[terms_of_service]": "accepted",
    }
    return {"confirm_body": body, "return_url": return_url,
            "redirect": f"https://pm-redirects.stripe.com/authorize/momo_{uuid.uuid4().hex[:10]}"}


def layer5_resolve_regex(text: str) -> str:
    """L5: resolve regex — MoMo 支付 URL 格式匹配。"""
    m = RE_MOMO_PAY_SEARCH.search(text or "")
    return m.group(0) if m else ""


# =============================================================================
# MoMo 链路执行器 (live: 复用 chain.py 7 段 + follow redirect + QR 提取;
# mock: 模拟展示)
# =============================================================================
class MomoChain:
    """MoMo 提链执行器。

    live 模式真实流程 (对齐 run_momo/momo_qr_extract.emit_momo_qr):
      checkout(VN/VND) -> init -> update(压0) -> PM(momo) -> confirm
      -> approve -> poll -> follow redirect -> payment.momo.vn QR 产物

    mock 模式沿用原五层 Patch 模拟展示。
    """

    STAGES = ["checkout", "init", "update", "provider", "approve", "poll", "resolve"]

    def __init__(self, chain_id: str, token: dict[str, Any], patches: MomoPatches,
                 emitter: Emitter) -> None:
        self.chain_id = chain_id
        self.token = token
        self.patches = patches
        self.emit = emitter
        self.email = token.get("email", "")

    async def _emit(self, evt: dict[str, Any]) -> None:
        evt.setdefault("chain_id", self.chain_id)
        await self.emit(evt)

    async def execute(self) -> dict[str, Any]:
        if settings.chain_mode != "live":
            return await self._execute_mock()
        return await self._execute_live()

    # ------------------------------------------------------------------
    # live: 真实 HTTP 链路
    # ------------------------------------------------------------------
    async def _execute_live(self) -> dict[str, Any]:
        from .chain import (
            build_ctx,
            stage_approve_live,
            stage_checkout_live,
            stage_confirm_live,
            stage_init_live,
            stage_payment_method_live,
            stage_poll_live,
            stage_update_live,
            verify_zero,
        )
        from .link_helpers import extract_qr_artifacts, follow_gateway_redirect
        from .proxy_pool import proxy_pool

        country = "VN"
        currency = "VND"

        def px(stage: str) -> str:
            return proxy_pool.pick_for_stage(stage, country) or ""

        async def run_in_executor(fn, *args):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: fn(*args))

        async def stage(stage_name: str, fn, fail_reason: str, *args) -> Any:
            await self._emit({"type": "stage_try", "stage": stage_name,
                              "country": country, "try_n": 1, "max_try": 1})
            try:
                res = await run_in_executor(fn, *args)
                await self._emit({"type": "stage_ok", "stage": stage_name, "country": country})
                return res
            except Exception as e:
                await self._emit({"type": "stage_fail", "stage": stage_name, "country": country})
                await self._emit({"type": "chain_failure", "reason_code": fail_reason,
                                  "reason_text": f"{type(e).__name__}: {e}", "country": country,
                                  "stage_reached": stage_name})
                raise

        await self._emit({"type": "chain_start", "email": self.email,
                          "token_sub": self.token.get("sub", ""), "attempt": 1})
        at = self.token.get("access_token") or self.token.get("raw") or ""
        st = self.token.get("session_token") or ""
        t0 = asyncio.get_event_loop().time()
        try:
            # S1 checkout (VN/VND, 账单越南)
            co = await stage("checkout", stage_checkout_live, "checkout_failed",
                             px("checkout"), at, st, country, currency, "momo")
            if not co.get("ok"):
                raise RuntimeError(f"checkout failed: {co.get('detail') or co.get('status')}")
            cs = co["checkout_session_id"]
            pk = co["publishable_key"]
            entity = co.get("processor_entity") or "openai_ie"

            # S2 init (渠道校验: 须含 momo)
            ini = await stage("init", stage_init_live, "init_failed", px("init"), pk, cs, "momo")
            init = ini.get("init") or {}
            verify_zero(init, require_zero=False, channel_check=True, channel="momo")

            # S3 update 压 0 (越南 promo 出口) + 重 init 守卫
            upd = await stage("update", stage_update_live, "non_zero_amount",
                              px("update"), at, st, cs, entity, country, currency, "momo")
            if not upd.get("ok"):
                raise RuntimeError(f"update failed: status={upd.get('status')}")
            ini2 = await stage("init", stage_init_live, "init_failed", px("init"), pk, cs, "momo")
            init2 = ini2.get("init") or {}
            gate = verify_zero(init2, require_zero=True, channel_check=True, channel="momo")
            ctx = build_ctx(init2)

            # S4 PM(momo) + confirm
            pm = await stage("provider", stage_payment_method_live, "pm_creation_failed",
                             px("provider"), pk, cs, init2, country, ctx, "momo")
            cf = await stage("provider", stage_confirm_live, "confirm_failed",
                             px("provider"), pk, cs, init2, pm, ctx, country, entity,
                             True, True, "momo", "momo")
            redirect = cf.get("redirect", "")
            state = cf.get("confirm_state", "")
            artifacts = cf.get("artifacts") or {}

            # S5 approve (requires_approval 时)
            if not redirect and state == "requires_approval":
                ap = await stage("approve", stage_approve_live, "approve_failed",
                                 px("approve"), at, st, cs, entity, "momo")
                if isinstance(ap, dict):
                    redirect = ap.get("redirect", "") or redirect
            else:
                await self._emit({"type": "stage_try", "stage": "approve",
                                  "country": country, "try_n": 1, "max_try": 1})
                await self._emit({"type": "stage_ok", "stage": "approve", "country": country})

            # S6 poll (无 redirect 时轮询 setup_intent redirect)
            if not redirect:
                poll_res = await stage("poll", stage_poll_live, "poll_timeout",
                                       px("poll"), pk, cs, None, "momo")
                redirect = poll_res.get("redirect", "") if isinstance(poll_res, dict) else str(poll_res or "")
                if isinstance(poll_res, dict) and poll_res.get("artifacts"):
                    artifacts.update(poll_res["artifacts"])
            else:
                await self._emit({"type": "stage_try", "stage": "poll",
                                  "country": country, "try_n": 1, "max_try": 1})
                await self._emit({"type": "stage_ok", "stage": "poll", "country": country})
            if not redirect:
                raise RuntimeError("no redirect after confirm/approve/poll")

            # S7 resolve: follow 到 payment.momo.vn 抓 QR
            await self._emit({"type": "stage_try", "stage": "resolve",
                              "country": country, "try_n": 1, "max_try": 1})
            gw = await run_in_executor(follow_gateway_redirect, px("resolve"), redirect)
            final_url = gw.get("final_url", "") or redirect
            for k in ("qr_image_url", "qr_png_url", "qr_data", "hosted_instructions_url", "deep_link"):
                if gw.get(k):
                    artifacts.setdefault(k, gw[k])
            await self._emit({"type": "stage_ok", "stage": "resolve", "country": country})

            momo_url = artifacts.get("qr_image_url") or artifacts.get("qr_png_url") or final_url
            link_type = "momo_qr" if (artifacts.get("qr_image_url") or artifacts.get("qr_png_url")) else "momo_url"
            elapsed = round(asyncio.get_event_loop().time() - t0, 2)
            await self._emit({
                "type": "chain_success",
                "paypal_approve_url": momo_url,
                "pm_authorize_url": redirect,
                "country": country, "email": self.email,
                "amount": gate.get("amount_due", 0), "currency": currency,
                "branch": "momo", "link_type": link_type, "artifacts": artifacts,
                "elapsed": elapsed,
            })
            return {"success": True, "momo_url": momo_url, "country": country,
                    "link_type": link_type, "artifacts": artifacts,
                    "pm_authorize_url": redirect, "elapsed": elapsed}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._emit({"type": "chain_failure", "reason_code": "momo_failed",
                              "reason_text": f"{type(e).__name__}: {e}", "country": country,
                              "stage_reached": "resolve"})
            return {"success": False, "reason_code": "momo_failed", "error": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------------
    # mock: 原五层 Patch 模拟
    # ------------------------------------------------------------------
    async def _execute_mock(self) -> dict[str, Any]:
        await self._emit({"type": "chain_start", "email": self.email,
                          "token_sub": self.token.get("sub", ""), "attempt": 1})
        t0 = asyncio.get_event_loop().time()
        country = "VN"  # MoMo 锁越南

        # L1 CONNECT 拦截
        if self.patches.connect_intercept:
            layer1_connect_intercept("api.stripe.com")
            await self._emit({"type": "stage_try", "stage": "checkout",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.4, 1.2))
            await self._emit({"type": "stage_ok", "stage": "checkout", "country": country})

        # L2 DNS 修复
        if self.patches.dns_fix:
            layer2_dns_fix("198.18.0.1")
            await self._emit({"type": "stage_try", "stage": "init",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.4, 1.0))
            await self._emit({"type": "stage_ok", "stage": "init", "country": country})

        # L3 PM 注入
        if self.patches.pm_inject:
            pm = layer3_pm_inject(country)
            await self._emit({"type": "stage_try", "stage": "provider",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.5, 1.5))
            if random.random() < settings.mock_success_rate:
                await self._emit({"type": "stage_ok", "stage": "provider", "country": country})
            else:
                await self._emit({"type": "stage_fail", "stage": "provider", "country": country})
                await self._emit({"type": "chain_failure", "reason_code": "pm_creation_failed",
                                  "reason_text": "MoMo payment_method 注入失败", "country": country})
                return {"success": False, "reason_code": "pm_creation_failed"}

        # L4 confirm 构造
        if self.patches.confirm_build:
            cf = layer4_confirm_build(f"cs_momo_{uuid.uuid4().hex[:8]}",
                                      pm.get("pm_id", ""), country)
            await self._emit({"type": "stage_try", "stage": "approve",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.4, 1.0))
            await self._emit({"type": "stage_ok", "stage": "approve", "country": country})
            await self._emit({"type": "stage_try", "stage": "poll",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.6, 1.5))
            await self._emit({"type": "stage_ok", "stage": "poll", "country": country})

        # L5 resolve regex
        if self.patches.resolve_regex:
            await self._emit({"type": "stage_try", "stage": "resolve",
                              "country": country, "try_n": 1, "max_try": 1})
            await asyncio.sleep(random.uniform(0.4, 1.2))
            pay_token = uuid.uuid4().hex[:16]
            momo_url = f"https://payment.momo.vn/pay/app/{pay_token}"
            # 验证正则匹配
            matched = layer5_resolve_regex(momo_url)
            if matched:
                await self._emit({"type": "stage_ok", "stage": "resolve", "country": country})
                elapsed = round(asyncio.get_event_loop().time() - t0, 2)
                await self._emit({
                    "type": "chain_success",
                    "paypal_approve_url": matched,  # 复用前端字段展示 MoMo URL
                    "pm_authorize_url": cf.get("redirect", "") if self.patches.confirm_build else "",
                    "country": country, "email": self.email,
                    "amount": 0, "currency": "vnd", "elapsed": elapsed,
                })
                return {"success": True, "momo_url": matched, "country": country}
            else:
                await self._emit({"type": "stage_fail", "stage": "resolve", "country": country})

        await self._emit({"type": "chain_failure", "reason_code": "resolve_failed",
                          "reason_text": "MoMo 支付 URL 未匹配", "country": country})
        return {"success": False, "reason_code": "resolve_failed"}


# 全局 Patch 状态
momo_patches = MomoPatches()
