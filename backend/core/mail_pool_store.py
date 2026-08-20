"""mail_pool_store.py — 自定义 IMAP 邮箱池存储 (离线开源项目)。

集中管理用户自有的 IMAP 邮箱账号, 供注册功能按需领用取验证码。
存储位置: backend/mail_pool.json (与 secrets.json 同级, 不进 git)。
写回模式参照 secrets_store.py (原子写 tmp + os.replace)。

地址策略:
  - direct : 用 username 原地址注册, 一个邮箱注册一个号
  - catchall : 生成 alias+随机串@catchall_domain 别名, 共用同一收件箱取码 (适合批量)

领用策略:
  - catchall 邮箱可无限复用 (不消耗计数, 取 used_count 最少的)
  - direct 邮箱用一次标记 used_count++ (不禁用, 靠用户手动删/禁)
  - 仅从 enabled 且 status != disabled 的池里取
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

_POOL_FILE = Path(__file__).resolve().parent.parent / "mail_pool.json"
_USER_PRESETS_FILE = Path(__file__).resolve().parent.parent / "mail_presets.json"

_DEFAULT_RULES: dict[str, Any] = {
    # 发件人白名单 (域名或关键字, 命中其一即通过; 空则不判发件人)
    "sender_whitelist": ["openai.com", "noreply", "auth0", "email.openai.com"],
    # 主题白名单 (关键字, 命中其一即通过; 空则不判主题)
    "subject_whitelist": ["verification", "verify", "confirm", "code", "otp"],
    # 验证码正则 (第一个捕获组即验证码)
    "code_regex": r"\b(\d{4,8})\b",
}

# 预设 IMAP 主机 (前端一键填充用) — 内置默认, 用户可在 mail_presets.json 增删改
_DEFAULT_PRESETS: list[dict[str, Any]] = [
    {"label": "Gmail", "imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True},
    {"label": "Outlook / Office365", "imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True},
    {"label": "Yahoo", "imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True},
    {"label": "iCloud", "imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True},
    {"label": "163", "imap_host": "imap.163.com", "imap_port": 993, "imap_ssl": True},
    {"label": "QQ", "imap_host": "imap.qq.com", "imap_port": 993, "imap_ssl": True},
    {"label": "通用 / 自定义", "imap_host": "", "imap_port": 993, "imap_ssl": True},
]


def _load_user_presets() -> list[dict[str, Any]]:
    """从 mail_presets.json 加载用户自定义预设 (不存在则空)。"""
    try:
        if not _USER_PRESETS_FILE.exists():
            return []
        with open(_USER_PRESETS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        out = []
        for p in raw:
            if isinstance(p, dict) and p.get("label"):
                out.append({
                    "label": str(p["label"]),
                    "imap_host": str(p.get("imap_host") or ""),
                    "imap_port": int(p.get("imap_port") or 993),
                    "imap_ssl": bool(p.get("imap_ssl", True)),
                })
        return out
    except Exception:
        return []


def _save_user_presets(presets: list[dict[str, Any]]) -> None:
    """原子写 mail_presets.json。"""
    try:
        tmp = str(_USER_PRESETS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _USER_PRESETS_FILE)
    except OSError:
        pass


def list_presets() -> list[dict[str, Any]]:
    """合并内置默认 + 用户自定义预设 (用户预设追加在后, 不覆盖内置)。"""
    return list(_DEFAULT_PRESETS) + _load_user_presets()


def add_preset(label: str, imap_host: str, imap_port: int = 993, imap_ssl: bool = True) -> bool:
    """添加用户自定义预设 (label 唯一, 与内置/已有重复则失败)。"""
    label = str(label or "").strip()
    if not label:
        return False
    existing = {p["label"] for p in list_presets()}
    if label in existing:
        return False
    user = _load_user_presets()
    user.append({"label": label, "imap_host": imap_host or "",
                 "imap_port": int(imap_port or 993), "imap_ssl": bool(imap_ssl)})
    _save_user_presets(user)
    return True


def delete_preset(label: str) -> bool:
    """删除用户自定义预设 (内置默认不可删)。"""
    label = str(label or "").strip()
    builtins = {p["label"] for p in _DEFAULT_PRESETS}
    if label in builtins:
        return False
    user = _load_user_presets()
    before = len(user)
    user = [p for p in user if p["label"] != label]
    if len(user) == before:
        return False
    _save_user_presets(user)
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class MailPoolStore:
    """mail_pool.json 单例: load / CRUD / test_connection / consume。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: dict[str, Any] = json.loads(json.dumps(_DEFAULT_RULES))
        self._mailboxes: list[dict[str, Any]] = []
        self._rr = 0  # catch-all 轮询游标 (同档多邮箱轮换分配)
        self.load()

    # ---- 持久化 ----
    def load(self) -> None:
        """从 mail_pool.json 读取 (不存在则用空默认)。"""
        try:
            if not _POOL_FILE.exists():
                return
            with open(_POOL_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return
            rules = raw.get("rules")
            if isinstance(rules, dict):
                for k in _DEFAULT_RULES:
                    v = rules.get(k)
                    if isinstance(v, list) and all(isinstance(x, str) for x in v):
                        self._rules[k] = v
                    elif isinstance(v, str):
                        self._rules[k] = v
            mbs = raw.get("mailboxes")
            if isinstance(mbs, list):
                self._mailboxes = [self._normalize(m) for m in mbs if isinstance(m, dict)]
        except Exception:
            pass

    def _save(self) -> None:
        """原子写落盘 (tmp + os.replace)。"""
        try:
            tmp = str(_POOL_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"rules": self._rules, "mailboxes": self._mailboxes},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, _POOL_FILE)
        except OSError:
            pass

    @staticmethod
    def _normalize(m: dict[str, Any]) -> dict[str, Any]:
        """补全缺失字段, 确保结构一致。"""
        return {
            "id": str(m.get("id") or _new_id()),
            "label": str(m.get("label") or ""),
            "imap_host": str(m.get("imap_host") or ""),
            "imap_port": int(m.get("imap_port") or 993),
            "imap_ssl": bool(m.get("imap_ssl", True)),
            "username": str(m.get("username") or ""),
            "password": str(m.get("password") or ""),
            "alias_mode": "catchall" if str(m.get("alias_mode") or "direct") == "catchall" else "direct",
            "catchall_domain": str(m.get("catchall_domain") or ""),
            "sender_whitelist": list(m.get("sender_whitelist") or []),
            "subject_whitelist": list(m.get("subject_whitelist") or []),
            "code_regex": str(m.get("code_regex") or ""),
            "enabled": bool(m.get("enabled", True)),
            "status": str(m.get("status") or "unknown"),
            "last_check": str(m.get("last_check") or ""),
            "last_error": str(m.get("last_error") or ""),
            "used_count": int(m.get("used_count") or 0),
            "created_at": str(m.get("created_at") or _now_iso()),
        }

    # ---- 读取 ----
    def get_all(self) -> dict[str, Any]:
        """返回全部邮箱 (含密码明文) + 全局规则 (前端编辑需看到原值)。"""
        with self._lock:
            return {
                "rules": json.loads(json.dumps(self._rules)),
                "mailboxes": [json.loads(json.dumps(m)) for m in self._mailboxes],
                "presets": list_presets(),
            }

    def get_rules(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._rules))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._mailboxes)
            enabled = sum(1 for m in self._mailboxes if m.get("enabled"))
            ok_count = sum(1 for m in self._mailboxes if m.get("status") == "ok")
            fail = sum(1 for m in self._mailboxes if m.get("status") == "fail")
            used = sum(int(m.get("used_count") or 0) for m in self._mailboxes)
            return {"total": total, "enabled": enabled, "disabled": total - enabled,
                    "ok_count": ok_count, "fail": fail, "used_total": used}

    def has_enabled(self) -> bool:
        with self._lock:
            return any(m.get("enabled") for m in self._mailboxes)

    # ---- 写入 ----
    def add(self, mbox: dict[str, Any]) -> dict[str, Any]:
        m = self._normalize({**mbox, "id": _new_id(), "created_at": _now_iso()})
        with self._lock:
            self._mailboxes.append(m)
            self._save()
            return json.loads(json.dumps(m))

    def update(self, mbox_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for m in self._mailboxes:
                if m["id"] == mbox_id:
                    upd = {**m, **{k: v for k, v in fields.items() if k != "id"}}
                    m.update(self._normalize(upd))
                    self._save()
                    return {"ok": True, "mailbox": json.loads(json.dumps(m))}
            return {"ok": False, "error": "邮箱不存在"}

    def delete(self, mbox_id: str) -> bool:
        with self._lock:
            before = len(self._mailboxes)
            self._mailboxes = [m for m in self._mailboxes if m["id"] != mbox_id]
            if len(self._mailboxes) != before:
                self._save()
                return True
            return False

    def bulk_delete(self, mbox_ids: list[str]) -> int:
        """批量删除邮箱，返回实际删除条数。"""
        if not mbox_ids:
            return 0
        with self._lock:
            ids_set = set(mbox_ids)
            before = len(self._mailboxes)
            self._mailboxes = [m for m in self._mailboxes if m["id"] not in ids_set]
            deleted = before - len(self._mailboxes)
            if deleted:
                self._save()
            return deleted

    def bulk_set_enabled(self, mbox_ids: list[str], enabled: bool) -> int:
        """批量启停邮箱，返回实际修改条数。"""
        if not mbox_ids:
            return 0
        with self._lock:
            ids_set = set(mbox_ids)
            changed = 0
            for m in self._mailboxes:
                if m["id"] in ids_set:
                    m["enabled"] = bool(enabled)
                    if not enabled:
                        m["status"] = "disabled"
                    elif m["status"] == "disabled":
                        m["status"] = "unknown"
                    changed += 1
            if changed:
                self._save()
            return changed

    def set_enabled(self, mbox_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            for m in self._mailboxes:
                if m["id"] == mbox_id:
                    m["enabled"] = bool(enabled)
                    # 禁用时同步标 disabled (启用后回 unknown 待测)
                    if not enabled:
                        m["status"] = "disabled"
                    elif m["status"] == "disabled":
                        m["status"] = "unknown"
                    self._save()
                    return {"ok": True, "mailbox": json.loads(json.dumps(m))}
            return {"ok": False, "error": "邮箱不存在"}

    def bulk_import(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """批量添加, 逐条容错。返回 {ok, added, skipped, errors}。"""
        added = 0
        skipped = 0
        errors: list[str] = []
        to_add: list[dict[str, Any]] = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                skipped += 1
                errors.append(f"行{i+1}: 非对象")
                continue
            m = self._normalize({**it, "id": _new_id(), "created_at": _now_iso()})
            if not m["username"] or not m["imap_host"]:
                skipped += 1
                errors.append(f"行{i+1}: 缺 username 或 imap_host")
                continue
            to_add.append(m)
            added += 1
        with self._lock:
            self._mailboxes.extend(to_add)
            self._save()
        return {"ok": True, "added": added, "skipped": skipped, "errors": errors}

    def update_rules(self, rules: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for k in _DEFAULT_RULES:
                v = rules.get(k)
                if isinstance(v, list) and all(isinstance(x, str) for x in v):
                    self._rules[k] = v
                elif isinstance(v, str):
                    self._rules[k] = v
            self._save()
            return json.loads(json.dumps(self._rules))

    # ---- 连接测试 ----
    def test_connection(self, mbox_id: str) -> dict[str, Any]:
        """登录 IMAP + SELECT INBOX, 更新 status/last_check/last_error。"""
        import imaplib
        with self._lock:
            mbox = next((m for m in self._mailboxes if m["id"] == mbox_id), None)
        if not mbox:
            return {"ok": False, "error": "邮箱不存在"}
        host = mbox["imap_host"]
        port = int(mbox["imap_port"] or 993)
        user = mbox["username"]
        pwd = mbox["password"]
        ssl = bool(mbox.get("imap_ssl", True))
        err = ""
        try:
            if ssl:
                conn = imaplib.IMAP4_SSL(host, port, timeout=20)
            else:
                conn = imaplib.IMAP4(host, port, timeout=20)
            # 163/126/yeah.net 必须先发 IMAP ID, 否则登录被拒
            if any(h in host for h in ("163.com", "126.com", "yeah.net")):
                imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")
                conn._simple_command("ID", '("name" "IMAPClient" "version" "1.0")')
            conn.login(user, pwd)
            conn.select("INBOX", readonly=True)
            conn.logout()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        with self._lock:
            for m in self._mailboxes:
                if m["id"] == mbox_id:
                    m["last_check"] = _now_iso()
                    m["last_error"] = err
                    m["status"] = "ok" if not err else "fail"
                    self._save()
                    return {"ok": not err, "status": m["status"],
                            "last_error": err, "last_check": m["last_check"]}
        return {"ok": False, "error": "邮箱已删除"}

    def test_all(self, only_ids: list[str] | None = None) -> dict[str, Any]:
        """逐个测试邮箱 (同步, 调用方应放线程池)。返回每条结果。
        only_ids 非空时仅测试指定 id (不再要求 enabled)。"""
        with self._lock:
            if only_ids is not None:
                id_set = set(only_ids)
                ids = [m["id"] for m in self._mailboxes if m["id"] in id_set]
            else:
                ids = [m["id"] for m in self._mailboxes if m.get("enabled")]
        results = []
        for mid in ids:
            results.append({"id": mid, **self.test_connection(mid)})
        return {"ok": True, "results": results}

    # ---- 领用 ----
    def consume(self) -> dict[str, Any] | None:
        """领用一个可用邮箱。耗尽返回 None。

        策略:
          - 优先 status=ok, 其次 unknown, 跳过 disabled;
          - direct: 取 used_count 最少的 (用一次计数+1, 趋向均衡);
          - catchall: 不增计数, 同档多邮箱按轮询游标轮换分配 (避免单域集中注册被风控)。
        """
        with self._lock:
            candidates = [m for m in self._mailboxes
                           if m.get("enabled") and m.get("status") != "disabled"]
            if not candidates:
                return None
            # 优先级: ok > unknown > fail (fail 也给一次机会)
            rank = {"ok": 0, "unknown": 1, "fail": 2}
            candidates.sort(key=lambda m: (rank.get(m.get("status"), 3),
                                           int(m.get("used_count") or 0)))
            chosen = candidates[0]
            # catchall 同档轮询: 若首选也是 catchall 且同档有多个 catchall,
            # 用轮询游标在它们之间轮换 (避免 used_count 都为 0 时永远取第一个)
            if chosen.get("alias_mode") == "catchall":
                same_rank = [m for m in candidates
                             if rank.get(m.get("status"), 3) == rank.get(chosen.get("status"), 3)
                             and m.get("alias_mode") == "catchall"]
                if len(same_rank) > 1:
                    chosen = same_rank[self._rr % len(same_rank)]
                    self._rr = (self._rr + 1) % len(same_rank)
            # direct 邮箱用一次计数+1 (catchall 不消耗, 可无限复用)
            if chosen.get("alias_mode") == "direct":
                for m in self._mailboxes:
                    if m["id"] == chosen["id"]:
                        m["used_count"] = int(m.get("used_count") or 0) + 1
                        break
                self._save()
            return json.loads(json.dumps(chosen))

    def consume_by_id(self, mbox_id: str) -> dict[str, Any] | None:
        """按 id 领用指定邮箱 (每域独立渠道用)。

        不可用 (不存在/未启用/disabled) 返回 None;
        direct 邮箱用一次计数+1, catchall 不消耗可无限复用。
        """
        with self._lock:
            m = next((x for x in self._mailboxes if x["id"] == mbox_id), None)
            if not m or not m.get("enabled") or m.get("status") == "disabled":
                return None
            if m.get("alias_mode") == "direct":
                m["used_count"] = int(m.get("used_count") or 0) + 1
                self._save()
            return json.loads(json.dumps(m))

    # ---- 规则合并 ----
    def effective_rules(self, mbox: dict[str, Any]) -> dict[str, Any]:
        """合并邮箱级覆盖 + 全局默认 (空则回落全局)。"""
        senders = mbox.get("sender_whitelist") or []
        subjects = mbox.get("subject_whitelist") or []
        code_re = mbox.get("code_regex") or ""
        return {
            "sender_whitelist": senders if senders else self._rules["sender_whitelist"],
            "subject_whitelist": subjects if subjects else self._rules["subject_whitelist"],
            "code_regex": code_re if code_re else self._rules["code_regex"],
        }


mail_pool_store = MailPoolStore()
