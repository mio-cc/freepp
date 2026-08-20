# -*- coding: utf-8 -*-
"""api/mail_pool.py — 邮箱池管理 API (FastAPI Router)

  GET    /api/mail_pool              全部邮箱 + 全局规则 + 预设主机
  GET    /api/mail_pool/stats        统计
  POST   /api/mail_pool              添加单邮箱
  POST   /api/mail_pool/bulk         批量导入 (JSON 数组)
  PUT    /api/mail_pool/{id}         更新
  DELETE /api/mail_pool/{id}         删除
  POST   /api/mail_pool/{id}/enable   启用
  POST   /api/mail_pool/{id}/disable  禁用
  POST   /api/mail_pool/{id}/test     连接测试
  POST   /api/mail_pool/test_all      批量测试全部
  PUT    /api/mail_pool/rules         更新全局取码规则
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

from core.mail_pool_store import mail_pool_store
from core import mail_pool_store as _mps

router = APIRouter(prefix="/api/mail_pool", tags=["mail_pool"])


def _sync_channels() -> None:
    """池变更后同步注册/注销 imap:* 渠道 (运行时动态, 免重启)。

    邮箱增删/启停/改标签都会影响渠道列表; 失败仅记日志, 不影响 API 返回。
    """
    try:
        from reg import engine as _reg_engine
        _reg_engine.sync_imap_channels()
    except Exception as _e:
        print(f"[mail_pool] 同步 IMAP 渠道失败: {_e}")


@router.get("")
async def mail_pool_list():
    data = mail_pool_store.get_all()
    return {"ok": True, **data}


@router.get("/stats")
async def mail_pool_stats():
    return {"ok": True, **mail_pool_store.stats()}


# ---- 注意: /stats / /bulk / /test_all / /rules 等静态路径须在 /{mbox_id} 之前声明,
#      否则 FastAPI 会把 "stats" 当 mbox_id 捕获 ----

@router.put("/rules")
async def mail_pool_rules(body: dict):
    rules = mail_pool_store.update_rules(body)
    return {"ok": True, "rules": rules}


@router.get("/presets")
async def mail_pool_presets():
    return {"ok": True, "presets": _mps.list_presets()}


@router.post("/presets")
async def mail_pool_add_preset(body: dict | None = None):
    """添加用户自定义 IMAP 预设。body: {label, imap_host, imap_port?, imap_ssl?}"""
    body = body or {}
    label = str(body.get("label") or "").strip()
    host = str(body.get("imap_host") or "").strip()
    port = int(body.get("imap_port") or 993)
    ssl = bool(body.get("imap_ssl", True))
    if not label:
        return {"ok": False, "error": "缺少 label"}
    ok = _mps.add_preset(label, host, port, ssl)
    return {"ok": ok, "presets": _mps.list_presets() if ok else []}


@router.delete("/presets/{label}")
async def mail_pool_delete_preset(label: str):
    ok = _mps.delete_preset(label)
    return {"ok": ok, "presets": _mps.list_presets() if ok else []}


@router.post("/test_all")
async def mail_pool_test_all(body: dict | None = None):
    """批量测试邮箱 (放线程池, 逐个测)。
    body: {ids?: ["id1", ...]} 非空时仅测试指定邮箱, 否则测试全部 enabled。"""
    only_ids = (body or {}).get("ids")
    if only_ids and not isinstance(only_ids, list):
        only_ids = None
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, mail_pool_store.test_all, only_ids)
    return res


def _parse_bulk_body(body: dict) -> list[dict]:
    """支持两种批量导入格式: JSON 数组 / 竖线分隔文本行。"""
    items = body.get("items")
    if isinstance(items, list):
        return [i for i in items if isinstance(i, dict)]
    text = str(body.get("text") or "").strip()
    out: list[dict] = []
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 行格式: host|port|user|pass|alias_mode|catchall_domain
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        out.append({
            "imap_host": parts[0],
            "imap_port": int(parts[1]) if parts[1].isdigit() else 993,
            "username": parts[2],
            "password": parts[3],
            "alias_mode": parts[4] if len(parts) > 4 and parts[4] in ("direct", "catchall") else "direct",
            "catchall_domain": parts[5] if len(parts) > 5 else "",
        })
    return out


@router.post("")
async def mail_pool_add(body: dict):
    mbox = mail_pool_store.add(body)
    _sync_channels()
    return {"ok": True, "mailbox": mbox}


@router.post("/bulk")
async def mail_pool_bulk(body: dict):
    items = _parse_bulk_body(body)
    if not items:
        return {"ok": False, "error": "无可导入条目"}
    res = mail_pool_store.bulk_import(items)
    _sync_channels()
    return res


@router.put("/{mbox_id}")
async def mail_pool_update(mbox_id: str, body: dict):
    res = mail_pool_store.update(mbox_id, body)
    _sync_channels()
    return res


@router.delete("/{mbox_id}")
async def mail_pool_delete(mbox_id: str):
    deleted = mail_pool_store.delete(mbox_id)
    _sync_channels()
    return {"ok": deleted}


@router.post("/{mbox_id}/enable")
async def mail_pool_enable(mbox_id: str):
    res = mail_pool_store.set_enabled(mbox_id, True)
    _sync_channels()
    return res


@router.post("/{mbox_id}/disable")
async def mail_pool_disable(mbox_id: str):
    res = mail_pool_store.set_enabled(mbox_id, False)
    _sync_channels()
    return res


@router.post("/bulk_enable")
async def mail_pool_bulk_enable(body: dict | None = None):
    """批量启停邮箱。body: {mbox_ids: [...], enabled: true/false}"""
    mbox_ids = [str(x).strip() for x in (body or {}).get("mbox_ids", []) if str(x).strip()]
    enabled = bool((body or {}).get("enabled", True))
    if not mbox_ids:
        return {"ok": False, "error": "未提供有效 ID", "changed": 0}
    changed = mail_pool_store.bulk_set_enabled(mbox_ids, enabled)
    _sync_channels()
    return {"ok": True, "changed": changed}


@router.post("/bulk_delete")
async def mail_pool_bulk_delete(body: dict | None = None):
    """批量删除邮箱。body: {mbox_ids: [...]}"""
    mbox_ids = [str(x).strip() for x in (body or {}).get("mbox_ids", []) if str(x).strip()]
    if not mbox_ids:
        return {"ok": False, "error": "未提供有效 ID", "deleted": 0}
    deleted = mail_pool_store.bulk_delete(mbox_ids)
    _sync_channels()
    return {"ok": True, "deleted": deleted}


@router.post("/{mbox_id}/test")
async def mail_pool_test(mbox_id: str):
    """连接测试 (imaplib 阻塞调用放线程池)。"""
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(None, mail_pool_store.test_connection, mbox_id)
    return res
