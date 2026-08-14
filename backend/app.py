"""Min-Implant v2 后端入口：FastAPI (ASGI) + WebSocket + 静态文件服务。

启动:
    cd backend
    python -m uvicorn app:app --host 0.0.0.0 --port 8770
或:
    python app.py

功能:
- REST API (tokens / chain / proxy / stats / config / billing / paypal)
- WebSocket /ws 实时推送链路状态
- 静态文件服务 (../web/index.html + /static/*)
- 启动时初始化 SQLite / 代理池 / 调度器
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 确保当前目录在 sys.path (python app.py 直接运行时)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.chain import router as chain_router
from api.config import router as config_router
from api.proxy import router as proxy_router
from api.stats import router as stats_router
from api.tokens import router as tokens_router
from api.paypal import router as paypal_router
from api.directpay import router as directpay_router
from api.deps import runtime
from core.config import settings
from core.orchestrator import AsyncChainOrchestrator, ConnectionManager
from core.proxy_pool import proxy_pool
from core.token_store import token_store

def _parse_probe(raw):
    try:
        import json
        d = json.loads(raw or '{}')
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _split_tags(raw):
    return [x for x in (str(raw or '').split(',')) if x.strip()]



# =============================================================================
# 生命周期
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- 启动 ----
    # 1. Token 存储
    await token_store.init()
    await token_store.reset_running()
    # 2. WebSocket 连接管理 + 调度器
    conn_mgr = ConnectionManager()
    orchestrator = AsyncChainOrchestrator(conn_mgr)
    runtime.conn_mgr = conn_mgr
    runtime.orchestrator = orchestrator
    runtime.started = True
    # 3. 代理池健康检查循环
    await proxy_pool.start_health_loop()
    # 4. 初始健康检查
    try:
        nodes = await proxy_pool.health_check()
        await conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    except Exception:
        pass
    print(f"[min-implant] 后端已启动 -> http://{settings.host}:{settings.port}")
    print(f"[min-implant] 链路模式: {settings.chain_mode} | curl_cffi: {_has_curl()}")
    print(f"[min-implant] 静态目录: {settings.web_dir}")
    yield
    # ---- 关闭 ----
    await proxy_pool.stop_health_loop()
    await orchestrator.shutdown()
    await token_store.close()
    print("[min-implant] 后端已关闭")


def _has_curl() -> bool:
    try:
        from core.chain import _HAS_CURL  # type: ignore
        return _HAS_CURL
    except Exception:
        return False


# =============================================================================
# FastAPI 应用
# =============================================================================
app = FastAPI(
    title="Min-Implant v2",
    description="$0 ChatGPT Plus -> PayPal BA Approve 提链引擎",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS (开发期允许全部)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 REST 路由
app.include_router(tokens_router)
app.include_router(chain_router)
app.include_router(proxy_router)
app.include_router(stats_router)
app.include_router(config_router)
app.include_router(paypal_router)
app.include_router(directpay_router)


# =============================================================================
# 静态文件服务
# =============================================================================
_web_dir = settings.web_dir
_dist_dir = _web_dir / "dist"
_static_dir = _web_dir / "static"

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

if _dist_dir.exists():
    # 优先伺服 vite 构建产物 (web/dist): 新前端
    app.mount("/assets", StaticFiles(directory=str(_dist_dir / "assets")), name="dist-assets")


@app.get("/")
async def index():
    """返回前端首页 (优先 vite 构建产物 web/dist, 回退旧 web/index.html)。"""
    dist_index = _dist_dir / "index.html"
    if dist_index.exists():
        resp = FileResponse(str(dist_index))
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    index_path = _web_dir / "index.html"
    if index_path.exists():
        resp = FileResponse(str(index_path))
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    return JSONResponse({"ok": True, "service": "min-implant-v2", "version": "2.0.0",
                         "message": "前端未找到，请确认 ../web/dist 或 ../web/index.html 存在"})


# =============================================================================
# WebSocket
# =============================================================================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    conn_mgr = runtime.conn_mgr
    if not conn_mgr:
        await ws.close(code=1011, reason="引擎未就绪")
        return
    q = await conn_mgr.connect()
    # 推送初始 sync
    try:
        orchestrator = runtime.orchestrator
        sync = orchestrator.sync_payload() if orchestrator else {"type": "sync"}
        # 附带 tokens / nodes
        tokens = await token_store.list_tokens()
        sync["tokens"] = [
            {"id": t["id"], "email": t.get("email", ""), "sub": t.get("sub", ""),
             "account_id": t.get("account_id", ""), "plan_type": t.get("plan_type", ""),
             "register_method": t.get("register_method", "email"),
             "session_type": t.get("session_type", ""),
             "probe": _parse_probe(t.get("probe", "")),
             "tags": _split_tags(t.get("tags", "")),
             "expires_at": t.get("expires_at", ""), "status": t.get("status", "idle"),
             "source": t.get("source", "stripe")}
            for t in tokens
        ]
        sync["nodes"] = proxy_pool.list_nodes()
        await ws.send_text(json.dumps(sync, ensure_ascii=False, default=str))
    except Exception:
        pass

    # 后台发送任务：把队列事件推给客户端
    async def _sender():
        try:
            while True:
                event = await q.get()
                await ws.send_text(json.dumps(event, ensure_ascii=False, default=str))
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    sender_task = asyncio.create_task(_sender())
    try:
        # 接收循环：处理客户端消息 (sync_request 等)
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype == "sync_request":
                orchestrator = runtime.orchestrator
                sync = orchestrator.sync_payload() if orchestrator else {"type": "sync"}
                tokens = await token_store.list_tokens()
                sync["tokens"] = [
                    {"id": t["id"], "email": t.get("email", ""), "sub": t.get("sub", ""),
                     "account_id": t.get("account_id", ""), "plan_type": t.get("plan_type", ""),
                     "status": t.get("status", "idle"),
                     "register_method": t.get("register_method", "email"),
                     "session_type": t.get("session_type", ""),
                     "probe": _parse_probe(t.get("probe", "")),
                     "tags": _split_tags(t.get("tags", "")),
                     "expires_at": t.get("expires_at", ""),
                     "source": t.get("source", "stripe")}
                    for t in tokens
                ]
                sync["nodes"] = proxy_pool.list_nodes()
                await ws.send_text(json.dumps(sync, ensure_ascii=False, default=str))
            elif mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        sender_task.cancel()
        conn_mgr.disconnect(q)
        try:
            await sender_task
        except asyncio.CancelledError:
            pass


# =============================================================================
# 健康检查端点
# =============================================================================
@app.get("/api/health")
async def health():
    return {
        "ok": True, "service": "min-implant-v2", "version": "2.0.0",
        "chain_mode": settings.chain_mode,
        "curl_cffi": _has_curl(),
        "web_dir": str(settings.web_dir),
        "web_exists": settings.web_dir.exists(),
    }


# =============================================================================
# 直接运行
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.raw.get("logging", {}).get("level", "info").lower(),
    )

