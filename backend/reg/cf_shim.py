# -*- coding: utf-8 -*-
"""
cf_shim.py — curl_cffi Session 垫片，专治经 mihomo 代理时
间歇性的 BoringSSL "invalid library" / "TLS connect error"。

现象：同一个节点、同一个代理，偶发 TLS 握手失败，换一个全新 Session 重试就过。
根因：curl_cffi 复用底层 curl handle 时，个别连接进入坏状态，BoringSSL 抛
SSL_ERROR_INVALID_LIBRARY。修复方式 = 捕获该错误后重建 Session（全新 handle）重试。

用法（在 chatgpt.py 里）：
    from cf_shim import Session, requests
替代原来的：
    from curl_cffi.requests import Session
    from curl_cffi import requests
"""
import time
from curl_cffi import requests as _creq
from curl_cffi.requests import Session as _CSession

# 触发重试的报错特征
# 新增代理层错误（Proxy CONNECT aborted / ProxyError）：住宅代理池常按连接
# 轮换出口 IP，重建 Session 后新 CONNECT 可能落到未被 OpenAI 边缘冷却的 IP，
# 从而绕开“整体看似挂掉、实则某个出口 IP 被限”的间歇性阻断。
_RETRY_HINTS = (
    "invalid library", "TLS connect error", "SSL_ERROR", "tls connect error",
    "Proxy CONNECT", "ProxyError", "CONNECT aborted", "Failed to perform, curl: (56)",
    # 网络瞬断 timeout（DDG/mail.tm 直连间歇性超时）：重建 Session 后可能
    # 落到不同的 DNS 解析/路由路径，规避瞬时不可达
    "curl: (28)", "ConnectTimeout", "ReadTimeout", "FetchError",
)

class RetrySession(_CSession):
    def __init__(self, *args, **kwargs):
        self._init_args = args
        self._init_kwargs = kwargs
        super().__init__(*args, **kwargs)

    def request(self, method, url, *args, **kwargs):
        last = None
        for attempt in range(6):
            try:
                return super().request(method, url, *args, **kwargs)
            except Exception as e:
                msg = str(e)
                if any(h in msg for h in _RETRY_HINTS):
                    last = e
                    # 重建底层 curl handle：全新 Session 能绕开坏连接
                    try:
                        self.close()
                    except Exception:
                        pass
                    try:
                        self.__init__(*self._init_args, **self._init_kwargs)
                    except Exception:
                        pass
                    time.sleep(0.7 * (attempt + 1))
                    continue
                raise
        raise last

# 对外暴露与原 curl_cffi 一致的 API
# 关键：让 requests.Session 也指向 RetrySession，
# 这样 chatgpt.py 里 requests.Session(...) 自动带重试。
_creq.Session = RetrySession
requests = _creq
Session = RetrySession
