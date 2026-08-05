"""共享运行时状态：持有 orchestrator / conn_mgr 引用，由 app.py 启动时注入。

避免循环导入：core 模块不依赖 api，api 通过 runtime 访问 orchestrator。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.orchestrator import AsyncChainOrchestrator, ConnectionManager


class Runtime:
    """全局运行时状态容器。"""

    orchestrator: "AsyncChainOrchestrator | None" = None
    conn_mgr: "ConnectionManager | None" = None
    started: bool = False


runtime = Runtime()
