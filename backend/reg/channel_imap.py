# -*- coding: utf-8 -*-
"""reg/channel_imap.py — 自定义 IMAP 邮箱注册渠道

从 mail_pool_store 领用一个邮箱, 注册时生成 OpenAI 账户密码,
通过 IMAP 轮询收件箱取 OpenAI 验证码。

注册渠道契约（reg/engine.register_email_channel）：
    setup_fn(proxies, cancel_check) -> (email, openai_password, fetch_code)
    fetch_code(timeout_sec=None, seen_ids=None, not_before=None) -> code|None

地址策略:
    - direct    : 用 username 原地址注册
    - catchall  : 生成 alias+{随机8}@catchall_domain 别名 (共用同一收件箱取码)

不依赖 chatgpt_core 的死 IMAP 代码 (其引用未定义常量); 本文件自包含取码逻辑。
"""
from __future__ import annotations

import email as _email
import imaplib
import random
import re
import string
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from core.mail_pool_store import mail_pool_store

# 取码轮询节奏 (秒)
_POLL_INTERVAL = 6.0
# Date 头时间偏移容差 (秒): not_before 前后这点窗口内的 OTP 也接受, 防 clock skew
_DATE_SKEW_SEC = 30


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now(timezone.utc).isoformat()


def _msg_date_ts(msg) -> float | None:
    """解析邮件 Date 头为 unix 时间戳; 失败返回 None。"""
    try:
        raw = msg.get("Date") if msg is not None else None
        if not raw:
            return None
        dt = parsedate_to_datetime(str(raw))
        if dt is None:
            return None
        return float(dt.timestamp())
    except Exception:
        return None


def _imap_fetch_message_bytes(conn, mid) -> bytes | None:
    """拉取完整邮件原始字节。优先 BODY.PEEK[] (不改 \\Seen), 失败回退 RFC822。"""
    for spec in ("(BODY.PEEK[])", "(RFC822)"):
        try:
            _typ, data = conn.fetch(mid, spec)
        except Exception:
            continue
        if not data:
            continue
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                if item[1]:
                    return bytes(item[1])
    return None


def _gen_alias(mbox: dict[str, Any]) -> str:
    """根据 alias_mode 生成注册用邮箱地址。"""
    mode = mbox.get("alias_mode") or "direct"
    user = mbox.get("username") or ""
    if mode == "catchall":
        domain = (mbox.get("catchall_domain") or "").strip()
        # catchall_domain 形如 "@domain.com" 或 "domain.com"; 统一带 @
        if not domain.startswith("@"):
            domain = "@" + domain
        rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return f"oai{rnd}{domain}"
    return user


def _match_code(raw_bytes: bytes, rules: dict[str, Any], not_before: float | None,
                alias: str) -> str | None:
    """解析单封邮件: 发件人/主题白名单过滤 + 提取验证码 + not_before 时间过滤。

    raw_bytes: 完整邮件原始字节
    rules: effective_rules 返回 {sender_whitelist, subject_whitelist, code_regex}
    not_before: unix ts; 只接受 Date >= not_before - skew 的 OTP
    alias: catchall 时用于匹配收件人 (过滤发给别名的邮件)
    """
    try:
        msg = _email.message_from_bytes(raw_bytes)
    except Exception:
        return None
    # 时间过滤 (避免历史旧码)
    if not_before is not None:
        dts = _msg_date_ts(msg)
        if dts is None:
            # 无 Date 头的邮件无法判定时序, catch-all 收件箱里极可能是旧邮件, 跳过
            return None
        if dts < (float(not_before) - _DATE_SKEW_SEC):
            return None
    fr = str(msg.get("From", "") or "").lower()
    sj = str(msg.get("Subject", "") or "").lower()
    to = str(msg.get("To", "") or "").lower()

    # catchall 别名: 收件人须含别名 (避免同收件箱其他别名邮件干扰)
    if alias and "@" in alias and alias.lower() != (msg.get("To") or "").lower():
        # 放宽: 仅当 alias 不在 To 时跳过 (catch-all 收件箱可能多别名)
        if alias.lower() not in to:
            return None

    # 发件人白名单 (空则放行)
    senders = rules.get("sender_whitelist") or []
    if senders and not any(s.lower() in fr for s in senders):
        return None
    # 主题白名单 (空则放行)
    subjects = rules.get("subject_whitelist") or []
    if subjects and not any(s.lower() in sj for s in subjects):
        return None

    # 提取验证码: 优先主题, 回退正文
    code_re = rules.get("code_regex") or r"\b(\d{4,8})\b"
    try:
        rgx = re.compile(code_re)
    except re.error:
        rgx = re.compile(r"\b(\d{4,8})\b")
    body_text = ""
    plain_text = ""
    html_text = ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not plain_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        plain_text = payload.decode("utf-8", errors="ignore")
                elif ct == "text/html" and not html_text:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_text = payload.decode("utf-8", errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                txt = payload.decode("utf-8", errors="ignore")
                if msg.get_content_type() == "text/html":
                    html_text = txt
                else:
                    plain_text = txt
    except Exception:
        body_text = ""

    def _strip_html(html: str) -> str:
        """剥离 HTML 标签 + style 属性内的 CSS 颜色值 (#202123 等), 保留纯文本。
        OpenAI 验证码邮件用 HTML, 验证码以大字体独立呈现; 但 #颜色值(如 #202123)
        会被 \\b\\d+\\b 误匹配为验证码 → wrong_email_otp_code。必须先去标签再提取。
        """
        # 先移除 <style>...</style> 和 <head>...</head> 块 (CSS 定义)
        html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
        # 移除所有标签属性 (style="color:#202123" 等里的数字), 只留标签文本内容
        html = re.sub(r"<[^>]+>", " ", html)
        # 移除残余的 #十六进制颜色值 (# 后跟 3-8 位 hex)
        html = re.sub(r"#[0-9a-fA-F]{3,8}\b", " ", html)
        return re.sub(r"\s+", " ", html).strip()

    # 优先纯文本 (无 CSS 干扰); HTML 需先剥离标签/颜色值
    if plain_text:
        body_text = plain_text
    elif html_text:
        body_text = _strip_html(html_text)

    for blob in (sj, body_text):
        if not blob:
            continue
        m = rgx.search(blob)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return None


def build_channel(mbox_id: str | None = None):
    """构造 IMAP 注册渠道 setup_fn。

    mbox_id=None: 从 mail_pool_store 整池领用 (catch-all 同档轮询);
    mbox_id 指定: 锁定该邮箱 (每域独立渠道, 注册时只跑这一个域)。
    耗尽/不可用返回 (None, None, None); catchall 不消耗计数可无限复用。
    """
    from . import chatgpt_core as cc  # 延迟 import 避免循环

    def setup_fn(proxies, cancel_check) -> tuple[str | None, str | None, Callable | None]:
        mbox = mail_pool_store.consume_by_id(mbox_id) if mbox_id else mail_pool_store.consume()
        if not mbox:
            print(f"[imap] 邮箱不可用 (id={mbox_id or '整池'}, 可能已耗尽或被禁用)")
            return None, None, None
        alias = _gen_alias(mbox)
        openai_password = cc._gen_password()
        rules = mail_pool_store.effective_rules(mbox)
        host = mbox.get("imap_host") or ""
        port = int(mbox.get("imap_port") or 993)
        ssl = bool(mbox.get("imap_ssl", True))
        user = mbox.get("username") or ""
        pwd = mbox.get("password") or ""
        need_id = any(h in host for h in ("163.com", "126.com", "yeah.net"))

        print(f"[imap] 领用邮箱 {user} → 注册地址 {alias} (mode={mbox.get('alias_mode')})")

        def fetch_code(timeout_sec=None, seen_ids=None, not_before=None) -> str | None:
            import time as _t
            timeout_s = int(timeout_sec or 240)
            deadline = _t.time() + timeout_s
            seen = set(seen_ids or [])
            if not host or not user or not pwd:
                print("[imap] 邮箱配置不全, 无法取码")
                return None
            print(f"[imap] 等待 OTP: {alias} via {host}:{port} (timeout≈{timeout_s}s)")
            last_log = 0.0
            while _t.time() < deadline:
                if cancel_check and cancel_check():
                    print("[imap] 已取消（等 OTP 中）")
                    return None
                conn = None
                try:
                    if ssl:
                        conn = imaplib.IMAP4_SSL(host, port, timeout=20)
                    else:
                        conn = imaplib.IMAP4(host, port, timeout=20)
                    if need_id:
                        imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")
                        conn._simple_command("ID", '("name" "IMAPClient" "version" "1.0")')
                    conn.login(user, pwd)
                    conn.select("INBOX", readonly=True)
                    _, msg_nums = conn.search(None, "ALL")
                    ids = msg_nums[0].split() if msg_nums and msg_nums[0] else []
                    # 逆序遍历 (最新邮件优先)
                    for mid in reversed(ids):
                        mid_key = mid.decode("utf-8", errors="replace") if isinstance(mid, (bytes, bytearray)) else str(mid)
                        sk = f"{user}:{mid_key}"
                        if sk in seen:
                            continue
                        raw = _imap_fetch_message_bytes(conn, mid)
                        if not raw:
                            seen.add(sk)
                            continue
                        code = _match_code(raw, rules, not_before, alias)
                        seen.add(sk)
                        if code:
                            print(f"[imap] OTP ok: {code}")
                            return code
                    conn.logout()
                    conn = None
                except Exception as e:
                    print(f"[imap] 取码轮询异常（继续）: {repr(e)[:120]}")
                finally:
                    if conn is not None:
                        try:
                            conn.logout()
                        except Exception:
                            pass
                now = _t.time()
                if now - last_log >= 20:
                    print(f"[imap] still waiting OTP... remain≈{int(deadline - now)}s")
                    last_log = now
                _t.sleep(_POLL_INTERVAL)
            print("[imap] OTP timeout")
            return None

        # 对齐 api798 接口: 已注册标记钩子 (引擎在 OTP validate 失败时调用)
        def _mark_already_registered(detail):
            print(f"[imap] 邮箱已注册标记: {alias} ({detail})")
        fetch_code.mark_already_registered = _mark_already_registered

        return alias, openai_password, fetch_code

    return setup_fn
