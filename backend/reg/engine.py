# -*- coding: utf-8 -*-
"""reg/engine.py — ChatGPT 注册批量调度引擎（事件环形缓冲 + 轮询）

与 mail-otp-server 的 reg_engine.py 同构，但适配本项目：
  - 无 Flask/SSE：事件缓冲 + since 增量轮询（前端 3s 轮询）
  - 无 gevent：uvicorn 纯 asyncio；注册为重度阻塞线程任务（curl_cffi 不走事件循环），
    由调用方 asyncio.to_thread 执行 stream_registration()
  - stdout 线程本地转发：仅注册线程 print 转发为 log 事件，不污染 uvicorn 日志
  - 邮箱渠道：内置 mailtm；自定义渠道经 register_email_channel 注册
    （setup_fn(proxies, cancel_check) -> (email, openai_password, fetch_code)）
  - 落库：reg_accounts 表 + 成功账号同步写本项目 tokens 表（source=register）

单例 STATE 持有运行态；POST /api/register/start 抢占槽位后
asyncio.to_thread(stream_registration, ...) 后台执行。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from reg import chatgpt_core as chatgpt
from reg import repo_accounts as ra

ALIVE_STATUSES = (
    "active", "pending", "expired", "suspended", "deactivated",
    "logout", "disabled", "revoked", "unknown",
)

# 邮箱渠道：内置 mailtm（零依赖在线 API）；其余由用户注册自定义渠道
# （见 register_email_channel，可把任意邮箱接入：IMAP/outlook 池/自建邮箱等）
# 注意：不能在模块顶层求值 list_email_channels（chatgpt_core 可能尚未完成初始化），
# 用函数惰性获取，供 api 层与 register_one 校验使用。


def email_channels() -> tuple:
    return tuple(chatgpt.list_email_channels())


def register_email_channel(name: str, setup_fn) -> None:
    """注册自定义邮箱渠道，注册后立即出现在面板渠道下拉与校验白名单。

    setup_fn(proxies, cancel_check) -> (email, openai_password, fetch_code)
    fetch_code(timeout_sec=None, seen_ids=None, not_before=None) -> code|None
    """
    chatgpt.register_email_channel(name, setup_fn)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ==================== 运行态（进程内单例） ====================

class _RegistrationState:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._seq = 0
        self._running = False
        self._cancel = threading.Event()
        self._task_id = None

    def try_start(self) -> str | None:
        """原子抢占任务槽位：成功返回 task_id；已有任务在跑返回 None。"""
        with self._lock:
            if self._running:
                return None
            task_id = uuid.uuid4().hex
            self._running = True
            self._task_id = task_id
            self._cancel.clear()
            return task_id

    def set_running(self, task_id: str | None):
        with self._lock:
            self._running = task_id is not None
            self._task_id = task_id
            self._cancel.clear()

    def request_cancel(self) -> bool:
        with self._lock:
            if not self._running:
                return False
            self._cancel.set()
            return True

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def cancel_event(self):
        return self._cancel

    def push(self, ev: dict):
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": _now_iso(), **ev}
            self._events.append(ev)
            if len(self._events) > 1000:
                del self._events[: len(self._events) - 1000]

    def replay_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["seq"] > seq]

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "task_id": self._task_id,
                "last_seq": self._seq,
            }


STATE = _RegistrationState()


# ==================== stdout → 事件转发（线程本地，防污染 uvicorn） ====================

class _SSEForwarder(io.TextIOBase):
    """按行缓冲 stdout，转发为 log 事件。"""

    def __init__(self, on_event):
        super().__init__()
        self._on_event = on_event
        self._buf = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._on_event({"type": "log", "stage": "register_one",
                                "message": line})
        return len(s)

    def flush(self):
        if self._buf.strip():
            line = self._buf.strip()
            self._buf = ""
            if line:
                self._on_event({"type": "log", "stage": "register_one",
                                "message": line})

    def isatty(self) -> bool:
        return False


class _MuxStdout(io.TextIOBase):
    """全局 stdout 多路转发：仅注册线程设置后被转发为事件，其余线程直通真实 stdout。"""

    def __init__(self, real):
        super().__init__()
        self._real = real
        self._local = threading.local()

    def set_forwarder(self, fwd):
        self._local.fwd = fwd

    def clear_forwarder(self):
        self._local.fwd = None

    def write(self, s) -> int:
        fwd = getattr(self._local, "fwd", None)
        if fwd is not None:
            try:
                fwd.write(s)
                return len(s) if isinstance(s, str) else 0
            except Exception:
                pass
        try:
            self._real.write(s)
        except Exception:
            pass
        return len(s) if isinstance(s, str) else 0

    def flush(self):
        fwd = getattr(self._local, "fwd", None)
        if fwd is not None:
            try:
                fwd.flush()
            except Exception:
                pass
        try:
            self._real.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return self._real.isatty()
        except Exception:
            return False

    def fileno(self) -> int:
        return self._real.fileno()

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")


def _install_mux_stdout():
    if not isinstance(sys.stdout, _MuxStdout):
        mux = _MuxStdout(sys.stdout)
        sys.stdout = mux  # type: ignore[assignment]
        return mux
    return sys.stdout


_STDOUT_MUX = _install_mux_stdout()


# ==================== 失败分桶 ====================

def _fail_code(detail: str, tail: list[str]) -> str:
    blob = "\n".join(tail).lower()
    if "邮箱已注册" in blob or "already_registered" in blob or "external_url" in blob:
        return "ALREADY_REGISTERED"
    if "验证码等待超时" in blob or "otp timeout" in blob or "提取失败" in blob:
        return "OTP_TIMEOUT"
    if "地区" in blob or "unsupported_country" in blob:
        return "UNSUPPORTED_COUNTRY"
    if "sentinel" in blob and ("none" in blob or "缺少" in blob):
        return "SENTINEL_FAILED"
    if "user_already_exists" in blob:
        return "ALREADY_REGISTERED"
    return "REGISTER_FAILED"


# ==================== 注册单号 ====================

def register_one(email_mode: str, proxy: str | None, cancel: threading.Event,
                 on_event) -> dict:
    """注册单个账号。返回 dict（email/alive_status/error_code 等），由调用方落库。"""
    tail: list[str] = []
    tail_max = 60

    def _on_line(ev: dict):
        tail.append(str(ev.get("message") or ""))
        if len(tail) > tail_max:
            del tail[: len(tail) - tail_max]
        on_event(ev)

    mapped = email_mode
    if mapped not in email_channels():
        on_event({"type": "log", "stage": "engine",
                  "message": f"未知邮箱渠道: {email_mode}"})
        return {
            "email": None, "alive_status": "unknown", "plan_type": "unknown",
            "error_code": "UNKNOWN_EMAIL_MODE", "error_detail": email_mode,
        }

    on_event({"type": "log", "stage": "register_one",
              "message": f"开始注册（渠道={email_mode}, 代理={proxy or '自动'}）"})
    fwd = _SSEForwarder(_on_line)
    result = None
    _STDOUT_MUX.set_forwarder(fwd)
    try:
        result = chatgpt.run(proxy, email=mapped,
                             cancel_check=cancel.is_set)
    except Exception as e:
        on_event({"type": "log", "stage": "register_one",
                  "message": f"注册异常: {repr(e)[:300]}"})
        code = _fail_code("", tail)
        return {
            "email": None, "alive_status": "unknown", "plan_type": "unknown",
            "error_code": code,
            "error_detail": f"{code}: {repr(e)[:200]}; tail={tail[-6:]}",
        }
    finally:
        _STDOUT_MUX.clear_forwarder()
        fwd.flush()

    if not result:
        code = _fail_code("", tail)
        detail = code
        if tail:
            detail = f"{code}; tail={tail[-8:]}"
        on_event({"type": "log", "stage": "register_one",
                  "message": f"注册失败（{code}）"})
        return {
            "email": None, "alive_status": "unknown", "plan_type": "unknown",
            "error_code": code, "error_detail": detail,
        }

    token_json, email, password, access_token, session_token = result
    email = str(email or "").strip()
    if not email:
        on_event({"type": "log", "stage": "register_one",
                  "message": "注册返回异常（无 email）"})
        return {
            "email": None, "alive_status": "unknown", "plan_type": "unknown",
            "error_code": "REGISTER_FAILED", "error_detail": "run 返回无 email",
        }

    # 兼容两种 token 形态：web 流 next-auth accessToken / Codex OAuth 流 token_json
    tj = token_json or {}
    if not access_token:
        access_token = str(tj.get("access_token") or tj.get("accessToken") or "").strip()
    if not session_token:
        session_token = str(tj.get("session_token") or tj.get("sessionToken") or "").strip()
    plan_type = str(tj.get("plan_type") or "").strip() or "unknown"

    on_event({"type": "log", "stage": "register_one",
              "message": f"注册成功: {email}（plan={plan_type}）"})
    return {
        "email": email,
        "password": password,
        "access_token": access_token,
        "session_token": session_token,
        "refresh_token": str(tj.get("refresh_token") or "").strip() or None,
        "alive_status": "active",
        "plan_type": plan_type,
        "source_email": email if email_mode in email_channels() else None,
        "error_code": None,
        "error_detail": None,
    }


# ==================== 批量调度 ====================

def _emit(ev: dict):
    STATE.push(ev)


def stream_registration(count: int, email_mode: str = "mailtm", concurrency: int = 1,
                        cooldown: float = 30.0, task_id: str | None = None,
                        proxy: str | None = None):
    """批量注册（阻塞，须在独立线程运行）。逐号同步，事件推入 STATE。

    proxy: None 时由 chatgpt.run 自动启用 711 住宅中继（需 PROXY_711_USER/PASS）。
    """
    conn = ra.connect()
    task_id = task_id or uuid.uuid4().hex
    try:
        total = max(int(count or 1), 1)
        concurrency = min(max(int(concurrency or 1), 1), 10)
        STATE.set_running(task_id)
        _emit({"type": "start", "task_id": task_id, "total": total,
               "email_mode": email_mode, "concurrency": concurrency})

        results, success, failed = [], 0, 0
        cancel = STATE.cancel_event
        # 未显式给代理：优先自动 711（chatgpt.run 内部无代理时自动 fallback 711）
        reg_proxy = proxy
        idx = 0
        while idx < total and not cancel.is_set():
            idx += 1
            r = register_one(email_mode, reg_proxy, cancel, _emit)
            ok = bool(r.get("email")) and not r.get("error_code")
            status = "active" if ok else "disabled"
            alive = r.get("alive_status") or ("active" if ok else "unknown")
            rid = None
            if r.get("email"):
                try:
                    rid = ra.upsert_account(conn, {
                        "email": r["email"], "password": r.get("password"),
                        "access_token": r.get("access_token"),
                        "session_token": r.get("session_token"),
                        "refresh_token": r.get("refresh_token"),
                        "alive_status": alive, "plan_type": r.get("plan_type") or "unknown",
                        "source_email": r.get("source_email"), "email_mode": email_mode,
                        "status": status, "error_code": r.get("error_code"),
                        "error_detail": (r.get("error_detail") or "")[:500],
                        "register_ts": _now_iso(),
                    })
                    if ok:
                        # 成功账号同步写入本项目 tokens 表（source=register），可直接提链
                        ra.push_to_tokens(conn, r)
                except Exception as e:
                    _emit({"type": "log", "stage": "db", "message": f"落库失败: {e}"})
            if ok:
                success += 1
            else:
                failed += 1
            results.append({"index": idx, "email": r.get("email"), "ok": ok,
                            "error": r.get("error_code"), "id": rid})
            _emit({"type": "progress", "index": idx, "total": total, "ok": ok,
                   "success": success, "failed": failed, "error": r.get("error_code")})
            if idx < total and not cancel.is_set() and cooldown > 0:
                _emit({"type": "log", "stage": "delay", "message": f"冷却 {cooldown:.0f}s"})
                time.sleep(cooldown)

        stopped = cancel.is_set()
        _emit({"type": "complete", "task_id": task_id, "total": total, "success": success,
               "failed": failed, "stopped": stopped, "results": results})
    except Exception as e:
        _emit({"type": "error", "message": str(e)[:300]})
    finally:
        with STATE._lock:
            STATE._running = False
            STATE._task_id = None
        ra.close(conn)


def cancel_registration() -> bool:
    return STATE.request_cancel()