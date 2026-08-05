"""配置加载器：读取 config.yaml，提供全局 settings 单例。

支持环境变量 MIN_BACKEND_DIR 定位 config.yaml；缺失时回退到模块同目录。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover - pydantic 不可用时降级为简单 dict
    BaseModel = object  # type: ignore[assignment]


class StageConfig(BaseModel):
    countries: list[str] = []
    timeout: int = 10
    retry: int = 3
    poll_interval: float = 0.75
    max_polls: int = 40


# 提链分支: paypal(提炼) / momo / grok / pix / ideal / upi / kakao / blik / twint / direct(直卡)
# 各自独立七段/开关/token 库/产出
BRANCH_NAMES: list[str] = ["paypal", "momo", "grok", "pix", "ideal", "upi", "kakao", "blik", "twint", "direct"]

BRANCH_LABELS: dict[str, str] = {
    "paypal": "PayPal 提炼",
    "momo": "MoMo 提链",
    "grok": "Grok 链路",
    "pix": "PIX 二维码",
    "ideal": "iDEAL 提链",
    "upi": "UPI 提链",
    "kakao": "Kakao Pay 提链",
    "blik": "BLIK 提链",
    "twint": "TWINT 提链",
    "direct": "直卡提链",
}


class BranchConfig(BaseModel):
    label: str = ""
    channel: str = "paypal"          # 支付渠道校验目标: paypal / momo / card / link
    token_source: str = "stripe"     # token 库来源标签 (隔离 token 库)
    require_zero: bool = True        # 金额校验开关
    channel_check: bool = True       # 支付渠道校验开关 (payment_method_types 含目标渠道)
    dual_init: bool = False          # 双 init 开关 (init0 借道 US 拿渠道类型 -> init1 回本地验真)
    init0_ccs: list[str] = []        # 双 init: init0 国家优先列表 (借道出口)
    init1_ccs: list[str] = []        # 双 init: init1 国家优先列表 (验真出口)
    init_t_ccs: list[str] = []       # 双 init: init_t 过渡国家
    follow_checkout: bool = False    # 分段跟随: 除 update 外所有段跟随 checkout 段
    billing_country: str = "auto"    # 账单国: "auto"=跟随 checkout 段国家, 否则固定国家
    attempts: int = 8                # 总尝试次数 (每 Token 最大尝试轮数)
    stages: dict[str, StageConfig] = {}


def _find_config_path() -> Path:
    env = os.environ.get("MIN_CONFIG_PATH", "").strip()
    if env and Path(env).exists():
        return Path(env)
    here = Path(__file__).resolve().parent.parent  # backend/
    cand = here / "config.yaml"
    if cand.exists():
        return cand
    # 运行目录
    cand2 = Path.cwd() / "config.yaml"
    if cand2.exists():
        return cand2
    return cand


class Settings:
    """全局配置单例。raw 为完整 YAML dict，提供便捷属性访问。"""

    def __init__(self) -> None:
        self.path: Path = _find_config_path()
        self.raw: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                self.raw = yaml.safe_load(f) or {}
        else:
            self.raw = {}
        # 解析分段配置
        self._stages: dict[str, StageConfig] = {}
        stages_raw = (self.raw.get("chain") or {}).get("stages") or {}
        for name, sc in stages_raw.items():
            self._stages[name] = StageConfig(**(sc or {}))
        # 解析提链分支
        self._branches: dict[str, BranchConfig] = {}
        branches_raw = (self.raw.get("chain") or {}).get("branches") or {}
        for name in BRANCH_NAMES:
            raw_b = branches_raw.get(name) or {}
            if not isinstance(raw_b, dict):
                raw_b = {}
            b_stages: dict[str, StageConfig] = {}
            # paypal 分支缺省回退到顶层 chain.stages (历史兼容)
            stage_src = raw_b.get("stages") if isinstance(raw_b.get("stages"), dict) else (stages_raw if name == "paypal" else {})
            for sname, sc in stage_src.items():
                b_stages[sname] = StageConfig(**(sc or {}))
            self._branches[name] = BranchConfig(
                label=str(raw_b.get("label") or BRANCH_LABELS.get(name, name)),
                channel=str(raw_b.get("channel") or ("paypal" if name == "paypal" else name if name in ("momo",) else "card")),
                token_source=str(raw_b.get("token_source") or ("stripe" if name == "paypal" else name)),
                require_zero=bool(raw_b.get("require_zero", True)),
                channel_check=bool(raw_b.get("channel_check", True)),
                dual_init=bool(raw_b.get("dual_init", False)),
                init0_ccs=list(raw_b.get("init0_ccs") or []),
                init1_ccs=list(raw_b.get("init1_ccs") or []),
                init_t_ccs=list(raw_b.get("init_t_ccs") or []),
                follow_checkout=bool(raw_b.get("follow_checkout", False)),
                billing_country=str(raw_b.get("billing_country") or "auto"),
                attempts=int(raw_b.get("attempts") or 8),
                stages=b_stages,
            )

    # ---- server ----
    @property
    def host(self) -> str:
        return (self.raw.get("server") or {}).get("host", "0.0.0.0")

    @property
    def port(self) -> int:
        return int((self.raw.get("server") or {}).get("port", 8770))

    @property
    def max_concurrent_chains(self) -> int:
        return int((self.raw.get("server") or {}).get("max_concurrent_chains", 10))

    @property
    def thread_pool_size(self) -> int:
        return int((self.raw.get("server") or {}).get("thread_pool_size", 20))

    @property
    def chain_mode(self) -> str:
        return (self.raw.get("server") or {}).get("chain_mode", "mock")

    @property
    def mock_success_rate(self) -> float:
        return float((self.raw.get("server") or {}).get("mock_success_rate", 0.6))

    @property
    def mock_stage_min(self) -> float:
        return float((self.raw.get("server") or {}).get("mock_stage_min", 0.4))

    @property
    def mock_stage_max(self) -> float:
        return float((self.raw.get("server") or {}).get("mock_stage_max", 1.6))

    # ---- chain ----
    @property
    def require_zero(self) -> bool:
        return bool((self.raw.get("chain") or {}).get("require_zero", True))

    @property
    def auto_billing(self) -> bool:
        return bool((self.raw.get("chain") or {}).get("auto_billing", True))

    @property
    def token_min_interval_ms(self) -> int:
        return int((self.raw.get("chain") or {}).get("token_min_interval_ms", 500))

    @property
    def fail_cooldown_sec(self) -> int:
        return int((self.raw.get("chain") or {}).get("fail_cooldown_sec", 60))

    def stage(self, name: str) -> StageConfig:
        return self._stages.get(name) or StageConfig()

    def branch(self, name: str = "paypal") -> BranchConfig:
        """按提链分支返回独立七段配置。未知分支回退 paypal。"""
        return self._branches.get(name) or self._branches.get("paypal") or BranchConfig()

    def branch_stage(self, branch: str, name: str) -> StageConfig:
        """分支内单段配置；分支未定义该段时回退顶层/默认。"""
        b = self.branch(branch)
        return b.stages.get(name) or self._stages.get(name) or StageConfig()

    @property
    def branch_names(self) -> list[str]:
        return list(BRANCH_NAMES)

    def branch_dict(self, name: str) -> dict[str, Any]:
        """分支完整配置 dict（供 /api/config 输出）。"""
        b = self.branch(name)
        stages = {}
        for sname in self.stage_names:
            sc = b.stages.get(sname) or self._stages.get(sname) or StageConfig()
            stages[sname] = {
                "countries": sc.countries,
                "timeout": sc.timeout,
                "retry": sc.retry,
                "poll_interval": sc.poll_interval,
                "max_polls": sc.max_polls,
            }
        return {
            "name": name,
            "label": b.label,
            "channel": b.channel,
            "token_source": b.token_source,
            "require_zero": b.require_zero,
            "channel_check": b.channel_check,
            "dual_init": b.dual_init,
            "init0_ccs": b.init0_ccs,
            "init1_ccs": b.init1_ccs,
            "init_t_ccs": b.init_t_ccs,
            "follow_checkout": b.follow_checkout,
            "billing_country": b.billing_country,
            "attempts": b.attempts,
            "stages": stages,
        }

    @property
    def stage_names(self) -> list[str]:
        # 7 段全部展示
        return ["checkout", "init", "update", "provider", "approve", "poll", "resolve"]

    # ---- proxy ----
    @property
    def proxy_cfg(self) -> dict[str, Any]:
        return self.raw.get("proxy") or {}

    def qg_pool(self, name: str = "qg_resi_pool") -> dict[str, Any]:
        pools = self.proxy_cfg
        key = name if name.startswith("qg_") else f"qg_{name}_pool"
        return pools.get(key) or {}

    @property
    def default_pool_name(self) -> str:
        return self.proxy_cfg.get("default_pool", "qg_resi_pool")

    @property
    def health_check_interval(self) -> int:
        return int(self.proxy_cfg.get("health_check_interval", 30))

    @property
    def max_concurrent_per_node(self) -> int:
        return int(self.proxy_cfg.get("max_concurrent_per_node", 3))

    # ---- stripe / tls ----
    @property
    def stripe(self) -> dict[str, Any]:
        return self.raw.get("stripe") or {}

    @property
    def tls(self) -> dict[str, Any]:
        return self.raw.get("tls") or {}

    @property
    def storage(self) -> dict[str, Any]:
        return self.raw.get("storage") or {}

    @property
    def db_path(self) -> str:
        p = self.storage.get("db_path", "tokens.db")
        # 相对路径基于 backend 目录
        if not os.path.isabs(p):
            p = str(self.path.parent / p)
        return p

    @property
    def momo_cfg(self) -> dict[str, Any]:
        return self.raw.get("momo") or {}

    # ---- web 静态目录 ----
    @property
    def web_dir(self) -> Path:
        return self.path.parent.parent / "web"

    @property
    def backend_dir(self) -> Path:
        return self.path.parent


settings = Settings()
