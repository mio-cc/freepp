# -*- coding: utf-8 -*-
"""ba_paypal — PayPal BA 授权支付段（恢复自原项目进度）

恢复资产（2026-08-01）：
  - paypal/  : openai-paypal-main 完整包（PayPalFlow 四阶段：Phase0 DataDome 过墙
               -> Phase2 建号(邮箱->EC) -> Phase3 填表+2FA SMS -> Phase4 authorize 出 BA/EUAT）
  - http_fp.py : 原项目 `_ba_authorize_http_fp.py`（纯 HTTP hCaptcha passive 指纹栈：
               ddbm2 mint -> bridge/legacy/protocol mint -> verifyhcaptchapassive/validatecaptcha 提交）
  - ba_fp_helpers/ : Node 桥（ba_hcaptcha_passive_node.js / hcaptcha_passive_node.js / ddbm2_node.js）
  - research/ : _research_semi_hybrid_mint.py（semi-hybrid 绿 token 研究路径，需 Chrome 算 n）

历史卡点（原项目进度，2026-07-16/17）：
  - DataDome ngrl 硬墙  ✅ 已破（modxo_direct_signup 桥）
  - visual->passive 压档 ✅ 已破（G11 verdict=PASSIVE）
  - passive 真 token 纯协议 mint ❌ 主卡点（happy-dom/纯 Node PoW host_sum≡4778 恒 soft-reject；
    仅 semi-hybrid（Chrome 算 n）出绿 token 2134/2143，触"生产零浏览器"红线）
  - 绿 token -> EUAT 收口  ❌ 子卡点（form_close 后 SignUpNewMember 仍 authchallenge，EUAT 不下发）

本模块提供 BAAuthorizer 兼容入口（原 min-implant ba_authorize.py 的对外接口）：
    from ba_paypal import BAAuthorizer
    auth = BAAuthorizer(proxy=..., fp_country="BR")
    result = auth.authorize(ba_url="https://www.paypal.com/agreements/approve?ba_token=BA-xxx",
                            phone="11980133818", sms_callback=lambda: input("code: "))
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

# openai-paypal 的模块用绝对导入（from paypal.xxx import ...），
# 把 ba_paypal 目录加进 sys.path 使其可被后端进程直接 import。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from .http_fp import solve_authchallenge_http_fp, is_hcaptcha_passive_challenge
from .http_fp import mint_hcaptcha_passive_token, validate_hcaptcha_passive
from .http_fp import extract_hcaptcha_passive_iframe_src, extract_hcaptcha_site_key
from .http_fp import ddbm2_warmup


class BAAuthorizer:
    """PayPal BA 协议化授权（原 min-implant ba_authorize.py 接口兼容层）。

    内部驱动 openai-paypal-main 的 PayPalFlow 四阶段 + http_fp 纯 HTTP passive mint。
    """

    def __init__(self, proxy: Optional[str] = None, fp_country: Optional[str] = None):
        raw_fp = (fp_country or os.environ.get("MIN_BA_FP_COUNTRY") or "BR").strip().upper()
        self.fp_country = raw_fp or "BR"
        self._proxy_region = (
            os.environ.get("MIN_BA_FP_REGION")
            or os.environ.get("MIN_BA_FP_COUNTRY")
            or self.fp_country
            or "BR"
        ).strip().upper() or "BR"
        self.proxy = proxy or os.environ.get("MIN_BA_PROXY", "").strip() or None
        self.session = None  # 延迟到 init_session / 由 flow 内部创建
        self.csrf = ""
        self.session_id = ""
        self.ec_token = ""
        self._ba_token = ""
        self._euat = ""
        self._flow = None

    # ---- 兼容入口 ----

    def init_session(self, ba_url: str) -> dict:
        """Step 1: GET ba_url /signin，取 ba_token / CSRF / sessionID / flowId / ec_token。

        返回 dict: {ba_token, page_kind, status_code, html_len, final_url, ec_token, ...}
        """
        import re as _re

        m = _re.search(r"ba_token=([A-Za-z0-9-]+)", ba_url or "")
        ba_token = m.group(1) if m else ""
        if ba_token:
            self._ba_token = ba_token

        # 延迟创建 PayPalSession（复用 openai-paypal 的 session 栈）
        from .paypal.models import SessionState
        from .paypal.session import PayPalSession

        state = SessionState()
        self._state = state
        try:
            self.session = PayPalSession(state=state, proxy_url=self.proxy or None)
        except Exception:
            from .http_fp import _make_proxy  # noqa: F401
            raise

        out: dict[str, Any] = {
            "ba_token": ba_token,
            "page_kind": "",
            "status_code": None,
            "html_len": 0,
            "final_url": ba_url,
            "ec_token": "",
            "ec_source": "",
            "dead": False,
            "diag_ec_on_error_url": False,
        }
        try:
            r = self.session.get(ba_url, follow_redirects=True)
        except Exception as e:
            out["page_kind"] = "network_error"
            out["error"] = f"{type(e).__name__}: {e}"
            return out
        out["status_code"] = getattr(r, "status_code", None)
        html = getattr(r, "text", "") or ""
        out["html_len"] = len(html)
        out["final_url"] = str(getattr(r, "url", "") or ba_url)
        # 复用原项目分类逻辑
        from .http_fp import extract_authchallenge_context  # noqa: F401
        cls = _classify_page(html, out["final_url"])
        out["page_kind"] = cls
        if "EC-" in html or "EC-" in out["final_url"]:
            mm = _re.search(r"EC-[A-Z0-9]+", html or out["final_url"])
            if mm:
                self.ec_token = mm.group(0)
                out["ec_token"] = self.ec_token
                out["ec_source"] = "html"
        return out

    def submit_email(self, email: str) -> str:
        """Step 2: 提交邮箱（兼容占位；完整实现走 PayPalFlow._phase2_create_account）。"""
        return ""

    def authorize(
        self,
        ba_url: str,
        phone: Optional[str] = None,
        sms_callback: Optional[Callable[[], str]] = None,
        identity: Optional[dict] = None,
        on_step: Optional[Callable[..., None]] = None,
        **kwargs: Any,
    ) -> dict:
        """完整 BA 授权流程（驱动 PayPalFlow 四阶段）。

        sms_callback: 返回 SMS 验证码字符串（单号一次）。
        """
        t0 = time.monotonic()
        steps: list[tuple[int, str, str, dict]] = []

        def _step(idx: int, name: str, status: str = "run", **kw: Any) -> None:
            steps.append((idx, name, status, kw))
            if on_step:
                try:
                    on_step(idx, name, status, kw)
                except Exception:
                    pass

        _step(0, "init_session")
        info = self.init_session(ba_url)
        if info.get("dead"):
            _step(0, "init_session", "fail", error=f"BA dead: {info.get('page_kind')}")
            return {"status": "error", "reason": "BA_DEAD", "info": info}
        if info.get("page_kind") == "network_error":
            _step(0, "init_session", "fail", error=info.get("error", ""))
            return {"status": "error", "reason": "NETWORK_ERROR", "error": info.get("error")}
        _step(0, "init_session", "ok")

        from .paypal.flow import PayPalFlow
        from .paypal.models import generate_user, generate_card, generate_address
        from .paypal.country_profile import country_context as _build_country_context

        buyer_mode = str(kwargs.get("buyer_mode") or "elevation").strip().lower()
        flow_cls = PayPalFlow
        if buyer_mode in {"elevation", "identity_elevation", "member"}:
            from .paypal.elevation_flow import IdentityElevationPayPalFlow
            flow_cls = IdentityElevationPayPalFlow

        cc = str(kwargs.get("country") or "").strip().upper()
        ctx = kwargs.get("country_context")
        if ctx is None:
            if cc:
                try:
                    ctx = _build_country_context(cc)
                except Exception:
                    ctx = None
        if ctx is not None:
            cc = str(getattr(ctx, "country", "") or cc or "BR").upper()
        cc = cc or "BR"

        phone_full = str(phone or "").strip()
        if identity:
            user = _identity_to_user(identity)
            address = _identity_to_address(identity, cc)
        elif ctx is not None:
            user = generate_user(phone=phone_full, country=cc)
            address = generate_address(country=cc)
        else:
            # 无上下文也无 identity: 空 phone 不允许静默兜底 BR 号码
            if not phone_full:
                raise ValueError(
                    "phone is required when no country_context/identity is provided "
                    "(removed legacy +5591980133818 fallback)"
                )
            user = generate_user(phone=phone_full)
            address = generate_address()
        card = generate_card(proxy_url=self.proxy or None, country=cc)

        sms_provider = kwargs.get("sms_provider")
        if sms_provider is None and sms_callback is not None:
            sms_provider = _callback_sms_provider(sms_callback)

        flow = flow_cls(
            ba_token=self._ba_token or "",
            user=user,
            card=card,
            address=address,
            max_card_attempts=int(kwargs.get("max_card_attempts") or 5),
            max_flow_attempts=int(kwargs.get("max_flow_attempts") or 1),
            max_authorize_attempts=int(kwargs.get("max_authorize_attempts") or 3),
            proxy_enabled=bool(self.proxy),
            proxy_config=_proxy_config(self.proxy) if self.proxy else None,
            sms_provider=sms_provider,
            country_context=ctx,
            progress_cb=_make_flow_progress_cb(on_step),
        )
        self._flow = flow
        try:
            result = flow.run()
        except Exception as e:
            import logging as _logging
            import traceback as _tb

            _logging.getLogger("ba_paypal").error(
                "BA authorize flow crashed: %s\n%s",
                e,
                _tb.format_exc(),
            )
            result = {"status": "error", "error": f"{type(e).__name__}: {e}", "reason": "FLOW_EXCEPTION"}
        result.setdefault("elapsed", round(time.monotonic() - t0, 2))
        if result.get("status") == "success":
            self._euat = getattr(flow.state, "euat_token", "") or ""
            result["euat"] = self._euat
            result["ec_token"] = getattr(flow.state, "ec_token", "") or ""
            result["user_id"] = getattr(flow.state, "user_id", "") or ""
            result["user"] = {
                "email": getattr(flow.user, "email", "") or "",
                "first_name": getattr(flow.user, "first_name", "") or "",
                "last_name": getattr(flow.user, "last_name", "") or "",
            }
            _step(4, "authorize", "ok")
        else:
            _step(4, "authorize", "fail", error=result.get("error") or result.get("reason") or "unknown")
        result["steps"] = steps
        return result

    # ---- 纯 HTTP passive mint 直通（原项目 http_fp 栈）----

    def solve_captcha(self, challenge_html: str, page_url: str = "") -> dict:
        """对 authchallenge HTML 做纯 HTTP passive mint + verify（不 SMS/不建号）。"""
        return solve_authchallenge_http_fp(
            self.session,
            challenge_html,
            page_url=page_url or "https://www.paypal.com/",
            proxy=self.proxy,
            ec_token=self.ec_token or "",
        )


def _classify_page(html: str, final_url: str = "") -> str:
    """简易页面分类（与原项目 _classify_ba_page 对齐）。"""
    low = (html or "").lower()
    url_low = (final_url or "").lower()
    if "invalid_resource" in low or "su5wquxjrf9srvnpvvjjrf9jra" in low or "su5wquxjrf9srvnpvvjjrf9jra" in url_low:
        return "dead_invalid_resource"
    if "genericerror" in url_low or "generic-error" in url_low or "checkoutweb/genericerror" in low:
        return "generic_error"
    if "authchallenge" in low or "data-captcha-type" in low:
        return "authchallenge"
    if "checkoutweb" in low or "checkoutweb" in url_low:
        return "checkoutweb"
    return "unknown"


def _identity_to_user(identity: dict) -> Any:
    from .paypal.models import UserInfo, _split_phone

    i = identity or {}
    phone_full = str(i.get("phone") or i.get("phone_number") or "")
    phone_cc = str(i.get("phone_country_code") or i.get("phone_country") or "+1")
    if phone_full and not phone_full.startswith("+"):
        phone_full = f"{phone_cc}{phone_full}"
    local, cc_out = _split_phone(phone_full, phone_cc)
    return UserInfo(
        email=str(i.get("email") or ""),
        first_name=str(i.get("first_name") or i.get("firstName") or ""),
        last_name=str(i.get("last_name") or i.get("lastName") or ""),
        phone=phone_full,
        phone_local=local,
        phone_country_code=cc_out,
        password=str(i.get("password") or ""),
        dob=str(i.get("dob") or ""),
        cpf=str(i.get("identity_document_number") or i.get("cpf") or ""),
        identity_document_type=str(i.get("identity_document_type") or ""),
        identity_document_number=str(i.get("identity_document_number") or i.get("cpf") or ""),
        nationality=str(i.get("nationality") or ""),
        middle_name=str(i.get("middle_name") or i.get("middleName") or ""),
        kana_first=str(i.get("kana_first") or ""),
        kana_last=str(i.get("kana_last") or ""),
        crs_data=i.get("crs_data") or i.get("crs_tax_details"),
        occupation=str(i.get("occupation") or ""),
    )


def _identity_to_address(identity: dict, country: str = "BR") -> Any:
    from .paypal.models import BillingAddress

    i = identity or {}
    addr = i.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}
    street = str(i.get("street") or addr.get("line1") or "")
    house_number = str(i.get("house_number") or "")
    if house_number and street and house_number not in street:
        street = f"{street}, {house_number}" if not street.endswith(house_number) else street
    if not house_number:
        parts = street.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            street, house_number = parts
    return BillingAddress(
        street=street,
        house_number=house_number,
        district=str(i.get("district") or addr.get("line2") or ""),
        city=str(i.get("city") or addr.get("city") or ""),
        state=str(i.get("state") or addr.get("state") or ""),
        postal_code=str(i.get("postal_code") or addr.get("postal_code") or ""),
        country=str(i.get("country") or addr.get("country") or country or "BR").upper(),
    )


def _digits_only(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _proxy_config(proxy_url: str) -> Any:
    from .paypal.proxy import build_proxy_config
    try:
        return build_proxy_config(proxy_url=proxy_url)
    except Exception:
        return None


def _make_flow_progress_cb(on_step: Any | None):
    """把 flow 的 progress_cb(step, detail, level) 桥接为 BAAuthorizer 的 on_step 回调。

    on_step 签名: on_step(idx, name, status, kw)。idx 为步骤序号 (供前端进度条),
    这里统一用 9x 段编号避免与 authorize() 内 0-4 的粗步骤冲突。
    """
    _PROGRESS_IDX = {
        "submit_email": 90, "captcha": 91, "sms": 92,
        "signup": 93, "consent_ba": 94, "done": 95,
    }

    def _cb(step: str, detail: str = "", level: str = "info") -> None:
        if on_step is None:
            return
        try:
            on_step(
                _PROGRESS_IDX.get(str(step), 99),
                str(step or "progress"),
                "run" if level != "err" else "fail",
                {"detail": str(detail or ""), "level": level},
            )
        except Exception:
            pass

    return _cb


class _callback_sms_provider:
    """把旧式 sms_callback 包装成 SmsOtpProviderProtocol。"""

    def __init__(self, callback: Callable[[], str]):
        self._callback = callback
        self.max_attempts = 3

    def reserve_number(self):
        return _CallbackActivation(self._callback)

    def mark_sms_sent(self, activation) -> None:
        pass

    def wait_for_code(self, activation, timeout_seconds=None) -> str | None:
        try:
            return self._callback()
        except Exception:
            return None

    def abandon(self, activation, reason: str) -> None:
        pass

    def register_confirmation_result(self, activation, confirmed: bool) -> None:
        pass


class _CallbackActivation:
    def __init__(self, callback: Callable[[], str]):
        self.activation_id = uuid.uuid4().hex
        self.phone_number = ""
        self.provider_id = "callback"
        self.price = 0.0
        self.expires_at = time.time() + 300
        self.reused = False


__all__ = ["BAAuthorizer", "solve_authchallenge_http_fp", "mint_hcaptcha_passive_token",
           "validate_hcaptcha_passive", "ddbm2_warmup"]
