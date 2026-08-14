"""线程亲和共享 Playwright sync 实例。

背景 (2026-08-15 BA-7FC18 实测): Playwright sync API 的 dispatcher 用 greenlet
在线程内 `loop.run_until_complete(...)`, 该 loop 在 dispatcher 存活期间保持
running。`sync_playwright()` 的检查只认"当前线程是否有 running loop"——
同一线程内第二次调用 `sync_playwright()` 就会命中
"It looks like you are using Playwright Sync API inside the asyncio loop"。

本模块按线程缓存单个实例: 同一线程的所有 playwright 调用 (local_headless /
recaptcha_solver / hcaptcha_semi_hybrid / roxy connect) 复用同一个实例,
从根上消除二次启动检查 (Playwright 官方要求 sync API 单线程单例)。
浏览器/页面句柄由调用方自行管理 (launch/close), 本模块只管实例复用。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

_thread = threading.local()


@contextmanager
def shared_playwright() -> Iterator[Any]:
    """返回当前线程的共享 playwright 实例 (首次调用时启动)。"""
    mgr = getattr(_thread, "mgr", None)
    if mgr is None:
        from playwright.sync_api import sync_playwright

        mgr = sync_playwright()
        pw = mgr.__enter__()
        _thread.mgr = mgr
        _thread.pw = pw
    yield _thread.pw


def close_shared_playwright() -> None:
    """关闭当前线程的共享实例 (先由调用方 close 各自的 browser)。"""
    mgr = getattr(_thread, "mgr", None)
    if mgr is not None:
        try:
            mgr.__exit__(None, None, None)
        except Exception:
            pass
    _thread.mgr = None
    _thread.pw = None


def shared_playwright_active() -> bool:
    return getattr(_thread, "mgr", None) is not None