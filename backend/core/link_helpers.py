"""提链健壮性工具库 (从 paypal-agreement-protocol/GPT-Register-Tool 的
pp_link_helpers.py 移植): approve -> poll -> resolve 段的
URL 识别 / 提取 / 跟跳 / 金额 / 诊断。

全部为纯函数 (resolve_external_redirect 接受 session 参数), 不依赖本项目
其它模块, 供 chain.py 及其它调用方复用。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Pattern
from urllib.parse import parse_qsl, urljoin, urlsplit

# pm-redirects (Stripe -> 支付网关跳板) 与 PayPal BA approve 链接
PM_REDIRECT_RE = re.compile(r"https://pm-redirects\.stripe\.com/authorize/[^\"'\s<>]+")
PAYPAL_BA_RE = re.compile(r"https://(?:www\.)?paypal\.com/agreements/approve\?ba_token=[^\"'\s<>&]+")

# 支付二维码 / 深链 / 指令页识别
RE_QR_DATA_IMAGE = re.compile(r"data:image/(?:png|svg\+xml|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=]+")
RE_UPI_DEEP_LINK = re.compile(r"upi://[^\s\"'<>]+")
RE_PIX_CODE = re.compile(r"br\.gov\.bcb\.pix[^\s\"'<>]+")
RE_VIETQR_PAYLOAD = re.compile(r"000201[0-9A-Za-z. :+/=-]{20,}")

# 各渠道网关 / 指令页域名
_GATEWAY_HINTS = ("momo.vn", "vietqr", "midtrans", "nicepay", "cashfree", "stripe.com", "pay.openai.com")

_QR_JUNK_TOKENS = {
    "momo", "qr", "qrcode", "pay", "payment", "vietqr",
    "stripe", "checkout", "data", "image", "png", "svg", "none", "null",
}


def _prefer_artifact(old: str, new: str) -> str:
    """选更长的非空值 (通常更有信息量)。"""
    old = str(old or "").strip()
    new = str(new or "").strip()
    if not old:
        return new
    if not new:
        return old
    return old if len(old) >= len(new) else new


def _is_pm_icon(text: str) -> bool:
    """品牌图标 URL / 短 token, 不可作为支付产物。"""
    t = str(text or "").strip().lower()
    if not t:
        return True
    if len(t) < 12:
        return True
    return any(t.endswith(suffix) for suffix in (".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif")) and (
        "momo" in t or "stripe" in t or "paypal" in t or "brand" in t or "logo" in t or "icon" in t
    )


def _usable_qr_image(text: str) -> bool:
    t = str(text or "").strip()
    return t.startswith("data:image") or t.lower().endswith((".png", ".svg", ".jpg", ".jpeg"))


def _usable_pay_text(text: str) -> bool:
    t = str(text or "").strip()
    if not t or len(t) < 16:
        return False
    low = t.lower()
    if low in _QR_JUNK_TOKENS or _is_pm_icon(t):
        return False
    if low.startswith(("http://", "https://", "data:image", "upi://", "000201", "2|99|")):
        return True
    if "vietqr" in low:
        return True
    return False


def extract_qr_artifacts(payload: Any) -> dict[str, Any]:
    """深度递归提取支付产物: QR 图 / 指令页 URL / 深链 / pix 码。

    返回:
      qr_image_url / qr_png_url / qr_data / hosted_instructions_url /
      deep_link (upi://) / pix_code (br.gov.bcb.pix) / next_action_type / expires_at
    """
    out: dict[str, Any] = {
        "qr_image_url": "",
        "qr_png_url": "",
        "qr_data": "",
        "hosted_instructions_url": "",
        "deep_link": "",
        "pix_code": "",
        "next_action_type": "",
        "expires_at": None,
    }

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            ntype = str(node.get("type") or "").strip()
            if ntype and not out["next_action_type"]:
                if "qr" in ntype.lower() or ntype in {
                    "display_qr_code", "momo_display_qr_code", "redirect_to_url",
                    "upi_handle_redirect_or_display_qr_code", "oxxo_display_details",
                }:
                    out["next_action_type"] = ntype
            for key, val in node.items():
                key_l = str(key).lower()
                if isinstance(val, str):
                    text = val.strip()
                    if not text:
                        continue
                    if key_l in {"image_url_png", "image_url_svg", "qr_image_url", "image_url", "png", "svg"}:
                        if _usable_qr_image(text):
                            if "png" in key_l or text.startswith("data:image/png") or text.lower().endswith(".png"):
                                out["qr_png_url"] = out["qr_png_url"] or text
                            out["qr_image_url"] = out["qr_image_url"] or text
                    if key_l in {"hosted_voucher_url", "hosted_instructions_url", "mobile_auth_url",
                                 "url", "redirect_url", "hosted_url", "qr_png_url", "qr_image_url"}:
                        if text.startswith(("http://", "https://")) and not _is_pm_icon(text):
                            out["hosted_instructions_url"] = _prefer_artifact(out["hosted_instructions_url"], text)
                            if any(x in text.lower() for x in _GATEWAY_HINTS):
                                out["qr_data"] = _prefer_artifact(out["qr_data"], text)
                    if key_l in {"data", "qr_data", "qr_code", "qrcode", "payload", "value"}:
                        if _usable_pay_text(text):
                            if text.startswith(("http://", "https://")):
                                out["hosted_instructions_url"] = _prefer_artifact(out["hosted_instructions_url"], text)
                            if text.startswith("data:image"):
                                out["qr_image_url"] = out["qr_image_url"] or text
                            out["qr_data"] = _prefer_artifact(out["qr_data"], text)
                    if key_l in {"expires_at", "expires_after"} and out["expires_at"] is None:
                        out["expires_at"] = text
                    if text.startswith("upi://"):
                        out["deep_link"] = _prefer_artifact(out["deep_link"], text)
                        out["qr_data"] = _prefer_artifact(out["qr_data"], text)
                    if "br.gov.bcb.pix" in text.lower():
                        out["pix_code"] = _prefer_artifact(out["pix_code"], text)
                        out["qr_data"] = _prefer_artifact(out["qr_data"], text)
                    m_viet = RE_VIETQR_PAYLOAD.search(text)
                    if m_viet and len(m_viet.group(0)) >= 24:
                        out["qr_data"] = _prefer_artifact(out["qr_data"], m_viet.group(0))
                    if _usable_pay_text(text) and (
                        "momo" in text.lower() or "vietqr" in text.lower()
                        or text.startswith(("http://", "https://", "data:image", "2|99|", "000201"))
                    ):
                        if text.startswith("data:image"):
                            out["qr_image_url"] = out["qr_image_url"] or text
                        elif text.startswith(("http://", "https://")):
                            out["hosted_instructions_url"] = _prefer_artifact(out["hosted_instructions_url"], text)
                        else:
                            out["qr_data"] = _prefer_artifact(out["qr_data"], text)
                else:
                    _walk(val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)

    # 收尾: 去品牌图标/垃圾值
    for k in ("qr_data", "qr_image_url", "qr_png_url", "hosted_instructions_url", "deep_link", "pix_code"):
        val = str(out.get(k) or "").strip()
        bad = (
            not val
            or val.lower() in _QR_JUNK_TOKENS
            or _is_pm_icon(val)
            or (k in {"qr_image_url", "qr_png_url"} and not _usable_qr_image(val))
        )
        out[k] = "" if bad else val

    if out["qr_png_url"] and not out["qr_image_url"]:
        out["qr_image_url"] = out["qr_png_url"]
    if out["qr_image_url"] and not out["qr_png_url"] and (
        "png" in out["qr_image_url"].lower() or out["qr_image_url"].startswith("data:image/png")
    ):
        out["qr_png_url"] = out["qr_image_url"]
    if not out["qr_data"] and out["hosted_instructions_url"]:
        out["qr_data"] = out["hosted_instructions_url"]
    return out


def is_scannable_artifact(text: str) -> bool:
    """支付 APP 可扫/可跳转的产物判定 (类似 is_scannable_momo_artifact)。"""
    t = str(text or "").strip()
    if not t:
        return False
    low = t.lower()
    if _is_pm_icon(t):
        return False
    if "checkout.stripe.com" in low or "pay.openai.com" in low:
        return False
    if t.startswith(("data:image", "upi://")):
        return True
    if "payment.momo.vn" in low or "momo.vn" in low:
        return t.startswith(("http://", "https://"))
    if "vietqr" in low or low.startswith("000201") or low.startswith("2|99|"):
        return len(t) >= 24
    if "br.gov.bcb.pix" in low:
        return True
    if t.startswith(("http://", "https://")) and any(x in low for x in ("/qr", "qrcode", "voucher", "barcode", "instructions")):
        return "payment-methods" not in low
    return False


def follow_gateway_redirect(
    proxy: str,
    url: str,
    timeout: float = 40,
) -> dict[str, Any]:
    """跟随 pm-redirects.stripe.com -> 渠道网关, 抓取 QR 图/指令页/深链。

    参考 momo_qr_extract.follow_momo_redirect: 网关页内嵌 data:image QR,
    /gateway/pay 类绝对 URL, 或深链 (upi://)。返回最终 URL + QR 产物。
    """
    out: dict[str, Any] = {
        "final_url": "",
        "qr_image_url": "",
        "qr_png_url": "",
        "hosted_instructions_url": "",
        "qr_data": "",
        "deep_link": "",
        "error": "",
    }
    if not str(url or "").startswith("http"):
        out["error"] = "bad_redirect"
        return out
    try:
        from curl_cffi import requests as curl_requests  # type: ignore
    except Exception:
        curl_requests = None  # type: ignore
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    try:
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": timeout,
            "impersonate": "chrome",
            "allow_redirects": True,
        }
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        if curl_requests is not None:
            resp = curl_requests.get(url, **kwargs)
        else:
            import requests  # type: ignore
            resp = requests.get(url, **{k: v for k, v in kwargs.items() if k != "impersonate"})
        final = str(getattr(resp, "url", "") or url)
        text = str(getattr(resp, "text", "") or "")
    except Exception as exc:
        out["error"] = f"follow_network_{type(exc).__name__}"
        return out

    out["final_url"] = final
    low = final.lower()
    if any(x in low for x in ("momo.vn", "midtrans", "nicepay", "cashfree", "payments.stripe.com")):
        out["hosted_instructions_url"] = final
        out["qr_data"] = out["qr_data"] or final
    m_img = RE_QR_DATA_IMAGE.search(text)
    if m_img:
        img = m_img.group(0)
        out["qr_image_url"] = img
        if "png" in img[:30] or img.startswith("data:image/png"):
            out["qr_png_url"] = img
        out["qr_data"] = img
    if not out["hosted_instructions_url"]:
        for u in re.findall(r"https?://[^\s\"'<>]+", text):
            ul = u.lower()
            if any(g in ul for g in ("payment.momo.vn", "gateway/pay", "midtrans.com/snap", "nicepay", "cashfree")):
                out["hosted_instructions_url"] = u
                out["qr_data"] = out["qr_data"] or u
                break
    m_upi = RE_UPI_DEEP_LINK.search(text)
    if m_upi:
        out["deep_link"] = m_upi.group(0)
        out["qr_data"] = out["qr_data"] or m_upi.group(0)
    m_pix = RE_PIX_CODE.search(text)
    if m_pix:
        out["qr_data"] = out["qr_data"] or m_pix.group(0)
    if not (out["qr_image_url"] or out["hosted_instructions_url"] or out["deep_link"]):
        out["error"] = f"no_artifact final={final[:80]}"
    return out


def setup_intent_redirect(payload: Any) -> str:
    """提取 setup_intent / payment_intent 的 next_action.redirect_to_url (momo/upi 等)。"""
    if not isinstance(payload, dict):
        return ""
    for intent_key in ("setup_intent", "payment_intent"):
        intent = payload.get(intent_key)
        if isinstance(intent, dict):
            na = intent.get("next_action")
            if isinstance(na, dict):
                redir = na.get("redirect_to_url")
                if isinstance(redir, dict):
                    url = str(redir.get("url") or "").strip()
                    if url.startswith("http"):
                        return url
    return ""


def generate_valid_cpf() -> str:
    """生成合法巴西 CPF (11 位, 校验位正确) — 移植 pix-extract.generate_valid_cpf。"""
    import random

    digits = [random.randint(0, 9) for _ in range(9)]
    for _ in range(2):
        total = sum((len(digits) + 1 - i) * v for i, v in enumerate(digits))
        digits.append((total * 10) % 11 if (total * 10) % 11 < 10 else 0)
    return "".join(str(d) for d in digits)


# =============================================================================
# 统一结果契约 (protocol_core.ProtocolResult 移植)
# =============================================================================

RESULT_SCHEMA = "protocol_payment.v1"

TERMINAL_STATES = ("completed", "failed", "cancelled", "unknown", "timed_out")


def sanitize_text(value: Any) -> str:
    """脱敏: Bearer/JWT/BA token/敏感键值。"""
    text = str(value or "")
    text = re.sub(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b", "[REDACTED]", text)
    text = re.sub(r"\bBA-[A-Za-z0-9_.-]+\b", "[REDACTED]", text)
    text = re.sub(
        r"(?is)(access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|"
        r"client[_-]?secret|api[_-]?key|ba[_-]?token|totp(?:[_-]?secret)?|"
        r"card(?:[_-]?(?:number|cvv|cvc))?|blik[_-]?code)(\s*[=:]\s*)['\"]?[^\s,}\"']+",
        r"\1\2[REDACTED]",
        text,
    )
    return text


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in str(key).lower() for part in ("token", "secret", "card", "cvv", "blik_code"))
            else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


@dataclass(frozen=True)
class ProtocolResult:
    """统一提链/提取结果契约 (protocol_payment.v1)。"""
    payment_method: str
    ok: bool
    status: str
    operation: str = "extract_link"
    url: str = ""
    link_type: str = ""
    message: str = ""
    error: str = ""
    error_code: str = ""
    error_stage: str = ""
    retryable: bool = False
    side_effect_started: bool = False
    requires_reconciliation: bool = False
    artifacts: dict[str, Any] = None  # type: ignore[assignment]
    schema: str = RESULT_SCHEMA

    def __post_init__(self) -> None:
        if self.artifacts is None:
            object.__setattr__(self, "artifacts", {})

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return sanitize_payload(d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def is_paypal_ba_approve_url(url: str) -> bool:
    """结构化判定 PayPal BA approve 链接 (容忍子域/多余 query 参数)。"""
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    if not (host == "paypal.com" or host.endswith(".paypal.com")):
        return False
    path = parsed.path.rstrip("/").lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return path == "/agreements/approve" and bool(str(query.get("ba_token") or "").strip())


def extract_ba_token(url: str) -> str:
    """宽容提取 ba_token (按 & # 引号/空格截断, 可处理嵌在 JSON/HTML 里的 URL)。"""
    marker = "ba_token="
    lower = (url or "").lower()
    if marker not in lower:
        return ""
    start = lower.find(marker) + len(marker)
    end = len(url)
    for sep in ("&", "#", '"', "'", " "):
        pos = url.find(sep, start)
        if pos != -1:
            end = min(end, pos)
    return url[start:end]


def find_url_in_value(value: Any, patterns: list[Pattern] | None = None) -> str:
    """深度递归 dict/list/str, 优先 url/redirect_url/return_url 键, 找首个 URL 命中。

    patterns 缺省用 PM_REDIRECT_RE + PAYPAL_BA_RE。
    """
    if patterns is None:
        patterns = [PM_REDIRECT_RE, PAYPAL_BA_RE]
    if isinstance(value, str):
        for pat in patterns:
            m = pat.search(value)
            if m:
                return m.group(0)
        return ""
    if isinstance(value, dict):
        for key in ("url", "redirect_url", "return_url"):
            if key in value:
                found = find_url_in_value(value[key], patterns)
                if found:
                    return found
        for v in value.values():
            found = find_url_in_value(v, patterns)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = find_url_in_value(item, patterns)
            if found:
                return found
    return ""


def extract_redirect_url(payload: Any) -> str:
    """多来源结构化提取跳转 URL: next_action -> 递归找 URL -> intent.next_action。"""
    if not isinstance(payload, dict):
        return ""
    next_action = payload.get("next_action")
    if isinstance(next_action, dict) and next_action.get("type") == "redirect_to_url":
        redirect = next_action.get("redirect_to_url")
        if isinstance(redirect, dict) and redirect.get("url"):
            return str(redirect["url"])
    url = find_url_in_value(payload)
    if url:
        return url
    for intent_key in ("setup_intent", "payment_intent"):
        intent = payload.get(intent_key)
        if isinstance(intent, dict):
            action = intent.get("next_action") if isinstance(intent, dict) else {}
            redirect = action.get("redirect_to_url") if isinstance(action, dict) else {}
            if isinstance(redirect, dict) and redirect.get("url"):
                return str(redirect["url"])
    return ""


def resolve_external_redirect(
    session: Any,
    redirect_url: str,
    max_hops: int = 5,
    timeout: float = 15.0,
) -> str:
    """手动逐跳跟 3xx, 命中 PayPal BA approve 立即返回 (不再继续跟跳)。

    覆盖 Stripe pm-redirects -> 支付网关的多级跳转链; 某些链路上
    BA 链接出现后还会再 302 到验证页, 此时直接停在 BA 链接。
    """
    current = redirect_url
    for _ in range(max_hops):
        if not current:
            return ""
        if is_paypal_ba_approve_url(current):
            return current
        try:
            resp = session.get(current, allow_redirects=False, timeout=timeout)
        except Exception:
            return current
        if resp.status_code not in (301, 302, 303, 307, 308):
            return current
        location = str(resp.headers.get("Location") or resp.headers.get("location") or "").strip()
        if not location:
            return current
        current = urljoin(current, location)
    return current


def stripe_amount_details(init_payload: Any) -> dict:
    """统一提取 Stripe init 金额+币种, 带来源标注。"""
    if not isinstance(init_payload, dict):
        return {"amount": None, "currency": "", "source": "unknown"}
    currency = str(init_payload.get("currency") or "").lower()
    total_summary = init_payload.get("total_summary")
    if isinstance(total_summary, dict) and total_summary.get("due") is not None:
        return {
            "amount": int(total_summary["due"]),
            "currency": str(total_summary.get("currency") or currency).lower(),
            "source": "total_summary.due",
        }
    invoice = init_payload.get("invoice")
    if isinstance(invoice, dict) and invoice.get("amount_due") is not None:
        return {
            "amount": int(invoice["amount_due"]),
            "currency": str(invoice.get("currency") or currency).lower(),
            "source": "invoice.amount_due",
        }
    return {"amount": None, "currency": currency, "source": "unknown"}


def find_submission_attempt(payload: Any) -> dict[str, Any]:
    """深度递归找 submission_attempt (ChatGPT 侧 approve 审核状态)。"""
    if not isinstance(payload, dict):
        return {}
    direct = payload.get("submission_attempt")
    if isinstance(direct, dict):
        return direct
    for value in payload.values():
        if isinstance(value, dict):
            found = find_submission_attempt(value)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = find_submission_attempt(item)
                if found:
                    return found
    return {}


def stripe_confirm_error_diagnostics(
    response: Any,
    cs_id: str,
    pm_id: str,
    init_payload: Any,
) -> str:
    """把 Stripe confirm 失败拼成一行诊断串 (对应 402 generic_decline 分类基建)。"""
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    error = error if isinstance(error, dict) else {}
    submission = find_submission_attempt(payload)
    parts = [
        f"stripe_confirm_failed:http={getattr(response, 'status_code', 0)}",
        f"cs_id={str(cs_id)[:18]}",
        f"pm_id={str(pm_id)[:18]}",
        f"amount={stripe_amount_details(init_payload).get('amount')}",
        f"init_checksum={'present' if isinstance(init_payload, dict) and init_payload.get('init_checksum') else 'missing'}",
    ]
    for label, value in (
        ("error_type", error.get("type")),
        ("error_code", error.get("code")),
        ("error_param", error.get("param")),
        ("error_message", error.get("message")),
        ("submission_state", submission.get("state")),
        ("submission_reason", submission.get("reason")),
        ("submission_code", submission.get("code")),
        ("submission_message", submission.get("message")),
    ):
        if value not in (None, ""):
            compact = re.sub(r"\s+", " ", str(value)).strip()
            parts.append(f"{label}={compact[:180]}")
    if len(parts) == 5:
        body = re.sub(r"\s+", " ", str(getattr(response, "text", ""))).strip()
        parts.append(f"body={body[:180]}")
    return "; ".join(parts)
