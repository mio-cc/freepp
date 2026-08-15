# min-implant-v2

ChatGPT 订阅支付链路自动化研究项目(仅供学习研究)。

## 结构

```
backend/           FastAPI 后端 (提链引擎 / 直卡 / PayPal BA / hCaptcha)
frontend/          React + Vite 前端面板
web/               原生 JS 管理台
docs/              研究与架构文档
_archive_dev/      开发期测试脚本/抓包数据 (不随仓库发布)
```

## 快速开始

```bash
cd backend
pip install -r requirements.txt
cp config.example.yaml config.yaml    # 填入代理池凭据
cp ba_paypal/.env.example ba_paypal/.env   # 填入 SMSBower API key
uvicorn app:app --host 0.0.0.0 --port 8770
```

## 配置

- 代理池: `config.yaml` → `proxy.*`(QG 隧道 / 711 住宅池)
- 接码平台: `ba_paypal/.env` → `SMSBOWER_API_KEY`
- 环境变量(运行时注入,避免硬编码):
  - `PROXY_711_USER` / `PROXY_711_PASS` — 711 代理凭据
  - `MIN_TEST_CARD_NUMBER` / `MIN_TEST_CARD_EXP_MONTH` / `MIN_TEST_CARD_EXP_YEAR` / `MIN_TEST_CARD_CVC` — 测试卡

## 说明

- 本项目为研究用途,涉及第三方平台的自动化交互需遵守其服务条款与当地法律。
- 所有真实凭据(代理账号、API key、测试卡号、token 库)均不入库,请通过环境变量或本地配置文件注入。