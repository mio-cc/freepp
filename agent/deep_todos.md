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

## 2026-08-21 — Web 页面美化（已完成 ✅）

### 任务
自主叠代升级：先 commit 登录功能，之后美化 web 页面。附带：检查 emoji 换成 SVG。

### 决策摘要
- 风格：现代玻璃拟态（backdrop-blur + 半透明 + 柔光边框 + 柔阴影 + 品牌光晕）
- 动效：CSS 微交互（hover/transition/focus + view-fade-in 动画，不引入动画库）
- emoji：21 种装饰性 emoji 换成 SVG 组件（icons.tsx 集中库），纯箭头保留
- 亮暗双适配，reduced-motion 已有守护

### 工作项目（全数完成）
- [x] commit 登录功能（19fc695，25 files +1698/-60）
- [x] 截图 before 基线
- [x] tokens.css 加 7 亮 + 7 暗玻璃令牌（glass-bg/glass-bg-strong/glass-blur/glass-border/glass-highlight/glass-shadow/glow-brand）
- [x] index.css 主面板玻璃：.sidebar / .card / .stat-card / .input:focus 双光环 / .view-fade-in 动画 / .log-panel / .log-toolbar
- [x] index.css 微交互：.card:hover 阴影加深 / .btn:focus-visible 等键盘光环（无障碍）
- [x] emoji→SVG 替换：17 个 .tsx 文件，icons.tsx 集中 SVG 组件库
- [x] TitleBar emoji 遗留修复（SunIcon/DesktopIcon 内联 SVG，themeIcon 真正渲染 JSX）
- [x] LoginView 玻璃美化：双层径向渐变背景 + 玻璃容器 + 品牌光晕阴影 + view-fade-in + 图标放大 + 副标题
- [x] build 验证（3 次 build 全通过，52 modules，无型别错误）
- [x] after 截图对比（登录页 + 主面板亮 + SecretsView 亮 + Overview 暗 + Secrets 暗）

### 验证结果
- 前端 build：tsc 无型别错误，vite 8.2.0 成功产出 web/dist（CSS 50.07 kB / JS 488.24 kB）
- 真实浏览器验证：登录页玻璃容器 + 品牌光晕 + 副标题「面板登录」生效；主面板 sidebar/card/stat-card 玻璃生效；SecretsView 改密码 card 在顶部第一個；暗色模式玻璃令牌双适配
- WS 连接正常（未登录时不空转重连，登录后正常连接）
- 退出登录按钮在 sidebar 底部生效

### commit
- aaedd96 `feat: 现代玻璃拟态美化 + emoji 换 SVG 图标`（27 files, +470/-114）
- e6b4dd2 `chore: gitignore playwright MCP artifacts + ui test screenshots`

### 美化实作不动的区块（已有完善设计）
- .btn（纯色填充型，玻璃会降低对比度）
- .empty / .empty-icon（透明容器 / 太小）
- .skeleton（shimmer 动画，玻璃会破坏）
- 纯箭头 → ← ↑ ↓（文字符号非装饰）
