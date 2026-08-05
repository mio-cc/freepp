"""PayPal BA 支付授权 API 路由。

提供 BA 授权记录查询、单条/批量授权启动、授权配置管理等接口。
"""
from __future__ import annotations

import time
import uuid
import re
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/paypal", tags=["paypal"])

# ── 内存中的 BA 授权记录 (后续可替换为持久化存储) ──
_ba_records: list[dict[str, Any]] = []
_ba_config: dict[str, Any] = {
    "sms_provider": "smsbower",
    "sms_price": "0.008",
    "sms_timeout": 15,
    "exit_country": "BR",
    "proxy_type": "711_sticky",
    "captcha_strategy": "dense_signal_reorder_v1",
    "max_retries": 3,
}

BA_TOKEN_RE = re.compile(r"ba_token=(BA-[A-Za-z0-9]+)")


class BAAuthorizeRequest(BaseModel):
    ba_token: str
    config: dict[str, Any] | None = None


class BABatchRequest(BaseModel):
    ba_tokens: list[str]
    config: dict[str, Any] | None = None


class BAConfigUpdate(BaseModel):
    sms_provider: str | None = None
    sms_price: str | None = None
    sms_timeout: int | None = None
    exit_country: str | None = None
    proxy_type: str | None = None
    captcha_strategy: str | None = None
    max_retries: int | None = None


def _extract_ba_token(url: str) -> str:
    m = BA_TOKEN_RE.search(url or "")
    return m.group(1) if m else ""


def _record_to_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "ba_token": r.get("ba_token", ""),
        "email": r.get("email", ""),
        "approve_url": r.get("approve_url", ""),
        "status": r.get("status", "pending"),
        "step": r.get("step", "submit_email"),
        "country": r.get("country", ""),
        "chain_id": r.get("chain_id", ""),
        "captcha_type": r.get("captcha_type", ""),
        "sms_phone": r.get("sms_phone", ""),
        "error": r.get("error", ""),
        "created_at": r.get("created_at", 0),
        "updated_at": r.get("updated_at", 0),
    }


@router.get("/ba/records")
async def get_ba_records() -> dict[str, Any]:
    """获取所有 BA 授权记录。"""
    return {
        "ok": True,
        "records": [_record_to_dict(r) for r in _ba_records],
        "total": len(_ba_records),
    }


@router.get("/ba/pending")
async def get_pending_ba() -> dict[str, Any]:
    """获取待授权的 BA 记录（从成功链路中提取）。"""
    pending = [r for r in _ba_records if r.get("status") == "pending"]
    return {
        "ok": True,
        "records": [_record_to_dict(r) for r in pending],
        "count": len(pending),
    }


@router.post("/ba/authorize")
async def authorize_ba(req: BAAuthorizeRequest) -> dict[str, Any]:
    """启动单条 BA 授权流程。

    BA 授权流程:
    1. submit_email — 提交 PayPal 邮箱
    2. captcha — 触发验证码 (hCaptcha passive / reCAPTCHA Enterprise)
    3. sms — 短信验证码 (SMSBower 接码)
    4. signup — 注册 PayPal 新会员
    5. consent_ba — 同意 Billing Agreement
    6. done — 获取 EUAT, BA 授权完成
    """
    ba_token = req.ba_token.strip()
    if not ba_token:
        return {"ok": False, "error": "ba_token is required"}

    # 查找或创建记录
    record = None
    for r in _ba_records:
        if r.get("ba_token") == ba_token:
            record = r
            break

    if record is None:
        record = {
            "ba_token": ba_token,
            "email": "",
            "approve_url": f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
            "status": "pending",
            "step": "submit_email",
            "country": (req.config or {}).get("exit_country", "BR"),
            "chain_id": "",
            "captcha_type": "",
            "sms_phone": "",
            "error": "",
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }
        _ba_records.append(record)

    # 更新状态为运行中
    record["status"] = "running"
    record["step"] = "submit_email"
    record["updated_at"] = int(time.time() * 1000)

    # 后台执行 BA 授权（ba_paypal: BAAuthorizer 四阶段）
    import asyncio
    from ba_paypal import BAAuthorizer

    cfg = req.config or _ba_config
    proxy = str(cfg.get("proxy") or os.environ.get("MIN_BA_PROXY", "") or "").strip() or None
    country = str(cfg.get("exit_country") or "BR").strip().upper()
    phone = str(cfg.get("phone") or "").strip()
    sms_provider = str(cfg.get("sms_provider") or "smsbower").strip().lower()

    def _run() -> dict:
        auth = BAAuthorizer(proxy=proxy, fp_country=country)
        return auth.authorize(
            f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
            phone=phone or None,
        )

    async def _execute() -> None:
        try:
            result = await asyncio.to_thread(_run)
            ok = result.get("status") == "success"
            record["status"] = "success" if ok else "failed"
            record["step"] = "done" if ok else str(result.get("reason") or "failed")
            record["error"] = "" if ok else str(result.get("error") or result.get("reason") or "")
            if ok:
                record["email"] = result.get("user", {}).get("email", "") if isinstance(result.get("user"), dict) else ""
            record["updated_at"] = int(time.time() * 1000)
        except Exception as e:
            record["status"] = "failed"
            record["error"] = f"{type(e).__name__}: {e}"
            record["updated_at"] = int(time.time() * 1000)

    asyncio.create_task(_execute())

    return {
        "ok": True,
        "ba_token": ba_token,
        "status": "running",
        "step": "submit_email",
        "message": "BA 授权流程已启动 (ba_paypal)",
    }


@router.post("/ba/batch")
async def batch_authorize(req: BABatchRequest) -> dict[str, Any]:
    """批量启动 BA 授权流程。"""
    tokens = req.ba_tokens or []
    if not tokens:
        return {"ok": False, "error": "ba_tokens list is empty"}

    started = 0
    for ba_token in tokens:
        ba_token = ba_token.strip()
        if not ba_token:
            continue

        record = None
        for r in _ba_records:
            if r.get("ba_token") == ba_token:
                record = r
                break

        if record is None:
            record = {
                "ba_token": ba_token,
                "email": "",
                "approve_url": f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
                "status": "running",
                "step": "submit_email",
                "country": (req.config or {}).get("exit_country", "BR"),
                "chain_id": "",
                "captcha_type": "",
                "sms_phone": "",
                "error": "",
                "created_at": int(time.time() * 1000),
                "updated_at": int(time.time() * 1000),
            }
            _ba_records.append(record)
        else:
            record["status"] = "running"
            record["step"] = "submit_email"
            record["updated_at"] = int(time.time() * 1000)

        started += 1

    return {
        "ok": True,
        "started": started,
        "total": len(tokens),
        "message": f"已启动 {started}/{len(tokens)} 条 BA 授权",
    }


@router.post("/ba/import")
async def import_ba_from_chain(request: Request) -> dict[str, Any]:
    """从成功链路中导入 BA URL 到授权队列。

    接收链路成功结果中的 paypal_approve_url，提取 ba_token。
    """
    body = await request.json()
    url = body.get("paypal_approve_url", "")
    email = body.get("email", "")
    country = body.get("country", "")
    chain_id = body.get("chain_id", "")

    ba_token = _extract_ba_token(url)
    if not ba_token:
        return {"ok": False, "error": "No ba_token found in URL"}

    # 检查是否已存在
    for r in _ba_records:
        if r.get("ba_token") == ba_token:
            return {"ok": True, "exists": True, "ba_token": ba_token}

    record = {
        "ba_token": ba_token,
        "email": email,
        "approve_url": url,
        "status": "pending",
        "step": "submit_email",
        "country": country,
        "chain_id": chain_id,
        "captcha_type": "",
        "sms_phone": "",
        "error": "",
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
    }
    _ba_records.append(record)

    return {"ok": True, "imported": True, "ba_token": ba_token}


@router.get("/ba/config")
async def get_ba_config() -> dict[str, Any]:
    """获取 BA 授权配置。"""
    return {"ok": True, "config": _ba_config}


@router.post("/ba/config")
async def update_ba_config(req: BAConfigUpdate) -> dict[str, Any]:
    """更新 BA 授权配置。"""
    updates = req.model_dump(exclude_none=True)
    _ba_config.update(updates)
    return {"ok": True, "config": _ba_config}


@router.get("/ba/stats")
async def get_ba_stats() -> dict[str, Any]:
    """获取 BA 授权统计。"""
    total = len(_ba_records)
    pending = sum(1 for r in _ba_records if r.get("status") == "pending")
    running = sum(1 for r in _ba_records if r.get("status") == "running")
    success = sum(1 for r in _ba_records if r.get("status") == "success")
    failed = sum(1 for r in _ba_records if r.get("status") == "failed")

    # Captcha 类型统计
    iq_count = sum(1 for r in _ba_records if r.get("captcha_type") == "iq")
    pi_count = sum(1 for r in _ba_records if r.get("captcha_type") == "pi")

    return {
        "ok": True,
        "stats": {
            "total": total,
            "pending": pending,
            "running": running,
            "success": success,
            "failed": failed,
            "success_rate": round(success / (success + failed) * 100, 1) if (success + failed) > 0 else 0,
            "captcha_iq": iq_count,
            "captcha_pi": pi_count,
        },
    }


@router.delete("/ba/{ba_token}")
async def delete_ba_record(ba_token: str) -> dict[str, Any]:
    """删除 BA 授权记录。"""
    global _ba_records
    before = len(_ba_records)
    _ba_records = [r for r in _ba_records if r.get("ba_token") != ba_token]
    after = len(_ba_records)
    return {"ok": True, "deleted": before - after}
