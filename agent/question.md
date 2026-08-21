# 需求文件：登入密码验证

## 原始需求
> 为这个由deepseek(蓝色大肥鱼)做的项目增加登入密码验证,采用预设密码的方式,后续可在web面板中更改

## 已确认需求与决策（使用者回答 + 惯例推导）

### 使用者确认的决策
1. **预设密码**：随机产生 16 字元，启动时印在 uvicorn 终端日志
2. **密码杂凑**：Python 标准库 `hashlib.pbkdf2_hmac`（SHA256, 100k iterations, 16-byte salt），无需新增依赖
3. **WebSocket 验证**：要验证，WS 连线时检查 cookie session
4. **失败限制**：连续失败 5 次后强制等待 30 秒（记忆体计数，重启重置）

### 依专案惯例自行决定（简述取舍）
5. **密码储存位置**：新建 `backend/auth.json`（与 secrets.json 同级，沿用原子写模式，加入 .gitignore）
   - 理由：不污染既有 secrets.json 结构，与 SecretsStore 模式一致
6. **Session 机制**：简单签名 cookie（标准库 hmac + secrets，无新依赖）
   - 理由：离线工具，不需 JWT；标准库足够安全
7. **登入页 UI**：独立全萤幕登入页（非弹窗）
   - 理由：面板级存取控制，全萤幕体验佳
8. **改密码 UI**：放 SecretsView 顶部第一个 card
   - 理由：SettingsView 已有「编辑密码与凭据 →」按钮跳 SecretsView，改密码放此处最自然
9. **登出功能**：有，放 Sidebar 底部
   - 理由：有登入就应有登出
10. **静态资源豁免**：首页 `/` 与 `/assets/*` 豁免验证，其余 API 全部验证
    - 理由：登入页本身需载入 JS/CSS；豁免清单：`/`, `/assets/*`, `/api/auth/login`, `/api/auth/me`(GET), `/api/health`

## 验收条件

### 后端
- [x] `backend/core/auth_store.py`：AuthStore 类别，PBKDF2 杂凑、auth.json 原子读写、首次启动产生随机密码并印终端
- [x] `backend/core/session.py`：签名 cookie 生成/验证（含过期）
- [x] `backend/api/auth.py`：POST /api/auth/login、POST /api/auth/logout、GET /api/auth/me、POST /api/auth/password
- [x] `backend/app.py`：auth middleware（保护 /api/* 但豁免清单）、WS /ws 验证、lifespan 初始化 auth_store
- [x] ~~`backend/config.example.yaml`：加入 auth 段范例~~（密码独立存 auth.json，非 config.yaml 段，不需修改）
- [x] `.gitignore`：加入 backend/auth.json
- [x] 失败 5 次后锁定 30 秒，回传 429（注：第 5 次仍回 401，第 6 次起回 429，避免暴露锁定状态）

### 前端
- [x] `frontend/src/views/LoginView.tsx`：全屏登录页
- [x] `frontend/src/api/client.ts`：401 时清除 auth 状态（setUnauthorizedHandler 回调）
- [x] `frontend/src/store/useStore.ts`：auth state（authState, login, logout, checkAuth）
- [x] `frontend/src/App.tsx`：未登录渲染 LoginView，checking 显示加载提示
- [x] `frontend/src/components/layout/Sidebar.tsx`：底部登出按钮
- [x] `frontend/src/views/SecretsView.tsx`：顶部「登录密码」card（旧密码+新密码+确认+送出）
- [x] `frontend/src/hooks/useWebSocket.ts`：仅 authenticated 时连线，避免未登录空转重连

### 测试（TDD）
- [x] `backend/tests/conftest.py`：pytest fixture（sys.path + tmp_auth_file + autouse 重置失败计数）
- [x] `backend/tests/test_auth_store.py`：14 测试（杂凑/验证/改密码/首次产生/原子写）
- [x] `backend/tests/test_session.py`：11 测试（签名/验证/过期/篡改/不同 secret）
- [x] `backend/tests/test_auth_api.py`：12 测试（登录/登出/me/改密码/失败锁定）
- [x] `backend/tests/test_auth_middleware.py`：9 测试（HTTP 保护/豁免 + WS 验证）

## 约束
- 全程简体中文（已将实作中产生的繁体内容转为简体）
- 不新增第三方依赖（全用 Python/JS 标准库）
- 不扩张需求范围，尊重既有架构
- TDD：RED → GREEN → REFACTOR（共 4 轮，46 测试全通过）
- 密码档必须 gitignore（已加 backend/auth.json）
