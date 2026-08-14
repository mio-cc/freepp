# -*- coding: utf-8 -*-
"""钱包渠道 wire 层: 用本项目 chain.py 栈实现 WalletProviderTransport。

- create_checkout / stripe_init / PM / confirm / approve / poll: chain.py 各 stage
- follow_redirect: link_helpers.follow_gateway_redirect
- gcash custom PM: /checkout/custom_payment_method/start + GET /checkout/{p}/{cs}
- qris charge: midtrans snap charge (payment_type=qris) + QR 产物
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import core.chain as ch
from core.link_helpers import extract_qr_artifacts, follow_gateway_redirect
from core.wallet_adapter import (
    GCASH_CUSTOM_PM_TYPE_ID,
    WalletProviderError,
    WalletProviderTransport,
    WalletTransportRequest,
)

_BASE = "https://chatgpt.com"
_MIDTRANS_BASE = "https://app.midtrans.com"


class ChainWalletTransport:
    """运用于本项目 7 段栈 + 代理池的 transport。"""

    def __init__(self, proxy_pool=None, exit_country: str = "") -> None:
        from core.proxy_pool import proxy_pool as default_pool

        self.pool = proxy_pool or default_pool
        self._proxy_cache: dict[str, str] = {}
        self.exit_country = exit_country

    # ---- 代理 ----

    def _proxy(self, stage: str, spec) -> str:
        country = self.exit_country or spec.country
        key = f"{stage}:{country}"
        if stage != "update" and key in self._proxy_cache:
            return self._proxy_cache[key]
        proxy = self.pool.pick_for_stage(stage, country) or ""
        if stage != "update" and proxy:
            self._proxy_cache[key] = proxy
        return proxy

    def _session(self, req: WalletTransportRequest, stage: str, spec=None,
                 chatgpt: bool = False) -> Any:
        """chatgpt=True 用 chatgpt_session (带 Bearer); False 用 make_session (纯 Stripe)."""
        spec = spec or _spec(req)
        proxy = self._proxy(stage, spec)
        if chatgpt:
            return ch.chatgpt_session(proxy, req.access_token, req.session_token)
        return ch.make_session(proxy)

    # ---- wire 实现 ----

    def create_checkout(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        body = dict(req.payload)
        body.setdefault("promo_campaign", {
            "promo_campaign_id": "plus-1-month-free", "is_coupon_from_query_param": False})
        s = self._session(req, "checkout", spec, chatgpt=True)
        path = "/backend-api/payments/checkout"
        try:
            r = ch._req(s, "POST", _BASE + path, json=body,
                        headers={"Referer": "https://chatgpt.com/",
                                 "x-openai-target-path": path, "x-openai-target-route": path},
                        timeout=25)
            d = r.json() if r.text else {}
            out = dict(d)
            out["_status"] = r.status_code
            if r.status_code >= 400:
                out["detail"] = str(d.get("detail") or (r.text or "")[:200])
            return out
        finally:
            s.close()

    def stripe_init(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "init", spec, chatgpt=False)
        try:
            body = {
                "browser_locale": spec.locale,
                "browser_timezone": _tz(spec.country),
                "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
                "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
                "elements_session_client[elements_init_source]": "custom_checkout",
                "elements_session_client[referrer_host]": "chatgpt.com",
                "elements_session_client[stripe_js_id]": str(req.payload.get("stripe_js_id") or uuid.uuid4()),
                "elements_session_client[locale]": "en",
                "elements_session_client[is_aggregation_expected]": "false",
                "elements_options_client[saved_payment_method][enable_save]": "never",
                "elements_options_client[saved_payment_method][enable_redisplay]": "never",
                "key": req.publishable_key,
                "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
            }
            r = ch._req(s, "POST", f"https://api.stripe.com/v1/payment_pages/{req.checkout_session_id}/init",
                        data=body, timeout=20)
            d = r.json() if r.text else {}
            out = dict(d)
            out["_status"] = r.status_code
            if r.status_code >= 400:
                raise WalletProviderError(f"stripe init failed: {r.status_code} {(r.text or '')[:200]}",
                                          error_code="wallet_stripe_init_failed", error_stage="stripe_init",
                                          retryable=True)
            return out
        finally:
            s.close()

    def create_payment_method(self, req: WalletTransportRequest) -> Mapping[str, Any] | str:
        spec = _spec(req)
        s = self._session(req, "provider", spec, chatgpt=False)
        try:
            b = ch.billing_for(spec.country)
            body = {
                "type": spec.stripe_type,
                "billing_details[name]": b["name"],
                "billing_details[email]": f"{re.sub(r'[^a-z0-9]+', '.', b['name'].lower()).strip('.')}.{uuid.uuid4().hex[:6]}@example.com",
                "billing_details[address][country]": spec.country,
                "billing_details[address][line1]": b["line1"],
                "billing_details[address][city]": b["city"],
                "billing_details[address][state]": b["state"],
                "billing_details[address][postal_code]": b["postal_code"],
                "payment_user_agent": "stripe.js/6f8494a281; stripe-js-v3/6f8494a281; payment-element; deferred-intent",
                "referrer": "https://chatgpt.com",
                "time_on_page": str(25000 + uuid.uuid4().int % 30000),
                "client_attribution_metadata[client_session_id]": str(req.payload.get("client_session_id") or uuid.uuid4()),
                "client_attribution_metadata[checkout_session_id]": req.checkout_session_id,
                "client_attribution_metadata[merchant_integration_source]": "elements",
                "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
                "client_attribution_metadata[merchant_integration_version]": "2021",
                "guid": uuid.uuid4().hex, "muid": uuid.uuid4().hex, "sid": uuid.uuid4().hex,
                "key": req.publishable_key,
                "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1",
            }
            r = ch._req(s, "POST", "https://api.stripe.com/v1/payment_methods", data=body, timeout=20)
            d = r.json() if r.text else {}
            pm = str(d.get("id") or "")
            if r.status_code >= 400 or not pm.startswith("pm_"):
                raise WalletProviderError(f"payment_method failed: {r.status_code} {(r.text or '')[:200]}",
                                          error_code="wallet_pm_failed", error_stage="payment_method", retryable=True)
            return pm
        finally:
            s.close()

    def confirm_payment(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "provider", spec, chatgpt=False)
        try:
            return _confirm_via_chain(s, req, spec, custom=False)
        finally:
            s.close()

    def approve_checkout(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "approve", spec, chatgpt=True)
        path = "/backend-api/payments/checkout/approve"
        try:
            r = ch._req(s, "POST", _BASE + path, json={"checkout_session_id": req.checkout_session_id,
                                                       "processor_entity": req.processor_entity},
                        headers={"Referer": f"https://chatgpt.com/checkout/{req.processor_entity}/{req.checkout_session_id}",
                                 "x-openai-target-path": path, "x-openai-target-route": path}, timeout=20)
            d = r.json() if r.text else {}
            return {**d, "_status": r.status_code}
        finally:
            s.close()

    def poll_payment(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "poll", spec, chatgpt=False)
        try:
            r = ch._req(s, "GET", f"https://api.stripe.com/v1/payment_pages/{req.checkout_session_id}",
                        params={"elements_session_client[locale]": "en", "key": req.publishable_key,
                                "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1"},
                        timeout=20)
            d = r.json() if r.text else {}
            return {**d, "_status": r.status_code}
        finally:
            s.close()

    def follow_redirect(self, req: WalletTransportRequest) -> Mapping[str, Any] | str:
        spec = _spec(req)
        proxy = self._proxy("resolve", spec)
        gw = follow_gateway_redirect(proxy, req.redirect_url)
        if gw.get("error") and not (gw.get("qr_image_url") or gw.get("hosted_instructions_url")):
            raise WalletProviderError(gw["error"], error_code="wallet_follow_failed",
                                      error_stage="follow_redirect", retryable=True, status="unknown")
        return {"url": gw.get("final_url") or req.redirect_url, "artifacts": gw}

    # ---- gcash custom PM ----

    def probe_custom_payment(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "provider", spec, chatgpt=True)
        try:
            r = ch._req(s, "GET", f"{_BASE}/{req.processor_entity}/{req.checkout_session_id}",
                        headers={"Referer": f"https://chatgpt.com/checkout/{req.processor_entity}/{req.checkout_session_id}",
                                 "Accept": "application/json"}, timeout=20)
            d = r.json() if r.text else {}
            return dict(d)
        finally:
            s.close()

    def confirm_custom_payment(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "provider", spec, chatgpt=False)
        try:
            return _confirm_via_chain(s, req, spec, custom=True)
        finally:
            s.close()

    def start_custom_payment(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        spec = _spec(req)
        s = self._session(req, "provider", spec, chatgpt=True)
        path = "/backend-api/payments/checkout/custom_payment_method/start"
        try:
            r = ch._req(s, "POST", _BASE + path, json={
                "checkout_session_id": req.checkout_session_id,
                "processor_entity": req.processor_entity,
                "custom_payment_method_type_id": GCASH_CUSTOM_PM_TYPE_ID,
            }, headers={"Referer": f"https://chatgpt.com/checkout/{req.processor_entity}/{req.checkout_session_id}",
                         "x-openai-target-path": path, "x-openai-target-route": path}, timeout=20)
            d = r.json() if r.text else {}
            return {**d, "_status": r.status_code}
        finally:
            s.close()

    # ---- QRIS: midtrans snap charge ----

    def qris_charge(self, req: WalletTransportRequest) -> Mapping[str, Any]:
        """midtrans snap charge payment_type=qris (回退 gopay untokenized)。"""
        spec = _spec(req)
        s = self._session(req, "resolve", spec)
        try:
            snap_token = ""
            parsed = urlsplit(req.redirect_url)
            m = re.search(r"/snap/v4/redirection/([A-Za-z0-9_]+)", parsed.path)
            if m:
                snap_token = m.group(1)
            if not snap_token:
                raise WalletProviderError("qris redirect did not carry a midtrans snap token",
                                          error_code="qris_snap_token_missing", error_stage="charge")
            # 交易信息
            r = ch._req(s, "GET", f"{_MIDTRANS_BASE}/snap/v1/transactions/{snap_token}", timeout=20)
            # charge: 先 qris, 失败回退 gopay untokenized
            attempts = (
                ("qris", {"payment_type": "qris", "qris": {"acquirer": "gopay"}, "promo_details": None}),
                ("gopay-untokenized", {"payment_type": "gopay", "tokenization": "false", "promo_details": None}),
            )
            last_err = ""
            for label, payload in attempts:
                try:
                    r = ch._req(s, "POST", f"{_MIDTRANS_BASE}/snap/v2/transactions/{snap_token}/charge",
                                json=payload, headers={"Origin": "https://app.midtrans.com",
                                                       "Referer": f"https://app.midtrans.com/snap/v4/redirection/{snap_token}"},
                                timeout=20)
                except Exception as e:
                    last_err = f"{label}: {type(e).__name__}: {str(e)[:120]}"
                    continue
                if r.status_code in (200, 201):
                    d = r.json() if r.text else {}
                    parsed_charge = _parse_qris_charge(d)
                    if parsed_charge:
                        return {"url": req.redirect_url, "artifacts": parsed_charge, "charge_mode": label}
                    last_err = f"{label}: missing qr_string/charge_ref: {str(d)[:160]}"
                    continue
                last_err = f"{label}: status={r.status_code} {(r.text or '')[:120]}"
            raise WalletProviderError(f"midtrans qris charge 全部失败: {last_err}",
                                      error_code="qris_charge_failed", error_stage="charge", retryable=True)
        finally:
            s.close()


def _spec(req: WalletTransportRequest):
    from core.wallet_adapter import wallet_method_spec

    return wallet_method_spec(req.method)


def _tz(country: str) -> str:
    return {"ID": "Asia/Jakarta", "PH": "Asia/Manila"}.get(country, "UTC")


def _confirm_via_chain(s, req: WalletTransportRequest, spec, *, custom: bool) -> Mapping[str, Any]:
    """复用 stage_confirm_live 的 confirm body (含 init_checksum/return_url 全字段)。"""
    init = {"init_checksum": req.payload.get("init_checksum", ""),
            "payment_method_types": [spec.stripe_type],
            "invoice": {"amount_due": 0 if req.payload.get("expected_amount") == "0" else (req.payload.get("expected_amount") or 0)}}
    ctx = ch.build_ctx(init)
    pm_id = str(req.payload.get("payment_method") or "")
    entity = req.processor_entity or "openai_ie"
    proxy = ""  # session 已带代理
    cf = ch.stage_confirm_live(proxy, req.publishable_key, req.checkout_session_id, init, pm_id, ctx,
                               spec.country, entity, require_zero=False, channel_check=False,
                               channel=spec.stripe_type, branch=spec.key)
    return {"state": "requires_approval", "redirect": cf.get("redirect", ""),
            "confirm_state": cf.get("confirm_state", ""), "artifacts": cf.get("artifacts", {})}


def _parse_qris_charge(data: Mapping[str, Any]) -> dict[str, Any]:
    """midtrans charge 响应 -> QR 产物 (移植 Gpt-Agreement-Payment qris.py)。"""
    qr_string = str(data.get("qr_string") or data.get("qris_string") or "")
    actions = data.get("actions") or []
    qr_image_url, deeplink_url = "", ""
    for act in actions if isinstance(actions, list) else []:
        if not isinstance(act, dict):
            continue
        name = str(act.get("name") or "").lower()
        u = str(act.get("url") or "")
        if "qr" in name and u and not qr_image_url:
            qr_image_url = u
        elif "deeplink" in name and u and not deeplink_url:
            deeplink_url = u
    if not qr_image_url:
        qr_image_url = str(data.get("qr_code_url") or data.get("qris_url")
                           or data.get("gopay_verification_link_url") or "")
    if not deeplink_url:
        deeplink_url = str(data.get("deeplink_url") or data.get("gopay_deeplink_url") or "")
    charge_ref = str(data.get("transaction_id") or "")
    if not charge_ref and qr_image_url:
        m = re.search(r"/qris/[a-z]+/([A-Za-z0-9]+)/qr-code", qr_image_url)
        if m:
            charge_ref = m.group(1)
    if not charge_ref:
        m = re.search(r"reference=([A-Za-z0-9]+)", qr_image_url or "")
        if m:
            charge_ref = m.group(1)
    if not (qr_string or qr_image_url) or not charge_ref:
        return {}
    return {
        "qr_string": qr_string,
        "qr_image_url": qr_image_url,
        "deeplink_url": deeplink_url,
        "charge_ref": charge_ref,
        "expiry_time": str(data.get("expiry_time") or data.get("expires_at") or ""),
    }


__all__ = ["ChainWalletTransport", "_parse_qris_charge", "_confirm_via_chain"]