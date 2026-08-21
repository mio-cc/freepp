"""Session 签名 cookie 测试。

测试对象：backend/core/session.py。

行为契约：
1. create_session() → 回传字串 token（timestamp.signature 格式）
2. verify_session(token) → 有效 True
3. 篡改 signature → False
4. 篡改 timestamp → False
5. 过期 token（超过 max_age）→ False
6. 不同 secret 签的 token → False
7. 空字串/None/malformed → False
"""
from __future__ import annotations

import time

import pytest

from core.session import SessionManager


class TestSessionCreate:
    def test_create_returns_string(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        token = mgr.create()
        assert isinstance(token, str)
        assert "." in token

    def test_create_contains_timestamp_and_signature(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        token = mgr.create()
        parts = token.split(".")
        assert len(parts) == 2
        ts_hex, sig = parts
        # timestamp 应为合理 unix epoch
        ts = int(ts_hex, 16)
        assert abs(ts - int(time.time())) < 5


class TestSessionVerify:
    def test_verify_valid_token(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        token = mgr.create()
        assert mgr.verify(token) is True

    def test_verify_tampered_signature(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        token = mgr.create()
        ts, sig = token.split(".")
        # 篡改 signature 最后一字元
        bad_sig = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        assert mgr.verify(f"{ts}.{bad_sig}") is False

    def test_verify_tampered_timestamp(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        token = mgr.create()
        _, sig = token.split(".")
        # 篡改 timestamp
        assert mgr.verify("deadbeef." + sig) is False

    def test_verify_different_secret(self):
        mgr1 = SessionManager(secret="secret_one_32_chars_long_xxxxxxxxx")
        mgr2 = SessionManager(secret="secret_two_32_chars_long_xxxxxxxxx")
        token = mgr1.create()
        assert mgr2.verify(token) is False

    def test_verify_expired_token(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx", max_age_seconds=1)
        token = mgr.create()
        # 刚建立应有效
        assert mgr.verify(token) is True
        # 等 1.2 秒后过期
        time.sleep(1.2)
        assert mgr.verify(token) is False

    def test_verify_empty_string(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        assert mgr.verify("") is False

    def test_verify_none(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        assert mgr.verify(None) is False  # type: ignore[arg-type]

    def test_verify_malformed_no_dot(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        assert mgr.verify("nodot") is False

    def test_verify_malformed_bad_timestamp(self):
        mgr = SessionManager(secret="test_secret_32_chars_long_xxxxxx")
        # timestamp 非 hex
        assert mgr.verify("zzzz.haha") is False
