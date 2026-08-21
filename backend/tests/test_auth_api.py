"""Auth API 路由测试。

测试对象：backend/api/auth.py。

行为契约：
1. POST /api/auth/login  body: {password} → 正确密码 200 + Set-Cookie min_session
2. POST /api/auth/login  错误密码 → 401
3. GET  /api/auth/me     已登录 → 200 {ok, authenticated: true}
4. GET  /api/auth/me     未登录 → 401
5. POST /api/auth/logout → 清 cookie, 200 {ok: true}
6. POST /api/auth/password body: {old_password, new_password}
   - 旧密码正确 → 200 {ok: true}
   - 旧密码错误 → 401
7. 连续 5 次失败后锁定, 第 6 次返回 429 (锁定 30 秒, 重启重置)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth_store import AuthStore
from api import auth as auth_module


def _make_app(store: AuthStore) -> FastAPI:
    """构造一个仅含 auth 路由的 FastAPI 实例, 并注入测试 store。"""
    auth_module.auth_store = store
    auth_module.reset_session_manager()
    app = FastAPI()
    app.include_router(auth_module.router)
    return app


@pytest.fixture
def app(tmp_auth_file: Path) -> FastAPI:
    """预初始化 store 并写入已知密码 'TestPass123!'。"""
    store = AuthStore(tmp_auth_file)
    store.set_password("TestPass123!")
    return _make_app(store)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestLogin:
    def test_correct_password_sets_cookie(self, client: TestClient):
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 200
        # Set-Cookie 应包含 min_session
        set_cookie = r.headers.get("set-cookie", "")
        assert "min_session" in set_cookie

    def test_wrong_password_returns_401(self, client: TestClient):
        r = client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401
        body = r.json()
        assert body.get("ok") is False

    def test_empty_password_returns_401(self, client: TestClient):
        r = client.post("/api/auth/login", json={"password": ""})
        assert r.status_code == 401

    def test_missing_password_field_returns_400(self, client: TestClient):
        r = client.post("/api/auth/login", json={})
        assert r.status_code in (400, 422)


class TestMe:
    def test_me_authenticated(self, client: TestClient):
        # 先登录
        client.post("/api/auth/login", json={"password": "TestPass123!"})
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body.get("authenticated") is True

    def test_me_without_cookie_returns_401(self, client: TestClient):
        r = client.get("/api/auth/me")
        assert r.status_code == 401


class TestLogout:
    def test_logout_clears_session(self, client: TestClient):
        # 先登录
        client.post("/api/auth/login", json={"password": "TestPass123!"})
        # 再登出
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        # Set-Cookie 应清除 min_session (Max-Age=0 或空值)
        set_cookie = r.headers.get("set-cookie", "")
        assert "min_session" in set_cookie
        # 之后 me 应 401
        r2 = client.get("/api/auth/me")
        assert r2.status_code == 401


class TestChangePassword:
    def test_correct_old_password_succeeds(self, client: TestClient, app: FastAPI):
        # 先登录 (新改密需要有效 session)
        client.post("/api/auth/login", json={"password": "TestPass123!"})
        r = client.post("/api/auth/password", json={
            "old_password": "TestPass123!",
            "new_password": "NewPass456!",
        })
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        # 旧密码失效
        r2 = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r2.status_code == 401
        # 新密码可用
        r3 = client.post("/api/auth/login", json={"password": "NewPass456!"})
        assert r3.status_code == 200

    def test_wrong_old_password_returns_401(self, client: TestClient):
        client.post("/api/auth/login", json={"password": "TestPass123!"})
        r = client.post("/api/auth/password", json={
            "old_password": "wrong",
            "new_password": "NewPass456!",
        })
        assert r.status_code == 401

    def test_too_short_new_password_rejected(self, client: TestClient):
        client.post("/api/auth/login", json={"password": "TestPass123!"})
        r = client.post("/api/auth/password", json={
            "old_password": "TestPass123!",
            "new_password": "123",
        })
        assert r.status_code == 400


class TestFailureLockout:
    def test_five_failures_then_locked(self, client: TestClient):
        """连续 5 次错误密码 → 第 6 次返回 429。"""
        for i in range(5):
            r = client.post("/api/auth/login", json={"password": "wrong"})
            assert r.status_code == 401, f"第 {i+1} 次应 401, 实际 {r.status_code}"
        # 第 6 次应锁定
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 429
        body = r.json()
        assert "lock" in body.get("error", "").lower() or body.get("locked") is True

    def test_lockout_even_correct_password_blocked(self, client: TestClient):
        """锁定后即便用正确密码也 429。"""
        for _ in range(5):
            client.post("/api/auth/login", json={"password": "wrong"})
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 429
