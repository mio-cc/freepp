# -*- coding: utf-8 -*-
"""钱包渠道统一编排核心 (从 GPT-Register-Tool wallet_provider.py + gcash_provider.py 移植)。

无 HTTP 会话所有权: 调用方注入 WalletProviderTransport 执行 wire 调用。
支持三种模式:
  - stripe_pm:    普通 Stripe PM (gopay/grabpay) — PM/confirm/poll 走 Stripe
  - custom_pm:    custom payment method (gcash, cpmt_*, Adyen 跳转)
  - midtrans_qr:  midtrans snap charge (qris) — follow 后执行 charge 取 QR

产出与 ProtocolResult 契约字段对齐 (ok/status/url/link_type/retryable/error_stage/
requires_reconciliation/capability)。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

_RUNTIME_VERSION = "6f8494a281"
_SIDE_EFFECT_STAGES = frozenset({"confirm", "approve", "poll", "follow_redirect", "charge"})
_TERMINAL_FAILURE_STATES = frozenset({"declined", "denied", "failed", "rejected"})
_TERMINAL_CANCEL_STATES = frozenset({"canceled", "cancelled"})


@dataclass(frozen=True)
class WalletMethodSpec:
    key: str
    label: str
    country: str
    currency: str
    locale: str
    stripe_type: str
    redirect_hosts: tuple[str, ...]
    mode: str = "stripe_pm"  # stripe_pm | custom_pm | midtrans_qr


WALLET_SPECS: dict[str, WalletMethodSpec] = {
    "gopay": WalletMethodSpec("gopay", "GoPay", "ID", "IDR", "id-ID", "gopay",
                              ("gopay.co.id", "gojek.com", "midtrans.com")),
    "grabpay": WalletMethodSpec("grabpay", "GrabPay", "PH", "PHP", "en-PH", "grabpay",
                                ("grab.com", "grabpay.com")),
    "gcash": WalletMethodSpec("gcash", "GCash", "PH", "PHP", "en-PH", "gcash",
                              ("checkoutshopper-live.adyen.com", "checkoutshopper-test.adyen.com"),
                              mode="custom_pm"),
    "qris": WalletMethodSpec("qris", "QRIS", "ID", "IDR", "id-ID", "gopay",
                             ("midtrans.com",), mode="midtrans_qr"),
}

# gcash custom payment method type id (商户侧 cpmt)
GCASH_CUSTOM_PM_TYPE_ID = "cpmt_1TOgstC6h1nxGoI3WUVEY2cJ"


class WalletProviderError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "wallet_provider_failed",
                 error_stage: str = "wallet_provider", retryable: bool = False,
                 status: str = "failed") -> None:
        self.error_code = error_code
        self.error_stage = error_stage
        self.retryable = bool(retryable)
        self.status = status if status in {"failed", "cancelled", "timed_out", "unknown"} else "unknown"
        super().__init__(_redact(str(message)))


class WalletCancelledError(WalletProviderError):
    def __init__(self, message: str = "wallet flow was cancelled", *, error_stage: str = "wallet_provider") -> None:
        super().__init__(message, error_code="wallet_cancelled", error_stage=error_stage,
                         retryable=False, status="cancelled")


class WalletTimedOutError(WalletProviderError):
    def __init__(self, message: str = "wallet flow timed out", *, error_stage: str = "wallet_provider") -> None:
        super().__init__(message, error_code="wallet_timed_out", error_stage=error_stage,
                         retryable=True, status="timed_out")


class WalletUnknownResultError(WalletProviderError):
    def __init__(self, message: str, *, error_stage: str) -> None:
        super().__init__(message, error_code="wallet_result_unknown", error_stage=error_stage,
                         retryable=False, status="unknown")


@dataclass(frozen=True)
class WalletFlowIdentifiers:
    stripe_js_id: str
    elements_session_id: str
    elements_session_config_id: str
    runtime_version: str = _RUNTIME_VERSION

    @classmethod
    def create(cls, uuid_factory: Callable[[], Any] = uuid.uuid4) -> "WalletFlowIdentifiers":
        return cls(str(uuid_factory()), f"elements_session_{str(uuid_factory()).replace('-', '')[:11]}",
                   str(uuid_factory()))


class WalletProviderTransport(Protocol):
    """wire 边界: 由 wallet_transport.py (chain.py 栈) 实现。"""

    def create_checkout(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def stripe_init(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def create_payment_method(self, request: "WalletTransportRequest") -> Mapping[str, Any] | str: ...
    def confirm_payment(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def approve_checkout(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def poll_payment(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def follow_redirect(self, request: "WalletTransportRequest") -> Mapping[str, Any] | str: ...
    def probe_custom_payment(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def confirm_custom_payment(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def start_custom_payment(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...
    def qris_charge(self, request: "WalletTransportRequest") -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class WalletTransportRequest:
    stage: str
    method: str
    flow_id: str
    attempt: int = 1
    checkout_session_id: str = ""
    processor_entity: str = ""
    access_token: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)
    publishable_key: str = field(default="", repr=False)
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)
    redirect_url: str = field(default="", repr=False)


def wallet_method_spec(payment_method: Any) -> WalletMethodSpec:
    key = str(payment_method or "").strip().lower().replace("-", "").replace("_", "")
    spec = WALLET_SPECS.get(key)
    if spec is None:
        raise WalletProviderError(f"unsupported wallet payment method: {payment_method}",
                                  error_code="wallet_method_unsupported", error_stage="validation")
    return spec


def capability_result(spec: WalletMethodSpec, init_payload: Any) -> dict[str, Any]:
    """init 能力判定: 渠道是否在 payment_method_types/custom_payment_methods 中。"""
    if not isinstance(init_payload, Mapping):
        return {"classification": "unknown", "conclusive": False, "supported": None,
                "amount_minor": None, "currency": spec.currency,
                "payment_method_types": [], "custom_payment_methods": []}
    methods = [str(m).lower() for m in (init_payload.get("payment_method_types") or [])]
    customs = init_payload.get("custom_payment_methods") or []
    custom_ids = []
    for item in customs if isinstance(customs, list) else []:
        if isinstance(item, Mapping):
            custom_ids.append(str(item.get("custom_payment_method_type_id") or item.get("id") or ""))
    invoice = init_payload.get("invoice") if isinstance(init_payload.get("invoice"), Mapping) else {}
    amount = invoice.get("amount_due")
    currency = str(init_payload.get("currency") or spec.currency)
    supported = None
    if spec.mode == "custom_pm":
        cpmt = str(spec.stripe_type or "")  # placeholder
        supported = bool(custom_ids) if custom_ids else None
    else:
        supported = spec.stripe_type in methods if methods else None
    return {
        "classification": "supported" if supported is True else ("unsupported" if supported is False else "unknown"),
        "conclusive": supported is not None,
        "supported": supported,
        "amount_minor": amount,
        "currency": currency,
        "payment_method_types": methods,
        "custom_payment_methods": custom_ids,
    }


def run_wallet_provider(
    payment_method: Any,
    access_token: str,
    transport: WalletProviderTransport,
    *,
    session_token: str = "",
    probe_only: bool = False,
    stripe_publishable_key: str = "",
    require_zero: bool = False,
    entry_point: str = "all_plans_pricing_modal",
    max_approve_attempts: int = 6,
    max_poll_attempts: int = 25,
    poll_interval_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    uuid_factory: Callable[[], Any] = uuid.uuid4,
) -> dict[str, Any]:
    """probe 或执行钱包跳转流程 (checkout -> init -> PM/custom -> confirm -> approve -> poll -> follow/charge)。"""
    stage = "validation"
    spec = None
    capability = None
    flow_id = str(uuid_factory())
    try:
        spec = wallet_method_spec(payment_method)
        token = str(access_token or "").strip()
        if not token:
            raise WalletProviderError("access_token is required", error_code="wallet_access_token_missing",
                                      error_stage="validation")
        ids = WalletFlowIdentifiers.create(uuid_factory)

        def req(stage_name: str, payload: Mapping[str, Any], **kw: Any) -> WalletTransportRequest:
            return WalletTransportRequest(stage=stage_name, method=spec.key, flow_id=flow_id,
                                          access_token=token, session_token=session_token,
                                          checkout_session_id=kw.get("cs", ""),
                                          processor_entity=kw.get("entity", ""),
                                          publishable_key=kw.get("pk", ""),
                                          payload=payload, redirect_url=kw.get("redirect_url", ""),
                                          attempt=kw.get("attempt", 1))

        # S1 checkout
        stage = "checkout"
        co = transport.create_checkout(req(stage, {
            "entry_point": entry_point,
            "plan_name": "chatgptplusplan",
            "billing_details": {"country": spec.country, "currency": spec.currency},
            "checkout_ui_mode": "hosted",
        }))
        cs = str(co.get("checkout_session_id") or co.get("id") or "")
        entity = str(co.get("processor_entity") or ("openai_llc" if spec.country == "US" else "openai_ie"))
        pk = str(co.get("publishable_key") or stripe_publishable_key)
        if not (cs.startswith("cs_") or cs.startswith("oaics_")):
            raise WalletProviderError("checkout response missing checkout_session_id",
                                      error_code="wallet_checkout_bad_response", error_stage=stage, retryable=True)

        # S2 init + capability
        stage = "stripe_init"
        init_payload = transport.stripe_init(req(stage, {
            "browser_locale": spec.locale, "browser_timezone": _timezone(spec.country),
            "stripe_js_id": ids.stripe_js_id, "key": pk,
        }, cs=cs, entity=entity, pk=pk))
        capability = capability_result(spec, init_payload)
        base = {"payment_method": spec.key, "capability": capability, "retryable": False, "error_stage": ""}
        if probe_only:
            return {**base, "ok": True, "status": "probe_complete", "operation": "probe",
                    "probe_only": True, "url": "", "checkout_session_id": cs[:24]}
        if capability["supported"] is False:
            raise WalletProviderError(f"Stripe init does not offer {spec.label}",
                                      error_code="wallet_method_unavailable", error_stage=stage)
        if capability["supported"] is None:
            raise WalletProviderError(f"Stripe init did not provide conclusive {spec.label} capability evidence",
                                      error_code="wallet_capability_unknown", error_stage=stage,
                                      retryable=False, status="unknown")
        if require_zero and capability["amount_minor"] is not None and capability["amount_minor"] != 0:
            raise WalletProviderError(f"wallet checkout is not zero due: amount={capability['amount_minor']} "
                                      f"currency={capability['currency']}",
                                      error_code="wallet_checkout_not_zero_due", error_stage=stage)

        # S3 payment method (stripe_pm) 或 custom PM (gcash)
        stage = "payment_method"
        pm_id = ""
        if spec.mode == "custom_pm":
            probe_res = transport.probe_custom_payment(req(stage, {}, cs=cs, entity=entity, pk=pk))
            _raise_for_terminal_payload(probe_res, stage)
            pm_id = str(probe_res.get("payment_method") or probe_res.get("payment_method_id") or "")
        else:
            pm_res = transport.create_payment_method(req(stage, {
                "type": spec.stripe_type, "billing_country": spec.country, "key": pk,
            }, cs=cs, entity=entity, pk=pk))
            pm_id = pm_res if isinstance(pm_res, str) else str(pm_res.get("id") or "")
            if not pm_id.startswith("pm_"):
                raise WalletProviderError("payment method response did not contain a payment method id",
                                          error_code="wallet_payment_method_bad_response",
                                          error_stage=stage, retryable=True)

        # S4 confirm
        stage = "confirm"
        if spec.mode == "custom_pm":
            confirm_res = transport.confirm_custom_payment(req(stage, {
                "payment_method": pm_id, "init_checksum": str(init_payload.get("init_checksum") or ""),
            }, cs=cs, entity=entity, pk=pk))
        else:
            confirm_res = transport.confirm_payment(req(stage, {
                "payment_method": pm_id, "init_checksum": str(init_payload.get("init_checksum") or ""),
                "expected_amount": str(capability["amount_minor"] if capability["amount_minor"] is not None else 0),
                "expected_payment_method_type": spec.stripe_type,
            }, cs=cs, entity=entity, pk=pk))
        _raise_for_terminal_payload(confirm_res, stage)
        redirect_url = _extract_redirect_url(confirm_res)

        # S5 approve
        stage = "approve"
        approved = False
        for attempt in range(1, max(1, max_approve_attempts) + 1):
            ap = transport.approve_checkout(req(stage, {"checkout_session_id": cs, "processor_entity": entity},
                                                 cs=cs, entity=entity, pk=pk, attempt=attempt))
            state = _response_state(ap, prefer_result=True)
            if state == "approved":
                approved = True
                redirect_url = redirect_url or _extract_redirect_url(ap)
                break
            _raise_for_terminal_payload(ap, stage)
            if attempt < max_approve_attempts:
                sleep(max(0.0, poll_interval_seconds))
        if not approved:
            raise WalletUnknownResultError("ChatGPT checkout approval did not become approved", error_stage=stage)

        # S6 poll (无 redirect 时)
        stage = "poll"
        for attempt in range(1, max(1, max_poll_attempts) + 1):
            if redirect_url:
                break
            poll_res = transport.poll_payment(req(stage, {"key": pk}, cs=cs, entity=entity, pk=pk, attempt=attempt))
            _raise_for_terminal_payload(poll_res, stage)
            redirect_url = _extract_redirect_url(poll_res)
            if not redirect_url and attempt < max_poll_attempts:
                sleep(max(0.0, poll_interval_seconds))
        if not redirect_url:
            raise WalletUnknownResultError("wallet redirect did not materialize before polling ended", error_stage=stage)

        # S7 follow redirect (+ qris charge)
        stage = "follow_redirect"
        follow_res = transport.follow_redirect(req(stage, {}, cs=cs, entity=entity, pk=pk,
                                                    redirect_url=redirect_url))
        provider_url = _followed_url(follow_res) or redirect_url
        artifacts = dict(follow_res.get("artifacts") or {}) if isinstance(follow_res, Mapping) else {}
        if spec.mode == "midtrans_qr":
            stage = "charge"
            charge_res = transport.qris_charge(req(stage, {"redirect_url": redirect_url}, cs=cs,
                                                   entity=entity, pk=pk, redirect_url=redirect_url))
            if isinstance(charge_res, Mapping):
                provider_url = str(charge_res.get("url") or provider_url)
                artifacts.update(charge_res.get("artifacts") or {})
        if not _is_provider_url(provider_url, spec):
            raise WalletUnknownResultError(
                f"redirect chain did not resolve to a recognized {spec.label} provider host", error_stage=stage)

        return {**base, "ok": True, "status": "completed", "operation": "extract_link",
                "probe_only": False, "url": provider_url, "provider_redirect_url": provider_url,
                "link_type": f"{spec.key}_protocol", "checkout_session_id": cs[:24],
                "artifacts": artifacts}
    except asyncio.CancelledError:
        return _failure_result(spec, WalletCancelledError(error_stage=stage), capability=capability)
    except concurrent.futures.CancelledError:
        return _failure_result(spec, WalletCancelledError(error_stage=stage), capability=capability)
    except Exception as exc:
        return _failure_result(spec, _structured_error(exc, stage), capability=capability)


# =============================================================================
# 内部辅助
# =============================================================================

def _redact(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b", "[REDACTED_JWT]", text)
    text = re.sub(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9_]{6,}\b", "[REDACTED_STRIPE_KEY]", text)
    return text


def _timezone(country: str) -> str:
    return {"ID": "Asia/Jakarta", "PH": "Asia/Manila", "NL": "Europe/Amsterdam",
            "CH": "Europe/Zurich", "PL": "Europe/Warsaw"}.get(country, "UTC")


def _response_state(payload: Any, *, prefer_result: bool = False) -> str:
    if not isinstance(payload, Mapping):
        return ""
    keys = ("result", "state", "status") if prefer_result else ("state", "status", "result")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _raise_for_terminal_payload(payload: Any, stage: str) -> None:
    state = _response_state(payload)
    if state in _TERMINAL_CANCEL_STATES:
        raise WalletCancelledError(error_stage=stage)
    if state in _TERMINAL_FAILURE_STATES:
        raise WalletProviderError(f"wallet provider returned terminal state: {state}",
                                  error_code="wallet_provider_rejected", error_stage=stage)
    if isinstance(payload, Mapping) and payload.get("error"):
        error = payload.get("error")
        message = error.get("message") if isinstance(error, Mapping) else error
        raise WalletProviderError(f"wallet provider error: {message or 'unknown error'}",
                                  error_code="wallet_provider_response_error", error_stage=stage,
                                  retryable=stage in _SIDE_EFFECT_STAGES,
                                  status="unknown" if stage in _SIDE_EFFECT_STAGES else "failed")


def _extract_redirect_url(payload: Any, *, depth: int = 0) -> str:
    if depth > 10:
        return ""
    if isinstance(payload, str):
        return payload.strip() if _is_http_url(payload) else ""
    if isinstance(payload, Mapping):
        for key in ("provider_redirect_url", "final_url", "redirect_url", "url"):
            value = payload.get(key)
            if isinstance(value, str) and _is_http_url(value):
                return value.strip()
        for value in payload.values():
            found = _extract_redirect_url(value, depth=depth + 1)
            if found:
                return found
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found = _extract_redirect_url(value, depth=depth + 1)
            if found:
                return found
    return ""


def _followed_url(response: Mapping[str, Any] | str) -> str:
    return _extract_redirect_url(response)


def _is_http_url(value: Any) -> bool:
    try:
        parsed = urlsplit(str(value or "").strip())
    except Exception:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.hostname)


def _is_provider_url(value: str, spec: WalletMethodSpec) -> bool:
    if not _is_http_url(value):
        return False
    host = str(urlsplit(value).hostname or "").lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in spec.redirect_hosts)


def _structured_error(exc: Exception, stage: str) -> WalletProviderError:
    if isinstance(exc, WalletProviderError):
        return exc
    if isinstance(exc, TimeoutError) and stage in _SIDE_EFFECT_STAGES:
        return WalletUnknownResultError(str(exc) or "wallet transport timed out after a side effect",
                                        error_stage=stage)
    if isinstance(exc, TimeoutError):
        return WalletTimedOutError(str(exc) or "wallet transport timed out", error_stage=stage)
    status = _exception_status_code(exc)
    retryable = status == 429 or status >= 500 if status else not isinstance(exc, (TypeError, ValueError))
    uncertain = stage in _SIDE_EFFECT_STAGES and retryable
    return WalletProviderError(str(exc) or exc.__class__.__name__,
                               error_code="wallet_transport_error", error_stage=stage,
                               retryable=False if uncertain else retryable,
                               status="unknown" if uncertain else "failed")


def _exception_status_code(exc: Exception) -> int:
    for value in (getattr(exc, "status_code", None), getattr(getattr(exc, "response", None), "status_code", None)):
        try:
            status = int(value)
        except (TypeError, ValueError):
            continue
        if 100 <= status <= 599:
            return status
    return 0


def _failure_result(spec: WalletMethodSpec | None, error: WalletProviderError,
                    *, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "ok": False, "status": error.status,
        "payment_method": spec.key if spec else "",
        "url": "", "error": str(error), "error_code": error.error_code,
        "retryable": error.retryable, "error_stage": error.error_stage,
    }
    if capability is not None:
        result["capability"] = dict(capability)
    if error.status == "unknown":
        result["retryable"] = False
        result["requires_reconciliation"] = True
    return result


__all__ = [
    "GCASH_CUSTOM_PM_TYPE_ID", "WALLET_SPECS", "WalletCancelledError", "WalletFlowIdentifiers",
    "WalletMethodSpec", "WalletProviderError", "WalletProviderTransport", "WalletTimedOutError",
    "WalletTransportRequest", "WalletUnknownResultError", "capability_result",
    "run_wallet_provider", "wallet_method_spec",
]
