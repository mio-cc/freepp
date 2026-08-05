"""Token CRUD + 批量导入路由。

REST:
- POST /api/tokens/import  - 批量导入
- GET  /api/tokens         - Token 列表
- DELETE /api/tokens/{id}  - 删除
- POST /api/tokens/{id}/run - 单 Token 启动 (转发到 chain batch)
"""
from __future__ import annotations

from fastapi import APIRouter

from core.token_store import token_store
from .deps import runtime

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


@router.get("")
async def list_tokens(source: str | None = None):
    tokens = await token_store.list_tokens(source=source)
    # 兼容前端字段
    out = []
    for t in tokens:
        out.append({
            "id": t["id"], "email": t.get("email", ""), "sub": t.get("sub", ""),
            "account_id": t.get("account_id", ""), "plan_type": t.get("plan_type", ""),
            "register_method": t.get("register_method", "email"),
            "expires_at": t.get("expires_at", ""), "status": t.get("status", "idle"),
            "created_at": t.get("created_at", ""), "last_run_at": t.get("last_run_at", ""),
            "source": t.get("source", "stripe"),
        })
    return {"ok": True, "tokens": out, "total": len(out), "source": source or "all"}


@router.post("/import")
async def import_tokens(body: dict):
    raw = body.get("raw", "")
    source = str(body.get("source") or "stripe").strip().lower() or "stripe"
    if not raw.strip():
        return {"ok": False, "imported": 0, "failed": 0, "tokens": [], "error": "raw 为空"}
    imported, failed, new_tokens = await token_store.import_raw(raw, source=source)
    # 广播 token_imported 事件
    if runtime.conn_mgr:
        all_tokens = await token_store.list_tokens()
        token_list = [
            {"id": t["id"], "email": t.get("email", ""), "sub": t.get("sub", ""),
             "plan_type": t.get("plan_type", ""), "status": t.get("status", "idle"),
             "register_method": t.get("register_method", "email"),
             "expires_at": t.get("expires_at", ""), "source": t.get("source", "stripe")}
            for t in all_tokens
        ]
        await runtime.conn_mgr.broadcast({
            "type": "token_imported", "tokens": token_list,
            "imported": imported, "failed": failed,
        })
    return {"ok": True, "imported": imported, "failed": failed, "tokens": new_tokens}


@router.get("/inventory")
async def list_inventory(channel: str | None = None, limit: int = 200):
    """成功产出库存 (BA 库)。channel 非空时按支付渠道(提链分支)隔离过滤。"""
    recs = await token_store.list_success(limit=min(int(limit) or 200, 1000), channel=channel)
    out = []
    for r in recs:
        out.append({
            "ba_id": r.get("ba") or "",
            "email": r.get("email") or "",
            "country": r.get("billing_country") or "",
            "paypal_url": r.get("paypal_approve_url") or "",
            "pm_authorize_url": r.get("pm_authorize_url") or "",
            "amount": r.get("amount_due") if r.get("amount_due") is not None else "",
            "currency": r.get("currency") or "",
            "time": r.get("ts") or "",
            "channel": r.get("payment_channel") or "paypal",
        })
    return {"ok": True, "records": out, "total": len(out), "channel": channel or "all"}


@router.delete("/{token_id}")
async def delete_token(token_id: str):
    ok = await token_store.delete_token(token_id)
    return {"ok": ok, "error": "" if ok else "Token 不存在"}


@router.post("/{token_id}/run")
async def run_single(token_id: str, body: dict | None = None):
    """单 Token 启动链路。"""
    if not runtime.orchestrator:
        return {"ok": False, "error": "引擎未就绪"}
    options = {
        "max_concurrent": 1,
        "retry_per_stage": (body or {}).get("retry_per_stage", 3),
        "attempts": (body or {}).get("attempts", 8),
        "auto_billing": (body or {}).get("auto_billing", True),
        "require_zero": (body or {}).get("require_zero", True),
        "channel_check": (body or {}).get("channel_check", True),
        "branch": str((body or {}).get("branch") or "paypal"),
    }
    res = await runtime.orchestrator.run_batch([token_id], options)
    return res
