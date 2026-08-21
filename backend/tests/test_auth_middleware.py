"""认证 middleware 与 WebSocket 验证测试。

被测对象: api.auth.apply_auth (HTTP middleware) + api.auth.is_authenticated (WS 复用)。
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

import api.auth as auth_module
from core.auth_store import AuthStore


def _make_app(tmp_auth_file) -> FastAPI:
    """构造最小测试 app: auth router + mock 端点 + WS, 挂上 auth middleware。"""
    store = AuthStore(tmp_auth_file)
    store.set_password("TestPass123!")
    auth_module.auth_store = store
    auth_module.reset_session_manager()

    app = FastAPI()
    app.include_router(auth_module.router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/protected")
    def protected():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not auth_module.is_authenticated(websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    # 挂上认证 middleware (待实现)
    auth_module.apply_auth(app)
    return app


# ── HTTP middleware ───────────────────────────────────

class TestHttpMiddleware:
    def test_exempt_health_path_no_cookie(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_protected_path_no_cookie_returns_401(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        r = client.get("/api/protected")
        assert r.status_code == 401

    def test_protected_path_with_cookie_returns_200(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        # 先登录拿 cookie
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 200
        # 带 cookie 访问受保护端点
        r = client.get("/api/protected")
        assert r.status_code == 200

    def test_login_path_exempt(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 200

    def test_me_path_exempt_get(self, tmp_auth_file):
        """GET /api/auth/me 豁免 middleware, 端点自己回 401 (未登录)。"""
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        r = client.get("/api/auth/me")
        # 应该是端点回的 401, 而非 middleware 拦截
        assert r.status_code == 401
        assert r.json().get("authenticated") is False

    def test_assets_path_exempt(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        # /assets/* 不应被 middleware 拦截 (404 也行, 只要不是 401)
        r = client.get("/assets/nonexistent.js")
        assert r.status_code != 401

    def test_root_path_exempt(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        r = client.get("/")
        # 根路径豁免, 不应回 401 (404 也行)
        assert r.status_code != 401


# ── WebSocket 验证 ────────────────────────────────────

class TestWebSocketAuth:
    def test_ws_without_cookie_is_rejected(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        import pytest
        from starlette.websockets import WebSocketDisconnect
        # 未登录: 服务端在 accept 前就 close, TestClient __enter__ 立即抛 WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws"):
                pass
        # 4401 是自定义关闭码
        assert exc.value.code == 4401

    def test_ws_with_cookie_connects(self, tmp_auth_file):
        app = _make_app(tmp_auth_file)
        client = TestClient(app)
        # 先登录
        r = client.post("/api/auth/login", json={"password": "TestPass123!"})
        assert r.status_code == 200
        # 带 cookie 连 WS
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg == {"ok": True}
