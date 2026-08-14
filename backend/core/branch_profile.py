# -*- coding: utf-8 -*-
"""提链分支画像（branch profiles）：各支付渠道在 7 段链路上的差异点。

每个分支定义：
  - pm_type:                payment_method 创建的 type（paypal/momo/pix/ideal/upi/kakao/blik/twint/card）
  - confirm_type:           confirm 的 expected_payment_method_type
  - pm_extra:               PM body 额外字段（如 pix 的 billing_details[tax_id]=CPF）
  - resolve_re:             resolve 段成功判定正则
  - resolve_search_re:      resolve 跟随 302 链时在 Location/正文中搜索的正则
  - output_key:             ChainResult 的产出字段名（paypal_approve_url / payment_url ...）
  - require_ba:             True=产出 BA token 语义（paypal），False=产出支付 URL（momo/pix 等）
  - referrer:               PM/confirm 的 referrer（paypal=momo 的 chatgpt.com，ideal 等可能不同）

配方来源（D:\\整理 历史实现）：
  - paypal:  v2 已验证链路（init_checksum 下划线 + attribution 全套 + consent tos）
  - momo:    v1/v2 core/momo.py 五层 Patch（type=momo + VN 账单 + payment.momo.vn/pay/app）
  - pix:     pix-core-open-source + pix-qr-extractor（C7 实证：满价 PaymentIntent + tax_id CPF +
             next_action.pix_display_qr_code；0 元压价会被滤掉 pix）
  - ideal:   upl-main/ideal_qr_extract.py（NL/VN/NL；type=ideal + NL 账单 + 银行跳转 URL）
  - upi:     upl-main/upi/upi_extract.py（IN/VN/IN；payments.stripe.com/upi/instructions）
  - kakao:   upl-main/kakao/kakao_extract.py（KR/VN/KR；nicepay/kakao 跳转）
  - blik:    upl-main/blik/blik_qr_extract.py（PL/PL/PL；Stripe 接口提交 BLIK Code）
  - twint:   upl-main/twint/twint_extract.py（CH/VN/CH）
"""
from __future__ import annotations

import re
from typing import Any

# ---- 各分支成功产出正则 ----

RE_PAYPAL_BA = re.compile(r"^https://www\.paypal\.com/agreements/approve\?ba_token=[A-Za-z0-9-]+$")
RE_PAYPAL_BA_SEARCH = re.compile(r"https://www\.paypal\.com/agreements/approve\?ba_token=[A-Za-z0-9-]+")
RE_PM_AUTHORIZE = re.compile(r"^https://pm-redirects\.stripe\.com/authorize/")

RE_MOMO_PAY = re.compile(r"^https://payment\.momo\.vn/pay/app/[A-Za-z0-9]+")
RE_MOMO_PAY_SEARCH = re.compile(r"https://payment\.momo\.vn/pay/app/[A-Za-z0-9]+")

RE_DIRECT_LINK = re.compile(r"^https://chatgpt\.com/checkout/[a-z_]+/[A-Za-z0-9_-]+$")

RE_PIX_URL = re.compile(r"^https://(?:pay\.openai\.com|checkout\.stripe\.com)/[^\s\"']+")
RE_PIX_QR = re.compile(r"br\.gov\.bcb\.pix[^\s\"']+")

RE_IDEAL_URL = re.compile(r"^https://[^\s\"']+")
RE_UPI_URL = re.compile(r"^https://payments\.stripe\.com/upi/[^\s\"']+")
RE_UPI_URL_SEARCH = re.compile(r"https://payments\.stripe\.com/upi/[^\s\"']+")
RE_KAKAO_URL = re.compile(r"^https://[^\s\"']+(?:nicepay|kakao)[^\s\"']*", re.I)
RE_NAVER_URL = re.compile(r"^https://[^\s\"']+(?:nicepay|naver)[^\s\"']*", re.I)
RE_GOPAY_URL = re.compile(r"^https://[^\s\"']+(?:midtrans|snap)[^\s\"']*", re.I)
RE_BIZUM_URL = re.compile(r"^https://checkout\.stripe\.com/c/[^\s\"']+")
RE_BIZUM_SEARCH = re.compile(r"https://checkout\.stripe\.com/c/[^\s\"']+")
RE_BLIK_URL = re.compile(r"^https://[^\s\"']+")
RE_TWINT_URL = re.compile(r"^https://[^\s\"']+")
# 钱包渠道 (wallet_adapter 移植): gcash=Adyen, grabpay=Grab, qris=Midtrans
RE_GCASH_URL = re.compile(r"^https://checkoutshopper-live\.adyen\.com/[^\s\"']+")
RE_GCASH_SEARCH = re.compile(r"https://checkoutshopper[^\"'\s<>]+adyen\.com/[^\s\"']+")
RE_GRABPAY_URL = re.compile(r"^https://[^\s\"']*(?:grab\.com|grabpay\.com)[^\s\"']*", re.I)
RE_GRABPAY_SEARCH = re.compile(r"https://[^\s\"']*(?:grab\.com|grabpay\.com)[^\s\"']*", re.I)
RE_QRIS_URL = re.compile(r"^https://[^\s\"']*(?:midtrans|snap)[^\s\"']*", re.I)
RE_QRIS_SEARCH = re.compile(r"https://[^\s\"']*(?:midtrans|snap)[^\s\"']*", re.I)


def _pm_type(branch: str) -> str:
    return {
        "paypal": "paypal",
        "momo": "momo",
        "pix": "pix",
        "ideal": "ideal",
        "upi": "upi",
        "kakao": "kakao",
        "blik": "blik",
        "twint": "twint",
        "bizum": "bizum",
        "gopay": "gopay",
        "naver_pay": "naver_pay",
        "gcash": "gcash",
        "grabpay": "grabpay",
        "qris": "gopay",  # qris 在 Stripe 侧以 gopay PM 种子建立 (midtrans charge 分支)
        "grok": "card",
        "direct": "card",  # 直卡提链: 无 Stripe PM, 仅产出 checkout 短链接
    }.get(branch, branch)


def _pm_extra(branch: str, country: str = "") -> dict[str, str]:
    """PM body 额外字段（按分支）。"""
    if branch == "pix":
        # pix-core-open-source: billing_details[tax_id] 放 CPF (调用方已生成有效 CPF)
        from .link_helpers import generate_valid_cpf

        cpf = generate_valid_cpf()
        return {"billing_details[tax_id]": cpf}
    return {}


def _resolve_regexes(branch: str) -> tuple[re.Pattern | None, re.Pattern | None]:
    """返回 (success_re, search_re)。"""
    if branch == "paypal":
        return RE_PAYPAL_BA, RE_PAYPAL_BA_SEARCH
    if branch == "momo":
        return RE_MOMO_PAY, RE_MOMO_PAY_SEARCH
    if branch == "upi":
        return RE_UPI_URL, RE_UPI_URL_SEARCH
    if branch == "direct":
        return RE_DIRECT_LINK, RE_DIRECT_LINK
    if branch == "pix":
        return RE_PIX_URL, RE_PIX_QR
    if branch == "kakao":
        return RE_KAKAO_URL, RE_KAKAO_URL
    if branch == "naver_pay":
        return RE_NAVER_URL, RE_NAVER_URL
    if branch == "gopay":
        return RE_GOPAY_URL, RE_GOPAY_URL
    if branch == "qris":
        return RE_QRIS_URL, RE_QRIS_SEARCH
    if branch == "gcash":
        return RE_GCASH_URL, RE_GCASH_SEARCH
    if branch == "grabpay":
        return RE_GRABPAY_URL, RE_GRABPAY_SEARCH
    if branch == "bizum":
        # bizum 无渠道跳转 (await_authorization), 产出 hosted checkout 页由用户
        # 在手机上完成 Bizum 授权; extract_redirect 兜底到 stripe_hosted_url
        return RE_BIZUM_URL, RE_BIZUM_SEARCH
    if branch == "ideal":
        return RE_IDEAL_URL, RE_IDEAL_URL
    if branch == "blik":
        return RE_BLIK_URL, RE_BLIK_URL
    if branch == "twint":
        return RE_TWINT_URL, RE_TWINT_URL
    return RE_IDEAL_URL, RE_IDEAL_URL


def branch_profile(branch: str) -> dict[str, Any]:
    """返回分支画像 dict。"""
    b = str(branch or "paypal").lower()
    success_re, search_re = _resolve_regexes(b)
    return {
        "branch": b,
        "pm_type": _pm_type(b),
        "confirm_type": _pm_type(b),
        "pm_extra": _pm_extra(b),
        "resolve_re": success_re,
        "resolve_search_re": search_re,
        "output_key": "paypal_approve_url" if b == "paypal" else "payment_url",
        "require_ba": b == "paypal",
        "referrer": "https://chatgpt.com",
        # checkout 是否带 promo_campaign: 所有链路 checkout 不带 promo (先拿真实渠道),
        # update 段再注入 promo 压 0 元 (upl-main 全链路 "先渠道后压 0" 模式)
        "checkout_promo": False,
        # update 压 0 后是否要求 amount_due=0（所有链路都要压 0）
        "require_zero": True,
        # 截断模式: direct 在 update 压 0 验证后即产出 checkout 短链接,
        # 不经过 Stripe init/provider/approve/poll/resolve
        "truncate_after_update": b == "direct",
    }
