"""Main PayPal Billing Agreement approval flow orchestrator.

Implements the complete protocol:
  Phase 0: DataDome verification + initial page load
  Phase 2: Create account (email submission → signup page)
  Phase 3: Fill signup form + submit (triggers 2FA SMS)
  Phase 4: OTP verification + final authorize mutation
"""
import re
import time
import json
import os
import random
import string
import html as html_lib
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast
from uuid import uuid4
from loguru import logger

from paypal.models import (
    SessionState,
    UserInfo,
    CardInfo,
    BillingAddress,
    generate_user,
    generate_card,
    generate_random_email,
)
from paypal.session import (
    CAPTCHA_SOLVED_CFCI,
    CAPTCHA_FRONTEND_DISABLE_MODE,
    CAPTCHA_MANUAL_REQUIRED_MODE,
    PayPalAuthChallenge,
    PayPalSession,
    build_common_headers,
    captcha_frontend_disable_enabled,
    looks_like_paypal_authchallenge,
    paypal_captcha_bypass_mode,
    sanitize_for_log,
    strict_browser_risk_enabled,
)
from paypal.mtr import MTR_RUNTIME_PYTHON_GENERATED, extract_dfp_script_url, extract_mtr_config, ensure_mtr_config, send_mtr_signals
from paypal.proxy import build_proxy_config, ProxyConfig, _load_dotenv_value as _load_proxy_dotenv_value
from paypal.fingerprint import (
    ensure_runtime_profile,
    build_fn_sync_data,
    build_signup_fn_sync_data,
    send_da_bootstrap,
    send_device_fingerprint,
    send_fraudnet_rdt,
    send_identity_di_log,
    send_signup_field_events,
)
from paypal.tealeaf import send_tealeaf_data, TealeafSession
from paypal.analytics import (
    _DD_AUTHCHALLENGE_CONFIG,
    _DD_MODXO_CONFIG,
    _DD_WEASLEY_CONFIG,
    _DD_HAGRID_CONFIG,
    send_xo_logger,
    send_analytics_ts,
    send_observability_emit,
    send_weasley_log,
    send_datadog_rum_view,
    send_datadog_rum_action,
)
from paypal.graphql import (
    BUYER_CONTEXT_QUERY,
    BUYER_FUNDING_CONTEXT_QUERY,
    CHECKOUT_SESSION_DATA_QUERY,
    GRIFFIN_METADATA_QUERY,
    SUPPORTED_FUNDING_SOURCES_QUERY,
    DEFERRED_FEATURE_QUERY,
    INSTALLMENT_OPTIONS_QUERY,
    ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY,
    INITIATE_2FA_PHONE_MUTATION,
    CONFIRM_2FA_PHONE_MUTATION,
    SIGNUP_NEW_MEMBER_MUTATION,
    AUTHORIZE_BILLING_MUTATION,
)
from config import (
    USER_AGENT,
    FINGERPRINT_SOURCE,
    DATADOME_MODE,
    DATADOME_ROXY_WAIT_SECONDS,
    MTR_RUNTIME_MODE,
    RISK_ROXY_WAIT_SECONDS,
    RISK_SIGNALS_MODE,
)


_PHASE1_BROWSER_REQUIRED_SIGNALS = (
    "fraudnet_p1",
    "fraudnet_p2",
    "fraudnet_w",
    "identity_di_log",
    "datadog_rum",
)

_MODXO_STATIC_ACTION_IDS = {
    "fetch_device_fingerprint_action_id": "40119ea45de7135869f32892c6e0436cc9722b7775",
    "show_create_account_action_id": "408cdbfcfb063642520b8dde73b124955e07000967",
    "submit_public_credential_action_id": "403375d290e5845b191b7f22e6b940617e87334e8b",
    "create_user_action_id": "60187d0e8cbc4131987e2c84c8e430dce698c2ace3",
}


class SmsActivationProtocol(Protocol):
    activation_id: str
    phone_number: str
    provider_id: str
    price: float
    expires_at: float
    reused: bool


class SmsOtpProviderProtocol(Protocol):
    max_attempts: int
    wait_seconds: float

    def reserve_number(self) -> SmsActivationProtocol: ...

    def mark_sms_sent(self, activation: SmsActivationProtocol) -> None: ...

    def wait_for_code(self, activation: SmsActivationProtocol, timeout_seconds: float | None = None) -> str | None: ...

    def abandon(self, activation: SmsActivationProtocol, reason: str) -> None: ...

    def register_confirmation_result(self, activation: SmsActivationProtocol, confirmed: bool) -> None: ...


class PayPalFlow:
    # Strict buyer-context gate: when enabled (IdentityElevationPayPalFlow),
    # phase 4 refuses to authorize unless the member buyer context was synced.
    _strict_buyer_context = False

    def __init__(
        self,
        ba_token: str,
        user: UserInfo,
        card: CardInfo,
        address: BillingAddress,
        max_card_attempts: int = 5,
        max_flow_attempts: int = 1,
        max_authorize_attempts: int = 3,
        card_retry_delay_seconds: float = 6.0,
        card_retry_jitter_seconds: float = 2.0,
        proxy_enabled: bool | None = None,
        proxy_index: int | None = None,
        proxy_config: ProxyConfig | None = None,
        fingerprint_source: str | None = None,
        datadome_mode: str | None = None,
        mtr_runtime: str | None = None,
        risk_signals_mode: str | None = None,
        sms_provider: SmsOtpProviderProtocol | None = None,
        country_context: Any | None = None,
        progress_cb: Any | None = None,
    ):
        self.ba_token = ba_token
        self.user = user
        if not self.user.email:
            self.user.email = generate_random_email()
        self.card = card
        self.address = address
        self.country_context = country_context
        self.progress_cb = progress_cb
        self._used_card_bins: set[str] = set()
        if self.card.bin:
            self._used_card_bins.add(self.card.bin)
        self.max_card_attempts = max(1, max_card_attempts)
        self.max_flow_attempts = max(1, max_flow_attempts)
        self.max_authorize_attempts = max(1, max_authorize_attempts)
        self.card_retry_delay_seconds = max(0.0, float(card_retry_delay_seconds))
        self.card_retry_jitter_seconds = max(0.0, float(card_retry_jitter_seconds))
        self.proxy_config: ProxyConfig = proxy_config or build_proxy_config(
            enabled=proxy_enabled,
            index=proxy_index,
        )
        self.fingerprint_source = fingerprint_source
        self.datadome_mode = datadome_mode
        self.mtr_runtime = mtr_runtime
        self.risk_signals_mode = risk_signals_mode
        self.sms_provider = sms_provider
        self._requested_risk_signals_mode = self._risk_signals_mode_raw()
        self._roxy_runtime_disabled_reason = ""
        keep_roxy_browser = self._roxy_runtime_requested()
        self.state = SessionState(ba_token=ba_token)
        ensure_runtime_profile(
            self.state,
            source=self.fingerprint_source,
            roxy_proxy_url=self.proxy_config.url or "",
            keep_roxy_browser=keep_roxy_browser,
        )
        if self.country_context is not None:
            try:
                from paypal.country_profile import apply_profile_overrides

                self.state.browser_profile = apply_profile_overrides(
                    dict(self.state.browser_profile or {}),
                    self.country_context,
                )
            except Exception:
                pass
        self.session = PayPalSession(
            self.state,
            proxy_url=self.proxy_config.url,
            proxy_label=self.proxy_config.label,
        )
        self.captcha_bypass_mode = paypal_captcha_bypass_mode()
        self._used_partial_signup_token = False
        self._billing_address_autocomplete_succeeded = False
        self._roxy_skipped_telemetry_families: set[str] = set()
        self._signup_billing_address_prepared = False
        self._headless_session: Any | None = None
        self._headless_optimized_session: Any | None = None
        self._datadome_browser_document: dict[str, Any] = {}

        if keep_roxy_browser and self._fingerprint_runtime_requested_roxy():
            profile_source = str(
                (self.state.browser_profile or {}).get("fingerprint_source")
                or getattr(self.state, "fingerprint_source", "")
                or ""
            ).lower()
            roxy_browser = getattr(self.state, "roxy_browser", None) or {}
            if profile_source != "roxy" and not roxy_browser.get("cdp_info"):
                self._disable_roxy_runtime(
                    "Roxy fingerprint fell back to program random; Roxy Local API/runtime is unavailable."
                )

    def _emit_progress(self, step: str, detail: str = "", *, level: str = "info") -> None:
        """向外部 (API 层) 推送流程进度: progress_cb(step, detail, level)。

        回调异常绝不中断主流程; detail 过长截断。
        """
        cb = getattr(self, "progress_cb", None)
        if cb is None:
            return
        try:
            cb(str(step or ""), str(detail or "")[:400], str(level or "info"))
        except Exception:
            pass

    @staticmethod
    def _raw_mode_value(explicit: str | None, env_names: tuple[str, ...], default: object) -> str:
        for value in (explicit, *(_load_proxy_dotenv_value(name) for name in env_names), str(default or "")):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _mode_requests_roxy(raw: str) -> bool:
        return (raw or "").strip().lower().replace("-", "_") in {
            "roxy",
            "browser",
            "real_browser",
            "chrome",
            "chromium",
            "auto",
            "prefer_roxy",
            "roxy_auto",
        }

    @staticmethod
    def _roxy_runtime_fallback_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_ROXY_RUNTIME_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_ROXY_FALLBACK")
            or "1"
        ).strip().lower()
        return raw not in {"0", "false", "no", "off", "strict", "disabled", "disable"}

    @staticmethod
    def _safe_error_text(error: object) -> str:
        return str(sanitize_for_log({"error": str(error or "")})["error"])

    def _fingerprint_runtime_requested_roxy(self) -> bool:
        raw = self._raw_mode_value(
            self.fingerprint_source,
            ("PAYPAL_FINGERPRINT_SOURCE", "FINGERPRINT_SOURCE"),
            "",
        )
        return self._mode_requests_roxy(raw)

    def _disable_roxy_runtime(self, reason: object) -> None:
        """Stop retrying Roxy during this flow and fall back to protocol paths."""
        if not self._roxy_runtime_fallback_enabled():
            return
        reason_text = self._safe_error_text(reason or "Roxy runtime unavailable")
        if not self._roxy_runtime_disabled_reason:
            logger.warning(
                "Roxy runtime unavailable; falling back to protocol/python runtime for this job: {}",
                reason_text,
            )
        else:
            logger.debug("Roxy runtime remains disabled for this job: {}", reason_text)
        self._roxy_runtime_disabled_reason = reason_text
        try:
            setattr(self.state, "roxy_runtime_disabled_reason", reason_text)
        except Exception:
            pass
        if self._datadome_mode_raw() == "roxy":
            self.datadome_mode = "protocol"
        if self._risk_signals_mode_raw() == "roxy":
            self.risk_signals_mode = "protocol"
        if self._mtr_runtime_raw() == "roxy":
            self.mtr_runtime = "python_generated"

    def _roxy_runtime_requested(self) -> bool:
        fingerprint_source = self._raw_mode_value(
            self.fingerprint_source,
            ("PAYPAL_FINGERPRINT_SOURCE", "FINGERPRINT_SOURCE"),
            FINGERPRINT_SOURCE,
        )
        datadome_mode = self._raw_mode_value(
            self.datadome_mode,
            ("PAYPAL_DATADOME_MODE", "DATADOME_MODE"),
            DATADOME_MODE,
        )
        mtr_runtime = self._raw_mode_value(
            self.mtr_runtime,
            ("PAYPAL_MTR_RUNTIME", "MTR_RUNTIME"),
            MTR_RUNTIME_MODE,
        )
        risk_mode = self._raw_mode_value(
            self.risk_signals_mode,
            ("PAYPAL_RISK_SIGNALS_MODE", "RISK_SIGNALS_MODE"),
            RISK_SIGNALS_MODE,
        )
        return any(
            self._mode_requests_roxy(value)
            for value in (fingerprint_source, datadome_mode, mtr_runtime, risk_mode)
        )

    def close(self):
        self._cleanup_headless_session()
        self._cleanup_roxy_browser()
        self.session.close()

    def _browser_headers(self, *, accept: str = "*/*", content_type: str | None = None) -> dict[str, str]:
        headers = build_common_headers(self.state)
        headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _profile_user_agent(self) -> str:
        return str((self.state.browser_profile or {}).get("user_agent") or USER_AGENT)

    def _profile_country(self) -> str:
        return str((self.state.browser_profile or {}).get("country") or self.address.country or "BR")

    def _context_country(self) -> str:
        """授权段国家上下文 (唯一事实源); 无上下文时回退 address/BR。"""
        if self.country_context is not None:
            return str(getattr(self.country_context, "country", "") or "").upper()
        return str(self.address.country or "BR").upper()

    def _context_lang(self) -> str:
        if self.country_context is not None:
            return str(getattr(self.country_context, "lang", "") or "pt").lower()
        return "pt"

    def _context_kyc_fields(self) -> set[str]:
        if self.country_context is not None:
            return {str(f) for f in (getattr(self.country_context, "kyc_fields", None) or [])}
        return set()

    def _profile_locale(self) -> str:
        return str((self.state.browser_profile or {}).get("locale") or "pt_BR")

    def _profile_lang(self) -> str:
        locale = self._profile_locale()
        return str((self.state.browser_profile or {}).get("language") or locale.replace("_", "-") or "pt-BR")

    def _content_country(self) -> str:
        # SignUpNewMember sends `country` from the billing/account country.
        # The compliance identifier in the browser capture follows that same
        # country (for BR it sends BR:pt:<manifest hash>:compliance.signupTerms),
        # even when geolocation/proxy fields in __INITIAL_DATA__ differ.
        return str(self.address.country or self._profile_country() or "BR").upper()

    def _content_lang(self) -> str:
        locale = self._profile_locale()
        country = self._content_country()
        for sep in ("_", "-"):
            if sep in locale:
                language, locale_country = locale.split(sep, 1)
                if locale_country.upper() == country:
                    return (language or "pt").lower()
                break
        if country == "BR":
            return "pt"
        if "_" in locale:
            return locale.split("_", 1)[0].lower()
        if "-" in locale:
            return locale.split("-", 1)[0].lower()
        return (locale or "pt").lower()

    def _short_content_identifier(self) -> str:
        return f"{self._content_country()}:{self._content_lang()}:compliance.signupTerms"

    @staticmethod
    def _is_short_content_identifier_value(value: str) -> bool:
        """True when contentIdentifier has no deployment content hash."""
        return bool(
            re.fullmatch(
                r"[A-Z]{2}:[a-z]{2}:compliance\.signupTerms",
                (value or "").strip(),
                re.I,
            )
        )

    @staticmethod
    def _content_identifier_hash(value: str) -> str:
        """Return the hash embedded in a full signupTerms contentIdentifier."""
        match = re.fullmatch(
            r"[A-Z]{2}:[a-z]{2}:([A-Za-z0-9_-]{8,128}):compliance\.signupTerms",
            (value or "").strip(),
            re.I,
        )
        return match.group(1) if match else ""

    def _content_identifier_from_hash(self, content_hash: str | None = None) -> str:
        content_hash = (content_hash or self.state.content_hash or "").strip()
        if content_hash:
            return (
                f"{self._content_country()}:{self._content_lang()}:"
                f"{content_hash}:compliance.signupTerms"
            )
        return self._short_content_identifier()

    def _resolved_content_identifier(self) -> str:
        """Prefer a full contentIdentifier and synthesize one from contentHash."""
        current = (self.state.content_identifier or "").strip()
        if current and not self._is_short_content_identifier_value(current):
            embedded_hash = self._content_identifier_hash(current)
            if embedded_hash and not self.state.content_hash:
                self.state.content_hash = embedded_hash
            return current
        if self.state.content_hash:
            resolved = self._content_identifier_from_hash()
            self.state.content_identifier = resolved
            return resolved
        return current or self._short_content_identifier()

    def _log_profile_consistency(self) -> None:
        profile = self.state.browser_profile or {}
        screen = self.state.screen or {}
        viewport = self.state.viewport or {}
        logger.info(
            "Browser profile: source={} country={} locale={} language={} timezone={} offset={} ua={} screen={}x{} viewport={}x{} cmid={}",
            profile.get("fingerprint_source") or getattr(self.state, "fingerprint_source", "") or "random",
            profile.get("country"),
            profile.get("locale"),
            profile.get("language"),
            profile.get("timezone"),
            profile.get("timezone_offset_minutes"),
            str(profile.get("user_agent") or USER_AGENT)[:80],
            screen.get("width"),
            screen.get("height"),
            viewport.get("width"),
            viewport.get("height"),
            sanitize_for_log({"token": self.state.paypal_client_metadata_id})["token"],
        )

    @staticmethod
    def _extract_datadome_clientid(html: str) -> str:
        if "datadome" not in (html or "").lower():
            return ""
        # PayPal's DataDome bootstrap stores the initial client id in a local
        # `c` variable, then replaces it from the challenge iframe on
        # eventType=passed.  Capture that value so PayPalSession can mirror the
        # browser's x-datadome-clientid request hook until the cookie exists.
        for pattern in (
            r"\bvar\s+c\s*=\s*['\"]([^'\"]{40,})['\"]",
            r"\bc\s*=\s*['\"]([^'\"]{40,})['\"][^<]{0,400}datadome",
        ):
            m = re.search(pattern, html or "", re.I | re.S)
            if m:
                return html_lib.unescape(m.group(1))
        return ""

    def _capture_datadome_clientid(self, html: str) -> None:
        if self._datadome_mode() == "off":
            return
        client_id = self._extract_datadome_clientid(html)
        if client_id and client_id != getattr(self.state, "datadome_clientid", ""):
            self.state.datadome_clientid = client_id
            logger.info("DataDome client id captured for request header replay len={}", len(client_id))

    def _datadome_mode_raw(self) -> str:
        raw = (
            self.datadome_mode
            or _load_proxy_dotenv_value("PAYPAL_DATADOME_MODE")
            or _load_proxy_dotenv_value("DATADOME_MODE")
            or str(DATADOME_MODE or "")
        ).strip().lower().replace("-", "_")
        aliases = {
            "": "protocol",
            "protocol": "protocol",
            "edge": "protocol",
            "header": "protocol",
            "headers": "protocol",
            "clientid": "protocol",
            "roxy": "roxy",
            "browser": "roxy",
            "real_browser": "roxy",
            "headless": "headless",
            "headless_optimized": "headless",
            "optimized_headless": "headless",
            "local_headless": "headless",
            "playwright": "headless",
            "local_playwright": "headless",
            "prefer_headless": "headless",
            "auto": "auto",
            "off": "off",
            "none": "off",
            "disabled": "off",
            "disable": "off",
            "0": "off",
        }
        return aliases.get(raw, "protocol")

    def _datadome_mode(self) -> str:
        mode = self._datadome_mode_raw()
        if mode in {"roxy", "auto"} and self._roxy_runtime_disabled_reason:
            return "protocol"
        return mode

    @staticmethod
    def _headless_runtime_fallback_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_HEADLESS_RUNTIME_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_LOCAL_HEADLESS_FALLBACK")
            or "1"
        ).strip().lower()
        return raw not in {"0", "false", "no", "off", "strict", "disabled", "disable"}

    @staticmethod
    def _datadome_phase0_preflight_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_DATADOME_PHASE0_PREFLIGHT")
            or _load_proxy_dotenv_value("PAYPAL_DATADOME_PREFLIGHT")
            or _load_proxy_dotenv_value("PAYPAL_HEADLESS_DATADOME_PREFLIGHT")
            or ""
        ).strip().lower()
        return raw in {"1", "true", "yes", "on", "enable", "enabled"}

    @staticmethod
    def _headless_datadome_roxy_fallback_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_HEADLESS_DATADOME_ROXY_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_LOCAL_HEADLESS_DATADOME_ROXY_FALLBACK")
            or "0"
        ).strip().lower()
        if raw in {"0", "false", "no", "off", "strict", "disabled", "disable"}:
            return False
        try:
            from paypal.roxy_fingerprint import configured_roxy_api_key

            return bool(configured_roxy_api_key())
        except Exception:
            return False

    @staticmethod
    def _headless_signup_context_roxy_fallback_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_HEADLESS_SIGNUP_CONTEXT_ROXY_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_LOCAL_HEADLESS_SIGNUP_CONTEXT_ROXY_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_HEADLESS_DATADOME_ROXY_FALLBACK")
            or _load_proxy_dotenv_value("PAYPAL_LOCAL_HEADLESS_DATADOME_ROXY_FALLBACK")
            or "auto"
        ).strip().lower()
        if raw in {"0", "false", "no", "off", "strict", "disabled", "disable"}:
            return False
        try:
            from paypal.roxy_fingerprint import configured_roxy_api_key

            return bool(configured_roxy_api_key())
        except Exception:
            return False

    @staticmethod
    def _datadome_roxy_wait_seconds() -> float:
        raw = _load_proxy_dotenv_value("PAYPAL_DATADOME_ROXY_WAIT_SECONDS")
        if raw:
            try:
                return max(2.0, min(float(raw), 60.0))
            except ValueError:
                pass
        return max(2.0, min(float(DATADOME_ROXY_WAIT_SECONDS), 60.0))

    @staticmethod
    def _datadome_headless_wait_seconds() -> float:
        raw = _load_proxy_dotenv_value("PAYPAL_DATADOME_HEADLESS_WAIT_SECONDS")
        if raw:
            try:
                return max(2.0, min(float(raw), 60.0))
            except ValueError:
                pass
        return PayPalFlow._datadome_roxy_wait_seconds()

    def _ensure_roxy_browser_for_datadome(self) -> dict[str, object]:
        roxy_browser = getattr(self.state, "roxy_browser", None) or {}
        from paypal.roxy_fingerprint import (
            capture_roxy_runtime_profile,
            close_roxy_browser,
            roxy_browser_matches_proxy,
        )

        if roxy_browser.get("cdp_info") and roxy_browser_matches_proxy(roxy_browser, self.proxy_config.url):
            return roxy_browser
        if roxy_browser.get("cdp_info"):
            logger.info("Existing Roxy browser proxy does not match current flow proxy; reopening with current proxy.")
            try:
                close_roxy_browser(roxy_browser, delete=True)
            except Exception as exc:
                logger.debug("Roxy mismatched-proxy browser cleanup failed: {}", exc)
            self.state.roxy_browser = {}

        logger.info("Opening Roxy browser for DataDome runtime with proxy: {}", self.proxy_config.label)
        runtime = capture_roxy_runtime_profile(
            keep_browser=True,
            proxy_url=self.proxy_config.url or "",
        )
        if not self.state.browser_profile:
            self.state.browser_profile = runtime.get("browser_profile", {})
            self.state.screen = runtime.get("screen", {})
            self.state.viewport = runtime.get("viewport", {})
            self.state.device_fingerprint = runtime.get("device_fingerprint", {})
        self.state.roxy_browser = runtime.get("roxy_browser", {})
        return self.state.roxy_browser

    @staticmethod
    def _browser_document_from_datadome_result(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            return {}
        html = str(result.get("html") or "")
        if not html:
            return {}
        try:
            status = int(result.get("status") or 0)
        except Exception:
            status = 0
        if status >= 400:
            return {}
        if looks_like_paypal_authchallenge(html):
            return {}
        return {
            "status_code": status or 200,
            "url": str(result.get("url") or ""),
            "text": html,
        }

    @staticmethod
    def _response_from_browser_document(document: dict[str, Any]) -> Any | None:
        html = str(document.get("text") or "")
        if not html:
            return None
        return SimpleNamespace(
            status_code=int(document.get("status_code") or 200),
            text=html,
            url=str(document.get("url") or ""),
            headers={},
            content=html.encode("utf-8", "ignore"),
        )

    @staticmethod
    def _signup_context_seed_html_looks_usable(html: str) -> bool:
        lower = (html or "").lower()
        if not lower.lstrip().startswith("<"):
            return False
        if not any(marker in lower for marker in ("checkoutweb/signup", "weasley", "signupnewmember", "compliance.signupterms")):
            return False
        active_datadome_block_markers = (
            "device_check_redirect_to_slider",
            "block_page_loaded",
            "datadome captcha",
            "ddc-captcha",
            "edge_bot_protection",
        )
        return not any(marker in lower for marker in active_datadome_block_markers)

    def _apply_datadome_browser_result(self, result: dict[str, Any], *, reason: str, runtime: str) -> bool:
        solved = bool(result.get("ok"))
        document = self._browser_document_from_datadome_result(result)
        if document:
            self._datadome_browser_document = document
        self.state.datadome_browser_result = {
            "ok": solved,
            "runtime": runtime,
            "status": result.get("status"),
            "url": result.get("url"),
            "reason": reason,
            "cookie_count": len(result.get("cookies") or []),
            "datadome_present": bool(result.get("datadome")),
            "clientid_present": bool(result.get("clientid")),
            "blocked_by_datadome": bool(result.get("blocked_by_datadome")),
            "intercept": result.get("intercept") or {},
            "debug_log_path": result.get("debug_log_path") or "",
        }
        if result.get("clientid"):
            self.state.datadome_clientid = str(result["clientid"])
        if solved and result.get("cookies"):
            self.session.import_browser_cookies(result["cookies"])
        if solved and result.get("datadome"):
            self.state.datadome_cookie = str(result["datadome"])
            self.state.datadome_browser_solved = True
            logger.info(
                "DataDome solved through {} browser reason={} cookies={} datadome_len={}",
                runtime,
                reason,
                len(result.get("cookies") or []),
                len(str(result.get("datadome") or "")),
            )
            return True
        logger.warning(
            "{} DataDome run did not clear challenge reason={} status={} url={} datadome_present={} blocked_by_datadome={}",
            runtime,
            reason,
            result.get("status"),
            result.get("url"),
            bool(result.get("datadome")),
            bool(result.get("blocked_by_datadome")),
        )
        return False

    def _solve_datadome_with_roxy_browser(self, url: str, *, reason: str) -> bool:
        mode = self._datadome_mode()
        if mode in {"protocol", "off"}:
            return False
        try:
            if mode == "headless":
                if not self._headless_runtime_enabled():
                    logger.warning("Local headless runtime is disabled; falling back to protocol mode for this job.")
                    self.datadome_mode = "protocol"
                    return False
                headless_session = self._get_headless_session()
                result = headless_session.solve_datadome(
                    url,
                    wait_seconds=self._datadome_headless_wait_seconds(),
                )
                solved = self._apply_datadome_browser_result(result, reason=reason, runtime="headless")
                if not solved and self._headless_datadome_roxy_fallback_enabled():
                    logger.warning(
                        "Local headless DataDome remained challenged; retrying the same check through Roxy browser runtime."
                    )
                    try:
                        from paypal.roxy_fingerprint import solve_datadome_with_roxy

                        roxy_browser = self._ensure_roxy_browser_for_datadome()
                        roxy_result = solve_datadome_with_roxy(
                            roxy_browser,
                            url,
                            cookies=self.session.export_cookies_for_browser(),
                            wait_seconds=self._datadome_roxy_wait_seconds(),
                        )
                        roxy_solved = self._apply_datadome_browser_result(
                            roxy_result,
                            reason=f"{reason}_roxy_fallback",
                            runtime="roxy",
                        )
                        if roxy_solved:
                            return True
                    except Exception as roxy_exc:
                        logger.warning(
                            "Roxy fallback after local headless DataDome challenge failed: {}",
                            self._safe_error_text(roxy_exc),
                        )
                if not solved and self._headless_runtime_fallback_enabled():
                    self.datadome_mode = "protocol"
                    self._cleanup_headless_session()
                    logger.warning(
                        "Local headless DataDome did not clear challenge; falling back to protocol mode for this job."
                    )
                return solved
            from paypal.roxy_fingerprint import solve_datadome_with_roxy

            roxy_browser = self._ensure_roxy_browser_for_datadome()
            result = solve_datadome_with_roxy(
                roxy_browser,
                url,
                cookies=self.session.export_cookies_for_browser(),
                wait_seconds=self._datadome_roxy_wait_seconds(),
            )
            return self._apply_datadome_browser_result(result, reason=reason, runtime="roxy")
        except Exception as exc:
            self.state.datadome_browser_result = {
                "ok": False,
                "runtime": mode,
                "reason": reason,
                "error": str(exc),
            }
            if mode == "headless":
                if not self._headless_runtime_fallback_enabled():
                    raise
                self.datadome_mode = "protocol"
                self._cleanup_headless_session()
                logger.warning("Local headless DataDome failed; falling back to protocol method: {}", exc)
                return False
            if mode == "roxy" and not self._roxy_runtime_fallback_enabled():
                raise
            self._disable_roxy_runtime(exc)
            logger.warning("Roxy DataDome failed; falling back to protocol method: {}", exc)
            return False

    def _mtr_runtime_raw(self) -> str:
        raw = (
            self.mtr_runtime
            or _load_proxy_dotenv_value("PAYPAL_MTR_RUNTIME")
            or _load_proxy_dotenv_value("MTR_RUNTIME")
            or str(MTR_RUNTIME_MODE or "")
        ).strip().lower().replace("-", "_")
        aliases = {
            "": "python_generated",
            "protocol": "python_generated",
            "python": "python_generated",
            "python_generated": "python_generated",
            "synthetic": "python_generated",
            "template": "python_generated",
            "templates": "python_generated",
            "roxy": "roxy",
            "browser": "roxy",
            "real_browser": "roxy",
            "chrome": "roxy",
            "chromium": "roxy",
            "headless": "headless",
            "headless_optimized": "headless",
            "optimized_headless": "headless",
            "local_headless": "headless",
            "playwright": "headless",
            "local_playwright": "headless",
            "prefer_headless": "headless",
            "auto": "auto",
            "prefer_roxy": "auto",
            "off": "off",
            "none": "off",
            "disabled": "off",
            "disable": "off",
            "0": "off",
        }
        return aliases.get(raw, "python_generated")

    def _mtr_runtime_mode(self) -> str:
        mode = self._mtr_runtime_raw()
        if mode in {"roxy", "auto"} and self._roxy_runtime_disabled_reason:
            return "python_generated"
        return mode

    def _risk_signals_mode_raw(self) -> str:
        raw = (
            self.risk_signals_mode
            or _load_proxy_dotenv_value("PAYPAL_RISK_SIGNALS_MODE")
            or _load_proxy_dotenv_value("RISK_SIGNALS_MODE")
            or str(RISK_SIGNALS_MODE or "")
        ).strip().lower().replace("-", "_")
        return self._normalize_risk_signals_mode(raw)

    @staticmethod
    def _normalize_risk_signals_mode(raw: str) -> str:
        raw = (raw or "").strip().lower().replace("-", "_")
        aliases = {
            "": "protocol",
            "protocol": "protocol",
            "python": "protocol",
            "synthetic": "protocol",
            "template": "protocol",
            "templates": "protocol",
            "roxy": "roxy",
            "browser": "roxy",
            "real_browser": "roxy",
            "chrome": "roxy",
            "chromium": "roxy",
            "headless": "headless",
            "headless_optimized": "headless",
            "optimized_headless": "headless",
            "local_headless": "headless",
            "playwright": "headless",
            "local_playwright": "headless",
            "prefer_headless": "headless",
            "auto": "auto",
            "prefer_roxy": "auto",
            "off": "off",
            "none": "off",
            "disabled": "off",
            "disable": "off",
            "0": "off",
        }
        return aliases.get(raw, "protocol")

    def _risk_signals_mode(self) -> str:
        mode = self._risk_signals_mode_raw()
        if mode in {"roxy", "auto"} and self._roxy_runtime_disabled_reason:
            return "protocol"
        return mode

    def _signup_context_risk_mode(self) -> str:
        current_mode = self._risk_signals_mode()
        requested_mode = str(getattr(self, "_requested_risk_signals_mode", "") or "")
        runtime_source = str(getattr(self.state, "risk_signals_runtime_source", "") or "")
        if (
            current_mode in {"roxy", "auto"}
            or requested_mode in {"roxy", "auto"}
            or runtime_source == "roxy"
        ) and not self._roxy_runtime_disabled_reason:
            return "roxy"
        return "headless"

    @staticmethod
    def _risk_roxy_wait_seconds() -> float:
        raw = _load_proxy_dotenv_value("PAYPAL_RISK_ROXY_WAIT_SECONDS")
        if raw:
            try:
                return max(3.0, min(float(raw), 90.0))
            except ValueError:
                pass
        return max(3.0, min(float(RISK_ROXY_WAIT_SECONDS), 90.0))

    @staticmethod
    def _risk_headless_wait_seconds() -> float:
        raw = _load_proxy_dotenv_value("PAYPAL_RISK_HEADLESS_WAIT_SECONDS")
        if raw:
            try:
                return max(3.0, min(float(raw), 90.0))
            except ValueError:
                pass
        return PayPalFlow._risk_roxy_wait_seconds()

    @staticmethod
    def _roxy_datadog_runtime_ready_from_result(result: dict[str, Any]) -> bool:
        runtime = result.get("datadog_runtime") if isinstance(result.get("datadog_runtime"), dict) else {}
        if not isinstance(runtime, dict) or runtime.get("error"):
            return False
        if not (runtime.get("present") or runtime.get("hasDDRumGlobal")):
            return False
        keys = {str(item) for item in (runtime.get("keys") or [])}
        has_operational_api = bool(
            runtime.get("has_add_action")
            or "addAction" in keys
            or "startAction" in keys
            or "startResource" in keys
        )
        has_context_api = bool(
            "getInitConfiguration" in keys
            or "getInternalContext" in keys
            or runtime.get("hasInternalContext")
            or runtime.get("initConfiguration")
        )
        return bool(has_operational_api and has_context_api)

    @staticmethod
    def _phase1_browser_required_missing_from_result(result: dict[str, Any]) -> list[str]:
        raw_counts = result.get("counts")
        counts = cast(dict[str, object], raw_counts) if isinstance(raw_counts, dict) else {}
        raw_observed = result.get("observed")
        observed_items = cast(list[object], raw_observed) if isinstance(raw_observed, list) else []
        observed = {str(item) for item in observed_items if str(item)}
        missing: list[str] = []
        for family in _PHASE1_BROWSER_REQUIRED_SIGNALS:
            raw_count = counts.get(family)
            try:
                count = int(raw_count) if isinstance(raw_count, (str, int, float)) else 0
            except Exception:
                count = 0
            if count <= 0 and family not in observed:
                missing.append(family)
        return missing

    @staticmethod
    def _mark_phase1_browser_required_result(result: dict[str, Any]) -> list[str]:
        missing = PayPalFlow._phase1_browser_required_missing_from_result(result)
        result["required_signals"] = list(_PHASE1_BROWSER_REQUIRED_SIGNALS)
        if missing:
            raw_missing = result.get("missing")
            missing_items = cast(list[object], raw_missing) if isinstance(raw_missing, list) else []
            existing_missing = [str(item) for item in missing_items if str(item)]
            merged_missing = list(dict.fromkeys([*existing_missing, *missing]))
            result["missing"] = merged_missing
            result["required_missing"] = list(missing)
            result["ok"] = False
        else:
            raw_missing = result.get("missing")
            missing_items = cast(list[object], raw_missing) if isinstance(raw_missing, list) else []
            result["missing"] = [
                str(item)
                for item in missing_items
                if str(item) and str(item) not in _PHASE1_BROWSER_REQUIRED_SIGNALS
            ]
            result["required_missing"] = []
        return missing

    def _normalize_phase1_roxy_datadog_runtime_result(self, result: dict[str, Any]) -> bool:
        """Accept loaded DD_RUM runtime when the intake network batch is delayed.

        Some Roxy/Chromium runs load PayPal's full Datadog SDK but do not flush
        ``/api/v2/rum`` before the browser-risk wait times out.  The browser-side runtime is
        still present and operational, so don't fail strict browser-risk checks on that
        transport timing race.
        """
        if bool(result.get("ok")):
            return not self._mark_phase1_browser_required_result(result)
        browser_required_missing = self._phase1_browser_required_missing_from_result(result)
        datadog_is_only_required_gap = browser_required_missing == ["datadog_rum"]
        if not datadog_is_only_required_gap:
            self._mark_phase1_browser_required_result(result)
            return False
        if not self._roxy_datadog_runtime_ready_from_result(result):
            self._mark_phase1_browser_required_result(result)
            return False

        raw_counts = result.get("counts")
        counts = dict(cast(dict[str, object], raw_counts)) if isinstance(raw_counts, dict) else {}
        raw_datadog_count = counts.get("datadog_rum")
        datadog_count = int(raw_datadog_count) if isinstance(raw_datadog_count, (str, int, float)) else 0
        counts["datadog_rum"] = max(1, datadog_count)
        result["counts"] = counts

        raw_observed = result.get("observed")
        observed_items = cast(list[object], raw_observed) if isinstance(raw_observed, list) else []
        observed = [str(item) for item in observed_items if str(item)]
        if "datadog_rum" not in observed:
            observed.append("datadog_rum")
        result["observed"] = observed
        raw_missing = result.get("missing")
        missing_items = cast(list[object], raw_missing) if isinstance(raw_missing, list) else []
        existing_missing = [str(item) for item in missing_items if str(item)]
        result["missing"] = [item for item in existing_missing if item != "datadog_rum"]
        result["required_missing"] = []
        result["datadog_runtime_fulfilled"] = True
        result["datadog_runtime_fulfilled_reason"] = (
            result.get("datadog_runtime_fulfilled_reason")
            or "flow_layer_sdk_loaded_without_intake_capture"
        )
        raw_runtime_signals = result.get("runtime_signals")
        runtime_signals = list(cast(list[object], raw_runtime_signals)) if isinstance(raw_runtime_signals, list) else []
        runtime_signals.append(
            {
                "family": "datadog_rum",
                "source": "DD_RUM_runtime",
                "reason": result["datadog_runtime_fulfilled_reason"],
            }
        )
        result["runtime_signals"] = runtime_signals
        required_missing = self._mark_phase1_browser_required_result(result)
        result["ok"] = not required_missing
        logger.info(
            "Roxy browser-risk Datadog intake request was not captured, but DD_RUM runtime is loaded; accepting datadog_rum runtime signal."
        )
        return not required_missing

    def _headless_mtr_config_for_page(self, page_url: str) -> dict[str, object]:
        ensure_mtr_config(self.state, page_url=page_url)
        if not self.state.mtr_dfp_script_url:
            self.state.mtr_dfp_script_url = "https://www.paypalobjects.com/v15170r-1d3n71ph1c4710n/dfp.js"
        return {
            "channel": self.state.mtr_channel,
            "dfpChannel": self.state.mtr_channel,
            "clientMetadataId": self.state.mtr_client_metadata_id,
            "clientMetaDataId": self.state.mtr_client_metadata_id,
            "apiKey": self.state.mtr_api_key,
            "fppAPIKey": self.state.mtr_api_key,
            "isQa": bool(self.state.mtr_is_qa),
            "isQA": bool(self.state.mtr_is_qa),
        }

    def _apply_headless_mtr_result(self, result: dict[str, Any]) -> None:
        self.state.mtr_runtime_source = "headless"
        self.state.mtr_get_status = int(result.get("x0_status") or 0)
        self.state.mtr_post_status = int(result.get("post_status") or 0)
        self.state.mtr_request_id = str(result.get("requestId") or "")
        self.state.mtr_sealed_result = str(result.get("sealedResult") or "")
        self.state.mtr_visitor_token = str(result.get("visitorToken") or "")
        self.state.mtr_completed = bool(self.state.mtr_request_id and self.state.mtr_sealed_result)
        self.state.mtr_browser_result = {
            "ok": self.state.mtr_completed,
            "runtime": "headless",
            "status": result.get("status"),
            "url": result.get("url"),
            "request_id_present": bool(self.state.mtr_request_id),
            "sealed_result_present": bool(self.state.mtr_sealed_result),
            "visitor_token_present": bool(self.state.mtr_visitor_token),
            "debug_log_path": result.get("debug_log_path") or "",
            "intercept": result.get("intercept") or {},
        }

    def _apply_headless_optimized_mtr_result(self, result: dict[str, Any]) -> None:
        self._apply_headless_mtr_result(result)

    def _store_headless_browser_result(self, result: dict[str, Any], *, browser_ok: bool, runtime: str) -> None:
        raw_cookies = result.get("cookies")
        result_cookies = cast(list[dict[str, Any]], raw_cookies) if isinstance(raw_cookies, list) else []
        if result_cookies:
            self.session.import_browser_cookies(result_cookies)
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        observed = list(cast(list[object], result.get("observed"))) if isinstance(result.get("observed"), list) else []
        missing = list(cast(list[object], result.get("missing"))) if isinstance(result.get("missing"), list) else []
        self.state.risk_signals_runtime_source = runtime
        self.state.risk_signals_browser_result = {
            "ok": browser_ok,
            "runtime": runtime,
            "status": result.get("status"),
            "url": result.get("url"),
            "reason": result.get("reason") or "",
            "observed": observed,
            "observed_order": result.get("observed_order") or [],
            "missing": missing,
            "counts": counts,
            "response_counts": result.get("response_counts") or {},
            "cookie_count": len(result_cookies),
            "requests": result.get("requests") or [],
            "responses": result.get("responses") or [],
            "request_failures": result.get("request_failures") or [],
            "failed_requests": result.get("failed_requests") or result.get("request_failures") or [],
            "injected_scripts": result.get("injected_scripts") or [],
            "inject_errors": result.get("inject_errors") or [],
            "datadog_runtime": result.get("datadog_runtime") or {},
            "datadog_runtime_fulfilled": bool(result.get("datadog_runtime_fulfilled")),
            "datadog_runtime_fulfilled_reason": result.get("datadog_runtime_fulfilled_reason") or "",
            "datadog_probes": result.get("datadog_probes") or [],
            "datadog_flushes": result.get("datadog_flushes") or [],
            "runtime_signals": result.get("runtime_signals") or [],
            "interaction_profile": result.get("interaction_profile") or "",
            "interaction_summary": result.get("interaction_summary") or {},
            "idle_interaction_summary": result.get("idle_interaction_summary") or {},
            "interaction_error": result.get("interaction_error") or "",
            "idle_interaction_error": result.get("idle_interaction_error") or "",
            "required_signals": result.get("required_signals") or [],
            "required_missing": result.get("required_missing") or [],
            "reloads": result.get("reloads") or [],
            "max_reloads": result.get("max_reloads"),
            "blocked_requests": result.get("blocked_requests") or [],
            "allowed_requests": result.get("allowed_requests") or [],
            "learned_rules": result.get("learned_rules") or [],
            "intercept": result.get("intercept") or {},
            "debug_log_path": result.get("debug_log_path") or "",
        }

    @staticmethod
    def _signup_context_headless_result_is_datadome_challenge(result: dict[str, Any]) -> bool:
        reason = str(result.get("reason") or "")
        if reason in {
            "signup_context_datadome_challenge",
            "challenge_required",
            "datadome_missing",
        }:
            return True
        try:
            status = int(result.get("status") or 0)
        except Exception:
            status = 0
        if status in {403, 429}:
            return True
        if bool(result.get("blocked_by_datadome")):
            return True
        url = str(result.get("url") or "").lower()
        if any(marker in url for marker in ("geo.ddc.paypal.com", "/captcha/", "/interstitial/", "authchallenge")):
            return True
        for key in ("signup_context_page_after_runtime", "signup_context_page"):
            raw_page = result.get(key)
            if not isinstance(raw_page, dict):
                continue
            page = cast(dict[str, object], raw_page)
            page_reason = str(page.get("reason") or "")
            if page_reason == "signup_context_datadome_challenge" or bool(page.get("blocked_by_datadome")):
                return True
        return False

    def _send_signup_context_risk_signals_with_roxy(self, signup_url: str, token: str, *, force: bool = False) -> bool:
        mode = self._signup_context_risk_mode()
        if not force and mode not in {"roxy", "auto"} and not self._roxy_risk_runtime_active():
            return False
        try:
            from paypal.roxy_fingerprint import run_phase1_risk_with_roxy_browser

            roxy_browser = self._ensure_roxy_browser_for_datadome()
            seeded_signup_html = ""
            seeded_signup_status = 200
            last_signup_url = str(getattr(self, "_last_signup_url", "") or "")
            if (
                str(getattr(self, "_last_signup_html", "") or "")
                and "/checkoutweb/signup" in last_signup_url
            ):
                seeded_signup_html = str(getattr(self, "_last_signup_html", "") or "")
                try:
                    seeded_signup_status = int(getattr(self, "_last_signup_status", 200) or 200)
                except Exception:
                    seeded_signup_status = 200
            result = run_phase1_risk_with_roxy_browser(
                roxy_browser,
                signup_url,
                cookies=self.session.export_cookies_for_browser(),
                wait_seconds=self._risk_roxy_wait_seconds(),
                app_id="CHECKOUTUINODEWEB_ONBOARDING_LITE",
                correlation_id=token,
                document_html=seeded_signup_html,
                document_status=seeded_signup_status,
            )
            if result.get("cookies"):
                self.session.import_browser_cookies(result["cookies"])
            browser_ok = self._normalize_phase1_roxy_datadog_runtime_result(result)
            counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
            signup_context_result = {
                "ok": browser_ok,
                "runtime": "roxy",
                "status": result.get("status"),
                "url": result.get("url"),
                "reason": result.get("reason") or "",
                "app_id": "CHECKOUTUINODEWEB_ONBOARDING_LITE",
                "correlation_id": token,
                "observed": result.get("observed") or [],
                "observed_order": result.get("observed_order") or [],
                "missing": result.get("missing") or [],
                "counts": counts,
                "response_counts": result.get("response_counts") or {},
                "cookie_count": len(result.get("cookies") or []),
                "requests": result.get("requests") or [],
                "responses": result.get("responses") or [],
                "request_failures": result.get("request_failures") or [],
                "failed_requests": result.get("failed_requests") or result.get("request_failures") or [],
                "injected_scripts": result.get("injected_scripts") or [],
                "inject_errors": result.get("inject_errors") or [],
                "datadog_runtime": result.get("datadog_runtime") or {},
                "datadog_runtime_fulfilled": bool(result.get("datadog_runtime_fulfilled")),
                "datadog_runtime_fulfilled_reason": result.get("datadog_runtime_fulfilled_reason") or "",
                "datadog_probes": result.get("datadog_probes") or [],
                "datadog_flushes": result.get("datadog_flushes") or [],
                "runtime_signals": result.get("runtime_signals") or [],
                "interaction_profile": result.get("interaction_profile") or "",
                "interaction_summary": result.get("interaction_summary") or {},
                "idle_interaction_summary": result.get("idle_interaction_summary") or {},
                "interaction_error": result.get("interaction_error") or "",
                "idle_interaction_error": result.get("idle_interaction_error") or "",
                "required_signals": result.get("required_signals") or [],
                "required_missing": result.get("required_missing") or [],
                "reloads": result.get("reloads") or [],
                "max_reloads": result.get("max_reloads"),
                "blocked_requests": result.get("blocked_requests") or [],
                "allowed_requests": result.get("allowed_requests") or [],
                "learned_rules": result.get("learned_rules") or [],
                "intercept": result.get("intercept") or {},
                "missing_diagnostic_path": result.get("missing_diagnostic_path") or "",
                "missing_diagnostic_error": result.get("missing_diagnostic_error") or "",
                "debug_log_path": result.get("debug_log_path") or "",
            }
            previous = getattr(self.state, "risk_signals_browser_result", {})
            if isinstance(previous, dict):
                merged = dict(previous)
                merged["signup_context"] = signup_context_result
                merged["ok"] = bool(previous.get("ok")) or browser_ok
                self.state.risk_signals_browser_result = merged
            else:
                self.state.risk_signals_browser_result = {"signup_context": signup_context_result}
            self.state.risk_signals_runtime_source = "roxy"
            logger.info(
                "Signup context risk signals executed through Roxy browser observed={} missing={}",
                ",".join(str(item) for item in (result.get("observed") or [])) or "<none>",
                ",".join(str(item) for item in (result.get("missing") or [])) or "<none>",
            )
            required_missing = [
                str(item)
                for item in (result.get("required_missing") or [])
                if str(item)
            ]
            if required_missing:
                message = (
                    "Roxy signup-context risk runtime is missing required browser signals: "
                    f"{','.join(required_missing)}"
                )
                if mode == "roxy" or strict_browser_risk_enabled():
                    raise RuntimeError(message)
                logger.warning(message)
            return browser_ok
        except Exception as exc:
            error_text = self._safe_error_text(exc)
            self.state.risk_signals_runtime_source = "roxy_failed"
            self.state.risk_signals_browser_result = {
                "ok": False,
                "signup_context": {"ok": False, "error": error_text},
            }
            if (mode == "roxy" and not self._roxy_runtime_fallback_enabled()) or strict_browser_risk_enabled():
                raise RuntimeError(error_text) from None
            self._disable_roxy_runtime(exc)
            logger.warning(
                "Roxy signup-context risk runtime failed; falling back to local headless signup-context risk: {}",
                error_text,
            )
            return False

    def _send_signup_context_risk_signals_with_headless(self, signup_url: str, token: str) -> bool:
        mode = self._signup_context_risk_mode()
        if mode != "headless":
            return False
        if not self._headless_runtime_enabled():
            error_text = "Local headless signup-context risk runtime is disabled."
            self.state.risk_signals_runtime_source = "headless_failed"
            self.state.risk_signals_browser_result = {
                "ok": False,
                "signup_context": {"ok": False, "runtime": "headless", "error": error_text},
            }
            raise RuntimeError(error_text)
        try:
            from paypal.local_headless import run_local_headless_mtr_phase1

            headless_session = self._get_headless_session()
            dfp_config = self._headless_mtr_config_for_page(signup_url)
            run_mtr = self._mtr_runtime_mode() == "headless"
            seeded_signup_html = ""
            seeded_signup_status = 200
            last_signup_url = str(getattr(self, "_last_signup_url", "") or "")
            if (
                str(getattr(self, "_last_signup_html", "") or "")
                and "/checkoutweb/signup" in last_signup_url
            ):
                seeded_signup_html = str(getattr(self, "_last_signup_html", "") or "")
                try:
                    seeded_signup_status = int(getattr(self, "_last_signup_status", 200) or 200)
                except Exception:
                    seeded_signup_status = 200
            result = run_local_headless_mtr_phase1(
                signup_url,
                dfp_config=dfp_config,
                dfp_script_url=self.state.mtr_dfp_script_url,
                cookies=self.session.export_cookies_for_browser(),
                wait_seconds=self._risk_headless_wait_seconds(),
                proxy_url=self.proxy_config.url or "",
                browser_profile=cast(dict[str, object], self.state.browser_profile or {}),
                screen=cast(dict[str, object], self.state.screen or {}),
                viewport=cast(dict[str, object], self.state.viewport or {}),
                app_id="CHECKOUTUINODEWEB_ONBOARDING_LITE",
                correlation_id=token,
                session=headless_session,
                stage="signup_context",
                new_page=True,
                run_mtr=run_mtr,
                runtime="headless",
                document_html=seeded_signup_html,
                document_status=seeded_signup_status,
            )
            raw_cookies = result.get("cookies")
            result_cookies = cast(list[dict[str, Any]], raw_cookies) if isinstance(raw_cookies, list) else []
            if result_cookies:
                self.session.import_browser_cookies(result_cookies)
            if (
                self._signup_context_headless_result_is_datadome_challenge(result)
                and self._headless_signup_context_roxy_fallback_enabled()
            ):
                logger.warning(
                    "Local headless signup-context stopped on DataDome challenge; retrying signup-context risk through Roxy fallback."
                )
                try:
                    if self._send_signup_context_risk_signals_with_roxy(signup_url, token, force=True):
                        return True
                except Exception as roxy_exc:
                    if strict_browser_risk_enabled():
                        raise
                    logger.warning(
                        "Roxy fallback after signup-context DataDome challenge failed: {}",
                        self._safe_error_text(roxy_exc),
                    )
            browser_ok = self._normalize_phase1_roxy_datadog_runtime_result(result)
            counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
            observed = list(cast(list[object], result.get("observed"))) if isinstance(result.get("observed"), list) else []
            missing = list(cast(list[object], result.get("missing"))) if isinstance(result.get("missing"), list) else []
            required_missing = list(cast(list[object], result.get("required_missing"))) if isinstance(result.get("required_missing"), list) else []
            signup_context_result = {
                "ok": browser_ok,
                "runtime": "headless",
                "status": result.get("status"),
                "url": result.get("url"),
                "reason": result.get("reason") or "",
                "app_id": "CHECKOUTUINODEWEB_ONBOARDING_LITE",
                "correlation_id": token,
                "observed": observed,
                "observed_order": result.get("observed_order") or [],
                "missing": missing,
                "counts": counts,
                "response_counts": result.get("response_counts") or {},
                "cookie_count": len(result_cookies),
                "requests": result.get("requests") or [],
                "responses": result.get("responses") or [],
                "request_failures": result.get("request_failures") or [],
                "failed_requests": result.get("failed_requests") or result.get("request_failures") or [],
                "injected_scripts": result.get("injected_scripts") or [],
                "inject_errors": result.get("inject_errors") or [],
                "datadog_runtime": result.get("datadog_runtime") or {},
                "datadog_runtime_fulfilled": bool(result.get("datadog_runtime_fulfilled")),
                "datadog_runtime_fulfilled_reason": result.get("datadog_runtime_fulfilled_reason") or "",
                "datadog_probes": result.get("datadog_probes") or [],
                "datadog_flushes": result.get("datadog_flushes") or [],
                "runtime_signals": result.get("runtime_signals") or [],
                "required_signals": result.get("required_signals") or [],
                "required_missing": required_missing,
                "signup_context_page": result.get("signup_context_page") or {},
                "signup_context_bootstrap": result.get("signup_context_bootstrap") or {},
                "signup_context_seeded_document": result.get("signup_context_seeded_document") or {},
                "blocked_by_datadome": bool(result.get("blocked_by_datadome")),
                "blocked_requests": result.get("blocked_requests") or [],
                "allowed_requests": result.get("allowed_requests") or [],
                "learned_rules": result.get("learned_rules") or [],
                "intercept": result.get("intercept") or {},
                "missing_diagnostic_path": result.get("missing_diagnostic_path") or "",
                "missing_diagnostic_error": result.get("missing_diagnostic_error") or "",
                "debug_log_path": result.get("debug_log_path") or "",
            }
            previous = getattr(self.state, "risk_signals_browser_result", {})
            if isinstance(previous, dict):
                merged = dict(previous)
                merged["signup_context"] = signup_context_result
                merged["ok"] = bool(previous.get("ok")) or browser_ok
                self.state.risk_signals_browser_result = merged
            else:
                self.state.risk_signals_browser_result = {"signup_context": signup_context_result}
            self.state.risk_signals_runtime_source = "headless"
            logger.info(
                "Signup context risk signals executed through local headless observed={} missing={}",
                ",".join(str(item) for item in observed) or "<none>",
                ",".join(str(item) for item in missing) or "<none>",
            )
            required_missing_text = [str(item) for item in required_missing if str(item)]
            if required_missing_text:
                message = "Headless signup-context risk runtime is missing required browser signals: " + ",".join(required_missing_text)
                diagnostic_path = str(result.get("missing_diagnostic_path") or "")
                if diagnostic_path:
                    message += f" diagnostic={diagnostic_path}"
                if strict_browser_risk_enabled():
                    raise RuntimeError(message)
                logger.warning(message)
            return browser_ok
        except Exception as exc:
            error_text = self._safe_error_text(exc)
            self.state.risk_signals_runtime_source = "headless_failed"
            self.state.risk_signals_browser_result = {
                "ok": False,
                "signup_context": {"ok": False, "runtime": "headless", "error": error_text},
            }
            logger.warning("Local headless signup-context risk runtime failed: {}", error_text)
            raise RuntimeError(error_text) from None

    def _cleanup_roxy_browser(self) -> None:
        roxy_browser = getattr(self.state, "roxy_browser", None) or {}
        if not roxy_browser:
            return
        try:
            from paypal.roxy_fingerprint import close_roxy_browser

            close_roxy_browser(roxy_browser, delete=True)
        except Exception as exc:
            logger.debug("Roxy browser cleanup failed: {}", exc)
        finally:
            try:
                self.state.roxy_browser = {}
            except Exception:
                pass

    def _cleanup_headless_session(self) -> None:
        session = getattr(self, "_headless_session", None) or getattr(self, "_headless_optimized_session", None)
        if session is None:
            return
        # Playwright browser.close() 无超时, 在代理链路异常时可永久挂起 (8/14 BA-3YK 380s 卡死根因)。
        # 放后台线程 + join 超时: 超时后放弃清理, 不阻塞主流程收尾 (授权结果已产出)。
        import threading as _threading

        def _close_worker() -> None:
            try:
                session.close()
            except Exception:
                pass

        worker = _threading.Thread(target=_close_worker, daemon=True)
        worker.start()
        try:
            worker.join(timeout=15)
            if worker.is_alive():
                logger.warning("Local headless session close exceeded 15s; abandoning cleanup thread")
        finally:
            self._headless_session = None
            self._headless_optimized_session = None

    def _cleanup_headless_optimized_session(self) -> None:
        self._cleanup_headless_session()

    @staticmethod
    def _headless_env_enabled() -> bool:
        from paypal.local_headless import headless_enabled

        return headless_enabled()

    @staticmethod
    def _headless_optimized_env_enabled() -> bool:
        return PayPalFlow._headless_env_enabled()

    def _headless_runtime_enabled(self) -> bool:
        # The old local-headless path has been removed; "headless" always means
        # the local-headless runner unless explicitly disabled.
        return self._headless_env_enabled()

    def _headless_optimized_runtime_enabled(self) -> bool:
        return self._headless_runtime_enabled()

    def _get_headless_session(self):
        from paypal.local_headless import LocalHeadlessSession

        session = getattr(self, "_headless_session", None) or getattr(self, "_headless_optimized_session", None)
        if session is None:
            session = LocalHeadlessSession(
                cookies=self.session.export_cookies_for_browser(),
                proxy_url=self.proxy_config.url or "",
                browser_profile=cast(dict[str, object], self.state.browser_profile or {}),
                screen=cast(dict[str, object], self.state.screen or {}),
                viewport=cast(dict[str, object], self.state.viewport or {}),
                job_id=f"headless-{uuid4().hex[:12]}",
                runtime="headless",
            )
            self._headless_session = session
            self._headless_optimized_session = session
        else:
            session.import_cookies(self.session.export_cookies_for_browser())
        return session

    def _get_headless_optimized_session(self):
        return self._get_headless_session()

    def _warn_challenge_locale_mismatch(self, challenge_html: str) -> None:
        iframe_src = (
            self._extract_hcaptcha_passive_iframe_src(challenge_html)
            or self._extract_hcaptcha_iframe_src(challenge_html)
            or ""
        )
        country = self._first_query_value(iframe_src, "country.x")
        locale = self._first_query_value(iframe_src, "locale.x")
        expected_country = self.address.country
        expected_locale = (self.state.browser_profile or {}).get("locale") or "pt_BR"
        if country and country.upper() != expected_country.upper():
            logger.warning(
                "Challenge server country.x={} differs from configured checkout country {}; "
                "proxy/IP/cookie locale may be inconsistent.",
                country,
                expected_country,
            )
        if locale and locale != expected_locale:
            logger.warning(
                "Challenge server locale.x={} differs from configured browser locale {}; "
                "Accept-Language/profile may be inconsistent.",
                locale,
                expected_locale,
            )

    def _captcha_frontend_disable_enabled(self) -> bool:
        return captcha_frontend_disable_enabled()

    @staticmethod
    def _env_truthy(name: str) -> bool:
        return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int_between(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            logger.warning("Invalid {}={!r}; using default {}", name, raw, default)
            return default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _modxo_static_action_ids_enabled() -> bool:
        raw = (
            _load_proxy_dotenv_value("PAYPAL_MODXO_STATIC_ACTION_IDS")
            or _load_proxy_dotenv_value("PAYPAL_MODXO_HARDCODED_ACTION_IDS")
            or "1"
        ).strip().lower()
        return raw not in {
            "0",
            "false",
            "no",
            "off",
            "disable",
            "disabled",
            "dynamic",
            "scan",
        }

    @staticmethod
    def _synthetic_captcha_allowed() -> bool:
        return PayPalFlow._env_truthy("PAYPAL_ALLOW_SYNTHETIC_CAPTCHA")

    @staticmethod
    def _synthetic_risk_signals_allowed() -> bool:
        return not strict_browser_risk_enabled()

    def _roxy_risk_runtime_active(self) -> bool:
        """True when browser/Roxy owns risk telemetry for this flow."""
        return (
            self._risk_signals_mode() in {"roxy", "headless"}
            or getattr(self.state, "risk_signals_runtime_source", "") in {"roxy", "headless", "headless_optimized"}
        )

    def _skip_synthetic_behavior_telemetry(self, family: str) -> bool:
        if not self._roxy_risk_runtime_active():
            return False
        logged = self._roxy_skipped_telemetry_families
        if family not in logged:
            logger.info(
                "Skipping synthetic {} telemetry because browser risk runtime is active.",
                family,
            )
            try:
                logged.add(family)
                self._roxy_skipped_telemetry_families = logged
            except Exception:
                pass
        return True

    def _send_tealeaf_data(self, *args, **kwargs):
        if self._skip_synthetic_behavior_telemetry("Tealeaf"):
            return None
        return send_tealeaf_data(*args, **kwargs)

    def _send_datadog_rum_view(self, *args, **kwargs):
        if strict_browser_risk_enabled() and self._skip_synthetic_behavior_telemetry("Datadog RUM view"):
            return None
        return send_datadog_rum_view(*args, **kwargs)

    def _send_datadog_rum_action(self, *args, **kwargs):
        if strict_browser_risk_enabled() and self._skip_synthetic_behavior_telemetry("Datadog RUM action"):
            return None
        return send_datadog_rum_action(*args, **kwargs)

    def _send_authchallenge_datadog_rum(self, page_url: str, action_name: str = "authchallenge_detected") -> None:
        self._send_datadog_rum_view(
            self.session,
            page_url,
            self.ba_token,
            dd_config=_DD_AUTHCHALLENGE_CONFIG,
            referrer=page_url,
            api="fetch",
        )
        self._send_datadog_rum_action(
            self.session,
            action_name,
            page_url,
            dd_config=_DD_AUTHCHALLENGE_CONFIG,
            referrer=page_url,
            api="fetch",
        )

    def _is_step3_signup_field_events_call(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        app_id = str(kwargs.get("app_id") or "CHECKOUTUINODEWEB_ONBOARDING_LITE")
        if app_id != "CHECKOUTUINODEWEB_ONBOARDING_LITE":
            return False
        token = str((args[1] if len(args) > 1 else kwargs.get("ec_token")) or "")
        fields_arg = args[2] if len(args) > 2 else kwargs.get("field_ids")
        try:
            fields = {str(item) for item in (fields_arg or [])}
        except Exception:
            fields = set()
        step3_fields = {
            "email",
            "phone",
            "cardNumber",
            "cardExpiry",
            "cardCvv",
            "password",
            "firstName",
            "lastName",
            "billingLine1",
            "billingCity",
            "billingPostalCode",
            "billingState",
            "dateOfBirth",
            "identityDocumentNumber",
        }
        if fields and not fields.intersection(step3_fields):
            return False
        state_token = str(getattr(self.state, "ec_token", "") or "")
        return bool(token and (token == state_token or token.startswith("EC-")))

    def _send_signup_field_events(self, *args, **kwargs):
        if self._is_step3_signup_field_events_call(args, kwargs):
            return None
        if self._skip_synthetic_behavior_telemetry("signup field-events"):
            return None
        return send_signup_field_events(*args, **kwargs)

    def _send_tealeaf_form_interaction_batch(self, page_url: str, fields: list[str]) -> None:
        if self._skip_synthetic_behavior_telemetry("Tealeaf form-interaction batch"):
            return
        tl = TealeafSession(self.session, page_url)
        tl.send_form_interaction_batch(fields)

    def _capture_mtr_metadata(self, html: str, page_url: str = "") -> None:
        config = extract_mtr_config(html)
        if config:
            page_channel = str(config.get("dfpChannel") or "").strip()
            page_cmid = str(config.get("clientMetaDataId") or "").strip()
            page_api_key = str(config.get("fppAPIKey") or "").strip()
            if page_channel:
                self.state.mtr_channel = page_channel
            if page_cmid:
                self.state.mtr_client_metadata_id = page_cmid
            if page_api_key:
                self.state.mtr_api_key = page_api_key
            self.state.mtr_is_qa = bool(config.get("isQA", self.state.mtr_is_qa))
            if page_channel and page_cmid and page_api_key:
                logger.info(
                    "MTR dfpconfig captured: channel={} cmid={} api_key_present={}",
                    self.state.mtr_channel or "<missing>",
                    sanitize_for_log({"token": self.state.mtr_client_metadata_id or ""})["token"],
                    bool(self.state.mtr_api_key),
                )
            else:
                logger.info(
                    "MTR partial dfpconfig found; applying fallback: page_channel={} page_cmid={} page_api_key_present={}",
                    page_channel or "<missing>",
                    sanitize_for_log({"token": page_cmid or ""})["token"],
                    bool(page_api_key),
                )
        script_url = extract_dfp_script_url(html)
        if script_url:
            self.state.mtr_dfp_script_url = script_url
        after_page_config = {
            "channel": self.state.mtr_channel,
            "cmid": self.state.mtr_client_metadata_id,
            "api_key": self.state.mtr_api_key,
        }
        config_complete_after_page = bool(
            self.state.mtr_channel
            and self.state.mtr_client_metadata_id
            and self.state.mtr_api_key
        )
        if ensure_mtr_config(self.state, page_url=page_url) and (
            not config_complete_after_page
            or after_page_config["channel"] != self.state.mtr_channel
            or after_page_config["cmid"] != self.state.mtr_client_metadata_id
            or after_page_config["api_key"] != self.state.mtr_api_key
        ):
            logger.info(
                "MTR dfpconfig fallback applied: channel={} cmid={} api_key_present={}",
                self.state.mtr_channel or "<missing>",
                sanitize_for_log({"token": self.state.mtr_client_metadata_id or ""})["token"],
                bool(self.state.mtr_api_key),
            )

    def _risk_runtime_report(self) -> dict[str, object]:
        """Summarize strict-browser-risk gaps from automation_vs_real_browser_risk_diff.md."""
        high_entropy_forced = os.getenv("PAYPAL_FORCE_HIGH_ENTROPY_CH", "0").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        mtr_sealed_result_present = bool(self.state.mtr_sealed_result)
        datadome_cookie_present = bool(self.state.datadome_cookie)
        datadome_header_injected = bool(self.state.datadome_header_injected)
        captcha_synthetic_used = bool(self.state.captcha_synthetic_used)
        captcha_synthetic_allowed = self._synthetic_captcha_allowed()
        risk_browser_result = getattr(self.state, "risk_signals_browser_result", {}) or {}
        risk_browser_ok = bool(
            getattr(self.state, "risk_signals_runtime_source", "") in {"roxy", "headless", "headless_optimized"}
            and isinstance(risk_browser_result, dict)
            and risk_browser_result.get("ok")
        )
        synthetic_risk_families_used = not risk_browser_ok
        synthetic_risk_families_allowed = self._synthetic_risk_signals_allowed() or not synthetic_risk_families_used
        mtr_config_present = bool(
            self.state.mtr_channel
            and self.state.mtr_client_metadata_id
            and self.state.mtr_api_key
        )
        blockers: list[str] = []
        if not mtr_sealed_result_present:
            blockers.append("mtr_sealedResult_missing")
        if self.state.mtr_runtime_source == MTR_RUNTIME_PYTHON_GENERATED:
            blockers.append("mtr_python_generated_runtime")
        if captcha_synthetic_used or (
            self.captcha_bypass_mode == CAPTCHA_FRONTEND_DISABLE_MODE
            and not captcha_synthetic_allowed
        ):
            blockers.append("captcha_synthetic_or_fake_path")
        if datadome_header_injected and not datadome_cookie_present:
            blockers.append("datadome_header_without_cookie_chain")
        if high_entropy_forced:
            blockers.append("client_hints_forced_high_entropy")
        if synthetic_risk_families_used and not synthetic_risk_families_allowed:
            blockers.append("synthetic_fraudnet_fpti_tealeaf_datadog")
        mtr_browser_result = cast(
            dict[str, object],
            sanitize_for_log(getattr(self.state, "mtr_browser_result", {}) or {}),
        )
        datadome_browser_result = cast(
            dict[str, object],
            sanitize_for_log(getattr(self.state, "datadome_browser_result", {}) or {}),
        )
        risk_browser_result_public = cast(dict[str, object], sanitize_for_log(risk_browser_result))
        return {
            "strict_browser_risk": strict_browser_risk_enabled(),
            "mtr": {
                "config_present": mtr_config_present,
                "dfp_script_url": self.state.mtr_dfp_script_url,
                "get_status": self.state.mtr_get_status,
                "post_status": self.state.mtr_post_status,
                "sealed_result_present": mtr_sealed_result_present,
                "runtime_source": self.state.mtr_runtime_source or "not_sent",
                "browser_result": mtr_browser_result,
            },
            "datadome": {
                "mode": self._datadome_mode(),
                "cookie_present": datadome_cookie_present,
                "clientid_present": bool(self.state.datadome_clientid),
                "header_injected": datadome_header_injected,
                "browser_solved": bool(getattr(self.state, "datadome_browser_solved", False)),
                "browser_result": datadome_browser_result,
                "browser_js_cookie_chain_verified": bool(
                    datadome_cookie_present and getattr(self.state, "datadome_browser_solved", False)
                ),
            },
            "captcha": {
                "mode": self.captcha_bypass_mode,
                "paypal_captcha_solved": bool(self.state.paypal_captcha_solved),
                "synthetic_used": captcha_synthetic_used,
                "synthetic_allowed": captcha_synthetic_allowed,
            },
            "client_hints": {
                "force_high_entropy_env": high_entropy_forced,
                "accept_ch_observed": bool(getattr(self.session, "_accept_ch_received", False)),
            },
            "synthetic_risk_families": {
                "mode": self._risk_signals_mode(),
                "runtime_source": getattr(self.state, "risk_signals_runtime_source", "") or "not_sent",
                "browser_result": risk_browser_result_public,
                "fraudnet_python_generated": synthetic_risk_families_used,
                "fpti_python_generated": synthetic_risk_families_used,
                "tealeaf_template_generated": synthetic_risk_families_used,
                "datadog_template_generated": synthetic_risk_families_used,
                "allowed": synthetic_risk_families_allowed,
            },
            "strict_blockers": blockers,
        }

    def _with_risk_runtime_report(self, result: dict[str, object]) -> dict[str, object]:
        result = dict(result)
        result["risk_runtime"] = self._risk_runtime_report()
        return result

    def _strict_signup_preflight_or_raise(self) -> None:
        if not strict_browser_risk_enabled():
            return
        report = self._risk_runtime_report()
        raw_blockers = report.get("strict_blockers")
        blocker_items = raw_blockers if isinstance(raw_blockers, list) else []
        blockers = [
            str(blocker)
            for blocker in blocker_items
            if str(blocker)
        ]
        if not blockers:
            return
        raise RuntimeError(
            "Strict browser-risk preflight blocked SignUpNewMemberMutation because "
            f"browser proof is incomplete: {','.join(blockers)}. "
            f"report={json.dumps(sanitize_for_log(report), ensure_ascii=False)}"
        )

    @staticmethod
    def _url_with_paypal_client_cfci(url: str, cfci: str) -> str:
        parts = urllib.parse.urlsplit(url)
        query = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if key != "paypal_client_cfci"
        ]
        query.append(("paypal_client_cfci", cfci))
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(query),
                parts.fragment,
            )
        )

    @staticmethod
    def _url_append_params(url: str, params: dict[str, str]) -> str:
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        for key, value in (params or {}).items():
            query.append((str(key), str(value)))
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(query),
                parts.fragment,
            )
        )

    def _mark_frontend_captcha_solved(self, reason: str) -> None:
        if not self.state.paypal_captcha_solved:
            logger.info(
                "PayPal frontend CAPTCHA state noted via {} mode={}",
                reason,
                self.captcha_bypass_mode,
            )
        self.state.paypal_captcha_solved = True

    def _modxo_cfci(self, action: str) -> str:
        action = (action or "").strip()
        if action.startswith("modxo_vaulted_not_recurring-"):
            return action
        return f"modxo_vaulted_not_recurring-{action}"

    def _modxo_url_with_cfci(self, url: str, action: str) -> str:
        return self._url_with_paypal_client_cfci(url, self._modxo_cfci(action))

    def _paypal_nsid_header_value(self) -> str:
        """Return the browser header form of the signed PayPal nsid cookie."""
        nsid = (self.state.nsid or "").strip()
        if not nsid:
            return ""
        try:
            decoded = urllib.parse.unquote(nsid)
        except Exception:
            decoded = nsid
        if decoded.startswith("s:"):
            decoded = decoded[2:]
            if "." in decoded:
                decoded = decoded.rsplit(".", 1)[0]
        return decoded

    @staticmethod
    def _modxo_router_state_tree_header() -> str:
        """Next router state header captured from the ModXO app-router flow."""
        tree = [
            "",
            {
                "children": [
                    "(identity)",
                    {
                        "children": ["__PAGE__", {}, None, None, 0],
                        "authFlow": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "cookiedViewUl": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "emailUl": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "onboarding": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "otp": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "otpInput": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "passkeyUl": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "passwordUl": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                        "tokenizedLogin": ["(__SLOT__)", {"children": ["__PAGE__", {}, None, None, 0]}, None, None, 0],
                    },
                    None,
                    None,
                    0,
                ]
            },
            None,
            None,
            16,
        ]
        return urllib.parse.quote(json.dumps(tree, separators=(",", ":")), safe="()")

    def _modxo_server_action_headers(self, *, referer: str, action_id: str) -> dict[str, str]:
        headers = {
            **self._browser_headers(accept="text/x-component"),
            "Origin": "https://www.paypal.com",
            "Referer": referer,
            "Next-Action": action_id,
            "Next-Router-State-Tree": self._modxo_router_state_tree_header(),
            "PayPal-Client-Cfci": self._modxo_cfci("server_action"),
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        nsid = self._paypal_nsid_header_value()
        if nsid:
            headers["PayPal-NSID"] = nsid
        if self.state.modxo_deployment_id:
            headers["X-Deployment-Id"] = self.state.modxo_deployment_id
        return headers

    def _send_modxo_countries_packet(
        self,
        *,
        page_url: str,
        country: str | None = None,
        cfci: str | None = None,
    ) -> None:
        countries_base = "https://www.paypal.com/pay/api/countries"
        if country:
            countries_base = f"{countries_base}?country.x={urllib.parse.quote(country)}"
        cfci = cfci or self._modxo_cfci("countries_country" if country else "countries")
        countries_url = self._url_with_paypal_client_cfci(countries_base, cfci)
        try:
            headers = {
                **self._browser_headers(
                    accept="*/*",
                    content_type="application/json",
                ),
                "Referer": page_url,
                "PayPal-Client-Cfci": cfci,
                "X-TRPC-Source": "client",
                "X-TRPC-Token": self.ba_token,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            nsid = self._paypal_nsid_header_value()
            if nsid:
                headers["PayPal-NSID"] = nsid
            resp = self.session.get(
                countries_url,
                headers=headers,
                timeout=20,
            )
            logger.info(
                "ModXO countries packet status={} url={}",
                resp.status_code,
                countries_url,
            )
        except Exception as e:
            logger.debug("ModXO countries packet failed: {}", e)

    def _frontend_captcha_solved_cfci(self) -> str:
        return CAPTCHA_SOLVED_CFCI

    def _post_modxo_country_action(
        self,
        *,
        page_url: str,
        country: str,
        cfci: str,
    ):
        """Submit the inline ModXO country handleChange server action.

        The browser trace does this right after the passive captcha path is
        accepted.  The body is deployment-bound: action id and encrypted bound
        argument are extracted from the live ModXO Flight stream in Phase 0.
        """
        if not (self.state.modxo_country_action_id and self.state.modxo_country_action_bound):
            logger.debug("Skipping ModXO country action: dynamic action metadata is missing")
            return None

        headers = self._modxo_server_action_headers(
            referer=page_url,
            action_id=self.state.modxo_country_action_id,
        )
        headers["PayPal-Client-Cfci"] = cfci
        action_url = self._url_with_paypal_client_cfci(page_url, cfci)
        try:
            resp = self.session.post(
                action_url,
                files=[
                    ("1", (None, f'"{self.state.modxo_country_action_bound}"')),
                    ("0", (None, f'["$@1","{country}"]')),
                ],
                headers=headers,
            )
            logger.info(
                "ModXO country action status={} country={} cfci={}",
                resp.status_code,
                country,
                cfci,
            )
            if looks_like_paypal_authchallenge(resp.text):
                return resp
            redirect_url = self._modxo_action_redirect_url(resp)
            if redirect_url:
                redirect_url = urllib.parse.urljoin(page_url, redirect_url)
                self.state.modxo_pay_page_url = redirect_url
                ctx_id = self._first_query_value(redirect_url, "ctxId")
                if ctx_id:
                    self.state.ctx_id = ctx_id
                self.state.modxo_country_selected = True
            elif 200 <= resp.status_code < 400:
                # Some deployments return a Flight payload without an explicit
                # Location header.  Keep the caller on the current page URL but
                # still remember that the country action was accepted.
                self.state.modxo_country_selected = True
            return resp
        except Exception as e:
            logger.debug("ModXO country action failed: {}", e)
            return None

    def _send_modxo_frontend_captcha_solved_packets(
        self,
        page_url: str,
        *,
        include_base: bool = True,
        include_country: bool = False,
    ) -> None:
        if strict_browser_risk_enabled() and not self._synthetic_captcha_allowed():
            logger.warning(
                "Skipping synthetic ModXO CAPTCHA_SOLVED packets in strict browser-risk mode."
            )
            return
        if os.getenv("PAYPAL_SKIP_MODXO_CAPTCHA_SOLVED_PACKETS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return

        cfci = self._frontend_captcha_solved_cfci()
        country = self._profile_country()
        self._mark_frontend_captcha_solved("modxo_frontend_packets")

        if include_base:
            self._send_modxo_countries_packet(page_url=page_url, cfci=cfci)
            self._send_tealeaf_data(
                self.session,
                self._url_with_paypal_client_cfci(page_url, cfci),
                endpoint_url=self._url_with_paypal_client_cfci(
                    "https://www.paypal.com/platform/tealeaftarget",
                    cfci,
                ),
            )
            country_resp = self._post_modxo_country_action(
                page_url=page_url,
                country=country,
                cfci=cfci,
            )
            if country_resp is not None and not self.state.modxo_pay_page_url:
                redirect_url = self._modxo_action_redirect_url(country_resp)
                if redirect_url:
                    self.state.modxo_pay_page_url = urllib.parse.urljoin(page_url, redirect_url)

        if include_country or self.state.modxo_country_selected:
            country_page_url = (
                self.state.modxo_pay_page_url
                or f"https://www.paypal.com/pay/?ssrt={self.state.ssrt}"
                f"&token={self.ba_token}&ul=1&ctxId={self.state.ctx_id}"
                f"&country.x={country}"
            )
            self._send_modxo_countries_packet(
                page_url=country_page_url,
                country=country,
                cfci=cfci,
            )
            self._send_tealeaf_data(
                self.session,
                self._url_with_paypal_client_cfci(country_page_url, cfci),
                endpoint_url=self._url_with_paypal_client_cfci(
                    "https://www.paypal.com/platform/tealeaftarget",
                    cfci,
                ),
            )

    def run(self) -> dict[str, object]:
        """Execute the complete flow. Returns result dict with status and return_url."""
        final_result: dict[str, object] | None = None
        try:
            for flow_attempt in range(1, self.max_flow_attempts + 1):
                if flow_attempt > 1:
                    self._reset_for_full_retry(flow_attempt)

                self._log_flow_attempt_start(flow_attempt)

                try:
                    self._emit_progress("submit_email", "Phase 0: 初始化会话 / DataDome 过墙", level="info")
                    self._phase0_initial_load()
                    self._emit_progress("captcha", "Phase 2: 建号 / EC checkout 校验", level="info")
                    self._phase2_create_account()
                    self._emit_progress("sms", "Phase 3: 填表 + 2FA 短信验证", level="info")
                    self._phase3_signup_and_2fa()
                    self._emit_progress("consent_ba", "Phase 4: 最终授权 (billing.authorize)", level="info")
                    result = self._with_risk_runtime_report(self._phase4_authorize())
                except Exception as attempt_error:
                    self._emit_progress("submit_email", f"流程异常: {self._safe_error_text(attempt_error)[:200]}", level="err")
                    if (
                        self._should_retry_full_flow_exception(attempt_error)
                        and flow_attempt < self.max_flow_attempts
                    ):
                        final_result = {
                            "status": "error",
                            "error": str(attempt_error),
                            "reason": "SIGNUP_RETRYABLE_FAILURE",
                            "retryable": True,
                            "risk_runtime": self._risk_runtime_report(),
                        }
                        logger.warning(
                            "Flow attempt {}/{} failed with a retryable signup/session "
                            "error: {}. Retrying from Phase 0 with fresh data...",
                            flow_attempt,
                            self.max_flow_attempts,
                            attempt_error,
                        )
                        continue
                    raise
                final_result = result

                if result.get("status") == "success":
                    logger.success(f"=== Flow completed successfully ===")
                    self._emit_progress("done", "授权完成: EUAT 收口", level="ok")
                    return result

                if self._should_retry_full_flow(result) and flow_attempt < self.max_flow_attempts:
                    logger.warning(
                        "Flow attempt {}/{} ended with {}. Retrying from Phase 0 "
                        "with a fresh session, user, address and card...",
                        flow_attempt,
                        self.max_flow_attempts,
                        result.get("reason") or result.get("error") or "retryable error",
                    )
                    continue

                logger.error(f"=== Flow completed with error status ===")
                return result

            logger.error(f"=== Flow completed with error status ===")
            return final_result or {
                "status": "error",
                "error": "flow ended without result",
                "risk_runtime": self._risk_runtime_report(),
            }
        except Exception as e:
            logger.error("Flow failed: {}", self._safe_error_text(e))
            raise
        finally:
            self.close()

    def _log_flow_attempt_start(self, flow_attempt: int):
        suffix = (
            f" (attempt {flow_attempt}/{self.max_flow_attempts})"
            if self.max_flow_attempts > 1
            else ""
        )
        logger.info(f"=== PayPal Billing Agreement Flow{suffix} ===")
        logger.info("BA Token: {}", sanitize_for_log({"ba_token": self.ba_token})["ba_token"])
        logger.info("Email: {}", sanitize_for_log({"email": self.user.email})["email"])
        logger.info("Phone: {}", sanitize_for_log({"phone": self.user.phone})["phone"])
        logger.info(f"Proxy: {self.proxy_config.label}")
        self.captcha_bypass_mode = paypal_captcha_bypass_mode()
        logger.info("CAPTCHA mode: {}", self.captcha_bypass_mode)
        self._log_profile_consistency()

    def _should_retry_full_flow(self, result: dict[str, object] | None) -> bool:
        if not isinstance(result, dict):
            return False
        if result.get("retryable") is True:
            return True
        return result.get("reason") in {
            "BUYER_NOT_SET",
            "buyer_not_set_after_partial_signup",
        }

    @staticmethod
    def _should_retry_full_flow_exception(error: Exception) -> bool:
        text = str(error)
        retry_markers = (
            "Signup failed: card was rejected",
            "Signup failed: no usable access token",
            "ACCOUNT_ALREADY_EXISTS and no prior access token",
            "Create account flow did not produce an EC checkout token",
        )
        return any(marker in text for marker in retry_markers)

    def _on_full_retry_generated(self, flow_attempt: int):
        """Hook for UI adapters to publish regenerated retry data."""

    def _on_signup_retry_generated(self, signup_attempt: int, reason: str):
        """Hook for UI adapters to publish regenerated in-place signup data."""

    def _reset_for_full_retry(self, flow_attempt: int):
        """Start a clean browser/session attempt after an unrecoverable EC state.

        BUYER_NOT_SET after a partial SignUpNewMember response means the current
        EC checkout has an access token but no buyer bound to the billing
        agreement. Reusing the same SignUpNewMember request usually produces
        ACCOUNT_ALREADY_EXISTS, so the safer retry is a completely fresh HTTP
        session plus freshly generated signup/card data.
        """
        current_phone = self.user.phone
        current_address = self.address
        try:
            self.close()
        except Exception:
            pass

        self.user = generate_user(current_phone, country=self._context_country())
        self.card = generate_card(
            proxy_url=self.proxy_config.url,
            country=self._context_country(),
            used_bins=self._used_card_bins,
        )
        if self.card.bin:
            self._used_card_bins.add(self.card.bin)
        self.address = current_address
        self.state = SessionState(ba_token=self.ba_token)
        ensure_runtime_profile(
            self.state,
            source=self.fingerprint_source,
            roxy_proxy_url=self.proxy_config.url or "",
            keep_roxy_browser=self._roxy_runtime_requested(),
        )
        self.session = PayPalSession(
            self.state,
            proxy_url=self.proxy_config.url,
            proxy_label=self.proxy_config.label,
        )
        self.captcha_bypass_mode = paypal_captcha_bypass_mode()
        self._used_partial_signup_token = False
        self._billing_address_autocomplete_succeeded = False
        self._signup_billing_address_prepared = False
        self._headless_session = None
        self._headless_optimized_session = None
        self._datadome_browser_document = {}
        self._on_full_retry_generated(flow_attempt)

        logger.info(
            "Regenerated retry identity: email={}, phone={}, card={} exp={}, address={}, {}-{} (preserved)",
            sanitize_for_log({"email": self.user.email})["email"],
            sanitize_for_log({"phone": self.user.phone})["phone"],
            self._masked_card_number(),
            self.card.expiry,
            self.address.district,
            self.address.city,
            self.address.state,
        )

    def _phase0_initial_load(self):
        """Load the agreement approval page, handle DataDome if needed."""
        logger.info("--- Phase 0: Initial page load ---")

        url = f"https://www.paypal.com/agreements/approve?ba_token={self.ba_token}"
        datadome_mode = self._datadome_mode()
        resp = None
        if (
            datadome_mode in {"roxy", "headless"}
            and not self.state.datadome_cookie
            and self._datadome_phase0_preflight_enabled()
        ):
            solved_preflight = self._solve_datadome_with_roxy_browser(url, reason="phase0_preflight")
            datadome_mode = self._datadome_mode()
            if solved_preflight and datadome_mode == "headless":
                resp = self._response_from_browser_document(self._datadome_browser_document)
                if resp is not None:
                    logger.info(
                        "Using local headless browser document for Phase 0 after DataDome preflight status={} url={}",
                        resp.status_code,
                        sanitize_for_log({"url": str(resp.url)})["url"],
                    )
        elif datadome_mode in {"roxy", "headless"} and not self.state.datadome_cookie:
            logger.debug(
                "Skipping Phase 0 DataDome browser preflight; protocol GET will run first and browser runtime is reserved for HTTP 403."
            )

        # First GET - may return 403 with DataDome challenge or 302 redirect
        if resp is None:
            resp = self.session.get(url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            })
        self._capture_datadome_clientid(resp.text)

        if resp.status_code == 403:
            logger.info("Got 403 - DataDome challenge detected")
            browser_resp = self._response_from_browser_document(self._datadome_browser_document)
            if browser_resp is not None and datadome_mode == "headless":
                logger.info(
                    "Protocol Phase 0 replay was challenged; continuing with the local headless browser document status={} url={}",
                    browser_resp.status_code,
                    sanitize_for_log({"url": str(browser_resp.url)})["url"],
                )
                resp = browser_resp
            else:
                solved = self._solve_datadome_with_roxy_browser(url, reason="phase0_403") if datadome_mode in {"roxy", "headless", "auto"} else False
                datadome_mode = self._datadome_mode()
                if solved:
                    browser_resp = self._response_from_browser_document(self._datadome_browser_document)
                    if browser_resp is not None and datadome_mode == "headless":
                        logger.info(
                            "Using local headless browser document for Phase 0 after 403 challenge status={} url={}",
                            browser_resp.status_code,
                            sanitize_for_log({"url": str(browser_resp.url)})["url"],
                        )
                        resp = browser_resp
                    else:
                        resp = self.session.get(url, headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Upgrade-Insecure-Requests": "1",
                            "Sec-Fetch-Site": "none",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-User": "?1",
                            "Sec-Fetch-Dest": "document",
                        })
                    self._capture_datadome_clientid(resp.text)
                elif datadome_mode in {"roxy", "headless"}:
                    raise RuntimeError(f"{datadome_mode} DataDome did not produce a datadome cookie after HTTP 403")
                else:
                    # DataDome returns a page with embedded dd object and ct.ddc.paypal.com/c.js.
                    # Keep the old protocol/header method as the second configurable path.
                    logger.warning("DataDome challenge using protocol fallback. "
                                   "Cookie/client-id from response stored, attempting to proceed...")

                    # Try the POST approach that the browser uses after DataDome resolves
                    post_url = f"{url}&YWRzZGRjYXB0Y2hh=1"
                    resp = self.session.post(post_url, headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": "https://www.paypal.com",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-User": "?1",
                        "Sec-Fetch-Dest": "document",
                    }, data={"adsddtoken": ""})
                    self._capture_datadome_clientid(resp.text)

        if resp.status_code == 302:
            redirect_url = resp.headers.get("Location", "")
            logger.info(f"Redirected to: {redirect_url}")
            # Extract ssrt from redirect URL
            ssrt_match = re.search(r"ssrt=(\d+)", redirect_url)
            if ssrt_match:
                self.state.ssrt = ssrt_match.group(1)
            # Follow the redirect
            if redirect_url.startswith("/"):
                redirect_url = f"https://www.paypal.com{redirect_url}"
            resp = self.session.get(redirect_url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            })
            self._capture_datadome_clientid(resp.text)

        # Parse the login/signup page
        html = resp.text
        self._last_modxo_html = html
        self._last_modxo_base_url = str(resp.url)
        self._capture_datadome_clientid(html)
        self._capture_mtr_metadata(html, str(resp.url))
        logger.info(f"Page loaded: {resp.status_code}, {len(html)} bytes")
        self._apply_modxo_inline_metadata(html)
        self._extract_modxo_action_ids(html, str(resp.url))

        # Extract ctxId
        ctx_match = re.search(r'"ctxId"[^"]*"([^"]+)"', html)
        if ctx_match:
            self.state.ctx_id = ctx_match.group(1)
            logger.info(f"Context ID: {self.state.ctx_id}")

        # Extract ssrt if not yet found
        if not self.state.ssrt:
            ssrt_match = re.search(r"ssrt=(\d+)", str(resp.url))
            if not ssrt_match:
                ssrt_match = re.search(r"ssrt=(\d+)", html)
            if ssrt_match:
                self.state.ssrt = ssrt_match.group(1)
                logger.info(f"SSRT: {self.state.ssrt}")

        if not self.state.ec_token:
            ec_match = re.search(r"\b(EC-[A-Z0-9]+)\b", f"{resp.url}\n{html}")
            if ec_match:
                self.state.ec_token = ec_match.group(1)
                logger.info(
                    "EC Token already present on approval page: {}",
                    sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"],
                )

    @staticmethod
    def _extract_window_initial_data(html: str) -> dict[str, object]:
        """Extract checkoutweb/weasley window.__INITIAL_DATA__ JSON."""
        # The page contains many reads of window.__INITIAL_DATA__ before the
        # actual server-side assignment.  Anchor on `= {` so we do not parse a
        # JavaScript function body from an earlier reference.
        marker = re.search(r"window\.__INITIAL_DATA__\s*=", html or "")
        if not marker:
            return {}

        start = html.find("{", marker.end())
        if start < 0:
            return {}

        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(html)):
            ch = html[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:idx + 1])
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse __INITIAL_DATA__: {e}")
                        return {}

        return {}

    @staticmethod
    def _metadata_search_texts(text: str) -> list[str]:
        """Return decoded variants for metadata regex scanning.

        PayPal can expose checkout content metadata in plain JSON, escaped
        JSON-in-JS strings, URL-encoded RSC payloads, or HTML-escaped chunks.
        Searching only the raw HTML misses several currently observed shapes.
        """
        if not text:
            return []
        variants: list[str] = []

        def add(value: str) -> None:
            if value and value not in variants:
                variants.append(value)

        add(text)
        add(html_lib.unescape(text))
        for value in list(variants):
            try:
                add(urllib.parse.unquote(value))
            except Exception:
                pass
            try:
                add(value.replace("\\/", "/"))
            except Exception:
                pass

        # Decode common JavaScript string escapes conservatively.  Keep failures
        # silent because arbitrary JS chunks are not guaranteed to be valid
        # Python unicode_escape input.
        for value in list(variants):
            if "\\" not in value:
                continue
            try:
                add(bytes(value, "utf-8").decode("unicode_escape"))
            except Exception:
                pass

        # Extract and decode string payloads from React/Next flight pushes such
        # as self.__next_f.push([1,"..."]).
        for m in re.finditer(r"__next_f\.push\(\s*\[\s*\d+\s*,\s*(['\"])((?:\\.|(?!\1).){20,})\1", text or "", re.S):
            raw = m.group(2)
            try:
                add(json.loads(m.group(1) + raw + m.group(1)))
            except Exception:
                add(raw.replace("\\/", "/"))

        return variants

    @staticmethod
    def _extract_content_identifier(
        html: str,
        country: str = "BR",
        lang: str = "pt",
        initial_data: dict[str, object] | None = None,
    ) -> str:
        """Extract or build the dynamic signup terms contentIdentifier."""
        candidates: list[str] = []

        def add_candidate(value) -> None:
            if not isinstance(value, str):
                return
            value = html_lib.unescape(value.strip().replace("\\/", "/"))
            if not value or "signupTerms" not in value:
                return
            try:
                value = urllib.parse.unquote(value)
            except Exception:
                pass
            if value and value not in candidates:
                candidates.append(value)

        initial_identifier = PayPalFlow._find_first_recursive(
            initial_data or {},
            {"contentIdentifier", "content_identifier"},
            lambda item: isinstance(item, str) and "signupTerms" in item,
        )
        add_candidate(initial_identifier)

        for text in PayPalFlow._metadata_search_texts(html or ""):
            for pattern in (
                r'"contentIdentifier"\s*:\s*"([^"]*signupTerms[^"]*)"',
                r'"content_identifier"\s*:\s*"([^"]*signupTerms[^"]*)"',
                r"'contentIdentifier'\s*:\s*'([^']*signupTerms[^']*)'",
                r"'content_identifier'\s*:\s*'([^']*signupTerms[^']*)'",
                r'\\"contentIdentifier\\"\s*:\s*\\"([^"\\]*signupTerms[^"\\]*)\\"',
                r'\\"content_identifier\\"\s*:\s*\\"([^"\\]*signupTerms[^"\\]*)\\"',
                r"\\'contentIdentifier\\'\s*:\s*\\'([^'\\]*signupTerms[^'\\]*)\\'",
                r"\\'content_identifier\\'\s*:\s*\\'([^'\\]*signupTerms[^'\\]*)\\'",
                r'([A-Z]{2}:[a-z]{2}:[A-Za-z0-9_-]{8,128}:compliance\.signupTerms)',
                r'([A-Z]{2}:[a-z]{2}:compliance\.signupTerms)',
            ):
                for match in re.finditer(pattern, text, re.I | re.S):
                    add_candidate(match.group(1))

        expected_prefix = f"{country}:{lang}:".lower()
        for candidate in candidates:
            if (
                PayPalFlow._content_identifier_hash(candidate)
                and candidate.lower().startswith(expected_prefix)
            ):
                return candidate
        for candidate in candidates:
            if PayPalFlow._content_identifier_hash(candidate):
                return candidate
        for candidate in candidates:
            if not PayPalFlow._is_short_content_identifier_value(candidate):
                return candidate
        for candidate in candidates:
            if candidate.lower() == f"{country}:{lang}:compliance.signupTerms".lower():
                return candidate
        if candidates:
            return candidates[0]
        return f"{country}:{lang}:compliance.signupTerms"

    @staticmethod
    def _find_first_recursive(value, key_names: set[str], predicate=None):
        """Find the first nested value whose key matches key_names."""
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key) in key_names and (predicate is None or predicate(item)):
                    return item
                found = PayPalFlow._find_first_recursive(item, key_names, predicate)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = PayPalFlow._find_first_recursive(item, key_names, predicate)
                if found:
                    return found
        return None

    @staticmethod
    def _extract_content_hash(
        html: str,
        initial_data: dict[str, Any] | None = None,
        *,
        include_generic_content_hash: bool = True,
    ) -> str:
        """Extract the signupTerms content hash.

        The browser capture shows SignUpNewMember uses the per-locale entry
        from weasley `content-manifest.*.json` for compliance.signupTerms, for
        example `BR_pt -> 759169...`, not necessarily the generic
        `window.__INITIAL_DATA__.contentHash` value from the signup shell.
        Callers therefore disable generic `contentHash` extraction whenever a
        content manifest URL is present and fetch the manifest instead.
        """
        identifier = PayPalFlow._find_first_recursive(
            initial_data or {},
            {"contentIdentifier", "content_identifier"},
            lambda item: isinstance(item, str) and "signupTerms" in item,
        )
        embedded_hash = PayPalFlow._content_identifier_hash(identifier or "")
        if embedded_hash:
            return embedded_hash
        if include_generic_content_hash:
            value = PayPalFlow._find_first_recursive(
                initial_data or {},
                {"contentHash", "content_hash"},
                lambda item: isinstance(item, str) and bool(item),
            )
            if isinstance(value, str) and value:
                return value
        for text in PayPalFlow._metadata_search_texts(html or ""):
            for pattern in (
                r'([A-Za-z0-9_-]{8,128}):compliance\.signupTerms',
            ):
                match = re.search(pattern, text, re.I)
                if match:
                    return match.group(1)
            if include_generic_content_hash:
                for pattern in (
                    r'"contentHash"\s*:\s*"([A-Za-z0-9_-]{8,128})"',
                    r'"content_hash"\s*:\s*"([A-Za-z0-9_-]{8,128})"',
                    r'\\"contentHash\\"\s*:\s*\\"([A-Za-z0-9_-]{8,128})\\"',
                    r'\\"content_hash\\"\s*:\s*\\"([A-Za-z0-9_-]{8,128})\\"',
                    r'contentHash["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{8,128})["\']',
                    r'content_hash["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{8,128})["\']',
                ):
                    match = re.search(pattern, text, re.I)
                    if match:
                        return match.group(1)
        return ""

    @staticmethod
    def _extract_content_manifest_url(
        html: str,
        initial_data: dict[str, Any] | None = None,
        base_url: str = "https://www.paypal.com/checkoutweb/signup",
    ) -> str:
        """Extract weasley content-manifest URL from signup HTML/initial data."""
        initial_data = initial_data or {}
        value = PayPalFlow._find_first_recursive(
            initial_data,
            {"contentManifestUrl", "content_manifest_url"},
            lambda item: isinstance(item, str) and "content-manifest" in item,
        )
        if isinstance(value, str) and value:
            return urllib.parse.urljoin(base_url, html_lib.unescape(value).replace("\\/", "/"))

        value = PayPalFlow._find_first_recursive(
            initial_data,
            {"contentManifest", "content_manifest"},
            lambda item: isinstance(item, str) and "content-manifest" in item,
        )
        if isinstance(value, str) and value:
            cdn_host = (
                ((initial_data.get("geo") or {}).get("cdnHostName"))
                or "www.paypalobjects.com"
            )
            manifest_base = f"https://{cdn_host}/checkoutweb/release/weasley/"
            return urllib.parse.urljoin(manifest_base, html_lib.unescape(value).replace("\\/", "/"))

        for text in PayPalFlow._metadata_search_texts(html or ""):
            match = re.search(
                r'https?:\\?/\\?/[^"\']+/checkoutweb/release/weasley/content-manifest\.[A-Za-z0-9_-]+\.json',
                text,
                re.I,
            )
            if match:
                return html_lib.unescape(match.group(0)).replace("\\/", "/")
            match = re.search(
                r'["\']([^"\']*content-manifest\.[A-Za-z0-9_-]+\.json)["\']',
                text,
                re.I,
            )
            if match:
                raw = html_lib.unescape(match.group(1)).replace("\\/", "/")
                if raw.startswith("//"):
                    raw = "https:" + raw
                if raw.startswith("http"):
                    return raw
                return urllib.parse.urljoin(
                    "https://www.paypalobjects.com/checkoutweb/release/weasley/",
                    raw,
                )
        return ""

    def _content_manifest_key_candidates(self) -> list[str]:
        country = self._content_country().upper()
        lang = self._content_lang().lower()
        candidates = [
            f"{country}_{lang}",
            f"{country}_{lang.split('-', 1)[0]}",
        ]
        if country == "BR":
            candidates.append("BR_pt")
        candidates.extend([
            f"{country}_en",
            f"Base_{lang}",
            "Base_en",
        ])
        seen: set[str] = set()
        return [key for key in candidates if not (key in seen or seen.add(key))]

    def _apply_signup_content_manifest(self, manifest_data, source_url: str = "") -> bool:
        """Apply signupTerms hash from weasley content-manifest JSON."""
        if isinstance(manifest_data, str):
            try:
                manifest_data = json.loads(manifest_data)
            except Exception:
                return False
        if not isinstance(manifest_data, dict):
            return False

        selected_key = ""
        selected_hash = ""
        for key in self._content_manifest_key_candidates():
            value = manifest_data.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value):
                selected_key = key
                selected_hash = value
                break
        if not selected_hash:
            return False

        self.state.content_manifest_key = selected_key
        self.state.content_hash = selected_hash
        self.state.content_identifier = self._content_identifier_from_hash(selected_hash)
        try:
            payload = {
                "manifest_url": source_url,
                "manifest_key": selected_key,
                "content_hash": self.state.content_hash,
                "content_identifier": self.state.content_identifier,
            }
            for path in self._signup_content_manifest_cache_paths():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception:
            pass
        logger.info(
            "Content metadata found in content manifest: key={} hash={} identifier={}",
            selected_key,
            self.state.content_hash,
            self.state.content_identifier,
        )
        return True

    def _fetch_signup_content_manifest_metadata(self, manifest_url: str, referer: str = "") -> bool:
        """Fetch weasley content-manifest and resolve signupTerms contentIdentifier."""
        manifest_url = (manifest_url or "").strip()
        if not manifest_url or not urllib.parse.urlparse(manifest_url).scheme.startswith("http"):
            return False
        self.state.content_manifest_url = manifest_url
        try:
            resp = self.session.get(
                manifest_url,
                headers={
                    **self._browser_headers(accept="*/*"),
                    "Origin": "https://www.paypal.com",
                    "Referer": referer or "https://www.paypal.com/",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "cross-site"
                    if "paypalobjects.com" in manifest_url
                    else "same-origin",
                },
            )
            if resp.status_code != 200:
                logger.debug(
                    "Signup content manifest fetch returned status={} url={}",
                    resp.status_code,
                    manifest_url[:160],
                )
                return False
            try:
                manifest_data = resp.json()
            except Exception:
                manifest_data = json.loads(resp.text or "{}")
            return self._apply_signup_content_manifest(manifest_data, manifest_url)
        except Exception as e:
            logger.debug("Signup content manifest fetch failed {}: {}", manifest_url[:160], e)
            return False

    def _apply_signup_content_metadata(self, html: str) -> None:
        """Refresh contentHash/contentIdentifier from the loaded signup app."""
        country = self._content_country()
        lang = self._content_lang()
        initial_data = self._extract_window_initial_data(html)
        manifest_url = self._extract_content_manifest_url(html, initial_data)
        if manifest_url:
            self.state.content_manifest_url = manifest_url
        content_hash = self._extract_content_hash(
            html,
            initial_data,
            include_generic_content_hash=True,
        )
        if content_hash:
            self.state.content_hash = content_hash
            logger.info(f"Content hash: {self.state.content_hash}")

        content_identifier = self._extract_content_identifier(
            html,
            country,
            lang,
            initial_data,
        )
        has_signup_metadata_signal = bool(content_hash) or bool(manifest_url) or any(
            "signupTerms" in text for text in self._metadata_search_texts(html or "")
        )
        if (
            not has_signup_metadata_signal
            and self._is_short_content_identifier_value(content_identifier)
        ):
            # This can be a DataDome/authchallenge shell or a redirect/error
            # page.  Do not replace a previously fresh identifier with a short
            # fallback merely because the loaded document had no signup app
            # metadata at all.
            logger.debug(
                "Loaded signup document did not contain content metadata; "
                "preserving contentIdentifier={}",
                self.state.content_identifier or "<missing>",
            )
            return
        if (
            (content_hash or self.state.content_hash)
            and content_identifier.endswith(":compliance.signupTerms")
            and (content_hash or self.state.content_hash) not in content_identifier
        ):
            content_identifier = f"{country}:{lang}:{content_hash or self.state.content_hash}:compliance.signupTerms"
        elif self._is_short_content_identifier_value(content_identifier):
            # Do not warn here yet: the metadata often lives in linked JS chunks
            # or in weasley content-manifest rather than the HTML shell.
            # Callers fetch the manifest/scan assets and only warn if the hash
            # is still unavailable afterwards.
            if self.state.content_identifier and not self._content_metadata_is_short():
                logger.debug(
                    "Signup document only exposed a short contentIdentifier; "
                    "preserving previous fresh identifier={}",
                    self.state.content_identifier,
                )
                return
            logger.debug(
                "Signup contentIdentifier is short in HTML shell; content manifest "
                "and linked assets will be checked before submission."
            )
        self.state.content_identifier = content_identifier
        logger.info(f"Content identifier: {self.state.content_identifier}")

    def _content_metadata_is_short(self) -> bool:
        return self._is_short_content_identifier_value(self.state.content_identifier or "")

    def _content_metadata_is_unresolved(self) -> bool:
        return not self.state.content_identifier or self._content_metadata_is_short()

    @staticmethod
    def _project_cache_dir() -> Path:
        return Path(__file__).resolve().parents[1] / "cache"

    def _signup_content_manifest_cache_paths(self) -> list[Path]:
        paths = [
            Path("/tmp/paypal_signup_content_manifest_last.json"),
            self._project_cache_dir() / "paypal_signup_content_manifest_last.json",
        ]
        configured = (os.getenv("PAYPAL_SIGNUP_CONTENT_CACHE") or "").strip()
        if configured:
            paths.append(Path(configured).expanduser())
        seen: set[str] = set()
        unique: list[Path] = []
        for path in paths:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _signup_content_asset_cache_paths(self) -> list[Path]:
        return [
            Path("/tmp/paypal_signup_metadata_asset_last.json"),
            self._project_cache_dir() / "paypal_signup_metadata_asset_last.json",
        ]

    @staticmethod
    def _configured_signup_content_manifest_url() -> str:
        return (
            os.getenv("PAYPAL_SIGNUP_CONTENT_MANIFEST_URL")
            or os.getenv("PAYPAL_CONTENT_MANIFEST_URL")
            # Last known checkoutweb/weasley release from the verified Roxy flow.
            # It is only used when the live signup document is an authchallenge
            # shell and therefore cannot expose its own `content` manifest field.
            or "https://www.paypalobjects.com/checkoutweb/release/weasley/content-manifest.269d408d25fd72bcea4047a79fb8ff61.json"
        ).strip()

    def _apply_configured_or_cached_signup_content_metadata(self) -> bool:
        """Use explicit/cache metadata when the live signup HTML is a challenge shell."""
        country = self._content_country()
        lang = self._content_lang()

        configured_identifier = (
            os.getenv("PAYPAL_SIGNUP_CONTENT_IDENTIFIER")
            or os.getenv("PAYPAL_CONTENT_IDENTIFIER")
            or ""
        ).strip()
        configured_hash = (
            os.getenv("PAYPAL_SIGNUP_CONTENT_HASH")
            or os.getenv("PAYPAL_CONTENT_HASH")
            or ""
        ).strip()
        if configured_identifier and not self._is_short_content_identifier_value(configured_identifier):
            self.state.content_identifier = configured_identifier
            embedded_hash = self._content_identifier_hash(configured_identifier)
            if embedded_hash:
                self.state.content_hash = embedded_hash
            logger.info("Content metadata loaded from environment identifier={}", configured_identifier)
            return True
        if configured_hash:
            self.state.content_hash = configured_hash
            self.state.content_identifier = self._content_identifier_from_hash(configured_hash)
            logger.info("Content metadata loaded from environment hash={}", configured_hash)
            return True

        cache_paths = [
            *self._signup_content_manifest_cache_paths(),
            *self._signup_content_asset_cache_paths(),
        ]
        for path in cache_paths:
            try:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            cached_identifier = str(data.get("content_identifier") or "").strip()
            cached_hash = str(data.get("content_hash") or "").strip()
            cached_key = str(data.get("manifest_key") or "").strip().upper()
            cached_manifest_url = str(data.get("manifest_url") or "").strip()
            expected_keys = {
                f"{country}_{lang}".upper(),
                f"{country}-{lang}".upper(),
                f"{country}:{lang}".upper(),
            }
            if cached_key and cached_key not in expected_keys:
                continue
            if cached_identifier and not self._is_short_content_identifier_value(cached_identifier):
                embedded_hash = self._content_identifier_hash(cached_identifier)
                if embedded_hash:
                    cached_hash = embedded_hash
                self.state.content_identifier = cached_identifier
                self.state.content_hash = cached_hash or self.state.content_hash
                self.state.content_manifest_key = cached_key or self.state.content_manifest_key
                if cached_manifest_url.startswith("http"):
                    self.state.content_manifest_url = cached_manifest_url
                logger.info(
                    "Content metadata restored from cache {} identifier={}",
                    path,
                    self.state.content_identifier,
                )
                return True
            if cached_hash:
                self.state.content_hash = cached_hash
                self.state.content_identifier = self._content_identifier_from_hash(cached_hash)
                self.state.content_manifest_key = cached_key or self.state.content_manifest_key
                if cached_manifest_url.startswith("http"):
                    self.state.content_manifest_url = cached_manifest_url
                logger.info(
                    "Content metadata restored from cache {} hash={}",
                    path,
                    self.state.content_hash,
                )
                return True
        return False

    def _scan_signup_assets_for_content_metadata(self, html: str, base_url: str) -> bool:
        """Search linked PayPal JS chunks for signupTerms content metadata."""
        country = self._content_country()
        lang = self._content_lang()
        script_urls: list[str] = []

        def add_asset_url(raw_url: str) -> None:
            if not raw_url:
                return
            url = urllib.parse.urljoin(base_url, html_lib.unescape(raw_url).replace("\\/", "/"))
            host = urllib.parse.urlparse(url).netloc.lower()
            if not (host.endswith("paypal.com") or host.endswith("paypalobjects.com")):
                return
            if not re.search(r'\.(?:js|mjs)(?:\?|$)', url, re.I):
                return
            if url not in script_urls:
                script_urls.append(url)

        for attr in ("src", "href"):
            for raw in re.findall(rf'\b{attr}=["\']([^"\']+)["\']', html or "", re.I):
                add_asset_url(raw)
        for raw in re.findall(r'["\']([^"\']*(?:/_next/static/|/checkoutweb/|/web/res/)[^"\']+?\.m?js(?:\?[^"\']*)?)["\']', html or "", re.I):
            add_asset_url(raw)

        # Stable order: app-specific chunks first, then framework/runtime chunks.
        script_urls.sort(
            key=lambda u: (
                0 if re.search(r"signup|weasley|onboard|checkout", u, re.I) else 1,
                u,
            )
        )

        for script_url in script_urls[:140]:
            try:
                resp = self.session.get(
                    script_url,
                    headers={
                        **self._browser_headers(accept="*/*"),
                        "Referer": base_url,
                        "Sec-Fetch-Dest": "script",
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Site": "cross-site"
                        if "paypalobjects.com" in script_url
                        else "same-origin",
                    },
                )
                if resp.status_code != 200:
                    continue
                text = resp.text or ""
                if "signupTerms" not in text and "contentHash" not in text:
                    continue
                content_hash = self._extract_content_hash(text)
                content_identifier = self._extract_content_identifier(
                    text,
                    country,
                    lang,
                )
                if content_hash:
                    self.state.content_hash = content_hash
                if (
                    self._is_short_content_identifier_value(content_identifier)
                    and (content_hash or self.state.content_hash)
                ):
                    content_identifier = (
                        f"{country}:{lang}:{content_hash or self.state.content_hash}:compliance.signupTerms"
                    )
                if not self._is_short_content_identifier_value(content_identifier):
                    self.state.content_identifier = content_identifier
                    embedded_hash = self._content_identifier_hash(content_identifier)
                    if embedded_hash:
                        self.state.content_hash = embedded_hash
                    try:
                        payload = {
                            "script_url": script_url,
                            "content_hash": self.state.content_hash,
                            "content_identifier": self.state.content_identifier,
                        }
                        for path in self._signup_content_asset_cache_paths():
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_text(
                                json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8",
                            )
                    except Exception:
                        pass
                    logger.info(
                        "Content metadata found in asset: hash={} identifier={}",
                        self.state.content_hash or "<missing>",
                        self.state.content_identifier,
                    )
                    return True
            except Exception as e:
                logger.debug("Signup metadata asset scan failed {}: {}", script_url[:140], e)
        return False

    def _refresh_signup_content_metadata(self, referer: str = "") -> bool:
        """Reload checkoutweb/signup and refresh dynamic compliance metadata."""
        if not self.state.ec_token:
            return False
        signup_url = self.state.signup_url or self._build_signup_url()
        self.state.signup_url = signup_url
        try:
            resp = self.session.get(
                signup_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Referer": referer or self.state.signup_url or "https://www.paypal.com/",
                    "Upgrade-Insecure-Requests": "1",
                    "Cache-Control": "max-age=0",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                },
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("Location", "")
                if location:
                    location = urllib.parse.urljoin(signup_url, location)
                    if "/checkoutweb/signup" in location:
                        self.state.signup_url = location
                    resp = self.session.get(
                        location,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Referer": signup_url,
                            "Upgrade-Insecure-Requests": "1",
                            "Cache-Control": "max-age=0",
                            "Sec-Fetch-Site": "same-origin",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-User": "?1",
                            "Sec-Fetch-Dest": "document",
                        },
                    )
            if looks_like_paypal_authchallenge(resp.text):
                logger.info(
                    "Signup metadata refresh returned authchallenge HTML; "
                    "validating challenge before extracting content manifest."
                )
                if self._validate_authchallenge_if_possible(resp.text, str(resp.url or signup_url)):
                    resp = self.session.get(
                        signup_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Referer": str(resp.url or signup_url),
                            "Upgrade-Insecure-Requests": "1",
                            "Cache-Control": "max-age=0",
                            "Sec-Fetch-Site": "same-origin",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-User": "?1",
                            "Sec-Fetch-Dest": "document",
                        },
                    )
                    self._capture_datadome_clientid(resp.text)
            try:
                Path("/tmp/paypal_signup_metadata_last.html").write_text(
                    resp.text or "",
                    encoding="utf-8",
                )
            except Exception:
                pass
            self._apply_signup_content_metadata(resp.text)
            manifest_url = (
                self._extract_content_manifest_url(
                    resp.text,
                    self._extract_window_initial_data(resp.text),
                    str(resp.url or signup_url),
                )
                or self.state.content_manifest_url
            )
            if manifest_url and (
                self._content_metadata_is_unresolved()
                or not self.state.content_manifest_key
            ):
                self._fetch_signup_content_manifest_metadata(
                    manifest_url,
                    referer=str(resp.url or signup_url),
                )
            if self._content_metadata_is_unresolved():
                self._scan_signup_assets_for_content_metadata(resp.text, str(resp.url or signup_url))
            if self._content_metadata_is_unresolved() and self.state.content_hash:
                self.state.content_identifier = self._resolved_content_identifier()
            if self._content_metadata_is_unresolved():
                self._apply_configured_or_cached_signup_content_metadata()
            if self._content_metadata_is_unresolved():
                try:
                    Path("/tmp/paypal_signup_metadata_last.json").write_text(
                        json.dumps(
                            {
                                "signup_url": str(resp.url or signup_url),
                                "status_code": resp.status_code,
                                "content_hash": self.state.content_hash,
                                "content_identifier": self.state.content_identifier,
                                "content_manifest_url": self.state.content_manifest_url,
                                "content_manifest_key": self.state.content_manifest_key,
                                "content_country": self._content_country(),
                                "content_lang": self._content_lang(),
                                "authchallenge_html": looks_like_paypal_authchallenge(resp.text),
                                "html_bytes": len(resp.content or b""),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                logger.warning(
                    "Signup contentIdentifier is still missing contentHash after HTML refresh "
                    "and asset scan; diagnostics saved to /tmp/paypal_signup_metadata_last.json"
                )
            return not self._content_metadata_is_unresolved()
        except Exception as e:
            logger.warning(f"Refreshing signup content metadata failed: {e}")
            return False

    def _ensure_live_signup_content_manifest(self, referer: str = "") -> bool:
        """Always try one live content-manifest request before SignUpNewMember.

        The successful browser flow performs a real
        `checkoutweb/release/weasley/content-manifest.*.json` fetch.  Cached
        contentIdentifier is a useful fallback, but relying on it removes this
        browser-visible fetch and can leave the flow with a stale hash.  This
        helper first uses any manifest URL extracted from the current signup
        page/cache; if missing, it refreshes signup once to discover the URL.
        """
        if self.state.content_manifest_url and not self.state.content_manifest_url.startswith("http"):
            self.state.content_manifest_url = ""

        if self.state.content_manifest_url:
            return self._fetch_signup_content_manifest_metadata(
                self.state.content_manifest_url,
                referer=referer or self.state.signup_url or "https://www.paypal.com/",
            )

        # If cache has the last manifest URL, apply it only to discover the URL;
        # `_fetch_signup_content_manifest_metadata` below still performs the
        # live request and overwrites hash/key with the current response.
        self._apply_configured_or_cached_signup_content_metadata()
        if self.state.content_manifest_url:
            return self._fetch_signup_content_manifest_metadata(
                self.state.content_manifest_url,
                referer=referer or self.state.signup_url or "https://www.paypal.com/",
            )

        self._refresh_signup_content_metadata(referer=referer or self.state.signup_url)
        if self.state.content_manifest_url:
            return self._fetch_signup_content_manifest_metadata(
                self.state.content_manifest_url,
                referer=referer or self.state.signup_url or "https://www.paypal.com/",
            )
        manifest_url = self._configured_signup_content_manifest_url()
        if manifest_url:
            return self._fetch_signup_content_manifest_metadata(
                manifest_url,
                referer=referer or self.state.signup_url or "https://www.paypal.com/",
            )
        return not self._content_metadata_is_unresolved()

    def _build_signup_url(self) -> str:
        """Build the canonical checkoutweb/signup URL used as GraphQL Referer."""
        country = self._profile_country()
        locale = self._profile_locale()
        params: list[tuple[str, str]] = []
        if self.state.ssrt:
            params.append(("ssrt", self.state.ssrt))
        params.extend([
            ("ul", "1"),
            ("modxo_redirect_reason", "guest_user"),
            ("locale.x", locale),
            ("country.x", country),
            ("ba_token", self.ba_token),
            ("token", self.state.ec_token),
            ("rcache", "1"),
            ("cookieBannerVariant", "hidden"),
        ])
        return "https://www.paypal.com/checkoutweb/signup?" + urllib.parse.urlencode(params)

    @staticmethod
    def _extract_onboarding_redirect(rsc_text: str) -> str:
        """Extract onboardingRedirectUrl from Next/RSC server-action response."""
        match = re.search(r'"onboardingRedirectUrl"\s*:\s*"([^"]+)"', rsc_text or "")
        if not match:
            return ""
        return PayPalFlow._unescape_auth_url(match.group(1))

    @staticmethod
    def _find_access_token(value) -> str:
        """Find an accessToken recursively in GraphQL data/errorData."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "accessToken" and isinstance(item, str) and item:
                    return item
                found = PayPalFlow._find_access_token(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = PayPalFlow._find_access_token(item)
                if found:
                    return found
        return ""

    def _sync_euat_token_from_cookie(self) -> str:
        """Refresh SessionState from the cookie jar and return the EUAT token if present."""
        try:
            sync = getattr(self.session, "_sync_state_cookies", None)
            if callable(sync):
                sync()
        except Exception as exc:
            logger.debug("EUAT cookie sync failed: {}", exc)
        return str(getattr(self.state, "euat_token", "") or "")

    def _signup_access_token_candidate(self, signup_result) -> str:
        """Find an access token in the full signup response or EUAT cookie state."""
        response_token = self._find_access_token(signup_result)
        if response_token:
            logger.info("Access token found in SignUpNewMember response; continuing.")
            return response_token
        cookie_token = self._sync_euat_token_from_cookie()
        if cookie_token:
            logger.info("EUAT token found in cookie jar after SignUpNewMember; continuing.")
            return cookie_token
        logger.info("No access token/EUAT found after SignUpNewMember response.")
        return ""

    @staticmethod
    def _unescape_auth_url(value: str) -> str:
        return (
            (value or "")
            .replace("&amp;", "&")
            .replace("&#38;", "&")
            .replace("&#x26;", "&")
            .replace("\\u0026", "&")
            .replace("\\/", "/")
        )

    @staticmethod
    def _first_query_value(url: str, name: str) -> str:
        try:
            return (urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query).get(name) or [""])[0]
        except Exception:
            return ""

    @staticmethod
    def _is_ec_token(token: str) -> bool:
        return bool(re.match(r"^EC-[A-Z0-9]+$", token or "", re.I))

    @staticmethod
    def _graphql_errors(result) -> list[dict[str, Any]]:
        items = result if isinstance(result, list) else [result]
        errors: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("errors"), list):
                errors.extend([err for err in item.get("errors") or [] if isinstance(err, dict)])
        return errors

    @staticmethod
    def _html_attr_value(html: str, attr: str) -> str:
        m = re.search(r'\b' + re.escape(attr) + r'=["\']([^"\']*)', html or "", re.I)
        return html_lib.unescape(m.group(1)) if m else ""

    @staticmethod
    def _extract_auth_fpti(challenge_html: str) -> dict[str, str]:
        m = re.search(r"PAYPAL\.analytics\.setup\(\{data:'([^']+)'", challenge_html or "", re.I)
        if not m:
            return {}
        try:
            return {
                str(k): str(v)
                for k, v in urllib.parse.parse_qsl(m.group(1), keep_blank_values=True)
            }
        except Exception:
            return {}

    @staticmethod
    def _authchallenge_captcha_type(challenge_html: str) -> str:
        return (PayPalFlow._html_attr_value(challenge_html, "data-captcha-type") or "").strip().lower()

    @staticmethod
    def _extract_hcaptcha_passive_iframe_src(challenge_html: str) -> str:
        m = re.search(
            r'<iframe[^>]+src=["\']([^"\']*hcaptcha/hcaptchapassive(?:_eval)?\.html[^"\']*)',
            challenge_html or "",
            re.I,
        )
        return PayPalFlow._unescape_auth_url(m.group(1)) if m else ""

    @staticmethod
    def _is_hcaptcha_passive_challenge(challenge_html: str) -> bool:
        captcha_type = PayPalFlow._authchallenge_captcha_type(challenge_html)
        return "hcaptchapassive" in captcha_type or bool(
            PayPalFlow._extract_hcaptcha_passive_iframe_src(challenge_html)
        )

    @staticmethod
    def _hcaptcha_passive_frontend_skip_enabled() -> bool:
        value = os.getenv("PAYPAL_HCAPTCHA_PASSIVE_FRONTEND_SKIP", "1").strip().lower()
        return value not in {"0", "false", "no", "off", "disable", "disabled"}

    @staticmethod
    def _extract_hcaptcha_site_key(challenge_html: str, iframe_src: str = "") -> str:
        candidates = [
            PayPalFlow._first_query_value(iframe_src, "siteKey"),
            PayPalFlow._first_query_value(iframe_src, "sitekey"),
            PayPalFlow._html_attr_value(challenge_html, "data-sitekey"),
            PayPalFlow._html_attr_value(challenge_html, "data-site-key"),
        ]
        for value in candidates:
            if value:
                return value
        m = re.search(r'\bsiteKey=([0-9a-f-]{20,})', challenge_html or "", re.I)
        return html_lib.unescape(m.group(1)) if m else ""

    @staticmethod
    def _extract_hcaptcha_iframe_src(challenge_html: str) -> str:
        """Extract any hCaptcha iframe source from an authchallenge document."""
        for pattern in (
            r'<iframe[^>]+src=["\']([^"\']*hcaptcha[^"\']*)',
            r'<iframe[^>]+src=["\']([^"\']*hcaptcha\.com[^"\']*)',
        ):
            m = re.search(pattern, challenge_html or "", re.I)
            if m:
                return PayPalFlow._unescape_auth_url(m.group(1))
        return ""

    @staticmethod
    def _extract_hcaptcha_rqdata(challenge_html: str, iframe_src: str = "") -> str:
        """Best-effort extraction for enterprise hCaptcha rqdata payloads."""
        candidates = [
            PayPalFlow._first_query_value(iframe_src, "rqdata"),
            PayPalFlow._first_query_value(iframe_src, "rqData"),
            PayPalFlow._html_attr_value(challenge_html, "data-rqdata"),
            PayPalFlow._html_attr_value(challenge_html, "data-rqData"),
            PayPalFlow._html_attr_value(challenge_html, "data-hcaptcha-rqdata"),
        ]
        for value in candidates:
            if value:
                return value
        m = re.search(r'\brqdata["\']?\s*[:=]\s*["\']([^"\']+)', challenge_html or "", re.I)
        return html_lib.unescape(m.group(1)) if m else ""

    def _authchallenge_session_cookie_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            for cookie in self.session.client.cookies.jar:
                out[str(cookie.name)] = str(cookie.value)
        except Exception:
            pass
        return out

    def _paypal_auth_logclientdata(
        self,
        *,
        challenge_html: str,
        csrf: str,
        session_id: str,
        ec_token: str,
        captcha_state: str,
        signup_url: str,
    ) -> None:
        """Replay authchallenge captcha-state telemetry seen in browser traces."""
        fpti = self._extract_auth_fpti(challenge_html)
        if not fpti:
            fpti = {
                "pgrp": "main:authchallenge::checkoutweb:signup",
                "page": "main:authchallenge::checkoutweb:signup",
                "comp": "checkoutuinodeweb",
                "tsrce": "xorouternodeweb",
                "pgtf": "Nodejs",
                "s": "ci",
                "env": "live",
                "pgst": str(int(time.time() * 1000)),
                "calc": "".join(random.choices("0123456789abcdef", k=13)),
                "csci": "".join(random.choices("0123456789abcdef", k=32)),
                "nsid": session_id,
                "rsta": self._profile_locale(),
                "ccpg": self._profile_country(),
            }
        profile = self.state.browser_profile or {}
        fpti.update({
            "pgrp": fpti.get("pgrp") or "main:authchallenge::checkoutweb:signup",
            "page": fpti.get("page") or "main:authchallenge::checkoutweb:signup",
            "comp": fpti.get("comp") or "checkoutuinodeweb",
            "tsrce": fpti.get("tsrce") or "xorouternodeweb",
            "flnm": "Weasley",
            "fltk": ec_token,
            "captchaState": captcha_state,
            "nsid": fpti.get("nsid") or session_id,
            "rsta": fpti.get("rsta") or self._profile_locale(),
            "ccpg": fpti.get("ccpg") or self._profile_country(),
        })
        fpti.setdefault("g", str(profile.get("timezone_offset_minutes", 180)))
        if captcha_state != "CLIENT_SIDE_RECAPTCHAV3_SERVED":
            fpti.setdefault("message", "")
        if captcha_state == "CLIENT_SIDE_PPCAPTCHA_SOLVED":
            fpti.setdefault("adsCaptcha", "explicit")

        body = {"fpti": fpti, "_csrf": csrf, "_sessionID": session_id}
        headers = {
            **self._browser_headers(accept="*/*", content_type="application/json;charset=UTF-8"),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        try:
            resp = self.session.post(
                "https://www.paypal.com/auth/logclientdata",
                json=body,
                headers=headers,
                timeout=20,
            )
            logger.info("auth logclientdata {} status={}", captcha_state, resp.status_code)
        except Exception as e:
            logger.debug("auth logclientdata {} soft-failed: {}", captcha_state, e)

    def _authchallenge_frontend_disable_states(self, challenge_html: str) -> list[str]:
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        lowered = (challenge_html or "").lower()
        if "hcaptchapassive" in captcha_type or self._extract_hcaptcha_passive_iframe_src(challenge_html):
            return [
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_SERVED",
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_SCRIPT_ONLOAD",
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_JS_LOADED",
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_SOLVED",
                "CLIENT_SIDE_PPCAPTCHA_SOLVED",
            ]
        if captcha_type == "recaptchav3" or "grcv3" in lowered or "recaptcha_v3" in lowered:
            return [
                "CLIENT_SIDE_RECAPTCHAV3_SERVED",
                "CLIENT_SIDE_RECAPTCHAV3_ENTERPRISE_API_JS_LOADED",
                "CLIENT_SIDE_RECAPTCHAV3_SOLVED",
                "CLIENT_SIDE_PPCAPTCHA_SOLVED",
            ]
        if captcha_type == "recaptcha" or "recaptcha" in lowered:
            return [
                "CLIENT_SIDE_RECAPTCHA_SERVED",
                "CLIENT_SIDE_RECAPTCHA_ENTERPRISE_API_JS_LOADED",
                "CLIENT_SIDE_PPCAPTCHA_SOLVED",
            ]
        if captcha_type == "hcaptcha" or "hcaptcha" in lowered:
            return [
                "CLIENT_SIDE_HCAPTCHA_SERVED",
                "CLIENT_SIDE_PPCAPTCHA_SOLVED",
            ]
        return ["CLIENT_SIDE_PPCAPTCHA_SOLVED"]

    def _post_frontend_disable_graphql_challenge_form(
        self,
        *,
        url: str,
        fields: dict[str, str],
        challenge_html: str,
        signup_url: str,
        fake_token: str,
    ):
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        lowered = (challenge_html or "").lower()
        now = int(time.time() * 1000)

        jse = self._html_attr_value(challenge_html, "data-jse")
        if jse:
            fields["jse"] = jse

        if "hcaptchapassive" in captcha_type or self._extract_hcaptcha_passive_iframe_src(challenge_html):
            fields["hcaptchaToken"] = fake_token
            iframe_src = self._extract_hcaptcha_passive_iframe_src(challenge_html)
            if iframe_src:
                iframe_src = urllib.parse.urljoin("https://www.paypal.com/", iframe_src)
            site_key = self._extract_hcaptcha_site_key(challenge_html, iframe_src)
            if site_key:
                fields.setdefault("publicKey", site_key)
            fields.setdefault("hcaptcha_passive_eval_start_time_utc", str(now - random.randint(5200, 7400)))
            fields.setdefault("hcaptcha_passive_render_start_time_utc", str(now - random.randint(4500, 6500)))
            fields.setdefault("hcaptcha_passive_render_end_time_utc", str(now - random.randint(300, 1200)))
            fields.setdefault("hcaptcha_passive_verification_time_utc", str(now))
        elif captcha_type == "recaptchav3" or "grcv3" in lowered or "recaptcha_v3" in lowered:
            fields["grcV3EntToken"] = fake_token
        elif captcha_type == "hcaptcha" or "hcaptcha" in lowered:
            fields["hcaptcha"] = fake_token
            fields.setdefault("hcaptcha_render_start_time_utc", str(now - random.randint(4500, 6500)))
            fields.setdefault("hcaptcha_render_end_time_utc", str(now - random.randint(300, 1200)))
            fields.setdefault("hcaptcha_verification_time_utc", str(now))
        else:
            fields["recaptcha"] = fake_token

        headers = {
            **self._browser_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
            ),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        logger.info(
            "frontend_disable submitting authchallenge form action={} fields={}",
            url,
            sorted(k for k in fields.keys() if k not in {"hcaptchaToken", "hcaptcha", "recaptcha", "grcV3EntToken"}),
        )
        resp = self.session.post(url, data=fields, headers=headers, timeout=60)
        text = resp.text or ""
        try:
            Path("/tmp/paypal_authchallenge_frontend_disable_form_last.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "status_code": resp.status_code,
                        "content_type": resp.headers.get("content-type", ""),
                        "fields": sanitize_for_log(fields),
                        "response_head": text[:3000],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        if looks_like_paypal_authchallenge(text):
            logger.warning(
                "frontend_disable authchallenge form returned another challenge head={}",
                text[:700],
            )
            return False
        try:
            return resp.json()
        except ValueError:
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return json.loads(stripped)
            return resp.status_code in {200, 202, 204, 302, 303}

    def _post_frontend_disable_verifyhcaptchapassive_fake(
        self,
        challenge_html: str,
        signup_url: str,
        *,
        fake_token: str = "frontend-hcaptcha-disabled",
        force_synthetic: bool = False,
    ) -> bool:
        """Replay the browser's passive hCaptcha verify packet before close.

        Roxy's successful flow sends `/auth/verifyhcaptchapassive` for passive
        challenges.  In frontend-disable mode PayPalSession answers the endpoint
        locally, but the request shape is still recorded and cookies/CH/header
        context stay aligned with the browser path.
        """
        fields = self._extract_input_values(challenge_html)
        csrf = fields.get("_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        session_id = (
            fields.get("_sessionID")
            or self._html_attr_value(challenge_html, "data-sessionid")
            or self.state.nsid
        )
        iframe_src = self._extract_hcaptcha_passive_iframe_src(challenge_html)
        if iframe_src:
            iframe_src = urllib.parse.urljoin("https://www.paypal.com/", iframe_src)
        site_key = self._extract_hcaptcha_site_key(challenge_html, iframe_src)
        now = int(time.time() * 1000)
        render_start = now - random.randint(7000, 11000)
        render_end = now - random.randint(300, 1300)
        form = {
            "_csrf": csrf,
            "hcaptcha_passive_eval_start_time_utc": str(render_start - random.randint(250, 900)),
            "hcaptchaToken": fake_token,
            "publicKey": site_key,
            "hcaptcha_passive_render_start_time_utc": str(render_start),
            "hcaptcha_passive_render_end_time_utc": str(render_end),
            "hcaptcha_passive_verification_time_utc": str(now),
            "_sessionID": session_id,
        }
        form = {k: v for k, v in form.items() if v}
        headers = {
            **self._browser_headers(accept="*/*", content_type="application/x-www-form-urlencoded"),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        try:
            resp = self.session.post(
                "https://www.paypal.com/auth/verifyhcaptchapassive",
                data=form,
                headers=headers,
                force_captcha_synthetic=force_synthetic,
                timeout=30,
            )
            ok = resp.status_code in {200, 202, 204, 302, 303}
            logger.info("frontend_disable fake /auth/verifyhcaptchapassive status={} ok={}", resp.status_code, ok)
            try:
                Path("/tmp/paypal_authchallenge_frontend_disable_verifyhcaptchapassive_last.json").write_text(
                    json.dumps(
                        {
                            "status_code": resp.status_code,
                            "content_type": resp.headers.get("content-type", ""),
                            "fields": sanitize_for_log(form),
                            "response_head": (resp.text or "")[:1000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            return ok
        except Exception as e:
            logger.debug("frontend_disable fake verifyhcaptchapassive soft-failed: {}", e)
            return False

    def _post_frontend_disable_validatecaptcha_fake(
        self,
        challenge_html: str,
        signup_url: str,
        *,
        force_synthetic: bool = False,
    ):
        action = self._extract_form_action(challenge_html) or "/auth/validatecaptcha"
        url = urllib.parse.urljoin("https://www.paypal.com/", self._unescape_auth_url(action))
        path = urllib.parse.urlparse(url).path.lower()
        if path not in {"/auth/validatecaptcha", "/auth/verifyhcaptchapassive"}:
            if path.startswith("/graphql"):
                if (
                    "hcaptchapassive" in self._authchallenge_captcha_type(challenge_html)
                    or self._extract_hcaptcha_passive_iframe_src(challenge_html)
                ):
                    # Passive hCaptcha's browser path verifies via
                    # /auth/verifyhcaptchapassive and then lets the app retry
                    # the original operation.  Submitting the GraphQL form with
                    # a dummy hcaptcha token escalates to interactive hcaptcha.
                    return self._post_frontend_disable_verifyhcaptchapassive_fake(
                        challenge_html,
                        signup_url,
                        force_synthetic=force_synthetic,
                    )
                return self._post_frontend_disable_graphql_challenge_form(
                    url=url,
                    fields=self._extract_input_values(challenge_html),
                    challenge_html=challenge_html,
                    signup_url=signup_url,
                    fake_token="frontend-hcaptcha-disabled",
                )
            logger.debug("frontend_disable skips unsupported form action {}", url)
            return False
        if path == "/auth/verifyhcaptchapassive":
            return self._post_frontend_disable_verifyhcaptchapassive_fake(
                challenge_html,
                signup_url,
                force_synthetic=force_synthetic,
            )

        fields = self._extract_input_values(challenge_html)
        jse = self._html_attr_value(challenge_html, "data-jse")
        if jse:
            fields["jse"] = jse

        now = int(time.time() * 1000)
        fake_token = "frontend-hcaptcha-disabled"
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        lowered = (challenge_html or "").lower()
        if "hcaptchapassive" in captcha_type or self._extract_hcaptcha_passive_iframe_src(challenge_html):
            if path != "/auth/verifyhcaptchapassive":
                self._post_frontend_disable_verifyhcaptchapassive_fake(
                    challenge_html,
                    signup_url,
                    fake_token=fake_token,
                    force_synthetic=force_synthetic,
                )
            fields.setdefault("hcaptchaToken", fake_token)
            iframe_src = self._extract_hcaptcha_passive_iframe_src(challenge_html)
            if iframe_src:
                iframe_src = urllib.parse.urljoin("https://www.paypal.com/", iframe_src)
            site_key = self._extract_hcaptcha_site_key(challenge_html, iframe_src)
            if site_key:
                fields.setdefault("publicKey", site_key)
            fields.setdefault("hcaptcha_passive_eval_start_time_utc", str(now - random.randint(5200, 7400)))
            fields.setdefault("hcaptcha_passive_render_start_time_utc", str(now - random.randint(4500, 6500)))
            fields.setdefault("hcaptcha_passive_render_end_time_utc", str(now - random.randint(300, 1200)))
            fields.setdefault("hcaptcha_passive_verification_time_utc", str(now))
        elif captcha_type == "recaptchav3" or "grcv3" in lowered or "recaptcha_v3" in lowered:
            fields.setdefault("grcV3EntToken", fake_token)
        elif captcha_type == "hcaptcha" or "hcaptcha" in lowered:
            fields.setdefault("hcaptcha", fake_token)
            fields.setdefault("hcaptcha_render_start_time_utc", str(now - random.randint(4500, 6500)))
            fields.setdefault("hcaptcha_render_end_time_utc", str(now - random.randint(300, 1200)))
            fields.setdefault("hcaptcha_verification_time_utc", str(now))
        else:
            fields.setdefault("recaptcha", fake_token)

        headers = {
            **self._browser_headers(accept="*/*", content_type="application/x-www-form-urlencoded"),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "X-Requested-With": "fetch",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        resp = self.session.post(
            url,
            data=fields,
            headers=headers,
            force_captcha_synthetic=force_synthetic,
            timeout=30,
        )
        ok = resp.status_code in {200, 202, 204, 302, 303}
        logger.info(
            "frontend_disable fake {} status={} ok={}",
            path,
            resp.status_code,
            ok,
        )
        return ok

    def _frontend_disable_authchallenge_close(
        self,
        challenge_html: str,
        signup_url: str,
        *,
        force_synthetic: bool = False,
    ) -> bool | dict[str, Any] | list[Any]:
        """Backend equivalent of the console `paypal_hcaptcha_console_disable_v2.js`.

        It does not ask CapSolver/hCaptcha/Google for a token.  Instead it
        mirrors the browser patch: captcha submission endpoints are locally
        answered by PayPalSession, the PPCAPTCHA_SOLVED telemetry is emitted,
        and the caller retries the original PayPal operation with cookies kept.
        """
        if strict_browser_risk_enabled() and not self._synthetic_captcha_allowed():
            logger.warning(
                "frontend_disable/fake CAPTCHA close is disabled by strict browser-risk mode."
            )
            return False
        if not force_synthetic and not self._captcha_frontend_disable_enabled():
            return False

        captcha_type = self._authchallenge_captcha_type(challenge_html) or "unknown"
        csrf = self._html_input_value(challenge_html, "_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        session_id = (
            self._html_input_value(challenge_html, "_sessionID")
            or self._html_attr_value(challenge_html, "data-sessionid")
            or self.state.nsid
        )
        ec_token = self.state.ec_token or self._first_query_value(signup_url, "token")
        states = self._authchallenge_frontend_disable_states(challenge_html)

        logger.info(
            "authchallenge type={} -> frontend_disable fake-close states={} forced={}",
            captcha_type,
            ",".join(states),
            force_synthetic,
        )
        self._emit_progress(
            "captcha",
            f"authchallenge {captcha_type} 已用 frontend_disable 关闭 (synthetic close)",
            level="ok",
        )
        if csrf and session_id:
            for state in states:
                self._paypal_auth_logclientdata(
                    challenge_html=challenge_html,
                    csrf=csrf,
                    session_id=session_id,
                    ec_token=ec_token,
                    captcha_state=state,
                    signup_url=signup_url,
                )
        else:
            logger.warning(
                "frontend_disable cannot replay auth logclientdata: csrf={} session_id={}",
                bool(csrf),
                bool(session_id),
            )

        fake_post_result = False
        try:
            fake_post_result = self._post_frontend_disable_validatecaptcha_fake(
                challenge_html,
                signup_url,
                force_synthetic=force_synthetic,
            )
        except Exception as e:
            logger.debug("frontend_disable fake validatecaptcha soft-failed: {}", e)

        self._mark_frontend_captcha_solved(f"authchallenge_{captcha_type}")
        try:
            Path("/tmp/paypal_authchallenge_frontend_disable_last.json").write_text(
                json.dumps(
                    {
                        "mode": self.captcha_bypass_mode,
                        "forced": force_synthetic,
                        "captcha_type": captcha_type,
                        "telemetry_states": states,
                        "csrf": bool(csrf),
                        "session_id": bool(session_id),
                        "fake_post_ok": bool(fake_post_result),
                        "fake_post_result_type": type(fake_post_result).__name__,
                        "signup_url": signup_url,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        if isinstance(fake_post_result, (dict, list)):
            return fake_post_result
        if bool(fake_post_result):
            return True

        if force_synthetic and self._is_hcaptcha_passive_challenge(challenge_html):
            logger.info(
                "frontend_disable forced hcaptchapassive close without backend validate; "
                "retrying original operation"
            )
            return True

        # Some ModXO server-action challenges post the captcha form back to the
        # same /pay URL instead of /auth/validatecaptcha.  The browser console
        # disable script closes the widget and lets the original server-action be
        # retried; there is no separate validatecaptcha packet to send.  Treat a
        # successfully logged frontend-disable sequence as solved so callers can
        # retry the original request instead of falling back to the legacy compact
        # /pay form.
        if csrf and session_id and captcha_type in {"hcaptcha", "recaptcha", "recaptchav3"}:
            logger.info(
                "frontend_disable solved {} without standalone validate endpoint; retrying original operation",
                captcha_type,
            )
            return True
        return False

    def _mint_hcaptcha_passive_token_via_node(
        self,
        *,
        iframe_url: str,
        parent_url: str,
        timeout: int = 60,
    ) -> tuple[str, dict[str, object]]:
        """Use the reference happy-dom helper to mint PayPal hCaptcha passive token."""
        iframe_url = (iframe_url or "").strip()
        if not iframe_url:
            return "", {}

        manual = (
            os.environ.get("PAYPAL_HCAPTCHA_TOKEN")
            or os.environ.get("PPS_PAYPAL_HCAPTCHA_TOKEN")
            or ""
        ).strip()
        if manual:
            logger.info("hcaptchapassive: using pre-supplied backend token len={}", len(manual))
            return manual, {"source": "env"}

        helper_override = (os.getenv("PAYPAL_HCAPTCHA_NODE_HELPER") or "").strip()
        helper_candidates = ([Path(helper_override)] if helper_override else [])
        helper_candidates += [
            Path(__file__).resolve().parents[1] / "tools" / "hcaptcha_passive_node.js",
            Path(__file__).resolve().parents[1] / "ba_fp_helpers" / "hcaptcha_passive_node.js",
            Path(__file__).resolve().parents[1] / "ba_fp_helpers" / "ba_hcaptcha_passive_node.js",
            Path(__file__).with_name("hcaptcha_passive_node.js"),
            Path("/home/nonewhite/Downloads/gpt-plus-pp/botcore/paypal_plus/hcaptcha_passive_node.js"),
        ]
        helper = next((p for p in helper_candidates if p.exists()), None)
        if not helper:
            logger.warning("hcaptchapassive node helper missing")
            return "", {}

        payload = {
            "iframeUrl": iframe_url,
            "parentUrl": parent_url or "https://www.paypal.com/",
            "userAgent": self._profile_user_agent(),
            "browserProfile": self.state.browser_profile or {},
            "screen": self.state.screen or {},
            "viewport": self.state.viewport or {},
            "acceptLanguage": self._browser_headers().get("Accept-Language", ""),
            "timeoutMs": int(max(15, timeout) * 1000),
            "proxy": self.proxy_config.url or "",
        }
        env = os.environ.copy()
        node_paths = [
            p.strip()
            for p in (env.get("NODE_PATH", "") or "").split(os.pathsep)
            if p.strip()
        ]
        node_paths.extend([
            "/home/nonewhite/Downloads/gpt-plus-pp/webui/frontend/node_modules",
            "/home/nonewhite/Downloads/GuJumpgate-v0.2.0/node_modules",
            "/app/webui/frontend/node_modules",
            "/usr/local/lib/node_modules",
        ])
        env["NODE_PATH"] = os.pathsep.join(dict.fromkeys([p for p in node_paths if p]))
        if self.proxy_config.url:
            env["HTTPS_PROXY"] = self.proxy_config.url
            env["HTTP_PROXY"] = self.proxy_config.url
            env["ALL_PROXY"] = self.proxy_config.url

        try:
            proc = subprocess.run(
                [os.environ.get("NODE", "node"), str(helper)],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=max(90, int(timeout) + 30),
                env=env,
                cwd=str(helper.parent),
            )
        except Exception as e:
            logger.warning("hcaptchapassive node helper launch failed: {}", e)
            return "", {}

        stderr = (proc.stderr or "").strip()
        if stderr:
            logger.debug("hcaptchapassive node helper stderr: {}", stderr[-2000:])
        stdout = (proc.stdout or "").strip()
        if not stdout:
            logger.warning("hcaptchapassive node helper produced no stdout rc={}", proc.returncode)
            return "", {}
        try:
            data = json.loads(stdout)
        except Exception as e:
            logger.warning("hcaptchapassive node helper JSON parse failed: {} stdout={}", e, stdout[:500])
            return "", {}

        try:
            Path("/tmp/paypal_hcaptchapassive_node_last.json").write_text(
                json.dumps(
                    {
                        "returncode": proc.returncode,
                        "ok": bool(data.get("token")),
                        "error": data.get("error"),
                        "elapsedMs": data.get("elapsedMs"),
                        "states": data.get("states"),
                        "iframeCount": data.get("iframeCount"),
                        "iframeSrcs": data.get("iframeSrcs"),
                        "token_len": len(str(data.get("token") or "")),
                        "renderData": data.get("renderData") or {},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        token = str(data.get("token") or "").strip()
        if token:
            logger.info("hcaptchapassive node helper token ready len={}", len(token))
            render = data.get("renderData") if isinstance(data.get("renderData"), dict) else {}
            return token, render
        logger.warning(
            "hcaptchapassive node helper no token rc={} error={}",
            proc.returncode,
            data.get("error"),
        )
        return "", {}

    def _mint_hcaptcha_passive_token_via_capsolver(
        self,
        *,
        iframe_url: str,
        parent_url: str,
        site_key: str,
    ) -> tuple[str, dict[str, Any]]:
        _ = (iframe_url, parent_url, site_key)
        logger.warning("CapSolver hcaptchapassive support has been removed; skipping external solver.")
        return "", {}

    def _mint_hcaptcha_passive_token(
        self,
        *,
        iframe_url: str,
        parent_url: str,
        site_key: str,
    ) -> tuple[str, dict[str, Any]]:
        preference = (
            os.getenv("PAYPAL_HCAPTCHA_PASSIVE_SOLVER")
            or os.getenv("PAYPAL_SIGNUP_CAPTCHA_SOLVER")
            or "node"
        ).strip().lower()
        solvers = [
            item.strip().replace("-", "_")
            for item in re.split(r"[,>\s]+", preference)
            if item.strip()
        ] or ["node"]
        seen: set[str] = set()
        for solver in solvers:
            if solver in seen:
                continue
            seen.add(solver)
            if solver in {"capsolver", "cap_solver", "real", "solve", "solver"}:
                if strict_browser_risk_enabled() and not self._env_truthy("PAYPAL_ALLOW_EXTERNAL_CAPTCHA_SOLVER"):
                    logger.warning(
                        "Skipping external hcaptchapassive solver={} in strict browser-risk mode.",
                        solver,
                    )
                    continue
                token, data = self._mint_hcaptcha_passive_token_via_capsolver(
                    iframe_url=iframe_url,
                    parent_url=parent_url,
                    site_key=site_key,
                )
            elif solver in {"node", "local", "happy_dom", "hcaptcha_passive_node"}:
                if strict_browser_risk_enabled():
                    logger.warning(
                        "Skipping local happy-dom hcaptchapassive helper in strict browser-risk mode."
                    )
                    continue
                token, data = self._mint_hcaptcha_passive_token_via_node(
                    iframe_url=iframe_url,
                    parent_url=parent_url,
                    timeout=int(os.getenv("PAYPAL_HCAPTCHA_PASSIVE_NODE_TIMEOUT", "60")),
                )
            else:
                logger.debug("Unknown hcaptchapassive solver preference {}; skipping", solver)
                continue
            if token:
                logger.info("hcaptchapassive solver={} produced token len={}", solver, len(token))
                return token, data
        return "", {}

    def _validate_paypal_hcaptcha_passive(self, challenge_html: str, signup_url: str) -> bool:
        """Submit PayPal `/auth/verifyhcaptchapassive` for hcaptchapassive challenge."""
        csrf = self._html_input_value(challenge_html, "_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        request_id = self._html_input_value(challenge_html, "_requestId")
        hsh = self._html_input_value(challenge_html, "_hash")
        session_id = self._html_input_value(challenge_html, "_sessionID") or self._html_attr_value(challenge_html, "data-sessionid")
        jse = self._html_attr_value(challenge_html, "data-jse")
        iframe_src = self._extract_hcaptcha_passive_iframe_src(challenge_html)
        if iframe_src:
            iframe_src = urllib.parse.urljoin("https://www.paypal.com/", iframe_src)
        site_key = self._extract_hcaptcha_site_key(challenge_html, iframe_src)
        ec_token = self.state.ec_token or self._first_query_value(signup_url, "token")

        if not all([csrf, session_id, iframe_src, site_key]):
            logger.warning(
                "hcaptchapassive fields missing csrf={} request={} hash={} session={} jse={} iframe={} sitekey={}",
                bool(csrf), bool(request_id), bool(hsh), bool(session_id), bool(jse), bool(iframe_src), bool(site_key),
            )
            return False

        for state in (
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_SERVED",
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_SCRIPT_ONLOAD",
            "CLIENT_SIDE_HCAPTCHA_PASSIVE_JS_LOADED",
        ):
            self._paypal_auth_logclientdata(
                challenge_html=challenge_html,
                csrf=csrf,
                session_id=session_id,
                ec_token=ec_token,
                captcha_state=state,
                signup_url=signup_url,
            )

        token, solution = self._mint_hcaptcha_passive_token(
            iframe_url=iframe_src,
            parent_url=signup_url,
            site_key=site_key,
        )
        if not token:
            return False

        for state in ("CLIENT_SIDE_HCAPTCHA_PASSIVE_SOLVED", "CLIENT_SIDE_PPCAPTCHA_SOLVED"):
            self._paypal_auth_logclientdata(
                challenge_html=challenge_html,
                csrf=csrf,
                session_id=session_id,
                ec_token=ec_token,
                captcha_state=state,
                signup_url=signup_url,
            )

        now = int(time.time() * 1000)
        render_start = int(
            solution.get("hcaptchaPassiveRenderStartTime")
            or solution.get("hcaptcha_passive_render_start_time_utc")
            or solution.get("renderStartTime")
            or (now - random.randint(3500, 6500))
        )
        render_end = int(
            solution.get("hcaptchaPassiveRenderEndTime")
            or solution.get("hcaptcha_passive_render_end_time_utc")
            or solution.get("renderEndTime")
            or (now - random.randint(500, 1800))
        )
        verify_ts = int(
            solution.get("hcaptchaPassiveVerificationTime")
            or solution.get("hcaptcha_passive_verification_time_utc")
            or solution.get("verificationTime")
            or now
        )
        form = {
            "_csrf": csrf,
            "hcaptcha_passive_eval_start_time_utc": str(render_start - random.randint(250, 900)),
            "hcaptchaToken": token,
            "publicKey": site_key,
            "hcaptcha_passive_render_start_time_utc": str(render_start),
            "hcaptcha_passive_render_end_time_utc": str(render_end),
            "hcaptcha_passive_verification_time_utc": str(verify_ts),
            "_sessionID": session_id,
        }
        if request_id:
            form["_requestId"] = request_id
        if hsh:
            form["_hash"] = hsh
        if jse:
            form["jse"] = jse
        headers = {
            **self._browser_headers(accept="*/*", content_type="application/x-www-form-urlencoded"),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        try:
            resp = self.session.post(
                "https://www.paypal.com/auth/verifyhcaptchapassive",
                data=form,
                headers=headers,
                disable_captcha_synthetic=True,
                timeout=60,
            )
            text = resp.text or ""
            try:
                Path("/tmp/paypal_verifyhcaptchapassive_last.json").write_text(
                    json.dumps(
                        {
                            "kind": "hcaptchapassive",
                            "status_code": resp.status_code,
                            "content_type": resp.headers.get("content-type", ""),
                            "token_len": len(token),
                            "site_key": site_key,
                            "iframe_src": iframe_src[:500],
                            "response_head": text[:1000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            if resp.status_code in {302, 303} and resp.headers.get("Location"):
                ok = True
            elif resp.status_code in {200, 202, 204}:
                head = text[:800].lower()
                ok = not looks_like_paypal_authchallenge(text) and (
                    not text.strip()
                    or text.lstrip().startswith("{")
                    or "captcha" not in head
                ) and "errors" not in head
            else:
                ok = False
            logger.info(
                "hcaptchapassive verifyhcaptchapassive status={} len={} token_len={} ok={}",
                resp.status_code,
                len(text),
                len(token),
                ok,
            )
            if not ok:
                logger.warning("hcaptchapassive verifyhcaptchapassive reject head={}", text[:700])
                return False

            action = self._extract_form_action(challenge_html) or "/auth/validatecaptcha"
            action_url = urllib.parse.urljoin("https://www.paypal.com/", self._unescape_auth_url(action))
            action_path = urllib.parse.urlparse(action_url).path.lower()
            if action_path != "/auth/validatecaptcha":
                return True

            validate_fields = self._extract_input_values(challenge_html)
            if jse:
                validate_fields["jse"] = jse
            validate_fields.update(
                {
                    "hcaptchaToken": token,
                    "publicKey": site_key,
                    "hcaptcha_passive_eval_start_time_utc": form["hcaptcha_passive_eval_start_time_utc"],
                    "hcaptcha_passive_render_start_time_utc": form["hcaptcha_passive_render_start_time_utc"],
                    "hcaptcha_passive_render_end_time_utc": form["hcaptcha_passive_render_end_time_utc"],
                    "hcaptcha_passive_verification_time_utc": form["hcaptcha_passive_verification_time_utc"],
                }
            )
            validate_resp = self.session.post(
                action_url,
                data=validate_fields,
                headers=headers,
                disable_captcha_synthetic=True,
                timeout=60,
            )
            validate_text = validate_resp.text or ""
            validate_ok = validate_resp.status_code in {200, 202, 204, 302, 303} and not looks_like_paypal_authchallenge(validate_text)
            try:
                Path("/tmp/paypal_hcaptchapassive_validatecaptcha_last.json").write_text(
                    json.dumps(
                        {
                            "status_code": validate_resp.status_code,
                            "content_type": validate_resp.headers.get("content-type", ""),
                            "location": validate_resp.headers.get("Location", "")[:500],
                            "token_len": len(token),
                            "site_key": site_key,
                            "fields": sanitize_for_log(validate_fields),
                            "response_head": validate_text[:1200],
                            "ok": validate_ok,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            logger.info(
                "hcaptchapassive validatecaptcha status={} len={} token_len={} ok={}",
                validate_resp.status_code,
                len(validate_text),
                len(token),
                validate_ok,
            )
            if not validate_ok:
                logger.warning("hcaptchapassive validatecaptcha reject head={}", validate_text[:700])
            return validate_ok
        except Exception as e:
            logger.warning("hcaptchapassive verifyhcaptchapassive soft-failed: {}", e)
            return False

    @staticmethod
    def _extract_recaptcha_site_key(challenge_html: str) -> str:
        candidates = [
            PayPalFlow._html_attr_value(challenge_html, "data-sitekey"),
            PayPalFlow._html_attr_value(challenge_html, "data-site-key"),
            PayPalFlow._html_attr_value(challenge_html, "data-grc-v3-ent-site-key"),
            PayPalFlow._html_attr_value(challenge_html, "grcV3EntSiteKey"),
        ]
        for value in candidates:
            if value:
                return value
        for pattern in (
            r'\bgrcV3EntSiteKey["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{20,})',
            r'\bsitekey["\']?\s*[:=]\s*["\']([A-Za-z0-9_-]{20,})',
            r'recaptcha/(?:enterprise|api2)/anchor[^"\']*[?&]k=([A-Za-z0-9_-]{20,})',
            # PayPalObjects 托管的 v2 页面: recaptcha_v2.html?siteKey=...
            r'recaptcha[^"\'\s<>]*[?&]site[Kk]ey=([A-Za-z0-9_-]{20,})',
        ):
            m = re.search(pattern, challenge_html or "", re.I)
            if m:
                return html_lib.unescape(m.group(1))
        return ""

    @staticmethod
    def _extract_recaptcha_action(challenge_html: str) -> str:
        return (
            PayPalFlow._html_attr_value(challenge_html, "data-policy-based-challenge-action")
            or PayPalFlow._html_attr_value(challenge_html, "data-action")
            or "default"
        )

    def _mint_recaptcha_v3_token_via_capsolver(
        self,
        *,
        challenge_html: str,
        signup_url: str,
    ) -> tuple[str, dict[str, Any]]:
        """本地 Playwright reCAPTCHA 解法 (替代已移除的 CapSolver)。

        优先打开真实 challenge 页 (带协议会话 cookies) 让 widget 自解;
        失败则退 bridge (v3 execute)。返回 (token, solution)。
        """
        from paypal.recaptcha_solver import solve_recaptcha

        site_key = self._extract_recaptcha_site_key(challenge_html)
        action = self._extract_recaptcha_action(challenge_html)
        if not site_key:
            logger.warning("recaptcha site_key 提取失败, 无法本地求解")
            return "", {}
        cookies = []
        try:
            cookies = self.session.export_cookies_for_browser()
        except Exception as e:
            logger.debug("recaptcha cookie export failed: {}", e)
        proxy = str(getattr(self.session, "proxy_url", "") or "")
        env_headless = os.getenv("PAYPAL_RECAPTCHA_HEADLESS", "1").strip().lower() not in {"0", "false", "no"}
        token = solve_recaptcha(
            challenge_html=challenge_html,
            site_key=site_key,
            action=action,
            page_url=signup_url,
            cookies=cookies,
            proxy=proxy,
            headless=env_headless,
            timeout_ms=120000,
        )
        if token:
            logger.info("recaptcha local solve OK token_len={}", len(token))
            return token, {}
        logger.warning("recaptcha local solve 失败 (site_key={} action={})", site_key[:16], action)
        return "", {}

    def _validate_paypal_recaptcha_v3(self, challenge_html: str, signup_url: str) -> bool:
        """Submit PayPal `/auth/validatecaptcha` for reCAPTCHA Enterprise v3."""
        csrf = self._html_input_value(challenge_html, "_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        request_id = self._html_input_value(challenge_html, "_requestId")
        hsh = self._html_input_value(challenge_html, "_hash")
        session_id = self._html_input_value(challenge_html, "_sessionID") or self._html_attr_value(challenge_html, "data-sessionid")
        jse = self._html_attr_value(challenge_html, "data-jse")
        ec_token = self.state.ec_token or self._first_query_value(signup_url, "token")

        if not all([csrf, request_id, hsh, session_id, jse]):
            logger.warning(
                "recaptchav3 fields missing csrf={} request={} hash={} session={} jse={}",
                bool(csrf), bool(request_id), bool(hsh), bool(session_id), bool(jse),
            )
            return False

        for state in (
            "CLIENT_SIDE_RECAPTCHAV3_SERVED",
            "CLIENT_SIDE_RECAPTCHAV3_ENTERPRISE_API_JS_LOADED",
        ):
            self._paypal_auth_logclientdata(
                challenge_html=challenge_html,
                csrf=csrf,
                session_id=session_id,
                ec_token=ec_token,
                captcha_state=state,
                signup_url=signup_url,
            )

        token, solution = self._mint_recaptcha_v3_token_via_capsolver(
            challenge_html=challenge_html,
            signup_url=signup_url,
        )
        if not token:
            return False

        self._paypal_auth_logclientdata(
            challenge_html=challenge_html,
            csrf=csrf,
            session_id=session_id,
            ec_token=ec_token,
            captcha_state="CLIENT_SIDE_RECAPTCHAV3_SOLVED",
            signup_url=signup_url,
        )

        now = int(time.time() * 1000)
        render_start = int(
            (solution.get("renderStartTime") if isinstance(solution, dict) else 0)
            or (now - random.randint(2500, 6000))
        )
        render_end = int(
            (solution.get("renderEndTime") if isinstance(solution, dict) else 0)
            or (now - random.randint(150, 900))
        )
        form = {
            "_csrf": csrf,
            "_requestId": request_id,
            "_hash": hsh,
            "_sessionID": session_id,
            "jse": jse,
            "grcV3EntToken": token,
            "grcV3RenderEndTime": str(render_end),
            "grcV3RenderStartTime": str(render_start),
        }
        site_key = self._extract_recaptcha_site_key(challenge_html)
        if site_key:
            form["publicKey"] = site_key

        headers = {
            **self._browser_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
            ),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        try:
            resp = self.session.post(
                "https://www.paypal.com/auth/validatecaptcha",
                data=form,
                headers=headers,
                disable_captcha_synthetic=True,
                timeout=60,
            )
            text = resp.text or ""
            location = resp.headers.get("Location", "")
            try:
                Path("/tmp/paypal_recaptcha_v3_validatecaptcha_last.json").write_text(
                    json.dumps(
                        {
                            "status_code": resp.status_code,
                            "content_type": resp.headers.get("content-type", ""),
                            "location": location[:500],
                            "token_len": len(token),
                            "site_key": site_key,
                            "response_head": text[:1000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            ok = resp.status_code in {200, 202, 204, 302, 303} and not looks_like_paypal_authchallenge(text)
            logger.info(
                "recaptchav3 validatecaptcha status={} len={} token_len={} location={} ok={}",
                resp.status_code,
                len(text),
                len(token),
                location[:120] or "-",
                ok,
            )
            if not ok:
                logger.warning("recaptchav3 validatecaptcha reject head={}", text[:700])
            return ok
        except Exception as e:
            logger.warning("recaptchav3 validatecaptcha soft-failed: {}", e)
            return False

    def _mint_recaptcha_v2_token_via_capsolver(
        self,
        *,
        challenge_html: str,
        signup_url: str,
    ) -> tuple[str, dict[str, Any]]:
        _ = (challenge_html, signup_url)
        logger.warning("CapSolver reCAPTCHA v2 support has been removed; skipping external solver.")
        return "", {}

    def _submit_paypal_recaptcha_challenge(self, challenge_html: str, signup_url: str, token: str):
        action = self._extract_form_action(challenge_html) or "/auth/validatecaptcha"
        url = urllib.parse.urljoin("https://www.paypal.com/", self._unescape_auth_url(action))
        fields = self._extract_input_values(challenge_html)
        jse = self._html_attr_value(challenge_html, "data-jse")
        if jse:
            fields["jse"] = jse
        site_key = self._extract_recaptcha_site_key(challenge_html)
        now = int(time.time() * 1000)
        fields["recaptcha"] = token
        if site_key:
            fields.setdefault("publicKey", site_key)
        fields.setdefault("grc_render_start_time_utc", str(now - random.randint(9000, 17000)))
        fields.setdefault("grc_render_end_time_utc", str(now - random.randint(3500, 6500)))
        fields.setdefault("grc_verification_time_utc", str(now))

        csrf = fields.get("_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        session_id = fields.get("_sessionID") or self._html_attr_value(challenge_html, "data-sessionid")
        ec_token = self.state.ec_token or self._first_query_value(signup_url, "token")
        if csrf and session_id:
            for state in (
                "CLIENT_SIDE_RECAPTCHA_SERVED",
                "CLIENT_SIDE_RECAPTCHA_ENTERPRISE_API_JS_LOADED",
                "CLIENT_SIDE_RECAPTCHA_SOLVED",
            ):
                self._paypal_auth_logclientdata(
                    challenge_html=challenge_html,
                    csrf=csrf,
                    session_id=session_id,
                    ec_token=ec_token,
                    captcha_state=state,
                    signup_url=signup_url,
                )

        headers = {
            **self._browser_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
            ),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        logger.info(
            "Posting solved recaptcha action={} fields={}",
            url,
            sorted(k for k in fields.keys() if k != "recaptcha"),
        )
        resp = self.session.post(
            url,
            data=fields,
            headers=headers,
            disable_captcha_synthetic=True,
            timeout=60,
        )
        text = resp.text or ""
        try:
            Path("/tmp/paypal_recaptcha_submit_last.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "status_code": resp.status_code,
                        "paypal_debug_id": resp.headers.get("paypal-debug-id", ""),
                        "content_type": resp.headers.get("content-type", ""),
                        "location": resp.headers.get("Location", "")[:500],
                        "token_len": len(token),
                        "site_key": site_key,
                        "fields": sanitize_for_log(fields),
                        "response_head": text[:3000],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
        if looks_like_paypal_authchallenge(text):
            logger.warning(
                "recaptcha submit returned another authchallenge paypal_debug_id={} head={}",
                resp.headers.get("paypal-debug-id", ""),
                text[:800],
            )
            return False
        try:
            return resp.json()
        except ValueError:
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return json.loads(stripped)
            return resp.status_code in {200, 202, 204, 302, 303}

    def _validate_paypal_recaptcha(self, challenge_html: str, signup_url: str):
        token, _solution = self._mint_recaptcha_v2_token_via_capsolver(
            challenge_html=challenge_html,
            signup_url=signup_url,
        )
        if not token:
            return False
        logger.info("authchallenge type=recaptcha -> submitting solved recaptcha token")
        return self._submit_paypal_recaptcha_challenge(challenge_html, signup_url, token)

    def _mint_hcaptcha_token_via_capsolver(
        self,
        *,
        challenge_html: str,
        signup_url: str,
    ) -> tuple[str, dict[str, Any]]:
        _ = (challenge_html, signup_url)
        logger.warning("CapSolver hCaptcha support has been removed; skipping external solver.")
        return "", {}

    def _submit_paypal_hcaptcha_challenge(self, challenge_html: str, signup_url: str, token: str):
        """Submit a solved regular hCaptcha token back to PayPal authchallenge."""
        action = self._extract_form_action(challenge_html) or "/graphql?SignUpNewMemberMutation"
        url = urllib.parse.urljoin("https://www.paypal.com/", self._unescape_auth_url(action))
        fields = self._extract_input_values(challenge_html)
        jse = self._html_attr_value(challenge_html, "data-jse")
        if jse:
            fields["jse"] = jse

        iframe_src = self._extract_hcaptcha_iframe_src(challenge_html)
        if iframe_src:
            iframe_src = urllib.parse.urljoin("https://www.paypal.com/", iframe_src)
        site_key = self._extract_hcaptcha_site_key(challenge_html, iframe_src)
        now = int(time.time() * 1000)
        render_start = now - random.randint(9000, 17000)
        render_end = now - random.randint(3500, 6500)
        verify_ts = now
        fields["hcaptcha"] = token
        fields.setdefault("publicKey", site_key)
        fields.setdefault("hcaptcha_render_start_time_utc", str(render_start))
        fields.setdefault("hcaptcha_render_end_time_utc", str(render_end))
        fields.setdefault("hcaptcha_verification_time_utc", str(verify_ts))

        csrf = fields.get("_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        session_id = fields.get("_sessionID") or self._html_attr_value(challenge_html, "data-sessionid")
        ec_token = self.state.ec_token or self._first_query_value(signup_url, "token")
        if csrf and session_id:
            for state in ("CLIENT_SIDE_HCAPTCHA_SERVED", "CLIENT_SIDE_HCAPTCHA_SOLVED"):
                self._paypal_auth_logclientdata(
                    challenge_html=challenge_html,
                    csrf=csrf,
                    session_id=session_id,
                    ec_token=ec_token,
                    captcha_state=state,
                    signup_url=signup_url,
                )

        headers = {
            **self._browser_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
            ),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        logger.info(
            "Posting solved hcaptcha action={} fields={}",
            url,
            sorted(k for k in fields.keys() if k != "hcaptcha"),
        )
        resp = self.session.post(
            url,
            data=fields,
            headers=headers,
            disable_captcha_synthetic=True,
            timeout=60,
        )
        text = resp.text or ""
        logger.info(
            "hcaptcha submit HTTP {} bytes={} content-type={}",
            resp.status_code,
            len(resp.content),
            resp.headers.get("content-type", ""),
        )
        try:
            Path("/tmp/paypal_hcaptcha_submit_last.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "status_code": resp.status_code,
                        "paypal_debug_id": resp.headers.get("paypal-debug-id", ""),
                        "content_type": resp.headers.get("content-type", ""),
                        "token_len": len(token),
                        "site_key": site_key,
                        "fields": sanitize_for_log(fields),
                        "response_head": text[:3000],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        if looks_like_paypal_authchallenge(text):
            logger.warning(
                "hcaptcha submit returned another authchallenge paypal_debug_id={} head={}",
                resp.headers.get("paypal-debug-id", ""),
                text[:800],
            )
            return False
        try:
            result = resp.json()
            logger.info("hcaptcha submit returned JSON")
            return result
        except ValueError:
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return json.loads(stripped)
            # A non-challenge document or redirect after captcha submission means
            # the authchallenge layer has been cleared; let caller retry GraphQL.
            return resp.status_code in {200, 302, 303}

    def _validate_paypal_hcaptcha(self, challenge_html: str, signup_url: str):
        captcha_type = self._authchallenge_captcha_type(challenge_html) or "hcaptcha"
        token, _solution = self._mint_hcaptcha_token_via_capsolver(
            challenge_html=challenge_html,
            signup_url=signup_url,
        )
        if not token:
            return False
        logger.info("authchallenge type={} -> submitting solved hcaptcha token", captcha_type)
        return self._submit_paypal_hcaptcha_challenge(challenge_html, signup_url, token)

    def _validate_authchallenge_real_solver(self, challenge_html: str, signup_url: str):
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        logger.warning(
            "External CAPTCHA solver support has been removed; manual PayPal "
            "verification is required for authchallenge type={}. signup_url={}",
            captcha_type or "unknown",
            signup_url,
        )
        return False

    def _validate_authchallenge_if_possible(self, challenge_html: str, signup_url: str):
        dump_path = os.environ.get("BA_DUMP_CHALLENGE", "").strip()
        if not dump_path:
            dump_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "_authchallenge_dump.html",
            )
        if os.environ.get("BA_DUMP_CHALLENGE_ENABLED", "1") != "0":
            try:
                with open(dump_path, "w", encoding="utf-8") as _dump_f:
                    _dump_f.write(challenge_html or "")
                logger.info("Authchallenge HTML dumped to {}", dump_path)
            except Exception as _dump_e:
                logger.warning("Authchallenge HTML dump failed: {}", _dump_e)
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        self._send_authchallenge_datadog_rum(
            signup_url or getattr(self.state, "signup_url", "") or "https://www.paypal.com/auth/validatecaptcha",
            f"authchallenge_{captcha_type or 'unknown'}",
        )
        self.captcha_bypass_mode = paypal_captcha_bypass_mode()
        # frontend_disable 模式下跳过 reCAPTCHA mint: 纯 HTTP mint 的 Enterprise v2 token
        # 必被 soft-reject, 真实 POST validatecaptcha 反而作废会话 (8/14 BA-2GM93699 失败根因)。
        # 直接走 synthetic close (8/11 成功路径)。PAYPAL_RECAPTCHA_SOLVER_ENABLED=0 显式关闭。
        if (
            "recaptcha" in captcha_type
            and self.captcha_bypass_mode != CAPTCHA_FRONTEND_DISABLE_MODE
            and os.getenv("PAYPAL_RECAPTCHA_SOLVER_ENABLED", "1") != "0"
        ):
            try:
                from paypal.recaptcha_solver import solve_recaptcha_from_html

                token = solve_recaptcha_from_html(
                    challenge_html=challenge_html,
                    proxy=getattr(self.proxy_config, "url", "") or "",
                )
                if token:
                    logger.info(
                        "reCAPTCHA Enterprise token minted (pure HTTP) len={}",
                        len(token),
                    )
                    close_result = self._post_authchallenge_form_close_once(
                        challenge_html=challenge_html,
                        signup_url=signup_url,
                        challenge_token=token,
                    )
                    if isinstance(close_result, (dict, list)) and close_result:
                        logger.info("Authchallenge form close produced JSON via recaptcha token")
                        return close_result
                    if close_result:
                        logger.info("Authchallenge close/validation completed via recaptcha token")
                        return True
                else:
                    logger.warning("reCAPTCHA solver produced no token")
            except Exception as e:
                logger.warning("reCAPTCHA solver failed: {}", e)
        if self.captcha_bypass_mode == CAPTCHA_FRONTEND_DISABLE_MODE:
            return self._frontend_disable_authchallenge_close(challenge_html, signup_url)
        logger.warning(
            "PayPal authchallenge type={} requires manual/official verification; "
            "external CAPTCHA solver support is disabled. signup_url={}",
            captcha_type or "unknown",
            signup_url,
        )
        return False

    @staticmethod
    def _extract_form_action(challenge_html: str) -> str:
        m = re.search(r'<form\b[^>]*\baction=["\']([^"\']+)', challenge_html or "", re.I)
        return html_lib.unescape(m.group(1)) if m else ""

    @staticmethod
    def _extract_input_values(challenge_html: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for tag_match in re.finditer(r"<input\b[^>]*>", challenge_html or "", re.I):
            tag = tag_match.group(0)
            name_match = re.search(r'\bname=["\']([^"\']*)', tag, re.I)
            if not name_match:
                continue
            value_match = re.search(r'\bvalue=["\']([^"\']*)', tag, re.I)
            values[html_lib.unescape(name_match.group(1))] = (
                html_lib.unescape(value_match.group(1)) if value_match else ""
            )
        return values

    def _post_authchallenge_form_close_once(
        self,
        *,
        challenge_html: str,
        signup_url: str,
        challenge_token: str,
    ):
        """Backend equivalent of console `postMessage(... NOT_REACHABLE ...)`.

        For PayPal JSON captcha flow the form action in the returned HTML is
        `/graphql?SignUpNewMemberMutation`; the browser appends the captcha
        token/render fields and submits that form.  This is different from the
        older `/auth/validatecaptcha` flow.
        """
        action = self._extract_form_action(challenge_html) or "/graphql?SignUpNewMemberMutation"
        url = urllib.parse.urljoin("https://www.paypal.com/", self._unescape_auth_url(action))
        captcha_type = self._authchallenge_captcha_type(challenge_html)
        fields = self._extract_input_values(challenge_html)
        jse = self._html_attr_value(challenge_html, "data-jse")
        if jse:
            fields["jse"] = jse

        now = int(time.time() * 1000)
        if "hcaptchapassive" in captcha_type or self._extract_hcaptcha_passive_iframe_src(challenge_html):
            fields["hcaptchaToken"] = challenge_token
            fields.setdefault("hcaptcha_passive_render_start_time_utc", str(now - random.randint(4800, 6200)))
            fields.setdefault("hcaptcha_passive_render_end_time_utc", str(now - random.randint(80, 350)))
            if challenge_token not in {"NOT_REACHABLE", "RENDER_FAILURE"}:
                fields.setdefault("hcaptcha_passive_verification_time_utc", str(now))
        else:
            # Generic fallback mirrors authchallenge.js' default token field.
            fields["recaptcha"] = challenge_token
            # PayPal recaptcha_v2.html 浏览器填入 g-recaptcha-response; 双写保险
            fields["g-recaptcha-response"] = challenge_token
            fields.setdefault("grc_eval_start_time_utc", str(now))

        csrf = fields.get("_csrf") or self._html_attr_value(challenge_html, "data-csrf")
        session_id = fields.get("_sessionID") or self._html_attr_value(challenge_html, "data-sessionid")
        if csrf and session_id:
            solved_state = (
                "CLIENT_SIDE_HCAPTCHA_PASSIVE_NOT_REACHABLE"
                if challenge_token == "NOT_REACHABLE"
                else "CLIENT_SIDE_HCAPTCHA_PASSIVE_RENDER_FAILURE"
                if challenge_token == "RENDER_FAILURE"
                else "CLIENT_SIDE_HCAPTCHA_PASSIVE_SOLVED"
            )
            self._paypal_auth_logclientdata(
                challenge_html=challenge_html,
                csrf=csrf,
                session_id=session_id,
                ec_token=self.state.ec_token or self._first_query_value(signup_url, "token"),
                captcha_state=solved_state,
                signup_url=signup_url,
            )

        headers = {
            **self._browser_headers(
                accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                content_type="application/x-www-form-urlencoded",
            ),
            "Origin": "https://www.paypal.com",
            "Referer": signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }

        logger.info(
            "Posting authchallenge form close token={} action={} fields={}",
            challenge_token,
            url,
            sorted(k for k in fields.keys() if k not in {"hcaptchaToken", "recaptcha"}),
        )
        resp = self.session.post(url, data=fields, headers=headers, timeout=60)
        logger.info(
            "Authchallenge form close HTTP {} bytes={} content-type={}",
            resp.status_code,
            len(resp.content),
            resp.headers.get("content-type", ""),
        )
        try:
            Path("/tmp/paypal_authchallenge_form_close_last.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "status_code": resp.status_code,
                        "content_type": resp.headers.get("content-type", ""),
                        "token": challenge_token,
                        "fields": sanitize_for_log(fields),
                        "response_head": resp.text[:3000],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

        text = resp.text or ""
        if looks_like_paypal_authchallenge(text):
            raise PayPalAuthChallenge(
                "SignUpNewMemberMutation",
                resp.status_code,
                resp.headers.get("paypal-debug-id", ""),
                text,
            )
        try:
            return resp.json()
        except ValueError:
            # Some form-submit challenge responses come back as text/html with a
            # JSON object body.  Try a conservative object extraction before
            # giving up.
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return json.loads(stripped)
            logger.warning("Authchallenge form close returned non-JSON head={}", text[:800])
            return None

    def _post_authchallenge_form_close(self, challenge_html: str, signup_url: str):
        logger.warning(
            "Authchallenge form-close fallback is disabled; manual PayPal verification is required. signup_url={}",
            signup_url,
        )
        return None
        for token_value in ("NOT_REACHABLE", "RENDER_FAILURE", "EMPTY_TOKEN"):
            try:
                result = self._post_authchallenge_form_close_once(
                    challenge_html=challenge_html,
                    signup_url=signup_url,
                    challenge_token=token_value,
                )
                if result:
                    logger.info("Authchallenge form close produced JSON using token={}", token_value)
                    return result
            except PayPalAuthChallenge:
                raise
            except Exception as e:
                logger.warning("Authchallenge form close token={} failed: {}", token_value, e)
        return None

    @staticmethod
    def _has_buyer_not_set(result) -> bool:
        items = result if isinstance(result, list) else [result]
        for item in items:
            if not isinstance(item, dict):
                continue
            for err in item.get("errors") or []:
                if isinstance(err, str):
                    # GraphQL 可能直接返回字符串错误 (如 "BUYER_NOT_SET")
                    if "BUYER_NOT_SET" in err:
                        return True
                    continue
                if not isinstance(err, dict):
                    continue
                data = err.get("data") or {}
                if isinstance(data, dict) and data.get("contingency") == "BUYER_NOT_SET":
                    return True
                if err.get("message") == "BUYER_NOT_SET":
                    return True
        return False

    @staticmethod
    def _html_input_value(html: str, name: str) -> str:
        patterns = (
            rf'<input\b[^>]*\bname=["\']{re.escape(name)}["\'][^>]*\bvalue=["\']([^"\']*)',
            rf'<input\b[^>]*\bvalue=["\']([^"\']*)["\'][^>]*\bname=["\']{re.escape(name)}["\']',
        )
        for pattern in patterns:
            m = re.search(pattern, html or "", re.I | re.S)
            if m:
                return html_lib.unescape(m.group(1))
        return ""

    @staticmethod
    def _extract_modxo_deployment_id(html: str) -> str:
        for pattern in (
            r'data-dpl-id=["\']([^"\']+)["\']',
            r'"x-deployment-id"\s*:\s*"([^"]+)"',
            r'"deploymentId"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pattern, html or "", re.I)
            if m:
                return html_lib.unescape(m.group(1))
        return ""

    def _apply_modxo_public_credential_fields(self, text: str) -> None:
        """Capture hidden fields used by submitPublicCredential."""
        if not text:
            return
        if not self.state.ctx_id:
            ctx_id = self._html_input_value(text, "ctxId") or self._first_json_string(text, "ctxId")
            if ctx_id:
                self.state.ctx_id = ctx_id
        passkey = (
            self._html_input_value(text, "passkeyChallenge")
            or self._first_json_string(text, "passkeyChallenge")
        )
        if passkey:
            self.state.passkey_challenge = passkey
        rp_id = self._html_input_value(text, "rpId") or self._first_json_string(text, "rpId")
        if rp_id:
            self.state.rp_id = rp_id
        phone_code = self._html_input_value(text, "login_phone_country_code")
        if phone_code:
            self.state.login_phone_country_code = phone_code

    @staticmethod
    def _first_json_string(text: str, key: str) -> str:
        for variant in (text or "", html_lib.unescape(text or "")):
            m = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)"', variant)
            if m:
                return html_lib.unescape(m.group(1).replace("\\/", "/"))
        return ""

    def _extract_modxo_country_action(self, html: str) -> tuple[str, str]:
        """Return the inline country-change server action id and bound token.

        The real browser's first ModXO click does not call
        showCreateAccountAction directly.  It submits the inline country
        handleChange action serialized in the React Flight stream:

            6a:{"id":"604...","bound":"$@6b"}
            6b:["$@70"]
            70:"<encrypted-bound-arg>"

        The request body then sends field `1=<encrypted-bound-arg>` and
        `0=["$@1","BR"]`.
        """
        def decode_js_string(value: str) -> str:
            try:
                return json.loads(f'"{value}"')
            except Exception:
                return html_lib.unescape(value.replace("\\/", "/"))

        variants: list[str] = []

        def add_variant(value: str) -> None:
            if value and value not in variants:
                variants.append(value)

        for text in self._metadata_search_texts(html or ""):
            add_variant(text)
            # The initial HTML often contains React Flight records still wrapped
            # as JS string fragments inside <script>self.__next_f.push(...)</script>.
            # Normalize the common escaped shape so one parser can handle both:
            #   6a:{\"id\":\"...\",\"bound\":\"$@6b\"}
            #   70:\"<bound>\"
            add_variant(text.replace('\\"', '"').replace("\\n", "\n").replace("\\/", "/"))

        for text in variants:
            handle_keys = re.findall(r'"handleChange"\s*:\s*"\$h([0-9a-f]+)"', text)

            candidates: list[tuple[str, str, str]] = []
            for key in handle_keys:
                desc = re.search(
                    rf'{re.escape(key)}:\{{\s*"id"\s*:\s*"([0-9a-f]{{32,64}})"\s*,\s*"bound"\s*:\s*"\$@([0-9a-f]+)"\s*\}}',
                    text,
                )
                if desc:
                    candidates.append((key, desc.group(1), desc.group(2)))

            # Fallback for captures where the handleChange record and the action
            # descriptor are split across decoded Flight chunks.  Only records
            # with a non-null "$@..." bound token can satisfy the browser country
            # action body, so this does not select the sibling form submit action.
            if not candidates:
                for desc in re.finditer(
                    r'([0-9a-f]+):\{\s*"id"\s*:\s*"([0-9a-f]{32,64})"\s*,\s*"bound"\s*:\s*"\$@([0-9a-f]+)"\s*\}',
                    text,
                ):
                    candidates.append((desc.group(1), desc.group(2), desc.group(3)))

            for _key, action_id, bound_key in candidates:
                bound = re.search(
                    rf'{re.escape(bound_key)}:\[\s*"\$@([0-9a-f]+)"\s*\]',
                    text,
                )
                value_key = bound.group(1) if bound else bound_key
                value = re.search(
                    rf'{re.escape(value_key)}:"((?:\\.|[^"\\])*)"',
                    text,
                )
                if value:
                    bound_value = decode_js_string(value.group(1))
                    if len(bound_value) > 20:
                        return action_id, bound_value
        return "", ""

    def _apply_modxo_inline_metadata(self, html: str) -> None:
        deployment_id = self._extract_modxo_deployment_id(html)
        if deployment_id:
            self.state.modxo_deployment_id = deployment_id
        self._apply_modxo_public_credential_fields(html)
        action_id, bound = self._extract_modxo_country_action(html)
        if action_id and bound:
            self.state.modxo_country_action_id = action_id
            self.state.modxo_country_action_bound = bound
            logger.info("ModXO country action id: {}", action_id)

    def _modxo_named_action_ids_complete(self) -> bool:
        return bool(
            self.state.show_create_account_action_id
            and self.state.create_user_action_id
            and self.state.submit_public_credential_action_id
            and self.state.fetch_device_fingerprint_action_id
        )

    def _apply_static_modxo_action_ids(self) -> None:
        if not self._modxo_static_action_ids_enabled():
            return
        for attr, action_id in _MODXO_STATIC_ACTION_IDS.items():
            if getattr(self.state, attr, ""):
                continue
            setattr(self.state, attr, action_id)
            logger.info("ModXO action {}: {} (static)", attr, action_id)

    def _clear_static_modxo_action_ids_for_refresh(self) -> None:
        for attr, action_id in _MODXO_STATIC_ACTION_IDS.items():
            if getattr(self.state, attr, "") == action_id:
                setattr(self.state, attr, "")

    def _refresh_modxo_action_ids_from_chunks(self, *, reason: str = "") -> bool:
        html = str(getattr(self, "_last_modxo_html", "") or "")
        base_url = str(getattr(self, "_last_modxo_base_url", "") or "")
        if not html or not base_url:
            logger.debug("Cannot refresh ModXO action ids: Phase 0 HTML/base URL is unavailable")
            return False

        before = {
            attr: getattr(self.state, attr, "")
            for attr in _MODXO_STATIC_ACTION_IDS
        }
        logger.warning(
            "Refreshing ModXO action ids from JS chunks after static ids failed{}",
            f" reason={reason}" if reason else "",
        )
        self._extract_modxo_action_ids(html, base_url, force_refresh=True)
        after = {
            attr: getattr(self.state, attr, "")
            for attr in _MODXO_STATIC_ACTION_IDS
        }
        refreshed = self._modxo_named_action_ids_complete() and after != before
        logger.info("ModXO action id refresh complete={} changed={}", self._modxo_named_action_ids_complete(), after != before)
        return refreshed

    def _extract_modxo_action_ids(self, html: str, base_url: str, *, force_refresh: bool = False):
        """Extract Next server-action IDs from ModXO JS chunks.

        The browser sends these values in the Next-Action header. They are
        deployment-specific, so hard-coding the values from one capture breaks
        after PayPal ships a new bundle.
        """
        action_names = {
            "show_create_account_action_id": "showCreateAccountAction",
            "create_user_action_id": "createUserAction",
            "submit_public_credential_action_id": "submitPublicCredential",
            "fetch_device_fingerprint_action_id": "fetchDeviceFingerprintDataAction",
        }

        def scan(text: str) -> bool:
            changed = False
            for attr, action_name in action_names.items():
                if getattr(self.state, attr):
                    continue
                name_idx = text.find(f'"{action_name}"')
                if name_idx < 0:
                    continue
                window = text[max(0, name_idx - 500):name_idx]
                ids = re.findall(r'"([0-9a-f]{32,64})"', window)
                if ids:
                    action_id = ids[-1]
                    setattr(self.state, attr, action_id)
                    logger.info(f"ModXO action {attr}: {action_id}")
                    changed = True
            return changed

        def complete() -> bool:
            return self._modxo_named_action_ids_complete()

        if force_refresh:
            self._clear_static_modxo_action_ids_for_refresh()
        else:
            self._apply_static_modxo_action_ids()
            if complete():
                return

        scan(html or "")
        if complete():
            return

        script_urls = []
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html or "", re.I):
            if "/pay/_next/static/chunks/" not in src:
                continue
            url = urllib.parse.urljoin(base_url, src)
            if url not in script_urls:
                script_urls.append(url)

        script_urls = script_urls[:80]
        if not script_urls:
            return

        concurrency = self._env_int_between(
            "PAYPAL_MODXO_ACTION_CHUNK_CONCURRENCY",
            12,
            1,
            32,
        )

        def fetch_script(script_url: str) -> tuple[str, int, str]:
            try:
                js_resp = self.session.get(
                    script_url,
                    headers={
                        "Accept": "*/*",
                        "Referer": base_url,
                        "Sec-Fetch-Dest": "script",
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                return script_url, int(getattr(js_resp, "status_code", 0) or 0), getattr(js_resp, "text", "") or ""
            except Exception as e:
                logger.debug(f"Failed to inspect ModXO chunk {script_url}: {e}")
                return script_url, 0, ""

        started = time.monotonic()
        fetched = 0
        failed_urls: list[str] = []

        if concurrency <= 1 or len(script_urls) == 1:
            for script_url in script_urls:
                _url, status_code, text = fetch_script(script_url)
                fetched += 1
                if status_code == 200:
                    scan(text)
                if complete():
                    break
        else:
            logger.debug(
                "Fetching {} ModXO JS chunks for action ids with concurrency={}",
                len(script_urls),
                concurrency,
            )
            for start in range(0, len(script_urls), concurrency):
                batch = script_urls[start:start + concurrency]
                with ThreadPoolExecutor(max_workers=min(concurrency, len(batch))) as executor:
                    futures = {executor.submit(fetch_script, script_url): script_url for script_url in batch}
                    for future in as_completed(futures):
                        script_url = futures[future]
                        try:
                            _url, status_code, text = future.result()
                        except Exception as e:
                            logger.debug(f"Failed to inspect ModXO chunk {script_url}: {e}")
                            continue
                        fetched += 1
                        if status_code == 0:
                            failed_urls.append(script_url)
                        if status_code == 200:
                            scan(text)
                        if complete():
                            break
                if complete():
                    break

        if failed_urls and not complete() and concurrency > 1:
            logger.debug(
                "Retrying {} failed ModXO JS chunk fetches serially after concurrent scan",
                len(failed_urls),
            )
            for script_url in failed_urls:
                _url, status_code, text = fetch_script(script_url)
                fetched += 1
                if status_code == 200:
                    scan(text)
                if complete():
                    break

        logger.debug(
            "ModXO action id chunk scan fetched={}/{} elapsed={:.2f}s complete={}",
            fetched,
            len(script_urls),
            time.monotonic() - started,
            bool(complete()),
        )

    def _card_issuer_type(self) -> str:
        """PayPal GraphQL CardIssuerType enum (JCB/TROY 正确映射, 见 identity_lib.issuer_type_for)。"""
        try:
            from paypal.identity_lib import issuer_type_for

            issuer = issuer_type_for(self.card.number)
            if issuer:
                return issuer
        except Exception:
            pass
        prefix2 = int(self.card.number[:2]) if self.card.number[:2].isdigit() else 0
        prefix4 = int(self.card.number[:4]) if self.card.number[:4].isdigit() else 0
        if 51 <= prefix2 <= 55 or 2221 <= prefix4 <= 2720:
            return "MASTER_CARD"
        if self.card.number.startswith("4"):
            return "VISA"
        if self.card.number.startswith("3"):
            return "AMEX"
        if self.card.number.startswith("6"):
            return "DISCOVER"
        return "VISA"

    def _masked_card_number(self) -> str:
        return sanitize_for_log({"cardNumber": self.card.number})["cardNumber"]

    def _masked_phone(self) -> str:
        return sanitize_for_log({"phone": self.user.phone})["phone"]

    def _on_phone_updated(self) -> None:
        pass


    def _update_user_phone(self, phone: str):
        """Update the phone fields used by the signup/2FA GraphQL calls (国家相关前缀)。"""
        raw = (phone or "").strip()
        if raw.lower().startswith("phone:"):
            raw = raw.split(":", 1)[1].strip()

        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) < 8:
            raise ValueError("phone number is too short")

        cc_prefix = self._context_phone_cc()
        if digits.startswith(cc_prefix) and len(digits) > len(cc_prefix) + 2:
            country_code = f"+{cc_prefix}"
            local = digits[len(cc_prefix):]
            full = f"+{digits}"
        else:
            country_code = f"+{cc_prefix}"
            local = digits
            full = f"+{cc_prefix}{digits}"

        if len(local) < 8:
            raise ValueError("local phone number is too short")

        self.user.phone = full
        self.user.phone_country_code = country_code
        self.user.phone_local = local
        logger.info("Phone updated for OTP retry: {}", self._masked_phone())
        self._on_phone_updated()

    def _context_phone_cc(self) -> str:
        """国家上下文的手机国家码数字部分 (无上下文回退 55)。"""
        if self.country_context is not None:
            prefix = str(getattr(self.country_context, "phone_country", "") or "+55")
            return prefix.lstrip("+") or "55"
        return str(self.user.phone_country_code or "+55").lstrip("+") or "55"

    def _graphql_with_authchallenge_frontend_retry(
        self,
        operation_name: str,
        query: str,
        variables: dict[str, Any],
        signup_url: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[Any]:
        last_challenge: PayPalAuthChallenge | None = None
        for attempt in range(1, 4):
            try:
                return self.session.graphql(operation_name, query, variables, **kwargs)
            except PayPalAuthChallenge as challenge:
                last_challenge = challenge
                logger.warning(
                    "GraphQL {} returned authchallenge attempt={} paypal_debug_id={}; "
                    "attempting configured CAPTCHA validation mode={}.",
                    operation_name,
                    attempt,
                    challenge.debug_id or "<missing>",
                    self.captcha_bypass_mode,
                )
                self.session.purge_security_challenge_state(
                    challenge.html,
                    reason=f"{operation_name}_frontend_disable_attempt_{attempt}",
                    clear_cookies=False,
                    clear_files=False,
                )
                validated = self._validate_authchallenge_if_possible(challenge.html, signup_url)
                if isinstance(validated, (dict, list)):
                    return validated
                if not validated:
                    raise
                time.sleep(0.4)
        if last_challenge:
            raise last_challenge
        raise RuntimeError(f"{operation_name} failed without a response")

    @staticmethod
    def _random_idapps_csrf_nonce(length: int = 96) -> str:
        alphabet = string.ascii_letters + string.digits + "-_"
        return "AA" + "".join(random.choice(alphabet) for _ in range(max(8, length - 2)))

    def _send_idapps_get_otp_challenge(self, token: str, signup_url: str) -> bool:
        """Mirror `/idapps/graphql getOtpChallengeOperation` before OTP flow."""
        if os.getenv("PAYPAL_SKIP_IDAPPS_OTP_CHALLENGE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return False
        token = token or self.state.ec_token
        if not self._is_ec_token(token):
            return False
        fn_sync_data = build_fn_sync_data(
            token,
            source="IWC_LOGIN_APP",
            include_d=False,
            session=self.session,
        )
        rdata = urllib.parse.quote(
            json.dumps(
                {"fn_sync_data": urllib.parse.quote(fn_sync_data, safe="")},
                separators=(",", ":"),
            ),
            safe="",
        )
        payload = {
            "operationName": "getOtpChallengeOperation",
            "query": "",
            "csrfNonce": self._random_idapps_csrf_nonce(),
            "variables": {
                "clientInfo": {
                    "fnId": token,
                    "ctxId": self.state.ctx_id,
                    "rData": rdata,
                },
                "credentials": {
                    "credentialValue": self.user.email,
                    "credentialType": "EMAIL",
                },
                "challengeInfo": {"autoSmsOtp": False},
            },
            "fn_sync_data": fn_sync_data,
        }
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://www.paypal.com",
            "X-Requested-With": "fetch",
        }
        try:
            resp = self.session.post(
                "https://www.paypal.com/idapps/graphql",
                json=payload,
                headers=headers,
                timeout=30,
            )
            text = resp.text or ""
            logger.info(
                "idapps getOtpChallengeOperation HTTP {} bytes={}",
                resp.status_code,
                len(resp.content),
            )
            try:
                Path("/tmp/paypal_idapps_get_otp_challenge_last.json").write_text(
                    json.dumps(
                        {
                            "status_code": resp.status_code,
                            "content_type": resp.headers.get("content-type", ""),
                            "payload": sanitize_for_log(payload),
                            "response_head": text[:2500],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            if looks_like_paypal_authchallenge(text):
                logger.info("idapps getOtpChallengeOperation returned authchallenge; closing it before OTP.")
                validated = self._validate_authchallenge_if_possible(text, signup_url)
                return bool(validated)
            return 200 <= resp.status_code < 400
        except Exception as e:
            logger.warning("idapps getOtpChallengeOperation soft-failed: {}", e)
            return False

    def _initiate_2fa_phone_confirmation(self, token: str, signup_url: str) -> tuple[str, str]:
        """Send a new 2FA SMS and return authId/challengeId."""
        logger.info("Step 1: Initiating 2FA phone confirmation for {}...", self._masked_phone())
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_risk_based_phone_confirmation_modal_component_mounted",
                "weasley_initiate_phone_confirmation_start",
                "weasley_api_request_initiate_risk_based_two_factor_phone_confirmation_mutation",
            ],
            country=self._context_country(),
            lang=self._context_lang(),
        )
        initiate_result = self._graphql_with_authchallenge_frontend_retry(
            "InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
            INITIATE_2FA_PHONE_MUTATION,
            {
                "phoneNumber": self.user.phone_local,
                "locale": {
                    "country": self._context_country(),
                    "lang": self._context_lang(),
                },
                "phoneCountry": self._context_country(),
                "token": token,
            },
            signup_url,
        )
        logger.info(
            "2FA initiation result (sanitized): {}",
            json.dumps(sanitize_for_log(initiate_result), ensure_ascii=False, indent=2)[:500],
        )

        result_obj = initiate_result[0] if isinstance(initiate_result, list) else initiate_result
        tfa_data = result_obj.get("data", {}).get(
            "initiateRiskBasedTwoFactorPhoneConfirmation", {}
        )
        auth_id = tfa_data.get("authId", "")
        challenge_id = tfa_data.get("challengeId", "")
        state = tfa_data.get("state", "")
        logger.info("2FA state: {}, authId=<redacted>, challengeId=<redacted>", state)

        if not auth_id or not challenge_id:
            raise RuntimeError("Failed to get authId/challengeId from 2FA initiation")
        return auth_id, challenge_id

    def _confirm_2fa_phone_confirmation(
        self,
        token: str,
        signup_url: str,
        auth_id: str,
        challenge_id: str,
        otp: str,
    ) -> bool:
        """Confirm one OTP attempt. Return True only on CONFIRMED."""
        logger.info("Step 2: Confirming OTP: <redacted>")
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_confirm_phone_confirmation_start",
                "weasley_api_request_confirm_risk_based_two_factor_phone_confirmation_mutation",
            ],
            country=self._context_country(),
            lang=self._context_lang(),
        )
        confirm_result = self._graphql_with_authchallenge_frontend_retry(
            "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
            CONFIRM_2FA_PHONE_MUTATION,
            {
                "pin": otp,
                "authId": auth_id,
                "challengeId": challenge_id,
                "token": token,
            },
            signup_url,
        )
        logger.info(
            "OTP confirmation result (sanitized): {}",
            json.dumps(sanitize_for_log(confirm_result), ensure_ascii=False, indent=2)[:500],
        )

        result_obj = confirm_result[0] if isinstance(confirm_result, list) else confirm_result
        confirm_data = result_obj.get("data", {}).get(
            "confirmRiskBasedTwoFactorPhoneConfirmation", {}
        ) or {}
        confirm_state = confirm_data.get("state", "")
        if confirm_state == "CONFIRMED":
            logger.success("OTP confirmed successfully!")
            return True

        errors = result_obj.get("errors") or []
        if errors:
            logger.warning(
                "OTP confirmation failed with errors: {}",
                json.dumps(sanitize_for_log(errors), ensure_ascii=False, indent=2),
            )
        else:
            logger.warning("OTP confirmation failed, state: {}", confirm_state or "<missing>")
        return False

    def _confirm_phone_with_retry(self, token: str, signup_url: str):
        """Loop until OTP is confirmed; user can enter a new phone to resend."""
        if self.sms_provider is not None:
            return self._confirm_phone_with_sms_provider(token, signup_url)
        while True:
            try:
                auth_id, challenge_id = self._initiate_2fa_phone_confirmation(token, signup_url)
            except Exception as e:
                logger.error("Failed to initiate OTP for {}: {}", self._masked_phone(), e)
                while True:
                    value = input(
                        "\n>>> 发送验证码失败。请输入新的手机号重新发送"
                        "（如 +5591980133818）；输入 q 退出: "
                    ).strip()
                    if value.lower() in {"q", "quit", "exit"}:
                        raise RuntimeError("OTP confirmation cancelled by user") from e
                    try:
                        self._update_user_phone(value)
                        break
                    except ValueError as phone_error:
                        logger.warning("手机号无效：{}。请重新输入。", phone_error)
                continue
            logger.info("SMS verification code sent to phone: {}", self._masked_phone())

            while True:
                value = input(
                    "\n>>> 输入6位短信验证码；如需换号，直接输入新手机号"
                    "（如 +5591980133818 或 phone:+5591980133818）；输入 q 退出: "
                ).strip()

                if value.lower() in {"q", "quit", "exit"}:
                    raise RuntimeError("OTP confirmation cancelled by user")

                if len(value) == 6 and value.isdigit():
                    if self._confirm_2fa_phone_confirmation(
                        token,
                        signup_url,
                        auth_id,
                        challenge_id,
                        value,
                    ):
                        return
                    logger.warning(
                        "验证码验证失败。可以继续输入新的6位验证码，或输入新手机号重新发送验证码。"
                    )
                    continue

                try:
                    self._update_user_phone(value)
                    logger.info("Re-sending OTP to the new phone...")
                    break
                except ValueError as e:
                    logger.warning(
                        "输入既不是6位验证码，也不是有效手机号：{}。请重新输入。",
                        e,
                    )

    def _confirm_phone_with_sms_provider(self, token: str, signup_url: str) -> None:
        if self.sms_provider is None:
            raise RuntimeError("SMS provider is not configured")
        # flow_id = 本次 flow 会话标识; 同 flow 内重跑 2FA 可复用已确认号, 跨 flow 不复用
        flow_id = getattr(self, "ba_token", "") or self.state.ec_token or ""
        last_reserve_error: Exception | None = None
        for attempt in range(1, self.sms_provider.max_attempts + 1):
            try:
                activation = self.sms_provider.reserve_number(flow_id=flow_id)
            except Exception as exc:
                last_reserve_error = exc
                logger.warning("SMS provider could not reserve a number (attempt {}): {}", attempt, exc)
                break
            self._update_user_phone(activation.phone_number)
            if getattr(activation, "reused", False):
                # 复用已确认号 (同 flow 重跑 2FA): 不花钱, 不回写价格, 提示复用
                self._emit_progress(
                    "sms",
                    f"复用已确认号 provider={activation.provider_id} phone={self._masked_phone()} (不新增扣费)",
                    level="info",
                )
            else:
                self._emit_progress(
                    "sms",
                    f"已取号 provider={activation.provider_id} price=${activation.price:.4f} phone={self._masked_phone()}",
                    level="info",
                )
            try:
                auth_id, challenge_id = self._initiate_2fa_phone_confirmation(token, signup_url)
            except Exception as exc:
                logger.error("Failed to initiate OTP for SMSBower phone {}: {}", self._masked_phone(), exc)
                self.sms_provider.abandon(activation, "paypal_initiation_failed")
                continue

            self.sms_provider.mark_sms_sent(activation)
            logger.info(
                "Waiting for SMSBower OTP attempt={} provider={} reused={} timeout={}s",
                attempt,
                activation.provider_id,
                activation.reused,
                self.sms_provider.wait_seconds,
            )
            code = self.sms_provider.wait_for_code(activation, timeout_seconds=self.sms_provider.wait_seconds)
            if not code:
                self.sms_provider.abandon(activation, "sms_timeout")
                continue
            if self._confirm_2fa_phone_confirmation(token, signup_url, auth_id, challenge_id, code):
                self.sms_provider.register_confirmation_result(activation, True)
                self._emit_progress("sms", "OTP 确认成功 (CONFIRMED)", level="ok")
                return
            self.sms_provider.register_confirmation_result(activation, False)
            logger.warning("SMSBower OTP was rejected by PayPal; trying another number.")
        if last_reserve_error is not None:
            raise RuntimeError(
                f"SMS number reservation failed after {attempt} attempts: {last_reserve_error}"
            ) from last_reserve_error
        raise RuntimeError("SMSBower OTP confirmation failed after all attempts")

    def _card_expiration_date(self) -> str:
        exp_parts = self.card.expiry.split("/")
        return f"{exp_parts[0]}/{exp_parts[1]}" if len(exp_parts) == 2 else self.card.expiry

    def _dob_payload(self) -> dict[str, str]:
        dob_parts = self.user.dob.split("/")
        return (
            {"day": dob_parts[0], "month": dob_parts[1], "year": dob_parts[2]}
            if len(dob_parts) == 3
            else {}
        )

    def _billing_line1(self) -> str:
        house_number = self.address.house_number.strip()
        if house_number and f", {house_number}" in self.address.street:
            return self.address.street
        return f"{self.address.street}, {self.address.house_number}"

    def _build_signup_variables(self, token: str) -> dict[str, object]:
        card_type = self._card_issuer_type()
        if self._content_metadata_is_unresolved() and self.state.content_manifest_url:
            self._fetch_signup_content_manifest_metadata(
                self.state.content_manifest_url,
                referer=self.state.signup_url,
            )
        if self._content_metadata_is_unresolved():
            self._apply_configured_or_cached_signup_content_metadata()
        content_identifier = self._resolved_content_identifier()
        billing_autocomplete_type = (
            "ANS" if self._billing_address_autocomplete_succeeded else "MANUAL"
        )
        country = self._context_country()
        kyc_fields = self._context_kyc_fields()

        identity_document: dict[str, str] | None = None
        if kyc_fields.intersection({"IdentityDocumentType", "IdentityDocumentNumber"}) or country == "BR":
            identity_document = {
                "type": (self.user.identity_document_type or "CPF"),
                "value": (self.user.identity_document_number or self.user.cpf or ""),
            }

        variables: dict[str, object] = {
            "card": {
                "cardNumber": self.card.number,
                "expirationDate": self._card_expiration_date(),
                "securityCode": self.card.cvv,
                "type": card_type,
                "productClass": self.card.card_type,
            },
            "country": country,
            "email": self.user.email,
            "firstName": self.user.first_name,
            "lastName": self.user.last_name,
            "phone": {
                "countryCode": self.user.phone_country_code.lstrip("+"),
                "number": self.user.phone_local,
                "type": "MOBILE",
            },
            "supportedThreeDsExperiences": ["IFRAME"],
            "token": token,
            "billingAddress": {
                "postalCode": self.address.postal_code,
                "line1": self._billing_line1(),
                "line2": self.address.district,
                "city": self.address.city,
                "state": self.address.state,
                "accountQuality": {
                    "autoCompleteType": billing_autocomplete_type,
                    "isUserModified": True,
                },
                "country": country,
                "familyName": self.user.last_name,
                "givenName": self.user.first_name,
            },
            "shippingAddress": {
                "postalCode": "",
                "line1": "",
                "city": "",
                "state": "",
                "accountQuality": {
                    "autoCompleteType": "MANUAL",
                    "isUserModified": False,
                },
                "country": country,
                "familyName": self.user.last_name,
                "givenName": self.user.first_name,
            },
            "contentIdentifier": content_identifier,
            "marketingOptOut": True,
            "password": self.user.password,
            "dateOfBirth": self._dob_payload(),
            "crsData": None,
            "legalAgreements": {},
        }

        if self.user.nationality and "Nationality" in kyc_fields:
            variables["nationality"] = self.user.nationality
        if self.user.middle_name and (
            country == "AU" or "MiddleName" in kyc_fields
        ):
            variables["middleName"] = self.user.middle_name
        if country == "JP" and (self.user.kana_first or self.user.kana_last):
            variables["countrySpecificFirstName"] = self.user.kana_first
            variables["countrySpecificLastName"] = self.user.kana_last
        if self.user.occupation and "Occupation" in kyc_fields:
            variables["occupation"] = self.user.occupation
        if identity_document is not None:
            variables["identityDocument"] = identity_document
        return variables

    def _send_address_autocomplete(self, token: str) -> None:
        self._billing_address_autocomplete_succeeded = False
        try:
            address_result = self.session.graphql(
                "AddressAutocompleteFromPostalCodeQuery",
                ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY,
                {
                    "country": self.address.country,
                    "postalCode": self.address.postal_code,
                    "token": token,
                },
            )
            result_obj = address_result[0] if isinstance(address_result, list) else address_result
            result_dict = cast(dict[str, Any], result_obj) if isinstance(result_obj, dict) else {}
            data = result_dict.get("data") if isinstance(result_dict.get("data"), dict) else {}
            normalized = cast(dict[str, Any], data).get("addressNormalization") or {}
            if not isinstance(normalized, dict) or not normalized:
                logger.warning(
                    "AddressAutocompleteFromPostalCodeQuery returned no usable normalized "
                    "address; using MANUAL billing address metadata."
                )
                return

            missing_fields = [
                field
                for field in ("line1", "city", "state", "postalCode")
                if not str(normalized.get(field) or "").strip()
            ]
            if missing_fields:
                logger.warning(
                    "AddressAutocompleteFromPostalCodeQuery returned incomplete normalized "
                    "address missing={}; using MANUAL billing address metadata. values={} {} {} {}",
                    ",".join(missing_fields),
                    normalized.get("line1"),
                    normalized.get("line2"),
                    normalized.get("city"),
                    normalized.get("state"),
                )
                return

            logger.info(
                "Address normalized: {}, {}, {} {}",
                normalized.get("line1"),
                normalized.get("line2"),
                normalized.get("city"),
                normalized.get("state"),
            )
            self.address.street = normalized.get("line1") or self.address.street
            self.address.district = normalized.get("line2") or self.address.district
            self.address.city = normalized.get("city") or self.address.city
            self.address.state = normalized.get("state") or self.address.state
            self.address.postal_code = normalized.get("postalCode") or self.address.postal_code
            self._billing_address_autocomplete_succeeded = True
        except Exception as e:
            logger.warning(f"AddressAutocompleteFromPostalCodeQuery failed: {e}")

    def _send_signup_attempt(self, token: str, signup_url: str) -> dict[str, Any] | list[Any]:
        card_type = self._card_issuer_type()
        # InstallmentOptionsQuery is only a UI warm-up for BR installment
        # offers.  PayPal resolves the payee from the EC checkout token here;
        # sending the original BA token produces INVALID_RESOURCE_ID at
        # payService.getPayee-contingency.  Do not let this optional preflight
        # pollute the job as an ERROR or block SignUpNewMember.
        installment_token = self.state.ec_token or token
        if self._is_ec_token(installment_token):
            try:
                installment_result = self.session.graphql(
                    "InstallmentOptionsQuery",
                    INSTALLMENT_OPTIONS_QUERY,
                    {
                        "buyerCountry": self.address.country,
                        "cardNumber": self.card.number,
                        "cardType": card_type,
                        "token": installment_token,
                    },
                    graphql_error_level="WARNING",
                )
                installment_errors = self._graphql_errors(installment_result)
                if installment_errors:
                    logger.warning(
                        "Optional InstallmentOptionsQuery was unavailable; "
                        "continuing without installment selection. errors={}",
                        json.dumps(
                            sanitize_for_log(installment_errors),
                            ensure_ascii=False,
                        )[:1000],
                    )
            except Exception as e:
                logger.warning(f"Optional InstallmentOptionsQuery failed: {e}")
        else:
            logger.warning(
                "Skipping optional InstallmentOptionsQuery: no EC checkout token "
                "is available (current token is {}).",
                sanitize_for_log({"token": installment_token or ""})["token"] or "<missing>",
            )

        if not getattr(self, "_signup_billing_address_prepared", False):
            self._send_address_autocomplete(token)
            self._signup_billing_address_prepared = True
        else:
            logger.info(
                "Reusing prepared billing address for card retry; "
                "skipping AddressAutocompleteFromPostalCodeQuery."
            )

        risk_mode = self._signup_context_risk_mode()
        if risk_mode == "headless":
            self._send_signup_context_risk_signals_with_headless(signup_url, token)
        elif risk_mode in {"roxy", "auto"} or self._roxy_risk_runtime_active():
            sent_signup_context_risk = self._send_signup_context_risk_signals_with_roxy(signup_url, token)
            if not sent_signup_context_risk and self._signup_context_risk_mode() == "headless":
                self._send_signup_context_risk_signals_with_headless(signup_url, token)

        self._strict_signup_preflight_or_raise()

        self._send_signup_field_events(
            self.session,
            token,
            [
                "email",
                "phone",
                "cardNumber",
                "cardExpiry",
                "cardCvv",
                "password",
                "firstName",
                "lastName",
                "billingLine1",
                "billingCity",
                "billingPostalCode",
                "billingState",
                "dateOfBirth",
                "identityDocumentNumber",
            ],
        )
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_create_account_and_pay_submit",
                "weasley_api_request_sign_up_new_member_mutation",
            ],
            country=self._context_country(),
            lang=self._context_lang(),
        )
        signup_variables = self._build_signup_variables(token)
        signup_result = self._post_signup_with_authchallenge_ignore(
            token,
            signup_url,
            signup_variables,
        )
        logger.info(
            "Signup result (sanitized): {}",
            json.dumps(
                sanitize_for_log(signup_result),
                ensure_ascii=False,
                indent=2,
            )[:4000],
        )
        return signup_result

    def _synthetic_signup_success_from_cookie(self, reason: str) -> dict[str, Any]:
        """Build a GraphQL-shaped success when SignUp set EUAT but returned UI HTML.

        In this PayPal checkout path the authchallenge document is a front-end
        verification page.  The backend may already have accepted the signup and
        placed the EUAT cookie before sending that HTML.  When we have that token
        in the cookie jar there is enough authenticated state for the next
        billingLite/Hagrid step, so normalize it to the same shape that
        _consume_signup_result already understands.
        """
        return {
            "data": {
                "onboardAccount": {
                    "buyer": {
                        "userId": self.state.user_id,
                        "auth": {"accessToken": self.state.euat_token},
                    }
                }
            },
            "extensions": {
                "authchallengeIgnored": True,
                "reason": reason,
            },
        }

    def _post_signup_once(self, token: str, signup_variables: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], self.session.graphql(
            "SignUpNewMemberMutation",
            SIGNUP_NEW_MEMBER_MUTATION,
            signup_variables,
            extra_body={"fn_sync_data": build_signup_fn_sync_data(token, session=self.session)},
        ))

    def _post_signup_with_authchallenge_ignore(
        self,
        token: str,
        signup_url: str,
        signup_variables: dict[str, Any],
    ) -> dict[str, Any] | list[Any]:
        last_challenge: PayPalAuthChallenge | None = None
        last_validated = False

        for challenge_attempt in range(1, 4):
            try:
                return self._post_signup_once(token, signup_variables)
            except PayPalAuthChallenge as challenge:
                last_challenge = challenge
                captcha_type = self._authchallenge_captcha_type(challenge.html) or "unknown"
                logger.warning(
                    "SignUpNewMember returned PayPal authchallenge HTML "
                    "attempt={} paypal_debug_id={} captcha_type={} mode={}",
                    challenge_attempt,
                    challenge.debug_id or "<missing>",
                    captcha_type,
                    self.captcha_bypass_mode,
                )
                # Non-destructive: do not clear PayPal session cookies here.
                self.session.purge_security_challenge_state(
                    challenge.html,
                    reason=f"signup_authchallenge_backend_attempt_{challenge_attempt}",
                    clear_cookies=False,
                    clear_files=False,
                )
                if self.state.euat_token:
                    logger.warning(
                        "Authchallenge response already yielded EUAT cookie; "
                        "continuing to billing authorization."
                    )
                    return self._synthetic_signup_success_from_cookie(
                        "authchallenge_html_with_euat_cookie"
                    )

                validated = self._validate_authchallenge_if_possible(challenge.html, signup_url)
                last_validated = bool(validated)
                if isinstance(validated, (dict, list)):
                    return validated
                if last_validated:
                    logger.info(
                        "authchallenge close/validation completed; "
                        "retrying SignUpNewMemberMutation."
                    )
                    time.sleep(0.4)
                    continue

                logger.warning(
                    "authchallenge validation did not complete in mode={}; "
                    "stopping signup retry loop.",
                    self.captcha_bypass_mode,
                )
                break

        status_code = last_challenge.status_code if last_challenge else 200
        debug_id = last_challenge.debug_id if last_challenge else ""
        captcha_type = self._authchallenge_captcha_type(last_challenge.html) if last_challenge else ""
        if self.captcha_bypass_mode == CAPTCHA_FRONTEND_DISABLE_MODE:
            error_message = "AUTHCHALLENGE_FRONTEND_DISABLE_VALIDATE_FAILED"
        else:
            error_message = "AUTHCHALLENGE_MANUAL_VERIFICATION_REQUIRED"
        return {
            "errors": [
                {
                    "message": error_message,
                    "statusCode": status_code,
                    "checkpoints": ["authchallenge"],
                    "data": {
                        "paypalDebugId": debug_id,
                        "captchaType": captcha_type or "unknown",
                        "backendValidatecaptcha": last_validated,
                        "captchaMode": self.captcha_bypass_mode,
                        "manualVerificationRequired": self.captcha_bypass_mode
                        == CAPTCHA_MANUAL_REQUIRED_MODE,
                    },
                }
            ]
        }

    @staticmethod
    def _iter_dicts(value):
        if isinstance(value, dict):
            yield value
            for item in value.values():
                yield from PayPalFlow._iter_dicts(item)
        elif isinstance(value, list):
            for item in value:
                yield from PayPalFlow._iter_dicts(item)

    @staticmethod
    def _href_from_value(value) -> str:
        if isinstance(value, dict):
            href = value.get("href")
            return href if isinstance(href, str) else ""
        return value if isinstance(value, str) else ""

    def _load_three_ds_url(self, url: str, referer: str, label: str) -> bool:
        url = self._unescape_auth_url(url)
        if not url:
            return False
        try:
            resp = self.session.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": referer or self.state.signup_url,
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "iframe",
                },
            )
            logger.info(
                "3DS {} URL loaded: status={} bytes={}",
                label,
                resp.status_code,
                len(resp.content),
            )
            return 200 <= resp.status_code < 400
        except Exception as e:
            logger.warning(f"3DS {label} URL load failed: {e}")
            return False

    def _handle_three_ds_contingency(self, signup_result, signup_url: str) -> bool:
        """Handle frictionless 3DS DDC and stop on interactive challenge."""
        result_obj = signup_result[0] if isinstance(signup_result, list) else signup_result
        onboard_data = (
            result_obj.get("data", {}).get("onboardAccount", {})
            if isinstance(result_obj, dict)
            else {}
        )
        flags = onboard_data.get("flags", {}) if isinstance(onboard_data, dict) else {}
        explicitly_required = bool(flags.get("is3DSecureRequired"))

        tds_items: list[dict[str, Any]] = []
        for item in self._iter_dicts(onboard_data):
            if isinstance(item.get("threeDomainSecure"), dict):
                tds_items.append(item["threeDomainSecure"])
            if isinstance(item.get("threeDSContingencyData"), dict):
                tds_items.append(item["threeDSContingencyData"])

        if not explicitly_required and not tds_items:
            return True

        accepted_statuses = {
            "",
            "NO_CONTINGENCY",
            "NOT_REQUIRED",
            "NOT_APPLICABLE",
            "SKIPPED",
            "PASSED",
            "PASS",
            "SUCCESS",
            "SUCCEEDED",
            "RESOLVED",
            "COMPLETED",
        }
        interactive_urls: list[str] = []
        ddc_urls: list[str] = []
        statuses: list[str] = []

        for item in tds_items:
            status = str(item.get("status") or item.get("name") or item.get("causeName") or "")
            if status:
                statuses.append(status)
            redirect_url = self._href_from_value(item.get("redirectUrl"))
            if redirect_url:
                interactive_urls.append(redirect_url)

            raw_resolution = item.get("resolution")
            resolution = raw_resolution if isinstance(raw_resolution, dict) else {}
            raw_context = resolution.get("contingencyContext")
            context = (
                raw_context
                if isinstance(raw_context, dict)
                else {}
            )
            ddc_url = self._href_from_value(context.get("deviceDataCollectionUrl"))
            if ddc_url:
                ddc_urls.append(ddc_url)

        for ddc_url in dict.fromkeys(ddc_urls):
            self._load_three_ds_url(ddc_url, signup_url, "device-data-collection")

        unresolved_statuses = [
            status for status in statuses if status.upper() not in accepted_statuses
        ]
        if interactive_urls or (explicitly_required and unresolved_statuses):
            logger.warning(
                "3DS interactive challenge required; statuses={} challenge_urls={}",
                ",".join(statuses) or "-",
                len(interactive_urls),
            )
            return False

        if explicitly_required:
            logger.info("3DS required flag present but no interactive challenge remained after DDC.")
        return True

    def _consume_signup_result(self, signup_result: dict[str, Any] | list[Any], signup_url: str = "") -> tuple[bool, list[dict[str, Any]]]:
        """Apply successful signup data to state. Return (success, errors)."""
        result_obj = signup_result[0] if isinstance(signup_result, list) else signup_result
        onboard_data = result_obj.get("data", {}).get("onboardAccount", {})
        if onboard_data:
            if not self._handle_three_ds_contingency(signup_result, signup_url):
                return False, [
                    {
                        "message": "THREE_DS_CHALLENGE_REQUIRED",
                        "checkpoints": ["threeDS"],
                        "data": {"requiresOperatorBrowser": True},
                    }
                ]
            buyer = onboard_data.get("buyer", {})
            self.state.user_id = buyer.get("userId", "")
            auth = buyer.get("auth", {})
            if auth:
                self.state.euat_token = auth.get("accessToken", "")
            if not self.state.euat_token:
                self.state.euat_token = self._signup_access_token_candidate(signup_result)
            if not self.state.euat_token:
                logger.warning(
                    "SignUpNewMember returned onboardAccount but no accessToken "
                    "was found in auth payload, response body, or EUAT cookie."
                )
            logger.success(f"Account created! User ID: {self.state.user_id}")
            return True, []

        errors = result_obj.get("errors", []) or []
        if errors:
            for err in errors:
                if not isinstance(err, dict):
                    continue
                logger.error(
                    "Signup error detail: {}",
                    json.dumps(
                        sanitize_for_log({
                            "message": err.get("message"),
                            "name": err.get("_name"),
                            "statusCode": err.get("statusCode"),
                            "checkpoints": err.get("checkpoints"),
                            "contingency": err.get("contingency"),
                            "path": err.get("path"),
                            "data": err.get("data"),
                            "errorData": err.get("errorData"),
                            "meta": err.get("meta"),
                            "extensions": err.get("extensions"),
                        }),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        logger.error(
            "Signup failed because onboardAccount is empty. Sanitized response: {}",
            json.dumps(
                sanitize_for_log(result_obj),
                ensure_ascii=False,
                indent=2,
            )[:8000],
        )
        return False, errors

    @staticmethod
    def _dict_contains_card_field(value) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                compact_key = str(key).lower().replace("_", "").replace("-", "")
                if compact_key in {"cardnumber", "card", "cardnumberfield"}:
                    return True
                if isinstance(item, str):
                    item_lower = item.lower()
                    if item_lower in {"cardnumber", "card_generic_error"}:
                        return True
                if PayPalFlow._dict_contains_card_field(item):
                    return True
        elif isinstance(value, list):
            return any(PayPalFlow._dict_contains_card_field(item) for item in value)
        return False

    @staticmethod
    def _is_card_related_signup_error(errors: list[dict[str, Any]]) -> bool:
        card_messages = {
            "CARD_GENERIC_ERROR",
            "INSTRUMENT_SHARING_LIMIT_EXCEEDED",
            "CC_LINKED_TO_FULL_ACCOUNT",
            "CREATE_CARD_ACCOUNT_CANDIDATE_VALIDATION_ERROR",
        }
        for err in errors or []:
            if not isinstance(err, dict):
                continue
            checkpoints = set(err.get("checkpoints") or [])
            if checkpoints.intersection({"addCard", "validate.fi", "card", "fi"}):
                return True
            message = str(err.get("message") or "")
            if message in card_messages:
                return True
            if PayPalFlow._dict_contains_card_field(err.get("errorData")):
                return True
        return False

    @staticmethod
    def _has_signup_error_message(errors: list[dict[str, Any]], message: str) -> bool:
        return any(
            isinstance(err, dict) and str(err.get("message") or "") == message
            for err in errors or []
        )

    @staticmethod
    def _is_create_member_account_retryable_signup_error(errors: list[dict[str, Any]]) -> bool:
        for err in errors or []:
            if not isinstance(err, dict):
                continue
            checkpoints = {str(item) for item in (err.get("checkpoints") or [])}
            message = str(err.get("message") or err.get("_name") or "")
            name = str(err.get("_name") or "")
            if "createMemberAccount" in checkpoints and (
                message == "OAS_ERROR"
                or name == "OAS_ERROR"
                or bool(err.get("contingency"))
            ):
                return True
        return False

    def _wait_and_rotate_card(self, reason: str) -> None:
        logger.warning(
            "{}. Waiting before generating a fresh local Visa/MasterCard...",
            reason,
        )
        delay = self.card_retry_delay_seconds
        if self.card_retry_jitter_seconds:
            delay += random.uniform(0, self.card_retry_jitter_seconds)
        if delay > 0:
            logger.info("Waiting {:.1f}s before next card retry...", delay)
            time.sleep(delay)

        self.card = generate_card(
            proxy_url=self.proxy_config.url,
            country=self._context_country(),
            used_bins=self._used_card_bins,
        )
        if self.card.bin:
            self._used_card_bins.add(self.card.bin)
        logger.info(
            "New generated card for retry: {} exp={}",
            self._masked_card_number(),
            self.card.expiry,
        )

    def _wait_and_rotate_signup_identity(self, reason: str, signup_attempt: int) -> None:
        logger.warning(
            "{}. Retrying SignUpNewMember in-place with fresh account/card info "
            "while preserving the confirmed phone and billing address...",
            reason,
        )
        delay = self.card_retry_delay_seconds
        if self.card_retry_jitter_seconds:
            delay += random.uniform(0, self.card_retry_jitter_seconds)
        if delay > 0:
            logger.info("Waiting {:.1f}s before next signup-info retry...", delay)
            time.sleep(delay)

        current_phone = self.user.phone
        self.user = generate_user(current_phone, country=self._context_country())
        self.card = generate_card(
            proxy_url=self.proxy_config.url,
            country=self._context_country(),
            used_bins=self._used_card_bins,
        )
        if self.card.bin:
            self._used_card_bins.add(self.card.bin)
        self.state.user_id = ""
        self.state.euat_token = ""
        self.state.signup_fallback_reason = ""
        self._used_partial_signup_token = False
        self._on_signup_retry_generated(signup_attempt, reason)
        logger.info(
            "New signup info for retry: email={}, phone={} (preserved), "
            "card={} exp={}, address={}, {}-{} (preserved)",
            sanitize_for_log({"email": self.user.email})["email"],
            sanitize_for_log({"phone": self.user.phone})["phone"],
            self._masked_card_number(),
            self.card.expiry,
            self.address.district,
            self.address.city,
            self.address.state,
        )

    def _signup_with_card_retry(self, token: str, signup_url: str):
        """Retry SignUpNewMember with a fresh generated Visa/MasterCard on card errors."""
        self.state.euat_token = ""
        self.state.signup_fallback_reason = ""
        self._signup_billing_address_prepared = False
        last_errors: list[dict[str, Any]] = []
        last_access_token = ""

        for attempt in range(1, self.max_card_attempts + 1):
            logger.info(
                "Step 3: Creating account (SignUpNewMember), card attempt {}/{}: {}",
                attempt,
                self.max_card_attempts,
                self._masked_card_number(),
            )
            signup_result = self._send_signup_attempt(token, signup_url)
            success, errors = self._consume_signup_result(signup_result, signup_url)
            if success:
                self.state.signup_fallback_reason = ""
                self._used_partial_signup_token = False
                return

            last_errors = errors
            access_token = self._signup_access_token_candidate(signup_result)
            if access_token:
                last_access_token = access_token

            if self._has_signup_error_message(errors, "ACCOUNT_ALREADY_EXISTS"):
                if last_access_token:
                    self.state.euat_token = last_access_token
                    self._used_partial_signup_token = True
                    self.state.signup_fallback_reason = "ACCOUNT_ALREADY_EXISTS"
                    logger.warning(
                        "Signup returned ACCOUNT_ALREADY_EXISTS after a previous "
                        "response already issued an access token. Reusing that "
                        "token and continuing instead of re-submitting signup."
                    )
                    return
                raise RuntimeError(
                    "Signup failed: ACCOUNT_ALREADY_EXISTS and no prior access "
                    "token is available for this session."
                )

            if self._has_signup_error_message(errors, "THREE_DS_CHALLENGE_REQUIRED"):
                if attempt >= self.max_card_attempts:
                    raise RuntimeError(
                        "Signup failed: 3DS interactive challenge required after "
                        f"{self.max_card_attempts} attempts"
                    )
                self._wait_and_rotate_card("3DS interactive challenge required")
                continue

            if self._is_card_related_signup_error(errors):
                if access_token:
                    self.state.euat_token = access_token
                    self._used_partial_signup_token = True
                    self.state.signup_fallback_reason = "CARD_GENERIC_ERROR"
                    logger.warning(
                        "Card/addCard failed but PayPal returned an access token "
                        "or EUAT cookie. "
                        "Continuing to authorization once; if BUYER_NOT_SET is "
                        "returned, the full flow will restart with a fresh "
                        "session/user/card instead of re-submitting signup on "
                        "this already-consumed checkout."
                    )
                    return

                if attempt >= self.max_card_attempts:
                    raise RuntimeError(
                        "Signup failed: card was rejected after "
                        f"{self.max_card_attempts} attempts"
                    )

                self._wait_and_rotate_card("Card rejected by signup/addCard")
                continue

            if access_token:
                self.state.euat_token = access_token
                self._used_partial_signup_token = True
                self.state.signup_fallback_reason = (
                    str(errors[0].get("message") or "SIGNUP_CONTINGENCY")
                    if errors
                    else "SIGNUP_CONTINGENCY"
                )
                logger.info("Got access token from signup error response or EUAT cookie")
                return

            if self._is_create_member_account_retryable_signup_error(errors):
                if attempt >= self.max_card_attempts:
                    raise RuntimeError(
                        "Signup failed: createMemberAccount/OAS_ERROR after "
                        f"{self.max_card_attempts} in-place signup-info attempts"
                    )
                self._wait_and_rotate_signup_identity(
                    "createMemberAccount/OAS_ERROR returned without access token",
                    attempt + 1,
                )
                continue

            if self._has_signup_error_message(errors, "PHONE_CONFIRMATION_REQUIRED"):
                if attempt >= self.max_card_attempts:
                    raise RuntimeError(
                        "Signup failed: PHONE_CONFIRMATION_REQUIRED persists after "
                        f"{self.max_card_attempts} phone-confirmation rounds"
                    )
                logger.warning(
                    "SignUpNewMember returned PHONE_CONFIRMATION_REQUIRED after OTP "
                    "was confirmed; re-running phone confirmation and retrying signup "
                    "(attempt {}/{}).",
                    attempt,
                    self.max_card_attempts,
                )
                if self.sms_provider is not None:
                    self._confirm_phone_with_sms_provider(token, signup_url)
                else:
                    raise RuntimeError(
                        "Signup failed: PHONE_CONFIRMATION_REQUIRED with no SMS "
                        "provider to re-confirm the phone."
                    )
                self._send_tealeaf_form_interaction_batch(signup_url, [
                    "phone", "cardNumber", "cardExpiry", "cardCvv",
                    "password", "firstName", "lastName",
                ])
                time.sleep(1.5)
                continue

            break

        raise RuntimeError(
            "Signup failed: no usable access token obtained. "
            f"Last errors: {json.dumps(sanitize_for_log(last_errors), ensure_ascii=False)[:1000]}"
        )

    @staticmethod
    def _modxo_action_redirect_url(resp) -> str:
        redirect_url = resp.headers.get("Location") or resp.headers.get("x-action-redirect") or ""
        if not redirect_url:
            return ""
        redirect_url = redirect_url.split(";", 1)[0]
        if redirect_url.startswith("/?"):
            redirect_url = f"https://www.paypal.com/pay{redirect_url}"
        elif redirect_url.startswith("/"):
            redirect_url = f"https://www.paypal.com{redirect_url}"
        return redirect_url

    def _follow_modxo_action_redirect(self, resp, referer: str):
        """Follow Next server-action redirects emitted by ModXO.

        PayPal's server action may return a normal Location header or an
        x-action-redirect header such as "/?...;push". In the latter case the
        path is relative to the /pay app, not the site root.
        """
        redirect_url = self._modxo_action_redirect_url(resp)
        if not redirect_url:
            return resp
        logger.info(f"Following ModXO action redirect: {redirect_url[:140]}...")
        return self.session.get(
            redirect_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Referer": referer,
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            },
        )

    def _load_modxo_rsc(self, page_url: str, referer: str):
        """Load the post-country-change RSC payload like the browser."""
        if not page_url:
            return None
        rsc_token = "".join(random.choice(string.ascii_letters + string.digits + "_-") for _ in range(15))
        rsc_url = self._url_append_params(page_url, {"_rsc": rsc_token})
        try:
            headers = {
                **self._browser_headers(accept="*/*"),
                "Referer": referer,
                "RSC": "1",
                "Next-Url": "/",
                "Next-Router-State-Tree": self._modxo_router_state_tree_header(),
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            nsid = self._paypal_nsid_header_value()
            if nsid:
                headers["PayPal-NSID"] = nsid
            if self.state.modxo_deployment_id:
                headers["X-Deployment-Id"] = self.state.modxo_deployment_id
            resp = self.session.get(
                rsc_url,
                headers=headers,
            )
            self._apply_modxo_public_credential_fields(resp.text)
            return resp
        except Exception as e:
            logger.debug("ModXO RSC refresh failed: {}", e)
            return None

    def _phase2_create_account(self):
        """Submit 'Create Account' action to get to the signup page."""
        logger.info("--- Phase 2: Create account flow ---")

        if self._is_ec_token(self.state.ec_token):
            self.state.signup_url = self.state.signup_url or self._build_signup_url()
            logger.info(
                "EC checkout token is already available; skipping ModXO create-account action: {}",
                sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"],
            )
            try:
                signup_resp = self.session.get(
                    self.state.signup_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                        "Referer": f"https://www.paypal.com/agreements/approve?ba_token={self.ba_token}",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-User": "?1",
                        "Sec-Fetch-Dest": "document",
                    },
                )
                self._capture_datadome_clientid(signup_resp.text)
                if getattr(signup_resp, "status_code", 0) == 200 and self._signup_context_seed_html_looks_usable(signup_resp.text):
                    self._last_signup_html = signup_resp.text
                    self._last_signup_url = str(getattr(signup_resp, "url", "") or self.state.signup_url)
                    self._last_signup_status = int(getattr(signup_resp, "status_code", 200) or 200)
                self._apply_signup_content_metadata(signup_resp.text)
                if self._content_metadata_is_unresolved():
                    self._ensure_live_signup_content_manifest(referer=self.state.signup_url)
            except Exception as e:
                logger.debug("Existing EC signup warm-up failed: {}", e)
            return

        resp = None
        pay_resp = None
        pay_with_card_url = ""
        # Browser trace (2026-07-04): ModXO is a Next server-action flow.
        # First click "Pay with Card", then submit an email/createAccount
        # action, whose RSC payload returns onboardingRedirectUrl.
        pay_base_url = (
            f"https://www.paypal.com/pay?ssrt={self.state.ssrt}"
            f"&token={self.ba_token}&ul=1"
        )
        pay_url = (
            f"https://www.paypal.com/pay/?ssrt={self.state.ssrt}"
            f"&token={self.ba_token}&ul=1&ctxId={self.state.ctx_id}"
            f"&country.x={self._profile_country()}"
        )
        try:
            submit_action_id = (
                self.state.submit_public_credential_action_id
                or self.state.create_user_action_id
            )
            if not submit_action_id:
                raise RuntimeError("missing dynamic ModXO Next-Action ids")

            pay_page_url = self.state.modxo_pay_page_url or pay_url
            if self.state.modxo_country_selected and self.state.ctx_id:
                logger.info(
                    "ModXO country action was already accepted during captcha-solved "
                    "frontend packets; continuing from {}...",
                    pay_page_url[:140],
                )
                self._send_modxo_frontend_captcha_solved_packets(
                    pay_page_url,
                    include_base=False,
                    include_country=True,
                )
                self._load_modxo_rsc(pay_page_url, referer=pay_page_url)
            elif self.state.modxo_country_action_id and self.state.modxo_country_action_bound:
                # Browser request 190: inline country handleChange action.  This
                # is what actually activates ctxId/country for the ModXO email
                # submit flow; using showCreateAccountAction here can return an
                # authchallenge HTML document and never produces an EC token.
                logger.info("Submitting browser-like ModXO country server action...")
                cfci = (
                    self._frontend_captcha_solved_cfci()
                    if self.state.paypal_captcha_solved
                    else self._modxo_cfci("Pay_With_Card")
                )
                pay_with_card_url = self._url_with_paypal_client_cfci(pay_base_url, cfci)
                pay_resp = self._post_modxo_country_action(
                    page_url=pay_base_url,
                    country=self._profile_country(),
                    cfci=cfci,
                )
                if pay_resp is None:
                    raise RuntimeError("ModXO country action returned no response")
            else:
                logger.info("Submitting browser-like Pay_With_Card server action...")
                pay_with_card_url = self._modxo_url_with_cfci(pay_url, "Pay_With_Card")
                pay_resp = self.session.post(
                    pay_with_card_url,
                    files=[
                        ("_1_ctxId", (None, self.state.ctx_id)),
                        ("_1_formName", (None, "createAccountAction")),
                        ("0", (None, '["$K1"]')),
                    ],
                    headers=self._modxo_server_action_headers(
                        referer=pay_url,
                        action_id=self.state.show_create_account_action_id,
                    ),
                )

            pay_redirect_url = self._modxo_action_redirect_url(pay_resp) if pay_resp is not None else ""
            if pay_redirect_url:
                pay_page_url = urllib.parse.urljoin(pay_base_url, pay_redirect_url)
                self.state.modxo_pay_page_url = pay_page_url
                ctx_id = self._first_query_value(pay_page_url, "ctxId")
                if ctx_id:
                    self.state.ctx_id = ctx_id
                self.state.modxo_country_selected = True
                self._send_modxo_frontend_captcha_solved_packets(
                    pay_page_url,
                    include_base=False,
                    include_country=True,
                )
                self._load_modxo_rsc(pay_page_url, referer=pay_page_url)
            elif pay_resp is not None and looks_like_paypal_authchallenge(pay_resp.text):
                logger.warning("Pay_With_Card returned authchallenge HTML; manual verification is required.")
                if not self._validate_authchallenge_if_possible(pay_resp.text, pay_base_url):
                    raise PayPalAuthChallenge(
                        "Pay_With_Card",
                        pay_resp.status_code,
                        pay_resp.headers.get("paypal-debug-id", ""),
                        pay_resp.text,
                    )
                else:
                    retry_cfci = (
                        self._frontend_captcha_solved_cfci()
                        if self.state.paypal_captcha_solved
                        else self._modxo_cfci("Pay_With_Card")
                    )
                    pay_resp = self.session.post(
                        pay_with_card_url,
                        files=[
                            ("1", (None, f'"{self.state.modxo_country_action_bound}"')),
                            ("0", (None, f'["$@1","{self._profile_country()}"]')),
                        ] if self.state.modxo_country_action_id and self.state.modxo_country_action_bound else [
                            ("_1_ctxId", (None, self.state.ctx_id)),
                            ("_1_formName", (None, "createAccountAction")),
                            ("0", (None, '["$K1"]')),
                        ],
                        headers={
                            **self._modxo_server_action_headers(
                                referer=pay_base_url if self.state.modxo_country_action_id else pay_url,
                                action_id=(
                                    self.state.modxo_country_action_id
                                    or self.state.show_create_account_action_id
                                ),
                            ),
                            "PayPal-Client-Cfci": retry_cfci,
                        },
                    )
                    pay_redirect_url = self._modxo_action_redirect_url(pay_resp)
                    if pay_redirect_url:
                        pay_page_url = urllib.parse.urljoin(pay_base_url, pay_redirect_url)
                        self.state.modxo_pay_page_url = pay_page_url
                        ctx_id = self._first_query_value(pay_page_url, "ctxId")
                        if ctx_id:
                            self.state.ctx_id = ctx_id
                        self.state.modxo_country_selected = True
                        self._send_modxo_frontend_captcha_solved_packets(
                            pay_page_url,
                            include_base=False,
                            include_country=True,
                        )
                        self._load_modxo_rsc(pay_page_url, referer=pay_page_url)

            # The /pay reload after Pay_With_Card loads the ddbm2 bootstrap,
            # then the INPUT_PASSWORD FraudNet/field events fire before the
            # Continue_To_Payment server action.
            send_da_bootstrap(self.session, referer=pay_page_url, include_ddbm=True)
            send_fraudnet_rdt(
                self.session,
                self.ba_token,
                app_id="IWC_NEXT_CHECKOUT_INPUT_PASSWORD",
                referer=pay_page_url,
            )
            send_device_fingerprint(
                self.session,
                self.ba_token,
                app_id="IWC_NEXT_CHECKOUT_INPUT_PASSWORD",
                referer="https://www.paypal.com/",
                wrapped=True,
                page_url=pay_page_url,
                page_referer="",
                include_pa=True,
            )
            self._send_signup_field_events(
                self.session,
                self.ba_token,
                ["password"],
                app_id="IWC_NEXT_CHECKOUT_INPUT_PASSWORD",
                referer=pay_page_url,
            )
            send_identity_di_log(self.session, self.ba_token, referer=pay_page_url, eligible=False)

            send_fraudnet_rdt(
                self.session,
                self.ba_token,
                app_id="IWC_NEXT_CHECKOUT",
                referer=pay_page_url,
            )
            self._send_signup_field_events(
                self.session,
                self.ba_token,
                ["login_email"],
                app_id="IWC_NEXT_CHECKOUT",
                referer=pay_page_url,
            )

            logger.info("Submitting browser-like Continue_To_Payment server action...")
            continue_cfci = (
                self._frontend_captcha_solved_cfci()
                if self.state.paypal_captcha_solved
                else self._modxo_cfci("Continue_To_Payment")
            )
            continue_url = self._url_with_paypal_client_cfci(pay_page_url, continue_cfci)
            if self.state.submit_public_credential_action_id:
                continue_files = [
                    ("_1_fn_sync_data", (None, build_fn_sync_data(self.ba_token, session=self.session))),
                    ("_1_ctxId", (None, self.state.ctx_id)),
                    ("_1_passkeyChallenge", (None, self.state.passkey_challenge)),
                    ("_1_rpId", (None, self.state.rp_id or "www.paypal.com")),
                    ("_1_login_email", (None, self.user.email)),
                    ("_1_login_password", (None, "")),
                    (
                        "_1_login_phone_country_code",
                        (None, self.state.login_phone_country_code or self.user.phone_country_code or "+55"),
                    ),
                    ("_1_formName", (None, "email")),
                    ("0", (None, '["$K1"]')),
                ]
            else:
                continue_files = [
                    ("_1_ctxId", (None, self.state.ctx_id)),
                    ("_1_token", (None, self.ba_token)),
                    ("_1_login_email", (None, self.user.email)),
                    ("_1_formName", (None, "createAccount")),
                    ("0", (None, f'["$K1",{{"emailSubmitTime":{int(time.time() * 1000)}}}]')),
                ]
            def post_continue_action(action_id: str):
                return self.session.post(
                    continue_url,
                    files=continue_files,
                    headers={
                        **self._modxo_server_action_headers(
                            referer=pay_page_url,
                            action_id=action_id,
                        ),
                        "PayPal-Client-Cfci": continue_cfci,
                    },
                )

            rsc_resp = post_continue_action(submit_action_id)
            if looks_like_paypal_authchallenge(rsc_resp.text):
                logger.warning("Continue_To_Payment returned authchallenge HTML; manual verification is required.")
                if not self._validate_authchallenge_if_possible(rsc_resp.text, pay_page_url):
                    raise PayPalAuthChallenge(
                        "Continue_To_Payment",
                        rsc_resp.status_code,
                        rsc_resp.headers.get("paypal-debug-id", ""),
                        rsc_resp.text,
                    )
                else:
                    rsc_resp = post_continue_action(submit_action_id)

            if (
                not self._extract_onboarding_redirect(rsc_resp.text)
                and not (rsc_resp.status_code in (301, 302, 303, 307, 308) or rsc_resp.headers.get("x-action-redirect"))
                and self._modxo_static_action_ids_enabled()
                and self._refresh_modxo_action_ids_from_chunks(reason="continue_to_payment_no_redirect")
            ):
                refreshed_submit_action_id = (
                    self.state.submit_public_credential_action_id
                    or self.state.create_user_action_id
                    or submit_action_id
                )
                if refreshed_submit_action_id != submit_action_id:
                    submit_action_id = refreshed_submit_action_id
                    rsc_resp = post_continue_action(submit_action_id)
            onboarding_url = self._extract_onboarding_redirect(rsc_resp.text)
            if onboarding_url:
                logger.info(f"Onboarding redirect URL: {onboarding_url[:140]}...")
                onboarding_token = self._first_query_value(onboarding_url, "token")
                if self._is_ec_token(onboarding_token):
                    self.state.ec_token = onboarding_token
                    logger.info(
                        "EC Token (from onboarding redirect): {}",
                        sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"],
                    )
                resp = self.session.get(
                    onboarding_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                        "Referer": pay_page_url,
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-User": "?1",
                        "Sec-Fetch-Dest": "document",
                    },
                )
            elif rsc_resp.status_code in (301, 302, 303, 307, 308) or rsc_resp.headers.get("x-action-redirect"):
                resp = self._follow_modxo_action_redirect(rsc_resp, pay_page_url)
        except Exception as e:
            logger.warning(f"Browser-like ModXO server-action path failed: {e}")

        if resp is None:
            # Fallback for older deployments that still accept a compact form.
            base_url = (
                f"https://www.paypal.com/pay?ssrt={self.state.ssrt}"
                f"&token={self.ba_token}&ul=1"
            )
            base_url = self._modxo_url_with_cfci(base_url, "Pay_With_Card")

            form_data = {
                "ctxId": self.state.ctx_id,
                "formName": "createAccountAction",
                "fn_sync_data": build_fn_sync_data(self.ba_token, session=self.session),
            }

            resp = self.session.post(base_url, data=form_data, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.paypal.com",
                "Referer": f"https://www.paypal.com/pay?ssrt={self.state.ssrt}&token={self.ba_token}&ul=1",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            })

        # Handle redirect chain
        while resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get("Location", "")
            if redirect_url.startswith("/"):
                redirect_url = f"https://www.paypal.com{redirect_url}"
            logger.info(f"Following redirect: {redirect_url[:100]}...")
            resp = self.session.get(redirect_url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
            })

        html = resp.text
        self._capture_datadome_clientid(html)

        # Extract EC token from the new URL or page content
        ec_match = re.search(r"token=(EC-\w+)", str(resp.url))
        if ec_match:
            self.state.ec_token = ec_match.group(1)
            logger.info("EC Token: {}", sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"])
        else:
            ec_match = re.search(r"EC-\w+", html)
            if ec_match:
                self.state.ec_token = ec_match.group(0)
                logger.info("EC Token (from HTML): {}", sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"])

        # The real browser next loads checkoutweb/weasley.  This request is not
        # just cosmetic: it sets checkout cookies (for example l7_az/x-pp-s),
        # exposes the current content hash, and matches the Referer/context
        # expected by the following GraphQL mutations.
        if self.state.ec_token:
            signup_url = self._build_signup_url()
            self.state.signup_url = signup_url
            logger.info(f"Loading checkout signup app: {signup_url}")
            signup_resp = self.session.get(
                signup_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Referer": str(resp.url),
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-User": "?1",
                    "Sec-Fetch-Dest": "document",
                },
            )
            logger.info(
                "Checkout signup app loaded: {} bytes={}",
                signup_resp.status_code,
                len(signup_resp.content),
            )
            self._capture_datadome_clientid(signup_resp.text)
            if signup_resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = signup_resp.headers.get("Location", "")
                if redirect_url:
                    redirect_url = urllib.parse.urljoin(signup_url, redirect_url)
                    if "/checkoutweb/signup" in redirect_url:
                        self.state.signup_url = redirect_url
                    logger.warning(
                        "Checkout signup app redirected to {}; preserving signup referer {}",
                        redirect_url[:140],
                        self.state.signup_url[:140],
                    )
                    signup_resp = self.session.get(
                        redirect_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Referer": signup_url,
                            "Upgrade-Insecure-Requests": "1",
                            "Sec-Fetch-Site": "same-origin",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-User": "?1",
                            "Sec-Fetch-Dest": "document",
                        },
                    )
                    self._capture_datadome_clientid(signup_resp.text)
            if looks_like_paypal_authchallenge(signup_resp.text):
                logger.info(
                    "Checkout signup app returned authchallenge HTML; "
                    "manual verification is required before content manifest extraction."
                )
                if not self._validate_authchallenge_if_possible(signup_resp.text, signup_url):
                    raise PayPalAuthChallenge(
                        "checkoutweb_signup",
                        signup_resp.status_code,
                        signup_resp.headers.get("paypal-debug-id", ""),
                        signup_resp.text,
                    )
                else:
                    signup_resp = self.session.get(
                        signup_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Referer": signup_url,
                            "Upgrade-Insecure-Requests": "1",
                            "Sec-Fetch-Site": "same-origin",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-User": "?1",
                            "Sec-Fetch-Dest": "document",
                        },
                    )
                    self._capture_datadome_clientid(signup_resp.text)
            if getattr(signup_resp, "status_code", 0) == 200 and self._signup_context_seed_html_looks_usable(signup_resp.text):
                self._last_signup_html = signup_resp.text
                self._last_signup_url = str(getattr(signup_resp, "url", "") or signup_url)
                self._last_signup_status = int(getattr(signup_resp, "status_code", 200) or 200)
            self._apply_signup_content_metadata(signup_resp.text)
            manifest_url = (
                self._extract_content_manifest_url(
                    signup_resp.text,
                    self._extract_window_initial_data(signup_resp.text),
                    str(signup_resp.url or signup_url),
                )
                or self.state.content_manifest_url
            )
            if manifest_url and (
                self._content_metadata_is_unresolved()
                or not self.state.content_manifest_key
            ):
                self._fetch_signup_content_manifest_metadata(
                    manifest_url,
                    referer=str(signup_resp.url or signup_url),
                )
            if self._content_metadata_is_unresolved():
                self._scan_signup_assets_for_content_metadata(
                    signup_resp.text,
                    str(signup_resp.url or signup_url),
                )

        if not self._is_ec_token(self.state.ec_token):
            raise RuntimeError(
                "Create account flow did not produce an EC checkout token. "
                "The original BA token cannot be used for checkoutweb signup "
                "or InstallmentOptionsQuery; check whether the BA token is "
                "expired/invalid or the ModXO server-action response changed."
            )

        if self._content_metadata_is_unresolved():
            self._ensure_live_signup_content_manifest(
                referer=self.state.signup_url or str(resp.url),
            )

        # Send Tealeaf for new page
        signup_page_url = self.state.signup_url or str(resp.url)
        self._send_tealeaf_data(
            self.session,
            signup_page_url,
        )
        self._send_datadog_rum_view(
            self.session,
            signup_page_url,
            self.ba_token,
            dd_config=_DD_WEASLEY_CONFIG,
        )
        send_observability_emit(self.session, self.ba_token)

        if self.state.ec_token:
            # Browser trace sends signup-page Weasley logs before the warm-up
            # GraphQL queries.
            send_weasley_log(
                self.session,
                self.state.ec_token,
                self.state.signup_url,
                [
                    "weasley_client_eligibility_check_success",
                    "WEASLEY_PAGE_INTERACTIVE_FPTI",
                    "WEASLEY_PREPARE_BILLING_PAGE_FPTI",
                    "weasley_payment_request_api_available",
                ],
                country=self._context_country(),
                lang=self._context_lang(),
            )

        logger.info("Sending checkout session GraphQL queries...")
        try:
            self.session.graphql(
                "DeferredFeature",
                DEFERRED_FEATURE_QUERY,
                {
                    "channel": "WEB",
                    "countryCodeAsString": self.address.country,
                    "integrationType": "XoSignupAuth",
                    "isBaslAsString": "false",
                    "isForcedGuest": "false",
                    "token": self.state.ec_token or self.ba_token,
                },
            )
        except Exception as e:
            logger.warning(f"DeferredFeature failed: {e}")

        try:
            self.session.graphql(
                "GriffinMetadataQuery",
                GRIFFIN_METADATA_QUERY,
                {
                    "countryCode": self.address.country,
                    "languageCode": "pt",
                    "shippingCountryCode": self.address.country,
                },
            )
        except Exception as e:
            logger.warning(f"GriffinMetadataQuery failed: {e}")

        try:
            self.session.graphql(
                "CheckoutSessionDataQuery",
                CHECKOUT_SESSION_DATA_QUERY,
                {"token": self.state.ec_token or self.ba_token},
            )
        except Exception as e:
            logger.warning(f"CheckoutSessionDataQuery failed: {e}")

        if os.getenv("PAYPAL_SKIP_SUPPORTED_FUNDING_SOURCES", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            logger.debug("Skipping SupportedFundingSourcesQuery warm-up by environment")
        else:
            try:
                self.session.graphql(
                    "SupportedFundingSourcesQuery",
                    SUPPORTED_FUNDING_SOURCES_QUERY,
                    {
                        "token": self.state.ec_token or self.ba_token,
                        "userCountry": self.address.country,
                    },
                )
            except Exception as e:
                logger.warning(f"SupportedFundingSourcesQuery failed: {e}")

        if self.state.ec_token:
            send_analytics_ts(
                self.session,
                "main:billing:hagrid:billingwithoutpurchase:member:billing",
                self.ba_token,
                ec_token=self.state.ec_token,
            )
            self._send_tealeaf_data(self.session, signup_page_url)
            send_device_fingerprint(
                self.session,
                self.state.ec_token,
                app_id="CHECKOUTUINODEWEB_ONBOARDING_LITE",
                referer=signup_page_url,
                wrapped=True,
            )

    def _phase3_signup_and_2fa(self):
        """Submit the signup form and trigger 2FA SMS.

        Actual flow discovered from traffic capture:
        1. InitiateRiskBasedTwoFactorPhoneConfirmationMutation → sends SMS, returns authId + challengeId
        2. ConfirmRiskBasedTwoFactorPhoneConfirmationMutation → verifies OTP pin with authId + challengeId
        3. SignUpNewMemberMutation → creates account with all user data + card + address
        """
        logger.info("--- Phase 3: Signup form + 2FA ---")

        # Send initial Tealeaf page activity before the user flow starts.
        signup_url = self.state.signup_url or "https://www.paypal.com/checkoutweb/signup"
        self._send_tealeaf_data(self.session, signup_url)

        if not self._is_ec_token(self.state.ec_token):
            raise RuntimeError(
                "Cannot start signup/2FA without an EC checkout token. "
                "Run Phase 2 again with a valid BA token."
            )
        token = self.state.ec_token

        # Browser emits idapps/graphql getOtpChallengeOperation around the OTP
        # challenge context before the signup/2FA path proceeds.  Missing this
        # packet leaves later authchallenge decisions with a thinner risk trace.
        self._send_idapps_get_otp_challenge(token, signup_url)

        # Step 1/2: Send SMS and confirm OTP. If the OTP is wrong, the
        # operator can either retry a code for the same phone or enter a new
        # phone number to trigger a fresh challenge.
        self._confirm_phone_with_retry(token, signup_url)

        self._send_tealeaf_form_interaction_batch(signup_url, [
            "email", "phone", "cardNumber", "cardExpiry", "cardCvv",
            "password", "firstName", "lastName",
            "billingLine1", "billingCity", "billingPostalCode", "billingState",
            "dateOfBirth", "identityDocumentNumber",
        ])
        self._send_datadog_rum_action(
            self.session,
            "signup_form_fill",
            signup_url,
            dd_config=_DD_WEASLEY_CONFIG,
            api="xhr",
        )

        # The compliance contentIdentifier is deployment/content-hash specific.
        # Refresh it right before SignUpNewMember so a stale checkoutweb bundle
        # hash does not turn into an opaque OAS/createMemberAccount rejection.
        self._ensure_live_signup_content_manifest(referer=signup_url)
        if self._content_metadata_is_unresolved():
            self._refresh_signup_content_metadata(referer=signup_url)
        if self._content_metadata_is_unresolved() and self.state.content_hash:
            self.state.content_identifier = self._resolved_content_identifier()
        if self._content_metadata_is_unresolved():
            self._apply_configured_or_cached_signup_content_metadata()
        if self._content_metadata_is_unresolved():
            logger.warning(
                "Proceeding with short signup contentIdentifier {}; no contentHash was found "
                "in the latest checkoutweb HTML/assets.",
                self.state.content_identifier or self._short_content_identifier(),
            )

        # Step 3: Sign up new member with all user data. If PayPal rejects the
        # card at addCard/validate.fi/cardNumber, fetch a new generated
        # Visa/MasterCard and submit SignUpNewMember again.
        self._signup_with_card_retry(token, signup_url)

        if not self.state.euat_token:
            raise RuntimeError(
                "Signup failed: no access token obtained. "
                "Cannot proceed to authorization without authentication."
            )

        self._ensure_euat_cookie()

        self._send_datadog_rum_action(
            self.session,
            "signup_complete",
            signup_url,
            dd_config=_DD_WEASLEY_CONFIG,
            api="xhr",
        )

        # Send analytics for signup completion
        send_analytics_ts(
            self.session,
            "main:billing:hagrid:billingwithoutpurchase:member:review",
            self.ba_token,
            ec_token=self.state.ec_token,
            user_id=self.state.user_id,
        )

    def _ensure_euat_cookie(self) -> None:
        """Write EUAT into both PayPal cookie scopes used by Hermes/Hagrid."""
        if not self.state.euat_token:
            return
        cookie_name = "AV894Kt2TSumQQrJwe-8mzmyREO"
        for domain in (".paypal.com", "www.paypal.com"):
            try:
                self.session.client.cookies.set(
                    cookie_name,
                    self.state.euat_token,
                    domain=domain,
                    path="/",
                )
            except Exception as e:
                logger.debug(f"Failed to set EUAT cookie for {domain}: {e}")

    def _load_checkoutweb_drop(self, signup_url: str) -> None:
        """Mirror the checkoutweb/drop cleanup requests before Hermes fallback."""
        if os.getenv("PAYPAL_SKIP_CHECKOUTWEB_DROP", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return
        headers = {
            "Accept": "*/*",
            "Referer": signup_url or self.state.signup_url,
            "X-Requested-With": "fetch",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        for attempt in range(2):
            try:
                resp = self.session.get(
                    "https://www.paypal.com/checkoutweb/drop",
                    headers=headers,
                )
                logger.info(
                    "checkoutweb/drop warm-up {}/2 status={}",
                    attempt + 1,
                    resp.status_code,
                )
            except Exception as e:
                logger.debug("checkoutweb/drop warm-up failed: {}", e)

    def _hermes_url(self, *, add_fi_contingency: bool = False, billing_lite: bool = False) -> str:
        params: dict[str, str] = {
            "ssrt": self.state.ssrt,
            "ul": "1",
            "modxo_redirect_reason": "guest_user",
            "locale.x": self._profile_locale(),
            "country.x": self._profile_country(),
            "ba_token": self.ba_token,
            "token": self.state.ec_token,
            "rcache": "1",
            "cookieBannerVariant": "hidden",
            "fromSignupLite": "true",
        }
        fallback_reason = (self.state.signup_fallback_reason or "").strip()
        if fallback_reason:
            params["fallback"] = "1"
            if fallback_reason == "CARD_GENERIC_ERROR":
                params["reason"] = "Q0FSRF9HRU5FUklDX0VSUk9S"
            else:
                params["reason"] = fallback_reason
        if add_fi_contingency:
            params["addFIContingency"] = "noretry"
            params["redirectToHermes"] = "true"
            params.setdefault("fallback", "1")
        if billing_lite:
            params["billingLite"] = "1"
        return "https://www.paypal.com/webapps/hermes?" + urllib.parse.urlencode(params)

    @staticmethod
    def _paypal_return_url_with_ba_token(return_url: str, ba_token: str) -> str:
        """Stripe's PayPal bridge expects the BA token on the return URL.

        PayPal's GraphQL `authorize.returnURL.href` can contain only
        `status=success&token=EC-*`; the browser request observed in roxy adds
        `ba_token=BA-*` before navigating to `pm-redirects.stripe.com`.
        """
        if not return_url or not ba_token:
            return return_url
        parts = urllib.parse.urlsplit(return_url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not any(key == "ba_token" for key, _ in query):
            query.append(("ba_token", ba_token))
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urllib.parse.urlencode(query),
                parts.fragment,
            )
        )

    @staticmethod
    def _parse_redirect_status(url: str) -> dict[str, object]:
        if not url:
            return {}
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        session_id = ""
        match = re.search(r"/c/pay/([^/?#]+)", parsed.path)
        if match:
            session_id = match.group(1)
        return {
            "host": parsed.netloc,
            "checkout_session_id": session_id,
            "redirect_status": query.get("redirect_status") or query.get("status"),
            "redirect_pm_type": query.get("redirect_pm_type"),
            "payment_intent": query.get("payment_intent"),
            "has_payment_intent_client_secret": bool(query.get("payment_intent_client_secret")),
        }

    def _extract_user_id_from_review_html(self, text: str) -> str:
        for pattern in (
            r'(?:party_id|cust|userId)["\'=:]+([A-Z0-9]{8,24})',
            r'"userId"\s*:\s*"([A-Z0-9]{8,24})"',
            r'"partyId"\s*:\s*"([A-Z0-9]{8,24})"',
        ):
            match = re.search(pattern, text or "")
            if match:
                return match.group(1)
        return ""

    def _load_hagrid_review_context(
        self,
        hermes_base_url: str,
        hermes_contingency_url: str,
        review_referer: str,
    ) -> bool:
        """Load billingLite/Hermes pages to bind EUAT cookies to buyer context."""
        self._ensure_euat_cookie()
        base_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Referer": self.state.signup_url,
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        }
        ok = False
        last_resp = None
        for url in (hermes_contingency_url, hermes_base_url, review_referer):
            try:
                referer = base_headers["Referer"]
                resp = self.session.get(url, headers={**base_headers, "Referer": referer})
                for _ in range(4):
                    if resp.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = resp.headers.get("Location", "")
                    if not location:
                        break
                    location = urllib.parse.urljoin(str(resp.url), location)
                    logger.info(f"Following Hermes/Hagrid redirect: {location[:140]}...")
                    resp = self.session.get(
                        location,
                        headers={**base_headers, "Referer": str(resp.url)},
                    )
                last_resp = resp
                ok = ok or (200 <= resp.status_code < 400)
                if not self.state.user_id:
                    user_id = self._extract_user_id_from_review_html(resp.text)
                    if user_id:
                        self.state.user_id = user_id
                        logger.info(f"User ID from Hermes page: {self.state.user_id}")
            except Exception as e:
                logger.warning(f"Loading Hermes/Hagrid URL failed: {e}")

        if last_resp is not None:
            logger.info(
                "Hermes/Hagrid review context loaded: status={} bytes={} user_id_present={} euat_present={}",
                last_resp.status_code,
                len(last_resp.content),
                bool(self.state.user_id),
                bool(self.state.euat_token),
            )
        return ok

    def _authorize_metadata_candidates(self) -> list[str]:
        candidates = [
            self.state.ec_token,
            self.ba_token,
            self.state.paypal_client_metadata_id,
        ]
        result: list[str] = []
        for value in candidates:
            if value and value not in result:
                result.append(value)
        return result or [self.state.paypal_client_metadata_id]

    def _sync_buyer_context(self, token: str, referer: str) -> bool:
        """Refresh the checkout buyer context with the signed-up member EUAT.

        Ported from paypal-agreement-protocol flow._sync_buyer_context: the
        final billing.authorize mutation requires the member identity to be
        attached to the EC checkout; without this sync it returns BUYER_NOT_SET.
        Refreshes the EUAT access token and userId from the GraphQL buyer block.
        """
        if not self.state.euat_token:
            logger.warning("Buyer-context sync skipped because the signup access token is missing")
            return False

        self._ensure_euat_cookie()

        try:
            result = self.session.graphql(
                "BuyerContextQuery",
                BUYER_CONTEXT_QUERY,
                {"token": token},
                extra_headers={
                    "Referer": referer,
                    "X-App-Name": "checkoutuinodeweb",
                    "X-PayPal-Internal-EUAT": self.state.euat_token,
                    "PayPal-Client-Context": token,
                    "PayPal-Client-Metadata-Id": self.state.paypal_client_metadata_id,
                    "X-Country": self._profile_country(),
                    "X-Locale": self._profile_locale(),
                },
                endpoint="https://www.paypal.com/graphql/",
            )
        except Exception as sync_error:
            logger.warning("Buyer-context GraphQL refresh failed: {}", sync_error)
            return False

        result_obj = result[0] if isinstance(result, list) and result else result
        if not isinstance(result_obj, dict):
            logger.warning("Buyer-context refresh returned type {}", type(result_obj).__name__)
            return False
        data_obj = result_obj.get("data")
        data_obj = data_obj if isinstance(data_obj, dict) else {}
        checkout = data_obj.get("checkoutSession")
        checkout = checkout if isinstance(checkout, dict) else {}
        buyer = checkout.get("buyer")
        buyer = buyer if isinstance(buyer, dict) else {}
        auth = buyer.get("auth")
        auth = auth if isinstance(auth, dict) else {}
        refreshed_access_token = auth.get("accessToken")
        if isinstance(refreshed_access_token, str) and refreshed_access_token:
            self.state.euat_token = refreshed_access_token
            self._ensure_euat_cookie()
        user_id = buyer.get("userId")
        if isinstance(user_id, str) and user_id:
            self.state.user_id = user_id
            logger.info("Buyer context synchronized for user {}", user_id)
            return True
        logger.warning("Buyer-context refresh completed without a buyer userId")
        return False

    def _phase4_authorize(self) -> dict[str, object]:
        """Send the final authorize mutation to approve the billing agreement."""
        logger.info("--- Phase 4: Final authorization ---")

        if self.state.signup_fallback_reason:
            self._load_checkoutweb_drop(self.state.signup_url)

        hermes_base_url = self._hermes_url()
        hermes_contingency_url = self._hermes_url(add_fi_contingency=True)
        review_referer = self._hermes_url(billing_lite=True)
        review_url = f"{review_referer}#/billingweb/review"

        # Browser trace shows that Hagrid/Hermes is actually loaded before the
        # authorize mutation. This binds EUAT/cookies to a buyer context; without
        # it GraphQL authorize can return BUYER_NOT_SET.
        self._load_hagrid_review_context(
            hermes_base_url,
            hermes_contingency_url,
            review_referer,
        )

        # Send Tealeaf for the review page
        self._send_tealeaf_data(self.session, review_url)
        self._send_datadog_rum_view(
            self.session,
            review_url,
            self.ba_token,
            dd_config=_DD_HAGRID_CONFIG,
        )
        self._send_datadog_rum_action(
            self.session,
            "review_page_loaded",
            review_url,
            dd_config=_DD_HAGRID_CONFIG,
            api="xhr",
        )
        # The critical authorize mutation
        billing_agreement_id = self.state.ec_token or self.ba_token
        logger.info(
            "Authorizing billing agreement: {}",
            sanitize_for_log({"billingAgreementId": billing_agreement_id})["billingAgreementId"],
        )

        buyer_synced = False
        if self.state.euat_token:
            buyer_synced = self._sync_buyer_context(billing_agreement_id, review_referer)
        if self._strict_buyer_context and not buyer_synced:
            raise RuntimeError(
                "BUYER_CONTEXT_SYNC_FAILED: member identity is not attached to the billing checkout"
            )

        def send_authorize(metadata_id: str):
            return self.session.graphql(
                "authorize",
                AUTHORIZE_BILLING_MUTATION,
                {
                    "billingAgreementId": billing_agreement_id,
                    "fundingPreference": {
                        "balancePreference": "OPT_OUT",
                    },
                    "legalAgreements": {},
                },
                extra_headers={
                    "Referer": review_referer,
                    "X-App-Name": "checkoutuinodeweb",
                    "PayPal-Client-Context": None,
                    "PayPal-Client-Metadata-Id": metadata_id,
                    "X-Country": None,
                    "X-Locale": None,
                },
                batched=True,
                endpoint="https://www.paypal.com/graphql/",
            )

        result: object = None
        last_authorize_attempt = 0
        metadata_candidates = self._authorize_metadata_candidates()
        for authorize_attempt in range(1, self.max_authorize_attempts + 1):
            last_authorize_attempt = authorize_attempt
            if authorize_attempt > 1:
                logger.warning(
                    "authorize returned BUYER_NOT_SET; reloading Hagrid/Hermes "
                    "review context and retrying authorize attempt {}/{}...",
                    authorize_attempt,
                    self.max_authorize_attempts,
                )
                self._load_hagrid_review_context(
                    hermes_base_url,
                    hermes_contingency_url,
                    review_referer,
                )
                self._send_tealeaf_data(self.session, review_url)
                if self.state.euat_token:
                    self._sync_buyer_context(billing_agreement_id, review_referer)
                time.sleep(min(2, authorize_attempt))

            metadata_id = metadata_candidates[(authorize_attempt - 1) % len(metadata_candidates)]
            logger.info(
                "authorize attempt {}/{} using metadata candidate {}",
                authorize_attempt,
                self.max_authorize_attempts,
                sanitize_for_log({"token": metadata_id})["token"],
            )
            result = send_authorize(metadata_id)
            if not self._has_buyer_not_set(result):
                break

        if result is None:
            result = {
                "errors": [
                    {
                        "message": "authorize was not attempted",
                    }
                ]
            }

        logger.info(
            "Authorization result (sanitized): {}",
            json.dumps(sanitize_for_log(result), ensure_ascii=False, indent=2)[:1000],
        )

        # Extract return URL and user ID from response
        try:
            if isinstance(result, list):
                result_list = list(result)
                first_result = result_list[0] if result_list else {}
                result_obj = first_result if isinstance(first_result, dict) else {}
            elif isinstance(result, dict):
                result_obj = result
            else:
                result_obj = {}
            data = result_obj.get("data") if isinstance(result_obj, dict) else {}
            billing_data = data.get("billing", {}) if isinstance(data, dict) else {}
            auth_data = billing_data.get("authorize") if isinstance(billing_data, dict) else None
            if not isinstance(auth_data, dict):
                errors = result_obj.get("errors") if isinstance(result_obj, dict) else None
                buyer_not_set = self._has_buyer_not_set(result)
                # 提取首个错误的真实原因 (data/message/contingency), 便于观测
                err_reason = ""
                if isinstance(errors, list):
                    for e in errors:
                        if isinstance(e, dict):
                            rd = e.get("data")
                            if isinstance(rd, str):
                                err_reason = rd
                            elif isinstance(rd, dict):
                                err_reason = str(rd.get("contingency") or rd.get("reason") or rd.get("code") or err_reason)
                            if not err_reason:
                                err_reason = str(e.get("message") or "")
                            break
                if buyer_not_set:
                    logger.error(
                        "Authorization failed: BUYER_NOT_SET after {}/{} authorize "
                        "attempts. partial_signup_token={}. The current EC session "
                        "has no buyer bound; outer flow retry will restart from Phase 0.",
                        last_authorize_attempt,
                        self.max_authorize_attempts,
                        self._used_partial_signup_token,
                    )
                    self._emit_progress(
                        "consent_ba",
                        f"authorize 返回 BUYER_NOT_SET (会员身份未绑定 checkout), 外层将重启",
                        level="err",
                    )
                else:
                    logger.error(
                        "Authorization failed: authorize is empty. Errors: {}",
                        json.dumps(sanitize_for_log(errors or []), ensure_ascii=False, indent=2),
                    )
                return {
                    "status": "error",
                    "error": (
                        "authorize returned BUYER_NOT_SET"
                        if buyer_not_set
                        else f"authorize returned empty result: {err_reason or 'no error detail'}"
                    ),
                    "reason": "BUYER_NOT_SET" if buyer_not_set else f"AUTHORIZE_EMPTY:{err_reason or 'NONE'}",
                    "retryable": bool(buyer_not_set),
                    "partial_signup_token": self._used_partial_signup_token,
                    "raw_response": result,
                }
            self.state.user_id = auth_data["buyer"]["userId"]
            ba_token_resp = auth_data["billingAgreementToken"]
            paypal_return_url = auth_data["returnURL"]["href"]
            self.state.return_url = self._paypal_return_url_with_ba_token(
                paypal_return_url,
                ba_token_resp,
            )

            logger.success(
                "Billing Agreement Token: {}",
                sanitize_for_log({"billingAgreementToken": ba_token_resp})["billingAgreementToken"],
            )
            self._emit_progress(
                "consent_ba",
                f"authorize 成功: paymentAction={auth_data.get('paymentAction')} buyerUserId={self.state.user_id}",
                level="ok",
            )
            logger.success(f"Payment Action: {auth_data['paymentAction']}")
            logger.success(f"Buyer User ID: {self.state.user_id}")
            logger.success("Return URL: <redacted>")

            final_redirect_url = ""
            try:
                logger.info("Following merchant return URL...")
                return_resp = self.session.get(
                    self.state.return_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": review_url,
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                for _ in range(8):
                    if return_resp.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = return_resp.headers.get("Location", "")
                    if not location:
                        break
                    final_redirect_url = urllib.parse.urljoin(str(return_resp.url), location)
                    return_resp = self.session.get(
                        final_redirect_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Referer": str(return_resp.url),
                            "Upgrade-Insecure-Requests": "1",
                        },
                    )
                if not final_redirect_url:
                    final_redirect_url = str(return_resp.url)
                logger.success("Final merchant URL: <redacted>")
            except Exception as e:
                logger.warning(f"Following merchant return URL failed: {e}")

            stripe_redirect = self._parse_redirect_status(final_redirect_url)

            # 对账层 (core.reconciliation): 沿白名单回跳链验证商户侧是否确认成功
            reconciliation = None
            try:
                from core.reconciliation import CurlTransport, reconcile_paypal_return

                rec_source = ""
                for candidate in (final_redirect_url, self.state.return_url):
                    cand = str(candidate or "").strip()
                    if not cand or not cand.startswith("https://"):
                        continue
                    if any(host in cand for host in ("stripe.com", "pay.openai.com", "chatgpt.com")):
                        rec_source = cand
                        break
                if rec_source:
                    from urllib.parse import urlsplit as _urlsplit

                    host = str(_urlsplit(rec_source).hostname or "").lower()
                    if host in {"pm-redirects.stripe.com", "pay.openai.com", "chatgpt.com"}:
                        rec = reconcile_paypal_return(
                            rec_source,
                            transport=CurlTransport(self.session),
                            max_hops=8,
                            timeout=15.0,
                        )
                        reconciliation = rec.to_dict()
            except Exception as rec_error:
                logger.warning("Reconciliation skipped: {}", rec_error)

            # Send final analytics
            send_analytics_ts(
                self.session,
                "main:billing:hagrid:billingwithoutpurchase:member:submitButtonFullEvent",
                self.ba_token,
                ec_token=self.state.ec_token,
                user_id=self.state.user_id,
                event="cl",
            )

            return {
                "status": "success",
                "ba_token": ba_token_resp,
                "ec_token": self.state.ec_token,
                "user_id": self.state.user_id,
                "return_url": self.state.return_url,
                "paypal_return_url": paypal_return_url,
                "final_redirect_url": final_redirect_url,
                "stripe_redirect": stripe_redirect,
                "reconciliation": reconciliation,
                "payment_action": auth_data["paymentAction"],
            }
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to parse authorization response: {e}")
            return {
                "status": "error",
                "error": str(e),
                "raw_response": result,
            }
