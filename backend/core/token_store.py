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

# JWS 3 段 / JWE 5 段 (alg=dir+A256GCM, 中间含空段): 每段允许空 (JWE 空 encrypted_key)
_RE_JWT = re.compile(r"^eyJ[A-Za-z0-9_-]*(\.[A-Za-z0-9_-]*){2,4}$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")

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
    """从 JWT payload 解析 sub/email/plan（不验签，仅供展示）。

    新版 OpenAI JWT 在 https://api.openai.com/profile.email 携带邮箱；
    旧版无 email claim。绝不把 user_id (user-xxx) 当作 email 回退，避免污染。

    JWE 加密 token (alg=dir, 5 段, session token): payload 不可解,
    返回 token_format=jwe 标记供导入标注, 字段留空。
    """
    try:
        parts = access_token.split(".")
        if len(parts) < 3 or len(parts) > 5:
            return {}
        import base64

        def _b64(s: str) -> str:
            return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

        try:
            hdr = json.loads(_b64(parts[0]))
        except Exception:
            hdr = {}
        if not isinstance(hdr, dict):
            hdr = {}
        if str(hdr.get("alg") or "").lower() == "dir" or len(parts) >= 4:
            # JWE (encrypted_key 空段 / dir 直接加密): payload 无法解密
            return {"token_format": "jwe", "tags": "jwe", "sub": "", "email": "", "account_id": "", "plan_type": ""}
        payload = json.loads(_b64(parts[1]))
        auth = payload.get("https://api.openai.com/auth") or {}
        prof = payload.get("https://api.openai.com/profile") or {}
        # 邮箱优先: profile.email (新版) > 顶层 email claim
        email = str(prof.get("email") or "") if isinstance(prof, dict) else ""
        if not email:
            email = str(payload.get("email") or "")
        return {
            "sub": str(payload.get("sub") or ""),
            "email": email,
            "account_id": str(auth.get("user_id") or ""),
            "plan_type": str(auth.get("chatgpt_plan_type") or auth.get("plan") or payload.get("plan") or "free"),
        }
    except Exception:
        return {}


def _looks_like_phone(s: str) -> bool:
    return bool(_PHONE_RE.match(str(s or "").replace("-", "").replace(" ", "")))


def _detect_register_method(sub: str, email: str = "", auth_provider: str = "") -> str:
    """从 sub 前缀 / authProvider / 手机号形态推断注册方式。

    sub 示例: user-xxx(邮箱注册) / google-oauth2|... / apple|... /
    auth0|... / phone|... / facebook|...
    """
    sub = str(sub or "")
    email = str(email or "")
    ap = str(auth_provider or "").strip().lower()
    if "|" in sub:
        prefix = sub.split("|", 1)[0].strip().lower()
        if prefix in ("google", "google-oauth2"):
            return "google"
        if prefix in ("apple", "sign_in_with_apple"):
            return "apple"
        if prefix in ("facebook", "facebook-oauth2"):
            return "facebook"
        if prefix in ("phone", "sms", "whatsapp", "twilio"):
            return "phone"
        if prefix == "auth0":
            return "phone" if _looks_like_phone(sub.split("|", 1)[1]) else "email"
        if prefix == "user":
            return "email"
    if ap in ("google", "google-oauth2"):
        return "google"
    if ap in ("apple", "sign_in_with_apple"):
        return "apple"
    if ap in ("facebook", "facebook-oauth2"):
        return "facebook"
    if ap in ("phone", "sms", "whatsapp"):
        return "phone"
    if _looks_like_phone(email):
        return "phone"
    return "email"


def _extract_tokens(raw: str) -> list[tuple[str, str, dict]]:
    """从原始文本解析出 [(access_token, session_token, meta), ...]。

    适配 mail-otp-server 全部 GPT 导出格式 (export_formats.py) 与手贴文本:
      - raw (JSONL):  {email, accessToken, session_token, refresh_token, plan_type, ...}
      - session:      [{"user":{email}, "accessToken", "sessionToken", "account":{id,planType}}]
      - cpa:          [{"type":"codex", "access_token", "session_token", "email", ...}]
      - sub2api:      {"exported_at", "proxies", "accounts":[{platform, credentials:{...}}]}
      - codex2api:    [{"access_token", "session_token", "email", ...}]
      - codexmanager: [{"tokens":{access_token}, "meta":{label, chatgpt_account_id}}]
      - cockpit:      [{"access_token", "refresh_token", "account_id", "email", "expired"}]
      - codex (JSONL):{"auth_mode":"chatgpt", "tokens":{access_token, refresh_token, account_id}}
      - 传统 JSON:    {"accessToken", "sessionToken", "user":{email}, "authProvider"}
      - 裸 JWT / JWE: 每行一个 (兼容 Bearer 前缀)
    """
    out: list[tuple[str, str, dict]] = []
    text = raw.strip()
    if not text:
        return out

    def _push(d: dict) -> None:
        """统一字段提取: 平铺 子对象(user/account/tokens/credentials/meta) + 顶层, 顶层优先。"""
        tokens = d.get("tokens") if isinstance(d.get("tokens"), dict) else {}
        user = d.get("user") if isinstance(d.get("user"), dict) else {}
        account = d.get("account") if isinstance(d.get("account"), dict) else {}
        creds = d.get("credentials") if isinstance(d.get("credentials"), dict) else {}
        meta_cfg = d.get("meta") if isinstance(d.get("meta"), dict) else {}
        src: dict[str, Any] = {}
        for layer in (user, account, tokens, creds, meta_cfg, d):
            if isinstance(layer, dict):
                src.update(layer)
        at = str(src.get("accessToken") or src.get("access_token") or "").strip()
        st = str(src.get("sessionToken") or src.get("session_token") or "").strip()
        if not at or not _RE_JWT.fullmatch(at):
            return
        meta = _decode_jwt_meta(at)
        email = str(src.get("email") or src.get("label") or "").strip()
        if email:
            meta["email"] = email
        ap = str(src.get("authProvider") or src.get("auth_provider") or "").strip()
        if ap:
            meta["auth_provider"] = ap
        if not meta.get("plan_type") or meta.get("plan_type") in ("", "free"):
            pt = str(src.get("planType") or src.get("plan_type") or "").strip()
            if pt:
                meta["plan_type"] = pt
        aid = str(src.get("chatgpt_account_id") or src.get("account_id") or "").strip()
        if aid:
            meta["account_id"] = aid
        phone = str(src.get("phone") or src.get("phone_number") or "").strip()
        if not meta.get("email") and phone:
            meta["email"] = phone
        out.append((at, st, meta))

    def _expand(d: dict) -> list[dict]:
        """包装结构展开: sub2api.accounts 数组 / codex·codexmanager 单账号 tokens 包装。"""
        accs = d.get("accounts")
        if isinstance(accs, list):
            return [x for x in accs if isinstance(x, dict)]
        if isinstance(d.get("tokens"), dict) and (
                "access_token" in d["tokens"] or "accessToken" in d["tokens"]):
            return [d]
        return [d]

    # 1) 整段 JSON (单对象 / 数组 / 包装结构)
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            for it in _expand(d):
                _push(it)
        elif isinstance(d, list):
            for x in d:
                if isinstance(x, dict):
                    _push(x)
        if out:
            return out
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        pass

    # 2) JSONL: 逐行 JSON 对象/数组 (raw / codex 每行一个账号)
    for line in text.splitlines():
        t = line.strip().strip(",").strip()
        if not t:
            continue
        if t.startswith(("{", "[")):
            try:
                o = json.loads(t)
                if isinstance(o, dict):
                    for it in _expand(o):
                        _push(it)
                elif isinstance(o, list):
                    for x in o:
                        if isinstance(x, dict):
                            _push(x)
                continue
            except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                pass
        # 裸 JWT / JWE (每行一个, 兼容 Bearer 前缀与首尾引号/逗号)
        at = t.strip('"')
        if at.lower().startswith("bearer "):
            at = at[7:].strip()
        if _RE_JWT.fullmatch(at):
            out.append((at, "", _decode_jwt_meta(at)))

    # 3) 正则兜底: 整段文本中抽取 "accessToken": "..." (含 user.email / authProvider)
    if not out and '"accessToken"' in text:
        for m in re.finditer(r'"accessToken"\s*:\s*"([^"]+)"', text):
            at = m.group(1).strip()
            if _RE_JWT.fullmatch(at):
                st = ""
                m2 = re.search(r'"sessionToken"\s*:\s*"([^"]+)"', text[m.end():m.end() + 500])
                if m2:
                    st = m2.group(1).strip()
                meta = _decode_jwt_meta(at)
                m3 = re.search(r'"user"\s*:\s*\{[^{}]{0,2000}?"email"\s*:\s*"([^"]+)"', text[m.end():m.end() + 3000])
                if m3:
                    meta["email"] = m3.group(1).strip()
                m4 = re.search(r'"authProvider"\s*:\s*"([^"]+)"', text[m.end():m.end() + 3000])
                if m4:
                    meta["auth_provider"] = m4.group(1).strip()
                out.append((at, st, meta))
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
        # 旧库迁移: 会话类型列 (cs_live / oaics / 未知, 导入时自动探测)
        if "session_type" not in cols:
            await db.execute("ALTER TABLE tokens ADD COLUMN session_type TEXT DEFAULT ''")
        # 旧库迁移: 完整探测结果 (JSON: promo/token/paypal/amount) 与用户标签 (逗号分隔)
        if "probe" not in cols:
            await db.execute("ALTER TABLE tokens ADD COLUMN probe TEXT DEFAULT ''")
        if "tags" not in cols:
            await db.execute("ALTER TABLE tokens ADD COLUMN tags TEXT DEFAULT ''")
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
        # 旧库迁移: 补出口国列 (授权段跟随 checkout 出口 IP 国家, 非账单国)
        try:
            _cur = await db.execute("PRAGMA table_info(success_inventory)")
            _cols = {r[1] for r in await _cur.fetchall()}
            if "exit_country" not in _cols:
                await db.execute("ALTER TABLE success_inventory ADD COLUMN exit_country TEXT DEFAULT ''")
        except Exception:
            pass
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
                payload TEXT,
                actual_country TEXT,
                requested_country TEXT,
                exit_ip TEXT,
                geo_confidence REAL
            )
        """)
        # 旧库迁移: 补真实出口地理列
        try:
            _cur = await db.execute("PRAGMA table_info(samples)")
            _cols = [r[1] for r in await _cur.fetchall()]
            for _col, _ddl in (
                ("actual_country", "TEXT"),
                ("requested_country", "TEXT"),
                ("exit_ip", "TEXT"),
                ("geo_confidence", "REAL"),
            ):
                if _col not in _cols:
                    await db.execute(f"ALTER TABLE samples ADD COLUMN {_col} {_ddl}")
        except Exception:
            pass
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
                    "INSERT INTO tokens (id,raw,access_token,session_token,account_id,sub,email,"
                    "plan_type,register_method,expires_at,status,created_at,last_run_at,source,tags) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex[:12], at, at, "", meta.get("account_id", ""),
                     meta.get("sub", email), email, plan, method, "", "idle", now, "", "stripe", ""),
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

    async def count_idle(self, source: str | None = None) -> int:
        """status='idle' 的账号计数（供一键流程守护判断是否需要注册更多）。"""
        if source:
            cur = await self.db.execute(
                "SELECT COUNT(*) FROM tokens WHERE status='idle' AND source=?", (source,))
        else:
            cur = await self.db.execute("SELECT COUNT(*) FROM tokens WHERE status='idle'")
        (cnt,) = await cur.fetchone()
        return int(cnt or 0)

    async def list_idle_tokens(self, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """status='idle' 的账号列表（供一键流程守护触发提链，按创建时间倒序）。"""
        if source:
            cur = await self.db.execute(
                "SELECT * FROM tokens WHERE status='idle' AND source=? ORDER BY created_at DESC LIMIT ?",
                (source, limit))
        else:
            cur = await self.db.execute(
                "SELECT * FROM tokens WHERE status='idle' ORDER BY created_at DESC LIMIT ?", (limit,))
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

    async def bulk_delete_tokens(self, token_ids: list[str]) -> int:
        """批量删除 token，返回实际删除条数。"""
        if not token_ids:
            return 0
        placeholders = ",".join("?" * len(token_ids))
        cur = await self.db.execute(
            f"DELETE FROM tokens WHERE id IN ({placeholders})", token_ids)
        await self.db.commit()
        return cur.rowcount

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
            method = _detect_register_method(sub, email, meta.get("auth_provider") or "")
            await self.db.execute(
                "INSERT INTO tokens (id,raw,access_token,session_token,account_id,sub,email,"
                "plan_type,register_method,expires_at,status,created_at,last_run_at,source,tags) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, at, at, st, meta.get("account_id", ""), sub, email, plan,
                 method, "", "idle", now, "", source, meta.get("tags") or ""),
            )
            new_tokens.append({
                "id": tid, "raw": at, "access_token": at, "session_token": st,
                "account_id": meta.get("account_id", ""), "sub": sub, "email": email,
                "plan_type": plan, "register_method": method, "expires_at": "",
                "status": "idle", "created_at": now, "last_run_at": "", "source": source,
            })
            imported += 1
        await self.db.commit()
        return imported, failed, new_tokens

    async def import_from_pool(self, records: list[dict], source: str = "stripe") -> tuple[int, int, list[dict]]:
        """从邮箱注册池导入未使用邮箱/token。

        records: [{email, accessToken, ...}, ...]（源自注册池 /api/emails）
        去重: access_token 重复 或 同 email 已存在（即使换 token）均跳过。
        返回 (imported, skipped, new_tokens)。
        """
        imported = 0
        skipped = 0
        now = _utc()
        new_tokens: list[dict] = []
        cur = await self.db.execute("SELECT access_token, email FROM tokens")
        rows = await cur.fetchall()
        seen_at = {r[0] for r in rows}
        seen_emails = {r[1] for r in rows if r[1]}
        for rec in records:
            if not isinstance(rec, dict):
                skipped += 1
                continue
            at = str(rec.get("accessToken") or rec.get("access_token") or "").strip()
            email = str(rec.get("email") or "").strip()
            if not email:
                # 手机号注册: 无邮箱时以手机号作为标识
                phone = str(rec.get("phone") or rec.get("phone_number") or "").strip()
                if phone:
                    email = phone
            if not _RE_JWT.fullmatch(at):
                skipped += 1
                continue
            if at in seen_at:
                skipped += 1
                continue
            if email and email in seen_emails:
                skipped += 1
                continue
            meta = _decode_jwt_meta(at)
            tid = uuid.uuid4().hex[:12]
            sub = meta.get("sub") or ""
            plan = meta.get("plan_type") or "free"
            method = _detect_register_method(sub, email, meta.get("auth_provider") or "")
            await self.db.execute(
                "INSERT INTO tokens (id,raw,access_token,session_token,account_id,sub,email,"
                "plan_type,register_method,expires_at,status,created_at,last_run_at,source,tags) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, at, at, "", meta.get("account_id", ""), sub, email, plan,
                 method, "", "idle", now, "", source, ""),
            )
            seen_at.add(at)
            if email:
                seen_emails.add(email)
            new_tokens.append({
                "id": tid, "raw": at, "access_token": at, "session_token": "",
                "account_id": meta.get("account_id", ""), "sub": sub, "email": email,
                "plan_type": plan, "register_method": method, "expires_at": "",
                "status": "idle", "created_at": now, "last_run_at": "", "source": source,
            })
            imported += 1
        await self.db.commit()
        return imported, skipped, new_tokens

    async def set_status(self, token_id: str, status: str) -> None:
        last_run = _utc() if status in ("running", "success", "failed") else None
        if last_run:
            await self.db.execute(
                "UPDATE tokens SET status=?, last_run_at=? WHERE id=?", (status, last_run, token_id))
        else:
            await self.db.execute("UPDATE tokens SET status=? WHERE id=?", (status, token_id))
        await self.db.commit()

    async def set_session_type(self, token_id: str, session_type: str) -> None:
        """记录会话类型探测结果: cs_live / oaics / error:<原因>。"""
        await self.db.execute(
            "UPDATE tokens SET session_type=? WHERE id=?", (session_type, token_id))
        await self.db.commit()

    async def set_probe(self, token_id: str, probe_json: str) -> None:
        """记录完整探测结果 JSON (promo/token/paypal/amount)。"""
        await self.db.execute(
            "UPDATE tokens SET probe=? WHERE id=?", (probe_json, token_id))
        await self.db.commit()

    async def set_tags(self, token_id: str, tags: list[str]) -> None:
        """设置用户标签 (去重去空, 逗号分隔存储)。"""
        seen: list[str] = []
        for t in tags or []:
            t = str(t).strip()
            if t and t not in seen:
                seen.append(t)
        await self.db.execute(
            "UPDATE tokens SET tags=? WHERE id=?", (",".join(seen), token_id))
        await self.db.commit()

    # ------------------------------------------------------------------
    # 同步写 (供线程内探测落库, 独立短连接避免跨线程 aiosqlite)
    # ------------------------------------------------------------------
    def set_session_type_sync(self, token_id: str, session_type: str) -> None:
        import sqlite3
        con = sqlite3.connect(self.db_path, timeout=10)
        try:
            con.execute(
                "UPDATE tokens SET session_type=? WHERE id=?", (session_type, token_id))
            con.commit()
        finally:
            con.close()

    def set_probe_sync(self, token_id: str, probe_json: str) -> None:
        import sqlite3
        con = sqlite3.connect(self.db_path, timeout=10)
        try:
            con.execute(
                "UPDATE tokens SET probe=? WHERE id=?", (probe_json, token_id))
            con.commit()
        finally:
            con.close()

    async def reset_running(self) -> None:
        """启动时把残留 running 状态重置为 idle。"""
        await self.db.execute("UPDATE tokens SET status='idle' WHERE status='running'")
        await self.db.commit()

    async def repair_metadata(self) -> int:
        """修复存量 Token 元数据: 重算注册方式, 清除被 user_id 污染的 email,
        并从 JWT 的 profile.email claim 回填缺失邮箱。"""
        rows = await self.list_tokens()
        fixed = 0
        for t in rows:
            tid = t.get("id")
            email = str(t.get("email") or "")
            sub = str(t.get("sub") or "")
            at = str(t.get("access_token") or t.get("raw") or "")
            if email and not _RE_EMAIL.fullmatch(email):
                email = ""
                fixed += 1
            if not email:
                # 回填: 新版 JWT 的 https://api.openai.com/profile.email
                meta = _decode_jwt_meta(at) if at else {}
                backfill = str(meta.get("email") or "")
                if backfill and _RE_EMAIL.fullmatch(backfill):
                    email = backfill
                    fixed += 1
            method = _detect_register_method(sub, email, "")
            if method != t.get("register_method"):
                fixed += 1
            await self.db.execute(
                "UPDATE tokens SET email=?, register_method=? WHERE id=?",
                (email, method, tid))
        await self.db.commit()
        return fixed

    # ------------------------------------------------------------------
    # 成功库存
    # ------------------------------------------------------------------
    async def add_success(self, email: str, ba: str, paypal_url: str, pm_url: str,
                          amount_due: int, currency: str, country: str, channel: str = "paypal",
                          exit_country: str = "") -> int:
        cur = await self.db.execute(
            "INSERT INTO success_inventory (ts,email,ba,paypal_approve_url,pm_authorize_url,"
            "amount_due,currency,billing_country,payment_channel,exit_country) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_utc(), email, ba, paypal_url, pm_url, amount_due, currency, country, channel,
             str(exit_country or "").upper()),
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

    async def clear_success(self, channel: str | None = None) -> int:
        """清空成功库存；channel 非空时仅清该支付渠道。返回删除条数。"""
        if channel:
            cur = await self.db.execute(
                "DELETE FROM success_inventory WHERE payment_channel=?", (channel,))
        else:
            cur = await self.db.execute("DELETE FROM success_inventory")
        await self.db.commit()
        return cur.rowcount

    async def bulk_delete_inventory(self, ids: list[int]) -> int:
        """批量删除成功库存记录，返回实际删除条数。"""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = await self.db.execute(
            f"DELETE FROM success_inventory WHERE id IN ({placeholders})", ids)
        await self.db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # 样本
    # ------------------------------------------------------------------
    async def add_sample(self, success: bool, email: str, chain_id: str,
                         reason_code: str = "", reason_text: str = "",
                         paypal_url: str = "", amount_due: int = 0, currency: str = "",
                         country: str = "", stage_reached: str = "", payload: str = "",
                         actual_country: str = "", requested_country: str = "",
                         exit_ip: str = "", geo_confidence: float = 0.0) -> int:
        cur = await self.db.execute(
            "INSERT INTO samples (ts,success,email,chain_id,reason_code,reason_text,"
            "paypal_approve_url,amount_due,currency,country,stage_reached,payload,"
            "actual_country,requested_country,exit_ip,geo_confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_utc(), 1 if success else 0, email, chain_id, reason_code, reason_text,
             paypal_url, amount_due, currency, country, stage_reached, payload,
             actual_country, requested_country, exit_ip, geo_confidence),
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

    async def bulk_delete_samples(self, ids: list[int]) -> int:
        """批量删除样本记录，返回实际删除条数。"""
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = await self.db.execute(
            f"DELETE FROM samples WHERE id IN ({placeholders})", ids)
        await self.db.commit()
        return cur.rowcount

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None


# 全局单例
token_store = TokenStore()
