# min-implant-v2

ChatGPT 订阅支付链路自动化研究项目(仅供学习研究, 请遵守目标平台服务条款与当地法律)。

## 目录结构

```
backend/                 FastAPI 后端 (提链引擎 / 直卡 / PayPal BA / hCaptcha)
  core/                  核心链路引擎 (chain / oaics_proto / bind_card / proxy)
  api/                   REST API 路由
  ba_paypal/             PayPal BA 授权四阶段流程 (flow.py) + hCaptcha mint 栈
    ba_fp_helpers/       Node 桥 (happy-dom / hsw PoW) — 需 npm install
    sentinel_assets/     OpenAI Sentinel 签名 SDK — 需 npm install
    paypal/              PayPal 流程模块 (flow / session / smsbower / identity_lib)
  config.yaml            ★ 本地配置 (gitignore, 用 config.example.yaml 复制)
frontend/                React 19 + Vite 8 前端面板 (源码, 需 npm install + build)
web/                     管理台静态资源 (index.html + static/ + dist/ 构建产物)
_archive_dev/            开发期测试脚本与抓包数据 (gitignore, 不随仓库发布)
运维.bat                 Windows 运维菜单 (启动/重启/日志/构建)
```

## 环境要求

| 依赖 | 版本 | 用途 | 必需 |
|---|---|---|---|
| Python | >=3.10 | 后端运行时 | ✅ |
| Node.js | >=18 (建议 20) | OpenAI Sentinel mint (Node/V8 sdk bridge)、前端构建 | ✅ |
| 代理池 | 见下 | 链路出口 (账单国/出口国必须对齐) | ✅ |
| SMSBower API key | 见下 | PayPal 授权 2FA 接码 | ⚠️ 仅 PayPal BA 线 |
| Clash/mihomo 本地代理 | — | 711 住宅池 relay 前置 (127.0.0.1:7890/7897) | ⚠️ 仅 711 池 |

> Playwright (可选): `backend/ba_paypal/paypal/recaptcha_solver.py` 需要, 默认走 HTTP 解
> 可 `pip install playwright` 启用备用路径。

## 一、安装

### 1. Python 依赖

```bash
cd backend
pip install -r requirements.txt
pip install -r ba_paypal/requirements.txt
```

### 2. Node 依赖 (三处)

```bash
cd backend/ba_paypal/ba_fp_helpers     && npm install
cd ../sentinel_assets                  && npm install
cd ../../..                            # 回项目根
cd frontend                            && npm install   # 仅需改前端时
```

> `ba_paypal/` 根目录还有一个 package.json (@msgpack/msgpack), 如 Node 桥报缺包
> 一并 `npm install`。

### 3. 配置文件

```bash
# 后端配置 (代理池/端口/链路参数)
cd backend
copy config.example.yaml config.yaml     # Windows
cp config.example.yaml config.yaml       # Linux/macOS

# SMSBower 接码平台 key (仅 PayPal BA 线需要)
cd ba_paypal
copy .env.example .env                   # Windows
cp .env.example .env                     # Linux/macOS
```

编辑 `config.yaml`, 至少填入代理池凭据 (见下)。

### 4. 前端构建 (可选, web/dist 已随仓库提供)

```bash
cd frontend
npm run build        # 产物输出到 ../web/dist, 后端直接服务
```

## 二、启动

### Windows

双击运行 `运维.bat`, 菜单选项:
- `1` 环境检查 (端口/健康/代理中继/日志)
- `2` 一键重启 (后端 + 前端构建)
- `4` 启动后端 (http://127.0.0.1:8770)

> 运维脚本自动探测 `python` / `node` (PATH 或 `PYTHON` / `NODE_BIN` 环境变量),
> 日志输出到 `%TEMP%\min-implant-v2\`。

### 手动

```bash
cd backend
python -m uvicorn app:app --host 0.0.0.0 --port 8770
# 打开 http://127.0.0.1:8770
```

## 三、所需外部资源详解

### 1. 代理池 (必需)

链路每一段都用独立出口 IP, 且**账单国必须与出口国一致** (否则 Stripe 400
`Billing country must match request country`)。项目支持三类代理源:

| 类型 | 配置位置 | 说明 |
|---|---|---|
| **QG 隧道代理** | `config.yaml → proxy.qg_super_pool / qg_resi_pool` | 主代理池, 连接串 `http://{auth_key}:{auth_pwd}:A{area}@host:port`, area 控制出口国家 |
| **711 住宅代理** | `config.yaml → proxy.proxy_711.enabled` + 环境变量 | 走本地 Clash/mihomo relay (7890/7897), sticky session 按国家, 通过 `PROXY_711_USER` / `PROXY_711_PASS` 注入 (不再硬编码) |
| **sing-box 节点** | `core/proxy_pool.py` 内置 | VLESS/Hysteria2 33 节点 (JP/HK/SG/US/KR/TW), 本地 relay 18077-18117 |

需要的东西:
- 一个 QG (或其他) 隧道代理账号: `auth_key` + `auth_pwd`, 支持按国家选出口
- (可选) 711 住宅代理账号: `USER` + `PASS` + 本地 Clash 客户端
- 代理可用性测试: 运维菜单 `1` 或直接看 `uvicorn.log` 的 `[proxy_711] smoke`

### 2. 接码平台 (SMSBower, 仅 PayPal BA 线)

PayPal 注册/2FA 需要手机验证码。项目对接 SMSBower:

```bash
# backend/ba_paypal/.env
SMSBOWER_API_KEY=你的key
PAYPAL_SMSBOWER_API_KEY=你的key
```

### 3. 中继/本地代理 (仅 711 池)

711 池链路: `client → 127.0.0.1:18794 (relay) → Clash 7890/7897 → 711 → 目标`。
需要本机运行 Clash 系客户端 (FlClash / Clash Verge), 且开启 mixed-port。

### 4. OpenAI 账号 token (运行原料)

链路消耗的是 ChatGPT 会话 token (access_token + session_token), 在面板的
"Token 导入" 中批量导入。**账号需要有促销资格** (plus-1-month-free) 才能走出
0 元链, 无资格账号压 0 无效。

## 四、环境变量一览

| 变量 | 默认 | 说明 |
|---|---|---|
| `PYTHON` | `python` | 运维脚本用的 Python 解释器 |
| `NODE_BIN` | `node` | 运维脚本用的 Node 可执行文件 |
| `SENTINEL_NODE` | `node` | Sentinel mint 调用的 Node |
| `PROXY_711_USER` | `YOUR_711_USER` | 711 代理用户名 |
| `PROXY_711_PASS` | `YOUR_711_PASS` | 711 代理密码 |
| `PROXY_711_RELAY_PORT` | `18794` | 711 relay 端口 |
| `MIN_TEST_CARD_NUMBER` | `4000000000000002` | 内置测试卡号 (占位) |
| `MIN_TEST_CARD_EXP_MONTH` | `12` | 测试卡月 |
| `MIN_TEST_CARD_EXP_YEAR` | `30` | 测试卡年 |
| `MIN_TEST_CARD_CVC` | `123` | 测试卡 CVC |
| `SMSBOWER_API_KEY` / `PAYPAL_SMSBOWER_API_KEY` | — | 接码平台 (ba_paypal/.env) |
| `MIN_OAICS_ATTESTATION` | — | 手动注入 OpenAI 前端部署证明 (跳过抓取) |
| `MIN_OAICS_P1` | — | 手动注入 Stripe hCaptcha P1 token |
| `MIN_OAICS_CUSTOMER` | — | 手动注入 Stripe customer id |
| `MIN_OAICS_SENTINEL` | `0` 时禁用 | Sentinel 头开关 |
| `PROXY_711_HOST` / `PROXY_711_PORT` | 711proxy.com:10000 | 711 网关覆盖 |

## 四·五、GPT 账号注册 (面板 "资源 → 账号注册")

内置 ChatGPT 账号注册功能 (`backend/reg/`)，协议：next-auth OAuth → OTP →
sentinel create_account → access/session token。成功账号自动写入 Token 库
(`source=register`)，可直接用于提链。

**内置邮箱渠道**：`mailtm` (零依赖在线 API，默认)。代理留空时自动启用
711 住宅中继。

**接入自定义邮箱渠道**：注册引擎提供扩展点，任意邮箱来源（IMAP / outlook
邮箱池 / 自建邮箱 / 临时邮箱 API）都可接入。在 `backend/app.py` 启动时调用：

```python
from reg import engine as reg_engine

def setup_my_mailbox(proxies, cancel_check):
    # 1) 取一个可用注册邮箱
    email = claim_mailbox()                 # 你的实现
    openai_password = "Aa1!xxxx"            # ≥12 位随机密码
    # 2) 返回取码器：轮询收件箱直到拿到 OpenAI OTP
    def fetch_code(timeout_sec=None, seen_ids=None, not_before=None):
        return wait_otp(email, timeout_sec)  # 你的实现
    return email, openai_password, fetch_code

reg_engine.register_email_channel("my_mailbox", setup_my_mailbox)
```

注册后渠道名 `my_mailbox` 自动出现在面板渠道下拉。`fetch_code` 约定与
`chatgpt_core.py` 内建渠道一致（`timeout_sec` / `seen_ids` / `not_before`
参数可选实现）。注册协议本身（OAuth/OTP/sentinel/create_account）与邮箱
来源完全解耦。

## 五、链路概览

项目内置 **16 个提链分支**, 均可在面板 "链路配置" 或 `config.yaml → chain.branches.<name>.stages`
调整七段出口 (checkout/init/update/provider/approve/poll/resolve) 与 OAICS 五段
(checkout/taxes/provider/confirm/resolve) 映射。

| 分支 | 渠道 | 账单国 (默认) | 产出 |
|---|---|---|---|
| `paypal` | PayPal | auto | `paypal.com/agreements/approve?ba_token=...` |
| `direct` | 直卡 | PH | 卡绑定 + 订阅验证 (SetupIntent 内联) |
| `momo` | MoMo | auto | `payment.momo.vn/pay/app` 跳转 |
| `pix` | PIX 二维码 | auto | PIX 支付二维码 |
| `ideal` | iDEAL | auto | iDEAL 银行跳转 |
| `upi` | UPI | auto | UPI 支付跳转 |
| `kakao` | Kakao Pay | auto | Kakao 支付跳转 |
| `blik` | BLIK | auto | BLIK 支付跳转 |
| `twint` | TWINT | auto | TWINT 支付跳转 |
| `bizum` | Bizum | auto | Bizum 支付跳转 |
| `gopay` | GoPay | auto | GoPay 支付跳转 |
| `qris` | QRIS | ID | QRIS 二维码 |
| `gcash` | GCash | PH | GCash 支付跳转 |
| `grabpay` | GrabPay | PH | GrabPay 支付跳转 |
| `naver_pay` | Naver Pay | auto | Naver Pay 支付跳转 |
| `grok` | Grok 链路 | auto | card 渠道提链 |

核心链路形态:
- **PayPal 提链 (paypal 分支)**: checkout → taxes → provider → confirm → resolve,
  产出 `paypal.com/agreements/approve?ba_token=...`
- **PayPal BA 授权 (ba_paypal 模块)**: DataDome → 建号 → 2FA → authorize, 产出 EUAT
- **直卡线 (direct 分支)**: 纯 HTTP 9 步 (checkout → SetupIntent 内联 → confirm → 订阅验证)