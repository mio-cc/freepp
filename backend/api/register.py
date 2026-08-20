# -*- coding: utf-8 -*-
"""api/register.py — ChatGPT 账号注册 API（FastAPI Router）

  GET   /api/register/events?since=N   事件增量轮询（log/progress/complete）
  POST  /api/register/start             开始批量注册（后台线程）
  POST  /api/register/stop              停止当前注册任务
  GET   /api/register/status            运行状态
  GET   /api/register/accounts          注册账号分页列表（脱敏）
  GET   /api/register/accounts/<id>     账号详情（含明文凭据）
  DELETE /api/register/accounts/<id>    删除单号
  GET   /api/register/stats             存活/套餐统计
"""
from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter

from reg import engine as reg_engine
from reg import repo_accounts as ra

router = APIRouter(prefix="/api/register", tags=["register"])


@router.get("/events")
async def register_events(since: int = 0):
    evs = reg_engine.STATE.replay_since(since)
    return {"ok": True, "events": evs, "last_seq": reg_engine.STATE.status()["last_seq"]}


@router.post("/start")
async def register_start(body: dict | None = None):
    body = body or {}
    count = min(max(int(body.get("count", 1)), 1), 200)
    email_mode = str(body.get("email_mode") or "").strip()
    if not email_mode:
        chans = list(reg_engine.email_channels())
        if not chans:
            return {"ok": False, "error": "无可用邮箱渠道（邮箱池为空，请先在邮箱池添加 IMAP 账号）"}
        email_mode = chans[0]
    if email_mode not in reg_engine.email_channels():
        return {"ok": False, "error": f"未知邮箱渠道: {email_mode}（可用: {', '.join(reg_engine.email_channels())}）"}
    concurrency = int(body.get("concurrency") or 1)
    raw_cd = body.get("cooldown")
    cooldown = float(raw_cd) if raw_cd is not None else 30.0
    proxy = str(body.get("proxy") or "").strip() or None
    country = str(body.get("country") or body.get("reg_country") or "auto").strip().upper()
    if country == "":
        country = "AUTO"
    task_id = reg_engine.STATE.try_start()
    if not task_id:
        return {"ok": False, "error": "已有注册任务在运行"}

    def _run():
        try:
            reg_engine.stream_registration(
                count=count, email_mode=email_mode, concurrency=concurrency,
                cooldown=cooldown, task_id=task_id, proxy=proxy, country=country)
        except Exception:
            pass

    threading.Thread(target=_run, name="reg-batch", daemon=True).start()
    return {"ok": True, "task_id": task_id, "count": count, "email_mode": email_mode}


@router.post("/stop")
async def register_stop():
    stopped = reg_engine.cancel_registration()
    return {"ok": True, "stopped": stopped}


@router.get("/countries")
async def register_countries():
    """注册可选出口国家列表 (GEO 表里有完整地理数据的国家)。"""
    try:
        from core.billing import GEO
        countries = sorted(GEO.keys())
    except Exception:
        countries = []
    return {"ok": True, "countries": countries}


@router.get("/channels")
async def register_channels():
    """可用注册邮箱渠道列表 (动态构建, 不再前端硬编码)。
    返回 [{name, label, hint}] — name 即 run(email=name) 的取值。
    """
    channels = []
    names = list(reg_engine.email_channels())
    for name in names:
        if name.startswith("imap:"):
            label = name[5:]
            channels.append({"name": name, "label": f"📧 {label} (IMAP)", "hint": "自建域名邮箱 · IMAP 取码"})
        elif name.startswith("api798"):
            channels.append({"name": name, "label": "api798 卡密邮箱", "hint": "第三方取码服务 · 按卡密消费"})
        else:
            channels.append({"name": name, "label": name, "hint": "自定义渠道"})
    return {"ok": True, "channels": channels}


@router.get("/status")
async def register_status():
    st = reg_engine.STATE.status()
    return {"ok": True, **st, "channels": list(reg_engine.email_channels())}


@router.get("/accounts")
async def register_accounts(search: str = "", status: str = "", page: int = 1,
                            page_size: int = 50):
    conn = ra.connect()
    try:
        result = ra.list_accounts(conn, search=search, status=status,
                                  page=page, page_size=min(max(int(page_size), 1), 200))
    finally:
        ra.close(conn)
    return {"ok": True, **result}


@router.get("/accounts/{account_id}")
async def register_account_detail(account_id: int):
    conn = ra.connect()
    try:
        acc = ra.get_account(conn, account_id)
    finally:
        ra.close(conn)
    if not acc:
        return {"ok": False, "error": "账号不存在"}
    return {"ok": True, "account": acc}


@router.delete("/accounts/{account_id}")
async def register_account_delete(account_id: int):
    conn = ra.connect()
    try:
        deleted = ra.delete_account(conn, account_id)
    finally:
        ra.close(conn)
    return {"ok": bool(deleted), "deleted": deleted}


@router.post("/accounts/bulk_delete")
async def register_accounts_bulk_delete(body: dict | None = None):
    """批量删除注册账号。body: {account_ids: [1, 2, 3]}"""
    account_ids = [int(x) for x in (body or {}).get("account_ids", []) if str(x).strip().isdigit()]
    if not account_ids:
        return {"ok": False, "error": "未提供有效 ID", "deleted": 0}
    conn = ra.connect()
    try:
        deleted = ra.bulk_delete_accounts(conn, account_ids)
    finally:
        ra.close(conn)
    return {"ok": True, "deleted": deleted}


@router.get("/stats")
async def register_stats():
    conn = ra.connect()
    try:
        st = ra.stats(conn)
    finally:
        ra.close(conn)
    return {"ok": True, **st}