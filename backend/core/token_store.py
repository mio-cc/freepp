"""Token 存储 (aiosqlite 异步 SQLite)。

表结构：
- tokens: id, raw, access_token, session_token, account_id, sub, email,
          plan_type, register_method, expires_at, status, created_at, last_run_at
- success_inventory: id, ts, email, ba, paypal_approve_url, pm_authorize_url,
                     amount_due, currency, billing_country, payment_channel
- samples: 成功/失败样本记录

首次启动若 tokens 表为空，自动注入 mock Token 让前端可展示。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .config import settings

_RE_JWT = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

# Mock Token 池 (合法 JWT 结构的假数据, 用于前端展示)
_MOCK_TOKENS = [
    ("user1@example.com", "plus", "email"),
    ("user2@example.com", "plus", "email"),
    ("testmail@proton.me", "plus", "email"),
    ("guest3@gmail.com", "plus", "email"),
    ("alex.brown@outlook.com", "plus", "email"),
    ("plus_user@yahoo.com", "plus", "email"),
    ("devtest@mail.com", "free", "email"),
    ("vip@icloud.com", "plus", "email"),
]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fake_jwt(sub: str) -> str:
    """构造一个结构合法的假 JWT（header.payload.sig，仅用于 mock 展示）。"""
    import base64

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).rstrip(b"=").decode()

    header = b64({"alg": "HS256", "typ": "JWT"})
    payload = b64({
        "sub": sub, "email": sub, "iss": "https://auth.openai.com/",
        "iat": int(time.time()) - 3600, "exp": int(time.time()) + 86400 * 7,
        "https://api.openai.com/auth": {"user_id": sub.split("@")[0], "plan": "plus"},
    })
    sig = base64.urlsafe_b64encode(b"mock-signature-" + uuid.uuid4().bytes).rstrip(b"=").decode()
    return f"{header}.{payload}.{sig}"


def _decode_jwt_meta(access_token: str) -> dict[str, Any]:
    """从 JWT payload 解析 sub/email/plan（不验签，仅供展示）。"""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return {}
        import base64
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        auth = payload.get("https://api.openai.com/auth") or {}
        return {
            "sub": str(payload.get("sub") or ""),
            "email": str(payload.get("email") or payload.get("https://api.openai.com/auth", {}).get("user_id") or ""),
            "account_id": str(auth.get("user_id") or ""),
            "plan_type": str(auth.get("plan") or payload.get("plan") or "free"),
        }
    except Exception:
        return {}


def _extract_tokens(raw: str) -> list[tuple[str, str, dict]]:
    """从原始文本解析出 [(access_token, session_token, meta), ...]。

    支持每行一个 accessToken，或整段 Session JSON。
    """
    out: list[tuple[str, str, dict]] = []
    text = raw.strip()
    if not text:
        return out
    # 尝试整段 JSON
    if text.startswith("{"):
        try:
            d = json.loads(text)
            at = str(d.get("accessToken") or d.get("access_token") or "").strip()
            st = str(d.get("sessionToken") or d.get("session_token") or "").strip()
            if at:
                out.append((at, st, _decode_jwt_meta(at)))
                return out
        except json.JSONDecodeError:
            pass
        # 正则抽取
        for m in re.finditer(r'"accessToken"\s*:\s*"([^"]+)"', text):
            at = m.group(1).strip()
            if _RE_JWT.fullmatch(at):
                st = ""
                m2 = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', text[m.end():m.end() + 500])
                if m2:
                    st = m2.group(1).strip()
                out.append((at, st, _decode_jwt_meta(at)))
        if out:
            return out
    # 逐行
    for line in text.splitlines():
        at = line.strip().strip(",").strip('"')
        if _RE_JWT.fullmatch(at):
            out.append((at, "", _decode_jwt_meta(at)))
    return out


class TokenStore:
    """异步 Token 存储。"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        db = await aiosqlite.connect(self.db_path)
        # WAL 模式提升并发读写
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id TEXT PRIMARY KEY,
                raw TEXT,
                access_token TEXT,
                session_token TEXT,
                account_id TEXT,
                sub TEXT,
                email TEXT,
                plan_type TEXT,
                register_method TEXT DEFAULT 'email',
                expires_at TEXT,
                status TEXT DEFAULT 'idle',
                created_at TEXT,
                last_run_at TEXT,
                source TEXT DEFAULT 'stripe'
            )
        """)
        # 旧库迁移: 无 source 列时补列 (默认 stripe = PayPal 提炼库)
        _cur = await db.execute("PRAGMA table_info(tokens)")
        cols = [r[1] for r in await _cur.fetchall()]
        if "source" not in cols:
            await db.execute("ALTER TABLE tokens ADD COLUMN source TEXT DEFAULT 'stripe'")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS success_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                email TEXT,
                ba TEXT,
                paypal_approve_url TEXT,
                pm_authorize_url TEXT,
                amount_due INTEGER,
                currency TEXT,
                billing_country TEXT,
                payment_channel TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                success INTEGER,
                email TEXT,
                chain_id TEXT,
                reason_code TEXT,
                reason_text TEXT,
                paypal_approve_url TEXT,
                amount_due INTEGER,
                currency TEXT,
                country TEXT,
                stage_reached TEXT,
                payload TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tokens_status ON tokens(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_samples_success ON samples(success)")
        await db.commit()
        self._db = db
        # 首次注入 mock token
        await self._seed_if_empty()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "TokenStore 未初始化，请先 await init()"
        return self._db

    async def _seed_if_empty(self) -> None:
        async with self._lock:
            cur = await self.db.execute("SELECT COUNT(*) FROM tokens")
            (cnt,) = await cur.fetchone()
            if cnt > 0:
                return
            now = _utc()
            for email, plan, method in _MOCK_TOKENS:
                at = _fake_jwt(email)
                meta = _decode_jwt_meta(at)
                await self.db.execute(
                    "INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], at, at, "", meta.get("account_id", ""),
                     meta.get("sub", email), email, plan, method, "", "idle", now, ""),
                )
            await self.db.commit()

    # ------------------------------------------------------------------
    # Token CRUD
    # ------------------------------------------------------------------
    async def list_tokens(self, source: str | None = None) -> list[dict[str, Any]]:
        """列出 tokens；source 非空时按 token 库来源隔离过滤。"""
        if source:
            cur = await self.db.execute(
                "SELECT * FROM tokens WHERE source=? ORDER BY created_at DESC", (source,))
        else:
            cur = await self.db.execute("SELECT * FROM tokens ORDER BY created_at DESC")
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def get_token(self, token_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM tokens WHERE id=?", (token_id,))
        row = await cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def delete_token(self, token_id: str) -> bool:
        cur = await self.db.execute("DELETE FROM tokens WHERE id=?", (token_id,))
        await self.db.commit()
        return cur.rowcount > 0

    async def import_raw(self, raw: str, source: str = "stripe") -> tuple[int, int, list[dict]]:
        """批量导入。返回 (imported, failed, tokens)。source 标记 token 库来源(分支隔离)。"""
        parsed = _extract_tokens(raw)
        imported = 0
        failed = max(0, len(raw.strip().splitlines()) - len(parsed)) if raw.strip() else 0
        now = _utc()
        new_tokens: list[dict] = []
        for at, st, meta in parsed:
            # 去重（按 access_token）
            cur = await self.db.execute("SELECT id FROM tokens WHERE access_token=?", (at,))
            if await cur.fetchone():
                failed += 1
                continue
            tid = uuid.uuid4().hex[:12]
            email = meta.get("email") or ""
            sub = meta.get("sub") or ""
            plan = meta.get("plan_type") or "free"
            await self.db.execute(
                "INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, at, at, st, meta.get("account_id", ""), sub, email, plan,
                 "email", "", "idle", now, "", source),
            )
            new_tokens.append({
                "id": tid, "raw": at, "access_token": at, "session_token": st,
                "account_id": meta.get("account_id", ""), "sub": sub, "email": email,
                "plan_type": plan, "register_method": "email", "expires_at": "",
                "status": "idle", "created_at": now, "last_run_at": "", "source": source,
            })
            imported += 1
        await self.db.commit()
        return imported, failed, new_tokens

    async def set_status(self, token_id: str, status: str) -> None:
        last_run = _utc() if status in ("running", "success", "failed") else None
        if last_run:
            await self.db.execute(
                "UPDATE tokens SET status=?, last_run_at=? WHERE id=?", (status, last_run, token_id))
        else:
            await self.db.execute("UPDATE tokens SET status=? WHERE id=?", (status, token_id))
        await self.db.commit()

    async def reset_running(self) -> None:
        """启动时把残留 running 状态重置为 idle。"""
        await self.db.execute("UPDATE tokens SET status='idle' WHERE status='running'")
        await self.db.commit()

    # ------------------------------------------------------------------
    # 成功库存
    # ------------------------------------------------------------------
    async def add_success(self, email: str, ba: str, paypal_url: str, pm_url: str,
                          amount_due: int, currency: str, country: str, channel: str = "paypal") -> int:
        cur = await self.db.execute(
            "INSERT INTO success_inventory (ts,email,ba,paypal_approve_url,pm_authorize_url,"
            "amount_due,currency,billing_country,payment_channel) VALUES (?,?,?,?,?,?,?,?,?)",
            (_utc(), email, ba, paypal_url, pm_url, amount_due, currency, country, channel),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def list_success(self, limit: int = 100, channel: str | None = None) -> list[dict[str, Any]]:
        """成功产出库存；channel 非空时按支付渠道(分支)隔离过滤。"""
        if channel:
            cur = await self.db.execute(
                "SELECT * FROM success_inventory WHERE payment_channel=? ORDER BY id DESC LIMIT ?",
                (channel, limit))
        else:
            cur = await self.db.execute(
                "SELECT * FROM success_inventory ORDER BY id DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    # ------------------------------------------------------------------
    # 样本
    # ------------------------------------------------------------------
    async def add_sample(self, success: bool, email: str, chain_id: str,
                         reason_code: str = "", reason_text: str = "",
                         paypal_url: str = "", amount_due: int = 0, currency: str = "",
                         country: str = "", stage_reached: str = "", payload: str = "") -> int:
        cur = await self.db.execute(
            "INSERT INTO samples (ts,success,email,chain_id,reason_code,reason_text,"
            "paypal_approve_url,amount_due,currency,country,stage_reached,payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_utc(), 1 if success else 0, email, chain_id, reason_code, reason_text,
             paypal_url, amount_due, currency, country, stage_reached, payload),
        )
        await self.db.commit()
        return cur.lastrowid or 0

    async def list_samples(self, success: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if success is None:
            cur = await self.db.execute("SELECT * FROM samples ORDER BY id DESC LIMIT ?", (limit,))
        else:
            cur = await self.db.execute(
                "SELECT * FROM samples WHERE success=? ORDER BY id DESC LIMIT ?",
                (1 if success else 0, limit))
        rows = await cur.fetchall()
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    async def count_samples(self) -> tuple[int, int]:
        cur = await self.db.execute("SELECT COUNT(*) FROM samples WHERE success=1")
        (s,) = await cur.fetchone()
        cur = await self.db.execute("SELECT COUNT(*) FROM samples WHERE success=0")
        (f,) = await cur.fetchone()
        return s, f

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None


# 全局单例
token_store = TokenStore()
