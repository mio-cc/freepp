"""secrets_store.py — 前端可编辑的密钥/凭据集中存储 (离线开源项目)。

把原本散落在 环境变量 / .env / 硬编码常量 里的凭据集中到 secrets.json,
前端「密钥与凭据」页可读可写; 写回后注入 os.environ 并热重载相关模块常量,
使变更立即生效而无需重启。

存储位置: backend/secrets.json (与 ba_config.json 同级, 不进 git)。
写回模式参照 api/paypal.py:_save_ba_config (原子写 tmp + os.replace)。
env 注入参照 api/paypal.py:821 (os.environ["PAYPAL_CAPTCHA_BYPASS_MODE"])。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# secrets.json 与 ba_config.json 同级 (backend 根目录)
_SECRETS_FILE = Path(__file__).resolve().parent.parent / "secrets.json"

# ── 字段定义: 每组 key → 对应 os.environ 变量名 ──────────────────────
# 每个字段在 secrets.json 里存原值; 注入时若非空则覆盖 os.environ。
# 711 代理凭据 + api798 卡密路径 + SMS key + PayPal 反爬 env 高频项。
_SECTIONS: dict[str, dict[str, str]] = {
    "seven11": {
        "PROXY_711_HOST": "PROXY_711_HOST",
        "PROXY_711_PORT": "PROXY_711_PORT",
        "PROXY_711_USER": "PROXY_711_USER",
        "PROXY_711_PASS": "PROXY_711_PASS",
        "CLASH_PROXY": "CLASH_PROXY",
        "PROXY_711_RELAY_PORT": "PROXY_711_RELAY_PORT",
        "PROXY_711_CONNECT_REWRITE_HOSTS": "PROXY_711_CONNECT_REWRITE_HOSTS",
    },
    "api798": {
        "REG_API798_MAILBOXES": "REG_API798_MAILBOXES",
        "REG_API798_ENDPOINT": "REG_API798_ENDPOINT",
        "REG_API798_ENABLED": "REG_API798_ENABLED",
    },
    "sms": {
        "SMSBOWER_API_KEY": "SMSBOWER_API_KEY",
        "GRIZZLYSMS_API_KEY": "GRIZZLYSMS_API_KEY",
    },
    "paypal_antibot": {
        "PAYPAL_ROXY_API_KEY": "PAYPAL_ROXY_API_KEY",
        "PAYPAL_DATADOME_MODE": "PAYPAL_DATADOME_MODE",
        "PAYPAL_MTR_RUNTIME": "PAYPAL_MTR_RUNTIME",
        "PAYPAL_MTR_CHANNEL": "PAYPAL_MTR_CHANNEL",
        "PAYPAL_MTR_API_KEY": "PAYPAL_MTR_API_KEY",
        "PAYPAL_RISK_SIGNALS_MODE": "PAYPAL_RISK_SIGNALS_MODE",
        "PAYPAL_FINGERPRINT_SOURCE": "PAYPAL_FINGERPRINT_SOURCE",
        "PAYPAL_HCAPTCHA_TOKEN": "PAYPAL_HCAPTCHA_TOKEN",
    },
}

# 所有合法字段名 (用于校验入参)
_ALL_FIELDS: set[str] = set()
for _grp in _SECTIONS.values():
    _ALL_FIELDS.update(_grp.values())


def _default_data() -> dict[str, dict[str, str]]:
    """空默认结构 (启动首次无 secrets.json 时用)。"""
    return {sec: {f: "" for f in fields} for sec, fields in _SECTIONS.items()}


class SecretsStore:
    """secrets.json 单例存储: load / get / update / inject。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = _default_data()
        self.load()

    # ---- 持久化 ----
    def load(self) -> None:
        """从 secrets.json 读取 (不存在则用空默认)。"""
        try:
            if not _SECRETS_FILE.exists():
                return
            with open(_SECRETS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for sec, fields in _SECTIONS.items():
                    grp = raw.get(sec)
                    if isinstance(grp, dict):
                        for fld in fields:
                            v = grp.get(fld)
                            if isinstance(v, str):
                                self._data[sec][fld] = v
        except Exception:
            pass

    def _save(self) -> None:
        """原子写落盘 (tmp + os.replace)。"""
        try:
            tmp = str(_SECRETS_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _SECRETS_FILE)
        except OSError:
            pass

    # ---- 读取 ----
    def get_all(self) -> dict[str, dict[str, str]]:
        """返回全部字段原值 (前端编辑需看到原值, 不脱敏)。"""
        # 深拷贝避免外部修改内部状态
        return {sec: dict(grp) for sec, grp in self._data.items()}

    def get(self, section: str) -> dict[str, str]:
        return dict(self._data.get(section, {}))

    # ---- 写入 ----
    def update(self, section: str, fields: dict[str, Any]) -> dict[str, str]:
        """更新单组字段, 落盘, 注入 env, 热重载模块常量。

        只接受 _SECTIONS 里声明的 section + 字段; 非法字段忽略。
        """
        if section not in _SECTIONS:
            return {"ok": False, "error": f"未知密钥组: {section}"}
        allowed = _SECTIONS[section]
        changed: dict[str, str] = {}
        for fld, val in fields.items():
            if fld not in allowed:
                continue
            sval = "" if val is None else str(val)
            if self._data[section].get(fld) != sval:
                self._data[section][fld] = sval
                changed[fld] = sval
        if changed:
            self._save()
            self._inject_env(changed)
            self._hot_reload(changed)
        return {"ok": True, "section": section, "changed": list(changed.keys())}

    # ---- env 注入 ----
    def _inject_env(self, changed: dict[str, str]) -> None:
        """非空值覆盖 os.environ (空串则删除, 回落到模块默认)。"""
        for fld, val in changed.items():
            if val:
                os.environ[fld] = val
            else:
                os.environ.pop(fld, None)
        # SMS key 派生: PayPal 版接码 key 与全局同名约定 (ba_paypal/.env 模式)
        if "SMSBOWER_API_KEY" in changed:
            v = changed["SMSBOWER_API_KEY"]
            if v:
                os.environ["PAYPAL_SMSBOWER_API_KEY"] = v
            else:
                os.environ.pop("PAYPAL_SMSBOWER_API_KEY", None)
        if "GRIZZLYSMS_API_KEY" in changed:
            v = changed["GRIZZLYSMS_API_KEY"]
            if v:
                os.environ["PAYPAL_GRIZZLY_API_KEY"] = v
            else:
                os.environ.pop("PAYPAL_GRIZZLY_API_KEY", None)

    def _hot_reload(self, changed: dict[str, str]) -> None:
        """热重载已 import 的模块常量 (在 env 注入之后)。

        proxy_711.py 在 import 时把 env 读成模块级常量 (DEFAULT_711_* 等),
        直接 patch 模块属性使运行中进程立即生效。
        proxy_pool.Proxy711 实例在 __init__ 时把模块常量拷到实例属性,
        故需同步更新 proxy_pool.proxy711 实例才能让 /api/proxy/711/status 反映新值。
        """
        try:
            import core.proxy_711 as p711  # noqa: WPS433 (运行时动态 patch)
            if "PROXY_711_HOST" in changed:
                p711.DEFAULT_711_HOST = changed["PROXY_711_HOST"] or "global.rotgb.711proxy.com"
            if "PROXY_711_PORT" in changed:
                try:
                    p711.DEFAULT_711_PORT = int(changed["PROXY_711_PORT"] or "10000")
                except ValueError:
                    pass
            if "PROXY_711_USER" in changed:
                p711.DEFAULT_711_USER = changed["PROXY_711_USER"] or "YOUR_711_USER"
            if "PROXY_711_PASS" in changed:
                p711.DEFAULT_711_PASS = changed["PROXY_711_PASS"] or "YOUR_711_PASS"
            if "CLASH_PROXY" in changed:
                new_clash = changed["CLASH_PROXY"]
                p711.CLASH_CANDIDATES = (
                    new_clash,
                    "127.0.0.1:7890",
                    "127.0.0.1:7897",
                    "127.0.0.1:17897",
                )
            if "PROXY_711_RELAY_PORT" in changed:
                try:
                    rp = int(changed["PROXY_711_RELAY_PORT"] or "18794")
                    p711.RELAY_PORT = rp
                    p711._RELAY_PORT_CANDIDATES = (rp, 18794, 18793, 18792, 18795)
                except ValueError:
                    pass
            if "PROXY_711_CONNECT_REWRITE_HOSTS" in changed:
                hosts = changed["PROXY_711_CONNECT_REWRITE_HOSTS"]
                p711._CONNECT_IP_REWRITE_HOSTS = frozenset(
                    h.strip().lower()
                    for h in (hosts or "www.paypal.com").split(",")
                    if h.strip()
                )
            # 同步更新 proxy_pool 单例的 Proxy711 实例属性 (status 端点读这里)
            try:
                from core import proxy_pool as _pp
                inst = getattr(_pp.proxy_pool, "proxy711", None)
                if inst is not None:
                    if "PROXY_711_HOST" in changed:
                        inst.gateway_host = p711.DEFAULT_711_HOST
                    if "PROXY_711_PORT" in changed:
                        inst.gateway_port = p711.DEFAULT_711_PORT
                    if "PROXY_711_USER" in changed:
                        inst.default_user = p711.DEFAULT_711_USER
                    if "PROXY_711_PASS" in changed:
                        inst.default_pass = p711.DEFAULT_711_PASS
                    if "CLASH_PROXY" in changed:
                        inst.clash_candidates = p711.CLASH_CANDIDATES
                    if "PROXY_711_RELAY_PORT" in changed:
                        inst.relay_port = p711.RELAY_PORT
            except Exception:
                pass
        except Exception:
            pass

        # ba_paypal/config.py 的指纹源 / Roxy key (env 读取, 无模块常量需 patch;
        # 该模块在 import 时已读 env 到模块级 ROXY_API_KEY 等, 这里同步 patch)
        try:
            import ba_paypal.config as bpcfg  # noqa: WPS433
            if "PAYPAL_ROXY_API_KEY" in changed:
                bpcfg.ROXY_API_KEY = changed["PAYPAL_ROXY_API_KEY"]
            if "PAYPAL_DATADOME_MODE" in changed:
                bpcfg.DATADOME_MODE = changed["PAYPAL_DATADOME_MODE"] or "protocol"
            if "PAYPAL_MTR_RUNTIME" in changed:
                bpcfg.MTR_RUNTIME_MODE = changed["PAYPAL_MTR_RUNTIME"] or "python_generated"
            if "PAYPAL_MTR_CHANNEL" in changed:
                bpcfg.MTR_CHANNEL = changed["PAYPAL_MTR_CHANNEL"] or "iwc-mxo"
            if "PAYPAL_MTR_API_KEY" in changed:
                bpcfg.MTR_API_KEY = changed["PAYPAL_MTR_API_KEY"]
            if "PAYPAL_RISK_SIGNALS_MODE" in changed:
                bpcfg.RISK_SIGNALS_MODE = changed["PAYPAL_RISK_SIGNALS_MODE"] or "protocol"
            if "PAYPAL_FINGERPRINT_SOURCE" in changed:
                bpcfg.FINGERPRINT_SOURCE = changed["PAYPAL_FINGERPRINT_SOURCE"] or "random"
        except Exception:
            pass

        # api798 渠道: endpoint 热重载 (channel_api798 模块级常量)
        try:
            import reg.channel_api798 as _c798  # noqa: WPS433
            if "REG_API798_ENDPOINT" in changed:
                _c798._API_BASE = changed["REG_API798_ENDPOINT"] or "https://api798.com/get_code"
        except Exception:
            pass

    def inject_all_env(self) -> None:
        """启动时把所有非空字段注入 os.environ (供后续 import 的模块读到)。

        proxy_pool / proxy_711 在 app.py 模块级 import 时已读 env 为模块常量
        (早于 lifespan 调本方法), 故仅设 env 不够 — 需同步 patch 已 import 的
        模块常量 + proxy_pool 实例属性 (_hot_reload), 否则凭据停留在占位符。
        """
        all_changed: dict[str, str] = {}
        for sec, fields in _SECTIONS.items():
            for fld in fields:
                v = self._data.get(sec, {}).get(fld, "")
                if v:
                    all_changed[fld] = v
        if all_changed:
            self._inject_env(all_changed)
            self._hot_reload(all_changed)


# 全局单例
secrets_store = SecretsStore()
