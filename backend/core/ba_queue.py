"""PayPal BA 授权队列 (JSON 文件持久化)。

提链成功后 orchestrator 自动把 paypal 渠道产出的 BA 导入本队列,
PayPal 授权页 (api/paypal.py) 从本队列读取/更新记录。
支持手动导入 (api/paypal.py /ba/import), 所有记录落盘 backend/ba_queue.json,
重启后不丢失; success_inventory 回填仅作为"文件为空且本进程未回填过"的兜底。

并发安全: 全部读写走模块级锁; try_start 提供 pending->running 原子转移
(重复启动返回 already_running); mark_stale 清理僵尸 running。
持久化: 每次变更后原子写 (tmp + os.replace)。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

_BA_TOKEN_RE = re.compile(r"ba_token=(BA-[A-Za-z0-9]+)")
_BARE_TOKEN_RE = re.compile(r"^(BA-[A-Za-z0-9]+)$")

_QUEUE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ba_queue.json"
)

_records: list[dict[str, Any]] = []
_lock = threading.Lock()
_loaded = False

_STALE_RUNNING_MS = 30 * 60 * 1000  # running 超 30min 标记僵尸


def extract_ba_token(url: str) -> str:
    """从 URL 或裸 token 中提取 BA-xxx。"""
    m = _BA_TOKEN_RE.search(url or "")
    if m:
        return m.group(1)
    m = _BARE_TOKEN_RE.match((url or "").strip())
    return m.group(1) if m else ""


def _load_locked() -> None:
    global _records, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _records = [r for r in data if isinstance(r, dict) and r.get("ba_token")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _records = []


def _save_locked() -> None:
    tmp = _QUEUE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_records, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _QUEUE_FILE)
    except OSError:
        pass


def add(ba_token: str, email: str = "", approve_url: str = "",
        country: str = "", chain_id: str = "", status: str = "pending",
        step: str = "submit_email", captcha_type: str = "",
        sms_phone: str = "", error: str = "", source: str = "chain") -> bool:
    """加入队列 (按 ba_token 去重)。返回是否新增。"""
    ba_token = (ba_token or "").strip()
    if not ba_token:
        return False
    with _lock:
        _load_locked()
        for r in _records:
            if r.get("ba_token") == ba_token:
                return False
        now = int(time.time() * 1000)
        _records.append({
            "ba_token": ba_token,
            "email": email,
            "approve_url": approve_url,
            "status": status,
            "step": step,
            "country": (country or "").upper(),
            "chain_id": chain_id,
            "captcha_type": captcha_type,
            "sms_phone": sms_phone,
            "error": error,
            "source": source,
            "created_at": now,
            "updated_at": now,
        })
        _save_locked()
        return True


def import_from_url(url: str, email: str = "", country: str = "",
                    chain_id: str = "", source: str = "chain") -> bool:
    """从提链产出的 paypal_approve_url 导入队列。"""
    tok = extract_ba_token(url or "")
    if not tok:
        return False
    return add(tok, email=email, approve_url=url,
               country=country, chain_id=chain_id, source=source)


def get(ba_token: str) -> dict[str, Any] | None:
    with _lock:
        _load_locked()
        for r in _records:
            if r.get("ba_token") == ba_token:
                return dict(r)
    return None


def update(ba_token: str, **fields: Any) -> None:
    with _lock:
        _load_locked()
        for r in _records:
            if r.get("ba_token") == ba_token:
                for k, v in fields.items():
                    r[k] = v
                if "country" in fields:
                    r["country"] = str(fields["country"] or "").upper()
                r["updated_at"] = int(time.time() * 1000)
                _save_locked()
                return


def list_records() -> list[dict[str, Any]]:
    with _lock:
        _load_locked()
        return [dict(r) for r in _records]


def remove(ba_token: str) -> bool:
    global _records
    with _lock:
        _load_locked()
        before = len(_records)
        _records = [r for r in _records if r.get("ba_token") != ba_token]
        changed = len(_records) != before
        if changed:
            _save_locked()
        return changed


def bulk_remove(ba_tokens: list[str]) -> int:
    """批量删除, 返回删除条数。"""
    global _records
    with _lock:
        _load_locked()
        tokens = set(t for t in (ba_tokens or []) if t)
        before = len(_records)
        _records = [r for r in _records if r.get("ba_token") not in tokens]
        removed = before - len(_records)
        if removed:
            _save_locked()
        return removed


def clear(status: str | None = None) -> int:
    """清空队列 (status 指定时只清该状态), 返回删除条数。"""
    global _records
    with _lock:
        _load_locked()
        before = len(_records)
        if status:
            _records = [r for r in _records if r.get("status") != status]
        else:
            _records = []
        removed = before - len(_records)
        if removed:
            _save_locked()
        return removed


def count() -> int:
    with _lock:
        _load_locked()
        return len(_records)


def try_start(ba_token: str) -> tuple[bool, str]:
    """pending -> running 原子转移。返回 (是否成功, 错误信息)。"""
    with _lock:
        _load_locked()
        r = next((x for x in _records if x.get("ba_token") == ba_token), None)
        if r is None:
            return False, "not_found"
        if r.get("status") == "running":
            return False, "already_running"
        r["status"] = "running"
        r["step"] = "submit_email"
        r["error"] = ""
        r["updated_at"] = int(time.time() * 1000)
        _save_locked()
        return True, ""


def retry(ba_token: str, allow_success: bool = False) -> bool:
    """failed/success -> pending (清空 error/step), 供批量重试。返回是否成功。

    allow_success=True 时已授权记录也可重跑 (消耗新号新卡, 用于 EUAT 到手但
    订阅未生效等场景); 默认仅 failed。
    """
    with _lock:
        _load_locked()
        r = next((x for x in _records if x.get("ba_token") == ba_token), None)
        if r is None:
            return False
        if r.get("status") == "running":
            return False
        if r.get("status") not in ("failed",) and not (allow_success and r.get("status") == "success"):
            return False
        r["status"] = "pending"
        r["step"] = "submit_email"
        r["error"] = ""
        r["updated_at"] = int(time.time() * 1000)
        _save_locked()
        return True


def mark_stale(older_than_ms: int = _STALE_RUNNING_MS) -> int:
    """僵尸 running 清理: running 且 updated_at 超时 -> failed + error=stale_running。"""
    now = int(time.time() * 1000)
    marked = 0
    with _lock:
        _load_locked()
        for r in _records:
            if r.get("status") == "running" and now - int(r.get("updated_at") or 0) > older_than_ms:
                r["status"] = "failed"
                r["error"] = "stale_running"
                r["updated_at"] = now
                marked += 1
        if marked:
            _save_locked()
    return marked