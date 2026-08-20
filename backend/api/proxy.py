"""代理管理路由。

REST:
- GET  /api/proxy/nodes        - 代理节点列表
- GET  /api/proxy/health       - 代理健康
- POST /api/proxy/parse        - 解析订阅
- POST /api/proxy/fetch-sub    - 拉取订阅
- POST /api/proxy/start        - 启动节点
- POST /api/proxy/stop         - 停止节点
- POST /api/proxy/start-all    - 全部启动
- POST /api/proxy/stop-all     - 全部停止
- GET  /api/proxy/711/status   - 711 完整状态 (只读禁改)
- POST /api/proxy/711/smoke    - 711 冒烟测试 (只读)
- GET  /api/proxy/qg/pools     - QG 隧道池列表
"""
from __future__ import annotations

import os

from fastapi import APIRouter

from core.proxy_pool import proxy_pool
from .deps import runtime

router = APIRouter(prefix="/api/proxy", tags=["proxy"])


@router.get("/nodes")
async def list_nodes():
    return {"ok": True, "nodes": proxy_pool.list_nodes()}


@router.get("/traffic")
async def get_traffic():
    """各功能块 (register/chain/pay/detect) 上传/下传流量快照。"""
    return {"ok": True, "traffic": proxy_pool.get_traffic()}


@router.post("/traffic/reset")
async def reset_traffic(body: dict | None = None):
    """重置流量计数; body.block 指定单块, 省略则全部清零。"""
    block = (body or {}).get("block") if body else None
    proxy_pool.reset_traffic(block)
    return {"ok": True, "traffic": proxy_pool.get_traffic()}


@router.get("/health")
async def proxy_health():
    nodes = await proxy_pool.health_check()
    # 广播健康状态
    if runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    return {"ok": True, "nodes": nodes}


@router.post("/parse")
async def parse_subscription(body: dict):
    raw = body.get("raw", "")
    if not raw.strip():
        return {"ok": False, "count": 0, "error": "raw 为空"}
    count = proxy_pool.parse_subscription(raw)
    nodes = proxy_pool.list_nodes()
    if runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    return {"ok": True, "count": count, "nodes": nodes}


@router.post("/fetch-sub")
async def fetch_subscription(body: dict):
    """拉取订阅链接内容。"""
    url = body.get("url", "")
    if not url:
        return {"ok": False, "error": "缺少 url", "raw": "", "length": 0}
    # 尝试用 curl_cffi 拉取；失败返回提示
    try:
        from curl_cffi import requests as curl  # type: ignore
        r = curl.get(url, impersonate="chrome", timeout=15)
        raw = r.text or ""
        return {"ok": True, "raw": raw, "length": len(raw)}
    except Exception as e:
        return {"ok": False, "error": f"拉取失败: {e}", "raw": "", "length": 0}


@router.post("/start")
async def start_node(body: dict):
    name = body.get("name", "")
    ok = proxy_pool.start_node(name)
    if ok and runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "node_started", "name": name})
    return {"ok": ok, "nodes": proxy_pool.list_nodes()}


@router.post("/stop")
async def stop_node(body: dict):
    name = body.get("name", "")
    ok = proxy_pool.stop_node(name)
    if ok and runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "node_stopped", "name": name})
    return {"ok": ok, "nodes": proxy_pool.list_nodes()}


@router.post("/start-all")
async def start_all():
    cnt = proxy_pool.start_all()
    nodes = proxy_pool.list_nodes()
    if runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    return {"ok": True, "started": cnt, "nodes": nodes}


@router.post("/stop-all")
async def stop_all():
    cnt = proxy_pool.stop_all()
    nodes = proxy_pool.list_nodes()
    if runtime.conn_mgr:
        await runtime.conn_mgr.broadcast({"type": "proxy_health", "nodes": nodes})
    return {"ok": True, "stopped": cnt, "nodes": nodes}


@router.get("/711/status")
async def proxy_711_status():
    """711 代理池完整状态 (只读禁改)。"""
    return {"ok": True, **proxy_pool.proxy711.status()}


@router.post("/711/smoke")
async def proxy_711_smoke():
    """手动触发 711 冒烟测试 (只读，不修改配置)。"""
    result = await proxy_pool.proxy711.smoke_test()
    return {"ok": True, "result": result}


@router.get("/qg/pools")
async def qg_pools():
    return {"ok": True, "pools": proxy_pool.qg_pools_status()}
