# -*- coding: utf-8 -*-
"""reg/repo_accounts.py — ChatGPT 注册账号仓储（同步 sqlite3，同一 tokens.db）

reg_accounts 表：注册产出全量记录（含密码/源邮箱/渠道/错误码）。
成功账号同步写入本项目 tokens 表（source='register'），复用 token_store
的字段约定，可直接被提链链路使用。

注意：token_store（aiosqlite）与这里（同步 sqlite3）共用同一 db 文件，
SQLite WAL 模式支持并发读写；写入用 INSERT OR IGNORE 避免与主库竞争。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from core.config import settings


def _db_path() -> str:
    return os.environ.get("MIN_REG_DB") or settings.db_path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reg_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            password TEXT,
            access_token TEXT,
            session_token TEXT,
            refresh_token TEXT,
            alive_status TEXT DEFAULT 'unknown',
            plan_type TEXT DEFAULT 'unknown',
            source_email TEXT,
            email_mode TEXT,
            status TEXT DEFAULT 'active',
            error_code TEXT,
            error_detail TEXT,
            register_ts TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_accounts_email ON reg_accounts(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reg_accounts_created ON reg_accounts(created_at)")
    conn.commit()
    return conn


def upsert_account(conn, rec: dict) -> int:
    """按 email 幂等写入：已存在则更新凭据与状态，否则插入。返回记录 id。"""
    email = str(rec["email"] or "").strip().lower()
    if not email:
        return 0
    row = conn.execute("SELECT id FROM reg_accounts WHERE email = ?", (email,)).fetchone()
    if row is None:
        cur = conn.execute(
            """INSERT INTO reg_accounts
               (email, password, access_token, session_token, refresh_token,
                alive_status, plan_type, source_email, email_mode, status,
                error_code, error_detail, register_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (email,
             rec.get("password"), rec.get("access_token"), rec.get("session_token"),
             rec.get("refresh_token"), rec.get("alive_status", "unknown"),
             rec.get("plan_type", "unknown"), rec.get("source_email"),
             rec.get("email_mode"), rec.get("status", "active"),
             rec.get("error_code"), rec.get("error_detail"), rec.get("register_ts")),
        )
        conn.commit()
        return cur.lastrowid
    conn.execute(
        """UPDATE reg_accounts SET
             password = COALESCE(?, password),
             access_token = COALESCE(?, access_token),
             session_token = COALESCE(?, session_token),
             refresh_token = COALESCE(?, refresh_token),
             alive_status = COALESCE(?, alive_status),
             plan_type = COALESCE(?, plan_type),
             source_email = COALESCE(?, source_email),
             email_mode = COALESCE(?, email_mode),
             status = COALESCE(?, status),
             error_code = ?, error_detail = ?,
             register_ts = COALESCE(?, register_ts)
           WHERE id = ?""",
        (rec.get("password"), rec.get("access_token"), rec.get("session_token"),
         rec.get("refresh_token"), rec.get("alive_status"), rec.get("plan_type"),
         rec.get("source_email"), rec.get("email_mode"), rec.get("status"),
         rec.get("error_code"), rec.get("error_detail"), rec.get("register_ts"),
         row["id"]),
    )
    conn.commit()
    return row["id"]


def push_to_tokens(conn, rec: dict) -> bool:
    """注册成功账号同步写入 tokens 表（source='register'）。

    去重：access_token 重复 或 同 email 已存在则跳过（对齐 token_store.import_from_pool）。
    """
    import uuid as _uuid
    at = str(rec.get("access_token") or "").strip()
    email = str(rec.get("email") or "").strip().lower()
    if not at or not email:
        return False
    try:
        dup = conn.execute(
            "SELECT 1 FROM tokens WHERE access_token=? OR (email=? AND email<>'') LIMIT 1",
            (at, email)).fetchone()
        if dup:
            return False
        plan = str(rec.get("plan_type") or "").strip() or "free"
        tid = _uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO tokens (id,raw,access_token,session_token,account_id,sub,email,"
            "plan_type,register_method,expires_at,status,created_at,last_run_at,source,tags) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, at, at, str(rec.get("session_token") or ""), "", "", email, plan,
             "email", "", "idle", now, "", "register", ""),
        )
        conn.commit()
        return True
    except Exception:
        return False


def list_accounts(conn, search: str = "", status: str = "", page: int = 1,
                  page_size: int = 50) -> dict:
    where = ["1=1"]
    params: list = []
    if search:
        where.append("(email LIKE ? OR source_email LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status:
        where.append("status = ?")
        params.append(status)
    wsql = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) AS c FROM reg_accounts WHERE {wsql}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM reg_accounts WHERE {wsql} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size)).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "email": r["email"], "alive_status": r["alive_status"],
            "plan_type": r["plan_type"], "source_email": r["source_email"],
            "email_mode": r["email_mode"], "status": r["status"],
            "error_code": r["error_code"], "error_detail": r["error_detail"],
            "register_ts": r["register_ts"], "created_at": r["created_at"],
            "has_password": bool(r["password"]),
            "has_access_token": bool(r["access_token"]),
            "has_session_token": bool(r["session_token"]),
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size)}


def get_account(conn, account_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reg_accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "email": row["email"], "password": row["password"],
        "access_token": row["access_token"], "session_token": row["session_token"],
        "refresh_token": row["refresh_token"], "alive_status": row["alive_status"],
        "plan_type": row["plan_type"], "source_email": row["source_email"],
        "email_mode": row["email_mode"], "status": row["status"],
        "error_code": row["error_code"], "error_detail": row["error_detail"],
        "register_ts": row["register_ts"], "created_at": row["created_at"],
    }


def delete_account(conn, account_id: int) -> bool:
    cur = conn.execute("DELETE FROM reg_accounts WHERE id = ?", (account_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_delete_accounts(conn, account_ids: list[int]) -> int:
    """批量删除注册账号，返回实际删除条数。"""
    if not account_ids:
        return 0
    placeholders = ",".join("?" * len(account_ids))
    cur = conn.execute(
        f"DELETE FROM reg_accounts WHERE id IN ({placeholders})", account_ids)
    conn.commit()
    return cur.rowcount


def stats(conn) -> dict:
    rows = conn.execute(
        """SELECT
             COUNT(*) AS total,
             COALESCE(SUM(CASE WHEN status='active' THEN 1 ELSE 0 END), 0) AS active,
             COALESCE(SUM(CASE WHEN status='disabled' THEN 1 ELSE 0 END), 0) AS disabled
           FROM reg_accounts"""
    ).fetchone()
    alive = conn.execute(
        "SELECT alive_status, COUNT(*) AS c FROM reg_accounts GROUP BY alive_status").fetchall()
    plans = conn.execute(
        "SELECT plan_type, COUNT(*) AS c FROM reg_accounts GROUP BY plan_type").fetchall()
    return {
        **dict(rows),
        "alive_status": {r["alive_status"]: r["c"] for r in alive},
        "plan_type": {r["plan_type"]: r["c"] for r in plans},
    }


def close(conn):
    try:
        conn.close()
    except Exception:
        pass