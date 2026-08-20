"""共享运行时状态：持有 orchestrator / conn_mgr 引用，由 app.py 启动时注入。

避免循环导入：core 模块不依赖 api，api 通过 runtime 访问 orchestrator。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.orchestrator import AsyncChainOrchestrator, ConnectionManager


class Runtime:
    """全局运行时状态容器。"""

    orchestrator: "AsyncChainOrchestrator | None" = None
    conn_mgr: "ConnectionManager | None" = None
    started: bool = False
    loop: "asyncio.AbstractEventLoop | None" = None  # 主事件循环引用 (供后台线程广播)


runtime = Runtime()


def emit_ws(event: dict[str, Any]) -> None:
    """线程安全的 WebSocket 广播 (从后台线程或事件循环内均可调用)。

    - 在事件循环线程内: create_task 异步广播 (非阻塞)
    - 在后台线程内: run_coroutine_threadsafe 投到主循环 (跨线程安全)
    - conn_mgr / loop 不可用时静默跳过 (注册/支付功能不依赖广播)
    """
    cm = runtime.conn_mgr
    if cm is None:
        return
    try:
        loop = asyncio.get_running_loop()
        # 当前在事件循环线程内
        asyncio.ensure_future(cm.broadcast(event))
        return
    except RuntimeError:
        # 当前在后台线程, 没有运行中的事件循环
        loop = runtime.loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(cm.broadcast(event), loop)
        except Exception:
            pass
