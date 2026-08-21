"""Min-Implant v2 后端入口：FastAPI (ASGI) + WebSocket + 静态文件服务。

启动:
    cd backend
    python -m uvicorn app:app --host 0.0.0.0 --port 8770
或:
    python app.py

功能:
- REST API (tokens / chain / proxy / stats / config / billing / paypal)
- WebSocket /ws 实时推送链路状态
- 静态文件服务 (web/dist — frontend/ React 面板构建产物)
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

from api.auth import (
    router as auth_router,
    init_store as init_auth_store,
    apply_auth,
    is_authenticated,
)
from api.chain import router as chain_router
from api.config import router as config_router
from api.proxy import router as proxy_router
from api.stats import router as stats_router
from api.tokens import router as tokens_router
from api.paypal import router as paypal_router
from api.directpay import router as directpay_router
from api.register import router as register_router
from api.mail_pool import router as mail_pool_router
from api.pipeline import router as pipeline_router
from api.deps import runtime
from core.auth_store import AuthStore
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
    # 0. 登入认证: 首次启动产生随机密码并印到 stderr, 后续从 auth.json 读取
    #    密码以 PBKDF2-HMAC-SHA256 杂凑存储, 明文不落盘; 面板可在「密钥与凭据」修改
    try:
        _auth_path = _HERE / "auth.json"
        _auth_store = AuthStore(_auth_path)
        _new_pw = _auth_store.ensure_ready()
        if _new_pw:
            _box = "=" * 60
            print(f"\n{_box}", file=sys.stderr)
            print("[min-implant] 首次启动: 已产生随机登录密码", file=sys.stderr)
            print(_box, file=sys.stderr)
            print(f"登录密码: {_new_pw}", file=sys.stderr)
            print(f"{_box}\n", file=sys.stderr)
            print("[min-implant] 密码仅本次显示, 已杂凑写入 backend/auth.json")
            print("[min-implant] 可在面板「系统 → 密钥与凭据」修改密码")
        init_auth_store(_auth_store)
    except Exception as _e:
        print(f"[min-implant] 认证初始化失败: {_e}")
    # 1. Token 存储
    await token_store.init()
    await token_store.reset_running()
    # 1b. BA 授权队列: 清掉上次进程遗留的僵尸 running (任务随旧进程死亡,
    #     队列状态没机会写回, 前端会一直显示"授权中"; 新进程内无旧任务)
    try:
        from core.ba_queue import mark_stale as _ba_mark_stale
        _stale = _ba_mark_stale(older_than_ms=0)
        if _stale:
            print(f"[min-implant] BA 队列清理僵尸 running: {_stale} 条")
    except Exception:
        pass
    # 2. WebSocket 连接管理 + 调度器
    conn_mgr = ConnectionManager()
    orchestrator = AsyncChainOrchestrator(conn_mgr)
    runtime.conn_mgr = conn_mgr
    runtime.orchestrator = orchestrator
    runtime.started = True
    runtime.loop = asyncio.get_event_loop()  # 供后台线程 (注册/支付) 广播 WebSocket 事件
    # 3. 代理池健康检查循环
    await proxy_pool.start_health_loop()
    # 2b. 密钥/凭据: 从 secrets.json 注入 os.environ (711 代理/api798 卡密/SMS/PayPal 反爬),
    #     早于 api798 渠道加载, 使前端编辑的卡密路径在此覆盖 REG_API798_MAILBOXES
    try:
        from core.secrets_store import secrets_store
        secrets_store.inject_all_env()
    except Exception:
        pass
    # 3b. 注册功能: api798 邮箱提取渠道 (卡密文件经环境变量注入, 不落仓库)
    #     REG_API798_ENABLED=0 可禁用该内置渠道 (开源项目可按需关闭)
    try:
        from reg import engine as _reg_engine
        from reg.channel_api798 import load_mailboxes, build_channel
        _api798_on = os.environ.get("REG_API798_ENABLED", "1") != "0"
        _kml = os.environ.get("REG_API798_MAILBOXES", "").strip()
        if _api798_on and _kml and os.path.isfile(_kml):
            _mbs = load_mailboxes(_kml)
            if _mbs:
                _reg_engine.register_email_channel("api798", build_channel(_mbs))
                print(f"[min-implant] 注册渠道 api798 已加载 {len(_mbs)} 个邮箱")
    except Exception as _e:
        print(f"[min-implant] 注册渠道 api798 加载失败: {_e}")
    # 3c. 注册功能: IMAP 邮箱池渠道 (每域独立渠道, 从 mail_pool.json 领用自有邮箱经 IMAP 取码)
    #     sync_imap_channels 按池状态同步注册/注销 imap:<标签> 渠道;
    #     运行时邮箱池变更 (增删/启停) 也经此函数同步, 免重启。
    try:
        _n = _reg_engine.sync_imap_channels()
        if _n:
            print(f"[min-implant] 注册渠道 imap 已加载 {_n} 个 (每邮箱独立渠道: imap:<标签>)")
    except Exception as _e:
        print(f"[min-implant] 注册渠道 imap 加载失败: {_e}")
    # 4. 初始健康检查
    try:
        nodes = await proxy_pool.health_check()
        await conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    except Exception:
        pass
    # 4b. 流量统计定时广播 (每 2s 推送各功能块上传/下传实时值)
    async def _traffic_loop():
        while True:
            try:
                await asyncio.sleep(2)
                if runtime.conn_mgr:
                    traffic = proxy_pool.get_traffic()
                    await runtime.conn_mgr.broadcast(
                        {"type": "traffic_update", "traffic": traffic})
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(2)
    traffic_task = asyncio.create_task(_traffic_loop())
    print(f"[min-implant] 后端已启动 -> http://{settings.host}:{settings.port}")
    print(f"[min-implant] 链路模式: {settings.chain_mode} | curl_cffi: {_has_curl()}")
    print(f"[min-implant] 静态目录: {settings.web_dir}")
    # 3d. 一键流程守护: 若 pipeline_config.enabled=true 则自动恢复
    try:
        from core.pipeline_daemon import pipeline_daemon
        if pipeline_daemon.config.get("enabled"):
            pipeline_daemon.start()
            print(f"[min-implant] 一键流程守护已自动恢复 (enabled=true)")
    except Exception as _e:
        print(f"[min-implant] 一键流程守护恢复失败: {_e}")
    yield
    # ---- 关闭 ----
    traffic_task.cancel()
    try:
        await traffic_task
    except Exception:
        pass
    try:
        from core.pipeline_daemon import pipeline_daemon
        pipeline_daemon.stop()
    except Exception:
        pass
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

# 登入认证中间件 (保护 /api/* 与 /ws, 豁免首页/静态/health/auth 入口)
# 顺序: CORS 在外层 (先处理 preflight), 认证在内层
apply_auth(app)

# 挂载 REST 路由
app.include_router(auth_router)
app.include_router(tokens_router)
app.include_router(chain_router)
app.include_router(proxy_router)
app.include_router(stats_router)
app.include_router(config_router)
app.include_router(paypal_router)
app.include_router(directpay_router)
app.include_router(register_router)
app.include_router(mail_pool_router)
app.include_router(pipeline_router)


# =============================================================================
# 静态文件服务
# =============================================================================
_web_dir = settings.web_dir
_dist_dir = _web_dir / "dist"

# 【废弃】原生 JS 管理台 (web/index.html + web/static/*) 已于 2026-08-15 移除。
# 旧前端为早期手写页面, 已被 frontend/ (React+Vite) 完全取代;
# 当前唯一前端 = frontend 源码 → vite build → web/dist, 由后端直接伺服。
# 如需恢复旧版, 可 git checkout 历史 commit 取回 web/index.html 与 web/static/,
# 并在此处重新挂载 "/static"。
# _static_dir = _web_dir / "static"
# if _static_dir.exists():
#     app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

if _dist_dir.exists():
    # 伺服 vite 构建产物 (web/dist): 当前唯一前端
    app.mount("/assets", StaticFiles(directory=str(_dist_dir / "assets")), name="dist-assets")


@app.get("/")
async def index():
    """返回前端首页 (vite 构建产物 web/dist, 当前唯一前端)。"""
    dist_index = _dist_dir / "index.html"
    if dist_index.exists():
        resp = FileResponse(str(dist_index))
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    return JSONResponse({"ok": True, "service": "min-implant-v2", "version": "2.0.0",
                         "message": "前端未找到，请先执行 frontend: npm run build (产物输出到 ../web/dist)"})


# =============================================================================
# WebSocket
# =============================================================================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # 登入认证: 未登录的 WS 连接直接拒绝 (close 4401)
    # HTTP middleware 不拦截 WebSocket, 故在端点内自行校验 cookie
    if not is_authenticated(ws):
        await ws.close(code=4401)
        return
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

