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

        phone_full = str(phone or "").strip() or "+5591980133818"
        user = identity and _identity_to_user(identity) or generate_user(phone=phone_full)
        card = generate_card()
        address = generate_address()

        flow = PayPalFlow(
            ba_token=self._ba_token or "",
            user=user,
            card=card,
            address=address,
            max_card_attempts=int(kwargs.get("max_card_attempts") or 5),
            max_flow_attempts=int(kwargs.get("max_flow_attempts") or 1),
            max_authorize_attempts=int(kwargs.get("max_authorize_attempts") or 3),
            proxy_enabled=bool(self.proxy),
            proxy_config=_proxy_config(self.proxy) if self.proxy else None,
            sms_provider=_callback_sms_provider(sms_callback) if sms_callback else None,
        )
        self._flow = flow
        try:
            result = flow.run()
        except Exception as e:
            result = {"status": "error", "error": f"{type(e).__name__}: {e}", "reason": "FLOW_EXCEPTION"}
        result.setdefault("elapsed", round(time.monotonic() - t0, 2))
        if result.get("status") == "success":
            self._euat = getattr(flow.state, "euat_token", "") or ""
            result["euat"] = self._euat
            result["ec_token"] = getattr(flow.state, "ec_token", "") or ""
            result["user_id"] = getattr(flow.state, "user_id", "") or ""
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
    from .paypal.models import UserInfo
    return UserInfo(
        email=str(identity.get("email") or ""),
        first_name=str(identity.get("first_name") or identity.get("firstName") or ""),
        last_name=str(identity.get("last_name") or identity.get("lastName") or ""),
        phone_country_code=str(identity.get("phone_country_code") or "+55"),
        phone=str(identity.get("phone") or ""),
    )


def _proxy_config(proxy_url: str) -> Any:
    from .paypal.proxy import build_proxy_config
    try:
        return build_proxy_config(proxy_url=proxy_url)
    except Exception:
        return None


class _callback_sms_provider:
    """把旧式 sms_callback 包装成 SmsOtpProviderProtocol。"""

    def __init__(self, callback: Callable[[], str]):
        self._callback = callback

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
