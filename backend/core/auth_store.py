"""登入密码存储 (PBKDF2 杂凑 + 原子写)。

模式参考 core/secrets_store.py 的原子写 (tmp → os.replace)。
全用标准库: hashlib / hmac / secrets / json / os / sys。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import string
import sys
from pathlib import Path
from typing import Optional

# PBKDF2 参数
_ITERATIONS = 100_000
_SALT_BYTES = 16
_SESSION_SECRET_BYTES = 32
_PASSWORD_LEN = 16

# 随机密码字元池: 字母 + 数字 + 常见符号
_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"


def _hash_password(password: str) -> dict:
    """PBKDF2-HMAC-SHA256 杂凑密码, 回传 {salt, iterations, hash}。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return {
        "salt": salt.hex(),
        "iterations": _ITERATIONS,
        "hash": digest.hex(),
    }


def _verify_password(password: str, stored: dict) -> bool:
    """定时安全比对密码杂凑。"""
    if not isinstance(stored, dict):
        return False
    try:
        salt = bytes.fromhex(stored["salt"])
        iterations = int(stored["iterations"])
        expected = bytes.fromhex(stored["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


def _generate_random_password() -> str:
    """产生 16 字元随机密码。"""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(_PASSWORD_LEN))


def _generate_session_secret() -> str:
    """产生 session 签名密钥 (hex)。"""
    return secrets.token_hex(_SESSION_SECRET_BYTES)


class AuthStore:
    """登入密码存储, PBKDF2 杂凑, 原子写 auth.json。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._data: dict = {}
        self._loaded = False

    # ---- 载入/写入 ----
    def _load(self) -> dict:
        """从磁碟载入 auth.json, 不存在回空 dict。"""
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
            return json.loads(text) if text.strip() else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        """原子写: tmp → json.dump → os.replace。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(self._path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self._path)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._data = self._load()
            self._loaded = True

    # ---- 公开 API ----
    def ensure_ready(self) -> Optional[str]:
        """首次启动产生随机密码并回传明文; 已存在回传 None。

        首次启动时在 stderr 印出密码提示。
        """
        self._ensure_loaded()
        if "password" in self._data:
            return None

        # 首次: 产生密码 + session_secret
        plaintext = _generate_random_password()
        self._data["password"] = _hash_password(plaintext)
        if "session_secret" not in self._data:
            self._data["session_secret"] = _generate_session_secret()
        self._save()

        # 印到终端 (stderr, 不影响 uvicorn stdout)
        print(
            "\n"
            "┌──────────────────────────────────────────────┐\n"
            "│  ★ 首次启动: 已产生登入密码                  │\n"
            f"│  密码: {plaintext}                        │\n"
            "│  请记下此密码, 可在面板「密钥与凭据」页变更  │\n"
            "│  密码档: " + str(self._path).ljust(32) + "│\n"
            "└──────────────────────────────────────────────┘\n",
            file=sys.stderr,
            flush=True,
        )
        return plaintext

    def verify(self, password: str) -> bool:
        """验证密码是否正确。"""
        self._ensure_loaded()
        stored = self._data.get("password")
        if not stored or not password:
            return False
        return _verify_password(password, stored)

    def set_password(self, new_password: str) -> None:
        """设定新密码 (杂凑后写入磁碟)。"""
        self._ensure_loaded()
        self._data["password"] = _hash_password(new_password)
        if "session_secret" not in self._data:
            self._data["session_secret"] = _generate_session_secret()
        self._save()

    @property
    def session_secret(self) -> str:
        """取得 session 签名密钥 (首次自动产生并持久化)。"""
        self._ensure_loaded()
        secret = self._data.get("session_secret")
        if not secret:
            secret = _generate_session_secret()
            self._data["session_secret"] = secret
            self._save()
        return secret

    @property
    def path(self) -> Path:
        return self._path
