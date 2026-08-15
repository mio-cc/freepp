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
    email_mode = str(body.get("email_mode") or "mailtm").strip()
    if email_mode not in reg_engine.email_channels():
        return {"ok": False, "error": f"未知邮箱渠道: {email_mode}（可用: {', '.join(reg_engine.email_channels())}）"}
    concurrency = int(body.get("concurrency") or 1)
    raw_cd = body.get("cooldown")
    cooldown = float(raw_cd) if raw_cd is not None else 30.0
    proxy = str(body.get("proxy") or "").strip() or None
    task_id = reg_engine.STATE.try_start()
    if not task_id:
        return {"ok": False, "error": "已有注册任务在运行"}

    def _run():
        try:
            reg_engine.stream_registration(
                count=count, email_mode=email_mode, concurrency=concurrency,
                cooldown=cooldown, task_id=task_id, proxy=proxy)
        except Exception:
            pass

    threading.Thread(target=_run, name="reg-batch", daemon=True).start()
    return {"ok": True, "task_id": task_id, "count": count, "email_mode": email_mode}


@router.post("/stop")
async def register_stop():
    stopped = reg_engine.cancel_registration()
    return {"ok": True, "stopped": stopped}


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


@router.get("/stats")
async def register_stats():
    conn = ra.connect()
    try:
        st = ra.stats(conn)
    finally:
        ra.close(conn)
    return {"ok": True, **st}