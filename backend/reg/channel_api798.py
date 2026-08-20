# -*- coding: utf-8 -*-
"""reg/channel_api798.py — api798.com 邮箱提取工具自定义注册渠道

卡密格式（每行）：email----https://api798.com/latest?email=...&auth_code=XXX
取码走 JSON 端点 GET /get_code?email=&auth_code= （返回 {success, code, subject, body, date}）。

注册渠道契约（reg/engine.register_email_channel）：
    setup_fn(proxies, cancel_check) -> (email, openai_password, fetch_code)
    fetch_code(timeout_sec=None, seen_ids=None, not_before=None) -> code|None

用法（app.py 启动时）：
    from reg import engine as reg_engine
    from reg.channel_api798 import load_mailboxes, build_channel
    reg_engine.register_email_channel("api798", build_channel(load_mailboxes("卡密.txt")))
"""
from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse

from . import chatgpt_core as cc

_API_BASE = os.environ.get("REG_API798_ENDPOINT", "https://api798.com/get_code")

# 已领取邮箱队列（线程安全，消费一个少一个）
_QUEUE: list[dict] = []
_QLOCK = threading.Lock()


def load_mailboxes(path: str, auth_code: str = "") -> list[dict]:
    """从卡密导出文本加载邮箱列表。

    支持行格式：
      email----https://api798.com/latest?email=xxx&auth_code=XXX
      email|auth_code
    """
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("卡密"):
                    continue
                # 行格式: email----https://api798.com/latest?email=...&auth_code=XXX
                m = re.match(r"^([^@\s]+@[^@\s]+)(?:----|,|\s+)(\S*)$", line)
                if not m:
                    continue
                email = m.group(1).strip()
                rest = m.group(2).strip()
                ac = auth_code
                if "auth_code=" in rest:
                    ac = re.search(r"auth_code=([^&\s]+)", rest).group(1)
                elif rest:
                    ac = rest
                if email and ac:
                    out.append({"email": email, "auth_code": ac})
    except Exception as e:
        print(f"[api798] 加载邮箱失败: {e}")
    return out


def build_channel(mailboxes: list[dict], poll_interval: float = 6.0):
    """构造注册渠道 setup_fn。

    每个 setup 调用领取一个邮箱（队列消费），注册失败/成功即用掉；
    批量注册时队列耗尽返回 None（外层换号重试）。
    """
    with _QLOCK:
        _QUEUE.extend(mailboxes)

    def setup_fn(proxies, cancel_check):
        with _QLOCK:
            if not _QUEUE:
                print("[api798] 邮箱队列已耗尽")
                return None, None, None
            mb = _QUEUE.pop(0)
        email = mb["email"]
        auth = mb["auth_code"]
        openai_password = cc._gen_password()

        def fetch_code(timeout_sec=None, seen_ids=None, not_before=None):
            timeout_s = int(timeout_sec or 240)
            deadline = time.time() + timeout_s
            seen = set(seen_ids or [])
            last_code = {"v": ""}
            last_log = 0.0
            print(f"[api798] 等待 OTP: {email} (timeout≈{timeout_s}s)")
            while time.time() < deadline:
                if cancel_check and cancel_check():
                    print("[api798] 已取消（等 OTP 中）")
                    return None
                try:
                    code = _fetch_code_once(email, auth, not_before=not_before)
                    if code and code not in seen and code != last_code["v"]:
                        print(f"[api798] OTP ok: {code}")
                        last_code["v"] = code
                        return code
                except Exception as e:
                    print(f"[api798] 取码异常（继续）: {repr(e)[:120]}")
                now = time.time()
                if now - last_log >= 20:
                    print(f"[api798] still waiting OTP... remain≈{int(deadline - now)}s")
                    last_log = now
                time.sleep(poll_interval)
            print("[api798] OTP timeout")
            return None

        fetch_code.mark_already_registered = lambda detail: print(
            f"[api798] 邮箱已注册标记: {email} ({detail})")
        return email, openai_password, fetch_code

    return setup_fn


def _fetch_code_once(email: str, auth_code: str, not_before=None) -> str | None:
    """GET /get_code，返回验证码。

    响应 JSON：
      {"success": true, "message": "查询成功",
       "data": {"code": "238909", "subject": "...", "body": "...html...", "date": "..."}}
    优先取 data.code；缺失时回退 subject/body 中最新的 6 位验证码
    （body 可能含历史旧码，code 字段才是当前码）。

    not_before: 时间戳（秒），只接受该时刻之后到达的邮件（date 字段），
    避免已注册邮箱重跑时取到历史旧邮件里的旧验证码。
    """
    from curl_cffi import requests

    qs = urllib.parse.urlencode({"email": email, "auth_code": auth_code})
    r = requests.get(f"{_API_BASE}?{qs}", timeout=25, impersonate="chrome131")
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    inner = data.get("data")
    if isinstance(inner, dict):
        if not_before is not None:
            d = str(inner.get("date") or "")
            if d:
                try:
                    dt = float(d)
                    if dt < float(not_before):
                        return None
                except Exception:
                    pass
        code = str(inner.get("code") or "").strip()
        if code:
            return code
        blob = " ".join(str(inner.get(k) or "") for k in ("subject", "body"))
        m = re.search(r"\b(\d{6,8})\b", blob)
        return m.group(1) if m else None
    code = str(data.get("code") or "").strip()
    if code:
        return code
    return None