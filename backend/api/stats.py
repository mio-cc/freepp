"""统计 + 样本 + 配方路由。

REST:
- GET /api/stats             - 累计统计
- GET /api/samples           - 样本查询 (?success=true|false)
- GET /api/formulas          - 成功配方

注意: /api/config 和 /api/billing/templates 已迁移至 api/config.py，
      此处不再注册以避免路由冲突。
"""
from __future__ import annotations

from fastapi import APIRouter

from core.config import settings
from core.token_store import token_store
from .deps import runtime

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def stats():
    if runtime.orchestrator:
        s = runtime.orchestrator.stats.to_dict()
        latencies = runtime.orchestrator.stats.latencies[-100:]
    else:
        s = {"success": 0, "failure": 0, "byCountry": {}, "failByCountry": {},
             "reasons": {}, "stageMatrix": {}}
        latencies = []
    # 合并数据库累计样本数
    try:
        succ, fail = await token_store.count_samples()
        s["success"] = max(s["success"], succ)
        s["failure"] = max(s["failure"], fail)
    except Exception:
        pass
    return {"ok": True, "stats": s, "latencies": latencies}


@router.get("/samples")
async def samples(success: str | None = None, limit: int = 100):
    """样本查询。success=true 仅成功, success=false 仅失败, 不传则全部。"""
    if success is None:
        rows = await token_store.list_samples(limit=limit)
    elif success.lower() == "true":
        rows = await token_store.list_samples(success=True, limit=limit)
    else:
        rows = await token_store.list_samples(success=False, limit=limit)
    # 兼容前端字段
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "ts": r.get("ts", ""), "email": r.get("email", ""),
            "success": bool(r.get("success", 0)),
            "reason_code": r.get("reason_code", ""),
            "reason_text": r.get("reason_text", ""),
            "paypal_approve_url": r.get("paypal_approve_url", ""),
            "amount_due": r.get("amount_due", 0),
            "currency": r.get("currency", ""),
            "country": r.get("country", ""),
            "stage_reached": r.get("stage_reached", ""),
            "chain_id": r.get("chain_id", ""),
            "actual_country": r.get("actual_country", ""),
            "requested_country": r.get("requested_country", ""),
            "exit_ip": r.get("exit_ip", ""),
            "geo_confidence": r.get("geo_confidence", 0.0),
        })
    return {"ok": True, "samples": out, "total": len(out)}


@router.post("/samples/bulk_delete")
async def bulk_delete_samples(body: dict | None = None):
    """批量删除样本记录。body: {ids: [1, 2, 3]}"""
    ids = [int(x) for x in (body or {}).get("ids", []) if str(x).strip().isdigit()]
    if not ids:
        return {"ok": False, "error": "未提供有效 ID", "deleted": 0}
    deleted = await token_store.bulk_delete_samples(ids)
    return {"ok": True, "deleted": deleted}


@router.get("/formulas")
async def formulas():
    """成功配方：基于分段国家策略生成推荐组合。"""
    formulas = [
        {"name": "US-主链 (USD $0)",
         "checkout": "US", "init": "US", "provider": "US",
         "approve": "US", "poll": "US", "resolve": "US", "success_count": 0},
        {"name": "JP-approve 稳定链",
         "checkout": "US", "init": "AU", "provider": "US",
         "approve": "JP", "poll": "JP", "resolve": "JP", "success_count": 0},
        {"name": "HK-中转链",
         "checkout": "GB", "init": "US", "provider": "US",
         "approve": "HK", "poll": "US", "resolve": "US", "success_count": 0},
    ]
    return {"ok": True, "formulas": formulas}



