"""traffic.py — 代理流量实时统计 (按功能分块: 注册/提链/支付/检测)。

在所有经过代理的 HTTP 请求出口 (chain._req / chatgpt_session / 注册流程 / PayPalSession)
调用 record(block, response) 即可累计上传/下传字节, 按 4 个功能块分别统计。

流量归类通过 threading.local 上下文传递: 各功能入口设置当前块标识,
拦截点从 threadlocal 读取, 默认归 "chain"。
"""
from __future__ import annotations

import threading
from typing import Any

# ── 功能块标识 ──────────────────────────────────────────────────────
BLOCK_REGISTER = "register"
BLOCK_CHAIN = "chain"
BLOCK_PAY = "pay"
BLOCK_DETECT = "detect"

_ALL_BLOCKS = (BLOCK_REGISTER, BLOCK_CHAIN, BLOCK_PAY, BLOCK_DETECT)

# 线程局部上下文: 当前功能块 (默认 chain)
_ctx = threading.local()


def set_block(block: str) -> None:
    """设置当前线程的功能块标识 (在功能入口调用)。"""
    _ctx.block = block if block in _ALL_BLOCKS else BLOCK_CHAIN


def get_block() -> str:
    """读取当前线程的功能块标识 (拦截点调用)。"""
    return getattr(_ctx, "block", BLOCK_CHAIN)


def clear_block() -> None:
    """清除当前线程的功能块标识 (功能结束时调用)。"""
    _ctx.block = BLOCK_CHAIN


class _BlockContext:
    """with block(BLOCK_REGISTER): ... 的上下文管理器。"""

    def __init__(self, block: str) -> None:
        self._block = block
        self._prev: str = BLOCK_CHAIN

    def __enter__(self) -> "_BlockContext":
        self._prev = get_block()
        set_block(self._block)
        return self

    def __exit__(self, *exc: Any) -> None:
        set_block(self._prev)


def block(block: str) -> _BlockContext:
    """上下文管理器: with traffic.block(BLOCK_REGISTER): ..."""
    return _BlockContext(block)


def _extract_bytes(response: Any) -> tuple[int, int]:
    """从 HTTP response 对象提取上传/下传字节。
    兼容 curl_cffi.Response 和 httpx.Response。
    """
    up = 0
    down = 0
    try:
        # 下传: response.content (bytes)
        content = getattr(response, "content", None)
        if isinstance(content, (bytes, bytearray)):
            down = len(content)
        elif content is None:
            # curl_cffi 懒加载 .text 时 .content 可能未读; 试 .text
            text = getattr(response, "text", "")
            if text:
                down = len(text.encode("utf-8", errors="ignore"))
    except Exception:
        pass

    try:
        # 上传: request body
        req = getattr(response, "request", None)
        if req is not None:
            body = getattr(req, "body", None)
            if isinstance(body, (bytes, bytearray)):
                up = len(body)
            elif isinstance(body, str):
                up = len(body.encode("utf-8", errors="ignore"))
            elif body is None:
                # httpx: request.content
                content = getattr(req, "content", None)
                if isinstance(content, (bytes, bytearray)):
                    up = len(content)
    except Exception:
        pass

    return up, down


def record(block: str | None, response: Any) -> None:
    """从 HTTP response 提取字节并累加到 proxy_pool 流量计数器。
    block=None 时用 threadlocal 上下文 (get_block())。
    """
    actual_block = block or get_block()
    if actual_block not in _ALL_BLOCKS:
        actual_block = BLOCK_CHAIN
    up, down = _extract_bytes(response)
    if up == 0 and down == 0:
        return
    try:
        from core.proxy_pool import proxy_pool
        proxy_pool.record_traffic(actual_block, up, down)
    except Exception:
        pass


def record_response(response: Any) -> None:
    """从 threadlocal 上下文自动归类并记录流量 (拦截点最常用入口)。"""
    record(None, response)
