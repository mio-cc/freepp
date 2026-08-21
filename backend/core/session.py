"""会话签名 cookie。

设计：
- token 格式：`{timestamp_hex}.{hmac_hexsig}`
- timestamp 为创建时刻的 unix epoch（秒），16 进制
- signature 为 HMAC-SHA256(timestamp_hex, secret)
- verify 时重算签名并以 hmac.compare_digest 常量时间比对
- 过期判定：now - timestamp > max_age_seconds 则失效

无第三方依赖，仅用标准库 hmac / hashlib / time。
"""
from __future__ import annotations

import hashlib
import hmac
import time

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 天


class SessionManager:
    """签名 cookie 会话管理器。

    参数：
        secret: 签名密钥（来自 auth_store.session_secret）
        max_age_seconds: token 有效期，默认 7 天
    """

    def __init__(self, secret: str, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> None:
        if not secret:
            raise ValueError("secret 不能为空")
        self._secret = secret.encode("utf-8")
        self._max_age = max_age_seconds

    def _sign(self, ts_hex: str) -> str:
        return hmac.new(self._secret, ts_hex.encode("utf-8"), hashlib.sha256).hexdigest()

    def create(self) -> str:
        """生成新 token。"""
        ts_hex = format(int(time.time()), "x")
        sig = self._sign(ts_hex)
        return f"{ts_hex}.{sig}"

    def verify(self, token: str | None) -> bool:
        """校验 token。有效返回 True，否则 False。"""
        if not token or not isinstance(token, str):
            return False
        parts = token.split(".")
        if len(parts) != 2:
            return False
        ts_hex, sig = parts
        # timestamp 必须是合法 hex
        try:
            ts = int(ts_hex, 16)
        except ValueError:
            return False
        # 过期检查（浮点比较，避免整秒截断误差）
        if self._max_age > 0 and (time.time() - ts) > self._max_age:
            return False
        # 常量时间签名比对
        expected = self._sign(ts_hex)
        return hmac.compare_digest(sig, expected)
