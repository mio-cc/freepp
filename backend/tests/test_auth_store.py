"""AuthStore 单元测试。

测试对象：backend/core/auth_store.py 的 AuthStore 类别。

行为契约：
1. 首次启动（auth.json 不存在）→ 产生 16 字元随机密码，回传明文，auth.json 写入 PBKDF2 杂凑
2. 载入（auth.json 存在）→ 读取既有杂凑与 session_secret
3. verify(password) → 正确 True / 错误 False
4. set_password(new) → 新杂凑覆写，旧密失效，新密生效
5. session_secret → 首次产生并持久化，重启后保持一致
6. 原子写 → 写入用 tmp + os.replace
7. 随机密码长度 16，含字母数字符号
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.auth_store import AuthStore


class TestAuthStoreInit:
    """首次启动行为。"""

    def test_first_run_generates_random_password(self, tmp_auth_file: Path):
        """auth.json 不存在时，ensure_ready() 产生随机密码并回传明文。"""
        store = AuthStore(str(tmp_auth_file))
        plaintext = store.ensure_ready()
        assert plaintext is not None
        assert len(plaintext) == 16
        assert tmp_auth_file.exists()

    def test_first_run_password_is_random(self, tmp_auth_file: Path):
        """两个独立 store 产生的密码不同。"""
        s1 = AuthStore(str(tmp_auth_file))
        p1 = s1.ensure_ready()
        # 不同档案路径
        other = tmp_auth_file.parent / "auth2.json"
        s2 = AuthStore(str(other))
        p2 = s2.ensure_ready()
        assert p1 != p2

    def test_second_run_returns_none(self, tmp_auth_file: Path):
        """auth.json 已存在时，ensure_ready() 回传 None（不重新产生）。"""
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        store2 = AuthStore(str(tmp_auth_file))
        assert store2.ensure_ready() is None

    def test_generated_file_has_hashed_password(self, tmp_auth_file: Path):
        """产生的 auth.json 中 password 栏位是 PBKDF2 杂凑（含 salt/iterations/digest），非明文。"""
        store = AuthStore(str(tmp_auth_file))
        plaintext = store.ensure_ready()
        data = json.loads(tmp_auth_file.read_text(encoding="utf-8"))
        assert "password" in data
        stored = data["password"]
        assert isinstance(stored, dict)
        assert "salt" in stored and "iterations" in stored and "hash" in stored
        # 杂凑不含明文
        assert plaintext not in json.dumps(stored)


class TestAuthStoreVerify:
    """密码验证。"""

    def test_verify_correct_password(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        plaintext = store.ensure_ready()
        assert store.verify(plaintext) is True

    def test_verify_wrong_password(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        assert store.verify("wrong_password") is False

    def test_verify_empty_password(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        assert store.verify("") is False


class TestAuthStoreSetPassword:
    """改密码。"""

    def test_set_password_invalidates_old(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        old = store.ensure_ready()
        store.set_password("NewPass123!@#")
        assert store.verify(old) is False

    def test_set_password_validates_new(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        new = "NewPass123!@#"
        store.set_password(new)
        assert store.verify(new) is True

    def test_set_password_persists_to_disk(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        store.set_password("Persisted456")
        # 重新载入
        store2 = AuthStore(str(tmp_auth_file))
        store2.ensure_ready()
        assert store2.verify("Persisted456") is True


class TestSessionSecret:
    """session_secret 行为。"""

    def test_session_secret_generated_on_first_run(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        secret = store.session_secret
        assert isinstance(secret, str)
        assert len(secret) >= 32

    def test_session_secret_persists(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        secret1 = store.session_secret
        store2 = AuthStore(str(tmp_auth_file))
        store2.ensure_ready()
        assert store2.session_secret == secret1

    def test_session_secret_is_random(self, tmp_auth_file: Path):
        s1 = AuthStore(str(tmp_auth_file))
        s1.ensure_ready()
        other = tmp_auth_file.parent / "auth2.json"
        s2 = AuthStore(str(other))
        s2.ensure_ready()
        assert s1.session_secret != s2.session_secret


class TestAtomicWrite:
    """原子写：写入后不留 .tmp 残留。"""

    def test_no_tmp_residue(self, tmp_auth_file: Path):
        store = AuthStore(str(tmp_auth_file))
        store.ensure_ready()
        store.set_password("Another789")
        assert not (tmp_auth_file.parent / (tmp_auth_file.name + ".tmp")).exists()
