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
# MoMo 链路执行器 (mock)
# =============================================================================
class MomoChain:
    """MoMo 提链执行器。复用 7 段框架, 注入 MoMo 专用 Patch。"""

    STAGES = ["checkout", "init", "provider", "approve", "poll", "resolve"]

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
        """执行 MoMo 提链 (mock 模式)。

        依次应用五层 Patch，每段模拟耗时与成败。
        """
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
