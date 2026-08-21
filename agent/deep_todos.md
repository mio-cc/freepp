# 专案历史任务纪录

## 2026-08-21 — 登录密码验证（已完成 ✅）

### 任务
为 min-implant-v2 专案增加登录密码验证，预设密码方式，后续可在 web 面板更改。
附带需求：看得见中文的部分换成简体中文。

### 决策摘要
- 预设密码：随机 16 字元，启动时印到 uvicorn stderr
- 杂凑：pbkdf2_hmac（SHA256, 100k iterations, 16-byte salt，标准库）
- Session：签名 cookie（标准库 hmac+secrets，token 格式 `timestamp_hex.hmac_hexsig`）
- WS 验证：要（WS 端点内 is_authenticated 检查，未登录 close 4401）
- 失败限制：连续 5 次后锁 30 秒（进程级内存计数，重启重置）
- 储存：backend/auth.json（原子写 tmp→os.replace，已 gitignore）
- 登录页：独立全屏页 LoginView
- 改密码 UI：SecretsView 顶部第一个 card ChangePasswordCard
- 登出：Sidebar 底部 footer 内
- 静态资源豁免：/、/assets/*、/api/auth/login(POST)、/api/auth/me(GET)、/api/health；其余 /api/* 与 /ws 全验证
- cookie: min_session, httponly=True, samesite="lax", secure=False, path="/", max_age=604800(7天)
- 密码最短 8 字元（前后端一致）
- 全项目中文用简体

### 工作项目（全数完成）
- [x] 后端 auth_store：PBKDF2 杂凑 + auth.json 原子读写 + 首次产生随机密码
- [x] 后端 session：签名 cookie 生成/验证/过期
- [x] 后端 auth API：login/logout/me/password + 失败锁定
- [x] 后端 middleware + WS 验证：保护 /api/* 豁免清单 + WS cookie 检查
- [x] 后端 app.py 整合 + .gitignore
- [x] 前端 store auth state + client 401 处理
- [x] 前端 LoginView 全屏登录页
- [x] 前端 App 登录闸道 + Sidebar 登出按钮
- [x] 前端 SecretsView 改密码 card
- [x] 前端 build + 整合验证

### 验证结果
- 后端 46 单元测试全通过（auth_store 14 + session 11 + auth_api 12 + auth_middleware 9）
- 后端整合：app.py 可 import，4 auth 路由正确挂载，auth_store.ensure_ready() 行为正确
- 前端：tsc 无型别错误，vite build 成功产出 web/dist
- **真实 HTTP 端到端验证（14 项全通过）**：server 启动于 port 8800（PID 23772），密码 xh7QHc0yFK@fqw%A
  - /api/health 200（豁免） / / 200（首页豁免）
  - /api/auth/me 未登录 401 / /api/tokens 未登录 401（middleware 拦截）
  - POST /api/auth/login 错密码 401「密码错误」 / 正确密码 200 + set-cookie min_session
  - GET /api/auth/me 带 cookie 200 {authenticated:true}
  - GET /api/tokens 带 cookie 200（返回真实 token 列表）
  - POST /api/auth/password 改密码 200 / 旧密码登录 401 / 新密码登录 200
  - POST /api/auth/logout 200 + clear cookie / 登出后 /api/auth/me 401

### TDD 轮次
共 4 轮 RED→GREEN：
1. auth_store（14 测试）
2. session（11 测试）
3. auth API（12 测试）
4. auth middleware + WS（9 测试）
