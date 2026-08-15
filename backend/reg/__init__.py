# -*- coding: utf-8 -*-
"""reg — OpenAI/ChatGPT 账号注册功能（复刻自 mail-otp-server 注册引擎）

核心注册协议（chatgpt_core.py）：复用 codex_register-main 的 chatgpt.py 注册链路
（next-auth signin → authorize → OTP → email-otp/validate → sentinel create_account
→ accessToken/session_token 收尾），sentinel t/so 由纯 Python VM 生成
（sentinel_pure_vm.py），邮箱渠道支持 mailtm / 163（IMAP，凭据走环境变量注入）。

调度（engine.py）：事件环形缓冲 + 轮询接口（本前端无 SSE，3s 轮询增量），
线程内 stdout 转发为日志事件（线程本地，不污染 uvicorn 日志）。

落库（repo_accounts.py）：注册产出写入 reg_accounts 表（含密码/源邮箱/渠道），
成功账号同时写入本项目 tokens 表（source=register），可直接用于提链。
"""
from . import engine  # noqa: F401
from . import repo_accounts  # noqa: F401