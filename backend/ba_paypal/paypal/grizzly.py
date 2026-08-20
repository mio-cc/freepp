"""GrizzlySMS (api.grizzlysms.com) OTP 接码 provider。

2026-08-16: SMSBower VN 号池被 PayPal 2FA 全量拒绝 (NUMBER_NOT_SUPPORTED),
接入 GrizzlySMS 作为第二接码平台。协议与 SMS-Activate 兼容
(stubs/handler_api.php, ACCESS_NUMBER:id:phone 文本响应), 但无按供应商
细分价格 (getPrices 仅国家+服务聚合价), 供应商阶梯退化为单档 + 服务端
maxPrice/minPrice 过滤。

接口与 SMSBowerOtpProvider 同构 (flow._confirm_phone_with_sms_provider 鸭子类型),
复用 SMSBowerActivationStore (号码黑名单 / 冷却 / 可复用激活 跨平台共享)。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from .smsbower import (
    SMSBowerActivation,
    SMSBowerActivationStore,
    SMSBOWER_DEFAULT_ACTIVATION_TTL_SECONDS,
    SMSBOWER_DEFAULT_MAX_ATTEMPTS,
    SMSBOWER_DEFAULT_POLL_INTERVAL_SECONDS,
    SMSBOWER_DEFAULT_WAIT_SECONDS,
    _env_float,
    _load_dotenv_value,
    _parse_float,
    _parse_int,
    normalize_phone,
)

GRIZZLY_API_URL = "https://api.grizzlysms.com/stubs/handler_api.php"
GRIZZLY_DEFAULT_SERVICE = "ts"  # PayPal (与 SMSBower 同族平台通用码; "pp" 是另一无货服务)
GRIZZLY_DEFAULT_COUNTRY = "10"  # 越南

# GrizzlySMS 国家编号 (官方文档 country 表, 2026-08-16 实测校对)
GRIZZLY_COUNTRY_IDS: dict[str, str] = {
    "US": "187",  # 美国实号 (12 为虚拟号池)
    "GB": "16",
    "AU": "175",
    "DE": "43",
    "JP": "182",
    "TH": "52",
    "NL": "48",
    "VN": "10",
    "BH": "145",
    "AO": "76",
    "AE": "95",
    "CI": "27",   # 象牙海岸 (科特迪瓦)
    "TR": "62",
    "BR": "73",
    "KR": "350",
}

# 国家编号 -> 手机国际前缀 (买到号后按实际国家校验, 防编号映射错)
GRIZZLY_PHONE_PREFIXES: dict[str, str] = {
    "187": "1", "73": "55", "6": "62", "4": "63", "16": "44", "43": "49",
    "10": "84", "175": "61", "52": "66", "48": "31", "145": "973",
    "76": "244", "95": "971", "27": "225", "62": "90", "350": "82",
}

_GRIZZLY_ERROR_CODES = {
    "BAD_KEY", "BAD_ACTION", "BAD_SERVICE", "BAD_COUNTRY",
    "NO_BALANCE", "NO_NUMBERS", "NO_ACTIVATION", "WRONG_MAX_PRICE",
    "STATUS_WAIT_CODE", "STATUS_WAIT_RESEND", "STATUS_CANCEL",
    "SERVICE_UNAVAILABLE_REGION",
}


class GrizzlyApiError(RuntimeError):
    pass


def grizzly_country_id(country: str) -> str:
    cc = (country or "").strip().upper()
    gid = GRIZZLY_COUNTRY_IDS.get(cc)
    if not gid:
        raise GrizzlyApiError(f"grizzly unsupported country: {country}")
    return gid


def _grizzly_proxy() -> str | None:
    """出网代理: GRIZZLY_PROXY 显式优先, 其次探测本机 Clash, 最后直连。"""
    explicit = (_load_dotenv_value("GRIZZLY_PROXY") or "").strip()
    if explicit:
        return explicit
    import socket

    for host, port in (("127.0.0.1", 7890), ("127.0.0.1", 7897)):
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return f"http://{host}:{port}"
        except OSError:
            continue
    return None


class GrizzlyClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = GRIZZLY_API_URL,
        timeout_seconds: float = 20.0,
        proxy_url: str | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise GrizzlyApiError("GrizzlySMS API key is not configured")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url if proxy_url is not None else _grizzly_proxy()

    def _request_text(self, action: str, params: dict[str, object] | None = None) -> str:
        query: dict[str, str] = {"api_key": self.api_key, "action": action}
        for key, value in (params or {}).items():
            if value is not None and str(value) != "":
                query[key] = str(value)
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(self.timeout_seconds), "trust_env": False}
        if self.proxy_url:
            client_kwargs["proxy"] = self.proxy_url
        with httpx.Client(**client_kwargs) as client:
            response = client.get(self.base_url, params=query)
            response.raise_for_status()
        text = (response.text or "").strip()
        if text in _GRIZZLY_ERROR_CODES:
            raise GrizzlyApiError(text)
        if text.startswith("The service is prohibited"):
            raise GrizzlyApiError("SERVICE_PROHIBITED")
        return text

    def get_balance(self) -> float:
        text = self._request_text("getBalance")
        if not text.startswith("ACCESS_BALANCE:"):
            raise GrizzlyApiError(f"unexpected getBalance response: {text[:120]}")
        return _parse_float(text.split(":", 1)[1])

    def get_prices(self, service: str, country: str) -> dict[str, float]:
        """返回聚合价 {price, count} (Grizzly 无按供应商细分)。"""
        text = self._request_text("getPrices", {"service": service, "country": country})
        import json

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GrizzlyApiError(f"unexpected getPrices response: {text[:120]}") from exc
        entry = ((data or {}).get(str(country)) or {}).get(service) or {}
        price = _parse_float(entry.get("price", entry.get("cost")))
        count = _parse_int(entry.get("count"))
        return {"price": price, "count": count}

    def get_number(
        self,
        service: str,
        country: str,
        *,
        max_price: float | None = None,
        min_price: float | None = None,
    ) -> dict[str, object]:
        text = self._request_text("getNumber", {
            "service": service, "country": country,
            "maxPrice": max_price, "minPrice": min_price,
        })
        # ACCESS_NUMBER:<activationId>:<phone>
        m = re.match(r"^ACCESS_NUMBER:(\d+):(\d{7,15})$", text)
        if not m:
            raise GrizzlyApiError(f"unexpected getNumber response: {text[:120]}")
        return {"activationId": m.group(1), "phoneNumber": m.group(2)}

    def get_status(self, activation_id: str) -> str:
        return self._request_text("getStatus", {"id": activation_id})

    def set_status(self, activation_id: str, status: int) -> str:
        return self._request_text("setStatus", {"id": activation_id, "status": status})


class GrizzlyOtpProvider:
    """与 SMSBowerOtpProvider 同构的 GrizzlySMS 接码 provider。

    差异: 平台无按供应商价格细分 -> 单一伪 provider ("grizzly"),
    min/max 价格区间直接透传服务端 maxPrice/minPrice 过滤。
    """

    def __init__(
        self,
        *,
        client: GrizzlyClient,
        store: SMSBowerActivationStore | None = None,
        service: str = GRIZZLY_DEFAULT_SERVICE,
        country: str = GRIZZLY_DEFAULT_COUNTRY,
        phone_cc: str = "+84",
        max_price: float | None = None,
        min_price: float | None = None,
        wait_seconds: float = SMSBOWER_DEFAULT_WAIT_SECONDS,
        poll_interval_seconds: float = SMSBOWER_DEFAULT_POLL_INTERVAL_SECONDS,
        activation_ttl_seconds: int = SMSBOWER_DEFAULT_ACTIVATION_TTL_SECONDS,
        max_attempts: int = SMSBOWER_DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.client = client
        self.store = store or SMSBowerActivationStore()
        self.service = service
        self.country = str(country)
        self.phone_cc = str(phone_cc or "+84")
        self.max_price = float(max_price) if max_price else None
        self.min_price = float(min_price) if min_price and float(min_price) > 0 else None
        self.wait_seconds = max(1.0, float(wait_seconds)) if wait_seconds >= 1 else float(wait_seconds)
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.activation_ttl_seconds = max(60, int(activation_ttl_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self._expected_prefix = self.phone_cc.lstrip("+")

    # ---- 与 SMSBowerOtpProvider 相同的公开接口 ----

    def reserve_number(self, flow_id: str = "") -> SMSBowerActivation:
        reusable = self.store.reusable_activation(flow_id=flow_id)
        if reusable is not None:
            logger.info("Reusing active GrizzlySMS phone (activation {})", reusable.activation_id)
            try:
                self.client.set_status(reusable.activation_id, 3)
            except Exception as exc:
                logger.warning("GrizzlySMS reuse set_status failed: {}", exc)
            return reusable
        return self._purchase_new_number(flow_id)

    def _purchase_new_number(self, flow_id: str = "") -> SMSBowerActivation:
        pause = self.store.global_pause_seconds()
        if pause > 0:
            raise GrizzlyApiError(
                f"GrizzlySMS purchase paused {pause:.0f}s (balance/global); "
                "refill the account or wait for cooldown"
            )
        # 指定国优先; 兜底链默认关闭 (号码必须与授权国一致, 换国号需显式配
        # GRIZZLY_FALLBACK_COUNTRIES=PH,ID,... 才启用)
        fallback_env = (_load_dotenv_value("GRIZZLY_FALLBACK_COUNTRIES") or "").strip()
        try_ids = [self.country]
        try_ids += [GRIZZLY_COUNTRY_IDS[c] for c in
                    (x.strip().upper() for x in fallback_env.split(",")) if c.strip()
                    and c in GRIZZLY_COUNTRY_IDS and GRIZZLY_COUNTRY_IDS[c] not in try_ids]
        no_stock: list[str] = []
        for cid in try_ids:
            # 注意: 不按冷却跳过 — 单国模式下冷却会吃掉整轮换号机会;
            # 冷却只记账 (黑名单已防同号重复, 每次实买都是新号)
            try:
                data = self.client.get_number(self.service, cid, max_price=self.max_price)
            except GrizzlyApiError as exc:
                if "NO_NUMBERS" in str(exc):
                    no_stock.append(cid)
                    continue
                raise
            prefix = GRIZZLY_PHONE_PREFIXES.get(cid, self._expected_prefix)
            try:
                price = self.client.get_prices(self.service, cid).get("price") or 0.0
            except Exception:
                price = 0.0
            activation = SMSBowerActivation(
                activation_id=str(data["activationId"]),
                phone_number=normalize_phone(str(data["phoneNumber"]), f"+{prefix}"),
                provider_id=f"grizzly-{cid}",
                price=price,
                expires_at=time.time() + self.activation_ttl_seconds,
                reused=False,
                flow_id=flow_id,
            )
            # 编号映射防错: 返回号必须带实际国家前缀, 否则立即退号报错
            digits = re.sub(r"\D", "", activation.phone_number)
            if prefix and not digits.startswith(prefix):
                try:
                    self.client.set_status(activation.activation_id, 8)
                except Exception:
                    pass
                raise GrizzlyApiError(
                    f"grizzly country mismatch: got phone {activation.phone_number} "
                    f"(expected prefix +{prefix}, country id {cid})"
                )
            if cid != self.country:
                logger.warning(
                    "GrizzlySMS country {} 无货, 兜底买到 {} 号 {}",
                    self.country, cid, activation.phone_number,
                )
            logger.info(
                "Reserved GrizzlySMS PayPal number {} (country id {} price cap {})",
                activation.phone_number, cid,
                self.max_price if self.max_price is not None else "inf",
            )
            return activation
        raise GrizzlyApiError(
            f"GrizzlySMS PayPal numbers all out of stock (tried country ids: "
            f"{', '.join(try_ids)}; no stock: {', '.join(no_stock) or 'none'})"
        )

    def mark_sms_sent(self, activation: SMSBowerActivation) -> None:
        if activation.reused:
            return
        try:
            self.client.set_status(activation.activation_id, 1)
        except Exception as exc:
            logger.warning("GrizzlySMS mark_sms_sent failed: {}", exc)

    def wait_for_code(self, activation: SMSBowerActivation, timeout_seconds: float | None = None) -> str | None:
        deadline = time.time() + (self.wait_seconds if timeout_seconds is None else float(timeout_seconds))
        while time.time() <= deadline:
            try:
                status = str(self.client.get_status(activation.activation_id))
            except Exception as exc:
                logger.warning("GrizzlySMS get_status failed: {}", exc)
                status = ""
            code = self._code_from_status(status)
            if code:
                self.store.remember_success(
                    activation_id=activation.activation_id,
                    phone_number=activation.phone_number,
                    provider_id=activation.provider_id,
                    price=activation.price,
                    expires_at=activation.expires_at,
                )
                return code
            if status in {"STATUS_CANCEL", "NO_ACTIVATION"}:
                self.store.abandon(activation.activation_id)
                return None
            time.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.time())))
        return None

    def abandon(self, activation: SMSBowerActivation, reason: str) -> None:
        logger.warning(
            "Abandoning GrizzlySMS activation {} reused={} reason={}",
            activation.activation_id, activation.reused, reason,
        )
        try:
            self.client.set_status(activation.activation_id, 8)
        except Exception as exc:
            logger.warning("GrizzlySMS activation cancel failed: {}", exc)
        self.store.abandon(activation.activation_id)
        if activation.phone_number:
            self.store.blacklist_phone(activation.phone_number)
        # provider_id 形如 grizzly-63 (国家粒度), 冷却只影响该国号段
        self.store.record_failure(activation.provider_id or "grizzly", reason)

    def register_confirmation_result(self, activation: SMSBowerActivation, confirmed: bool) -> None:
        if confirmed:
            self.store.remember_success(
                activation_id=activation.activation_id,
                phone_number=activation.phone_number,
                provider_id=activation.provider_id,
                price=activation.price,
                expires_at=activation.expires_at,
                flow_id=activation.flow_id,
            )
            try:
                self.client.set_status(activation.activation_id, 6)
            except Exception as exc:
                logger.warning("GrizzlySMS finish set_status failed: {}", exc)
            return
        self.abandon(activation, "paypal_rejected_code")

    @staticmethod
    def _code_from_status(status: str) -> str:
        if not status.startswith("STATUS_OK:"):
            return ""
        code = status.split(":", 1)[1].strip().strip("'").strip('"')
        match = re.search(r"\d{4,8}", code)
        return match.group(0) if match else code


def grizzly_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    provider = (_load_dotenv_value("PAYPAL_SMS_PROVIDER") or _load_dotenv_value("SMS_PROVIDER")).strip().lower()
    if provider:
        return provider in ("grizzly", "grizzlysms")
    return bool(_load_dotenv_value("GRIZZLYSMS_API_KEY"))


def build_grizzly_provider(
    *,
    enabled: bool | None = None,
    api_key: str | None = None,
    country: str = GRIZZLY_DEFAULT_COUNTRY,
    phone_cc: str = "+84",
    max_price: float | None = None,
    min_price: float | None = None,
    service: str = GRIZZLY_DEFAULT_SERVICE,
    wait_seconds: float | None = None,
) -> GrizzlyOtpProvider | None:
    if not grizzly_enabled(enabled):
        return None
    resolved_key = (
        api_key
        or _load_dotenv_value("GRIZZLYSMS_API_KEY")
        or _load_dotenv_value("PAYPAL_GRIZZLY_API_KEY")
    )
    client = GrizzlyClient(resolved_key)
    return GrizzlyOtpProvider(
        client=client,
        service=service,
        country=country,
        phone_cc=phone_cc,
        max_price=max_price,
        min_price=min_price,
        wait_seconds=(
            _env_float("GRIZZLY_WAIT_SECONDS", float(wait_seconds) if wait_seconds else SMSBOWER_DEFAULT_WAIT_SECONDS, 1.0, 300.0)
        ),
    )
