# 操作纪录

## 2026-08-21

### 读取
- backend/app.py（FastAPI 入口，355 行）
- backend/api/__init__.py, deps.py, config.py
- backend/core/config.py, secrets_store.py
- backend/requirements.txt, config.example.yaml
- frontend/src/App.tsx, api/client.ts
- frontend/src/views/SettingsView.tsx, SecretsView.tsx
- frontend/src/store/useStore.ts, types/index.ts
- frontend/src/components/layout/TitleBar.tsx, Sidebar.tsx
- .gitignore
- README.md

### 写入
- agent/question.md（需求文件）
- agent/deep_todos.md（历史任务）
- agent/项目表.md（文件结构）
- agent/memory.md（本文件）

### 探索结论
- 专案无既有认证机制，所有 API/WS/静态资源完全公开
- 专案无测试基础设施（无 test 目录/档案）
- 专案无 auth 相关依赖（无 bcrypt/passlib/python-jose）
- SecretsStore 的原子写模式（tmp → dump → os.replace）可作为 AuthStore 参考
- 前端 App.tsx 直接渲染面板，无路由守卫，登入闸道插入点在根层
- SecretsView 注解「无需鉴权」需随实作更新

## 2026-08-21（实作完成）

### 新增档案（写入）
- backend/core/auth_store.py — AuthStore + PBKDF2 + auth.json 原子读写
- backend/core/session.py — SessionManager + HMAC-SHA256 签名 cookie
- backend/api/auth.py — login/logout/me/password + middleware + WS 验证 + 失败锁定
- backend/tests/__init__.py, conftest.py, test_auth_store.py, test_session.py, test_auth_api.py, test_auth_middleware.py
- frontend/src/views/LoginView.tsx — 全屏登录页

### 修改档案（写入）
- backend/app.py — lifespan 初始化 auth_store + apply_auth + auth_router + WS 认证
- .gitignore — 加 backend/auth.json
- frontend/src/api/client.ts — credentials:"same-origin" + setUnauthorizedHandler + 401 回调
- frontend/src/store/useStore.ts — AuthState/checkAuth/login/logout/setAuthError + setUnauthorizedHandler 注册
- frontend/src/App.tsx — useEffect checkAuth + authState 分支渲染
- frontend/src/components/layout/Sidebar.tsx — footer 加登出按钮
- frontend/src/hooks/useWebSocket.ts — 仅 authenticated 时连线，onclose 用 getState() 读 authState
- frontend/src/views/SecretsView.tsx — 顶部注释更新 + ChangePasswordCard 子元件
- web/dist/* — 重新 build（tsc 无型别错误，vite 产出 3 档）

### 繁→简转换
- 用 Python 腳本（C:\Users\yoyo2\AppData\Local\Temp\opencode\t2s.py + t2s_patch.py）转换 8 个含繁体文件
- 验证无遗漏繁体字

### 验证结果
- 后端 46 单元测试全通过（auth_store 14 + session 11 + auth_api 12 + auth_middleware 9）
- app.py 可 import，4 auth 路由正确挂载
- auth_store.ensure_ready() 行为正确（首次产生 + 不重复生成）
- 前端 tsc 无型别错误，vite build 成功
- 未完整验证：真实 HTTP 端到端（专案缺 config.yaml，无法启动完整 server）
