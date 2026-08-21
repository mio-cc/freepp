"""登入认证 API 路由。

REST:
- POST /api/auth/login     - 登入, 成功设置签名 cookie
- GET  /api/auth/me        - 检查当前会话状态
- POST /api/auth/logout    - 登出, 清除 cookie
- POST /api/auth/password  - 变更密码 (需已登录)

失败锁定: 连续 5 次错误密码后锁 30 秒 (内存计数, 重启重置)。
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from core.auth_store import AuthStore
from core.session import SessionManager

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── 配置 ──────────────────────────────────────────────
SESSION_COOKIE_NAME = "min_session"
SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 天
PASSWORD_MIN_LEN = 8
MAX_FAILURES = 5
LOCK_SECONDS = 30

# ── middleware 豁免清单 ───────────────────────────────
# 精确匹配: 首页 / 健康检查 / 登入入口 / 会话检查
EXEMPT_PATHS_EXACT: set[str] = {
    "/",
    "/api/health",
    "/api/auth/login",
    "/api/auth/me",
}
# 前缀匹配: 静态资源
EXEMPT_PREFIXES: tuple[str, ...] = ("/assets",)

# ── 全局 store (由 app.py 启动时注入; 测试可替换) ────
auth_store: Optional[AuthStore] = None
_session_mgr: Optional[SessionManager] = None

# ── 失败计数 (内存, 进程级) ──────────────────────────
_fail_count: int = 0
_locked_until: float = 0.0


def init_store(store: AuthStore) -> None:
    """由 app.py 启动时调用, 注入 auth_store 并建立 session manager。"""
    global auth_store, _session_mgr
    auth_store = store
    _session_mgr = SessionManager(secret=store.session_secret, max_age_seconds=SESSION_MAX_AGE)


def reset_session_manager() -> None:
    """测试用: 依据当前 auth_store 重建 session manager。"""
    global _session_mgr
    if auth_store is not None:
        _session_mgr = SessionManager(secret=auth_store.session_secret, max_age_seconds=SESSION_MAX_AGE)


def _ensure_store():
    if auth_store is None or _session_mgr is None:
        raise RuntimeError("auth_store 未初始化, 请在 app.py 启动时调用 init_store()")
    return auth_store, _session_mgr


def _is_locked() -> bool:
    return time.time() < _locked_until


def _record_failure() -> None:
    global _fail_count, _locked_until
    _fail_count += 1
    if _fail_count >= MAX_FAILURES:
        _locked_until = time.time() + LOCK_SECONDS


def _record_success() -> None:
    global _fail_count, _locked_until
    _fail_count = 0
    _locked_until = 0.0


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # 离线项目, 通常 http
    )


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def _get_session_token(request: Request) -> Optional[str]:
    return request.cookies.get(SESSION_COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    """供 middleware / WS 调用的会话检查。"""
    store, mgr = _ensure_store()
    token = _get_session_token(request)
    return mgr.verify(token) if token else False


# ── HTTP middleware ────────────────────────────────────

def _is_path_exempt(path: str) -> bool:
    """路径是否豁免认证。"""
    if path in EXEMPT_PATHS_EXACT:
        return True
    for p in EXEMPT_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return True
    return False


async def _auth_middleware(request: Request, call_next):
    """认证中间件: 豁免清单外的 /api/* 与 WebSocket 都需要登录。"""
    path = request.url.path
    if _is_path_exempt(path) or is_authenticated(request):
        return await call_next(request)
    return JSONResponse(status_code=401, content={"ok": False, "error": "未登录"})


def apply_auth(app: FastAPI) -> None:
    """把认证中间件挂载到 app 上。"""

    @app.middleware("http")
    async def _middleware(request: Request, call_next):
        return await _auth_middleware(request, call_next)


# ── 路由 ──────────────────────────────────────────────

@router.post("/login")
async def login(body: dict, response: Response):
    """登入: 校验密码, 成功设置签名 cookie。"""
    store, mgr = _ensure_store()
    password = body.get("password")
    if not isinstance(password, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少 password 字段"})

    # 锁定检查 (锁定期间一律 429, 不透露密码对错)
    if _is_locked():
        return JSONResponse(
            status_code=429,
            content={"ok": False, "locked": True, "error": "登录失败次数过多, 请稍后再试"},
        )

    if store.verify(password):
        _record_success()
        token = mgr.create()
        _set_session_cookie(response, token)
        return {"ok": True}

    # 密码错误 (含空字串): 计入失败次数, 但本次仍回 401
    # 达到阈值后下次请求才锁定, 避免本次就暴露锁定状态
    _record_failure()
    return JSONResponse(status_code=401, content={"ok": False, "error": "密码错误"})


@router.get("/me")
async def me(request: Request):
    """检查当前会话状态。"""
    if is_authenticated(request):
        return {"ok": True, "authenticated": True}
    return JSONResponse(status_code=401, content={"ok": False, "authenticated": False})


@router.post("/logout")
async def logout(response: Response):
    """登出: 清除 cookie。"""
    _clear_session_cookie(response)
    return {"ok": True}


@router.post("/password")
async def change_password(body: dict, request: Request):
    """变更密码 (需已登录)。

    body: {old_password, new_password}
    """
    store, mgr = _ensure_store()

    # 必须已登录
    if not is_authenticated(request):
        return JSONResponse(status_code=401, content={"ok": False, "error": "未登录"})

    old_pw = body.get("old_password")
    new_pw = body.get("new_password")
    if not isinstance(old_pw, str) or not isinstance(new_pw, str):
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少字段"})
    if len(new_pw) < PASSWORD_MIN_LEN:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": f"新密码至少 {PASSWORD_MIN_LEN} 字元"},
        )

    if not store.verify(old_pw):
        return JSONResponse(status_code=401, content={"ok": False, "error": "旧密码错误"})

    store.set_password(new_pw)
    # 改密后旧 session 仍有效 (session_secret 不变), 但前端通常会重新登录
    return {"ok": True}
