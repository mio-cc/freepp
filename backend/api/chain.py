"""链路控制路由。

REST:
- POST /api/chain/batch   - 批量启动
- POST /api/chain/stop    - 停止
- GET  /api/chain/status  - 运行状态
- POST /api/chain/momo    - MoMo 提链启动
"""
from __future__ import annotations

from fastapi import APIRouter

from core.momo import momo_patches
from core.token_store import token_store
from .deps import runtime

router = APIRouter(prefix="/api/chain", tags=["chain"])


@router.post("/batch")
async def batch_start(body: dict):
    """批量启动链路。attempts 等启动参数缺省时取分支配置 (链路配置页管理)。"""
    if not runtime.orchestrator:
        return {"ok": False, "error": "引擎未就绪"}
    token_ids = body.get("token_ids", [])
    if not token_ids:
        return {"ok": False, "error": "未选择 Token"}
    branch = str(body.get("branch") or "paypal")
    from core.config import settings
    bcfg = settings.branch(branch)
    options = {
        "max_concurrent": body.get("max_concurrent", settings.max_concurrent_chains),
        "retry_per_stage": body.get("retry_per_stage", 3),
        "attempts": body.get("attempts", bcfg.attempts),
        "auto_billing": body.get("auto_billing", True),
        "require_zero": body.get("require_zero", True),
        "channel_check": body.get("channel_check", True),
        "branch": branch,
    }
    return await runtime.orchestrator.run_batch(token_ids, options)


@router.post("/stop")
async def stop_chain(body: dict):
    """停止链路。chain_ids 为空则停止全部。"""
    if not runtime.orchestrator:
        return {"ok": False, "error": "引擎未就绪"}
    chain_ids = body.get("chain_ids")
    return await runtime.orchestrator.stop_batch(chain_ids)


@router.get("/status")
async def chain_status():
    if not runtime.orchestrator:
        return {"running": False, "active": 0, "queued": 0, "success": 0, "failure": 0}
    return runtime.orchestrator.status()


@router.post("/momo")
async def momo_run(body: dict):
    """MoMo 提链启动（走通用 AsyncChain，branch=momo 参数化 PM/confirm/resolve）。

    五层 Patch（momo.py）已并入 branch_profile：pm_type=momo、confirm_type=momo、
    resolve 正则 payment.momo.vn/pay/app。connect_intercept/dns_fix 仍需代理层支持。
    """
    if not runtime.orchestrator or not runtime.conn_mgr:
        return {"ok": False, "error": "引擎未就绪"}
    token_id = body.get("token_id")
    if not token_id:
        return {"ok": False, "error": "缺少 token_id"}
    # 更新 Patch 开关（兼容旧前端，仅记录状态）
    patches = body.get("patches")
    if patches:
        momo_patches.update(patches)
    options = {
        "branch": "momo",
        "max_concurrent": 1,
        "attempts": int(body.get("attempts") or 3),
        "require_zero": False,
        "channel_check": True,
    }
    res = await runtime.orchestrator.run_batch([token_id], options)
    return {"ok": bool(res and res.get("ok")), "total": (res or {}).get("total", 0),
            "patches": momo_patches.to_dict(),
            "note": "momo 走 AsyncChain 真实链路 (branch=momo)"}


@router.post("/detect")
async def detect_channel_api(body: dict):
    """渠道检测：checkout(无 promo) -> init0(渠道) -> update(压0) -> init1(验证渠道+0元)。

    body: {channel: "momo"|"pix"|"ideal"|... , country: "VN", currency: "VND"(可选),
           update_country: "VN"(可选, 默认取分支 update 配置), token_id: "123"(可选)}
    返回: {ok, channel, present, zero_ok, methods0, methods1, amount0, amount1, error}
    """
    from core.detect import detect_channel
    from core.proxy_pool import proxy_pool
    from core.config import settings

    channel = str(body.get("channel") or "momo").strip().lower()
    country = str(body.get("country") or "VN").strip().upper()
    currency = str(body.get("currency") or "").strip().upper()
    token_id = str(body.get("token_id") or "").strip()

    # update 出口国家：显式或按分支配置
    branch = channel
    bcfg = settings.branch(branch)
    upd_stage = bcfg.stages.get("update") if isinstance(bcfg.stages, dict) else None
    upd_countries = upd_stage.countries if upd_stage else []
    update_country = str(body.get("update_country") or "").strip().upper() or (upd_countries[0] if upd_countries else "VN")

    # 取 token
    tok = None
    if token_id:
        tok = await token_store.get_token(token_id)
    else:
        toks = await token_store.list_tokens()
        if toks:
            tok = toks[0]
    if not tok:
        return {"ok": False, "error": "未找到可用 Token"}
    at = tok.get("access_token") or tok.get("raw") or ""
    st = tok.get("session_token") or ""

    # 取代理（checkout 出口 + update 出口）
    try:
        proxy = proxy_pool.pick_for_stage("checkout", country)
        update_proxy = proxy_pool.pick_for_stage("update", update_country)
    except Exception as e:
        return {"ok": False, "error": f"代理获取失败: {e}"}

    import asyncio
    try:
        result = await asyncio.to_thread(
            detect_channel, proxy, at, st, country, currency, channel,
            update_country, update_proxy,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    result["token_id"] = tok.get("id")
    return {"ok": True, **result}
