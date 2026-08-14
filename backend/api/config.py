"""配置与账单模板路由。

REST:
- GET  /api/config             - 返回完整运行配置
- GET  /api/billing/templates  - 返回全部账单模板
- GET  /api/billing/countries  - 返回支持的国家列表
- POST /api/config/stage       - 更新单段配置 (前端可编辑)
"""
from __future__ import annotations

from fastapi import APIRouter

from core.config import settings
from core.billing import BILLING_TEMPLATES, ALL_COUNTRIES, PAYPAL_BLOCKED, AREA_CODES, GEO

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config():
    """返回完整运行配置（前端设置页只读展示 + 可编辑段配置）。"""
    raw = settings.raw
    chain = raw.get("chain") or {}

    # 构建分段配置
    stages = {}
    for name in settings.stage_names:
        sc = settings.stage(name)
        stages[name] = {
            "countries": sc.countries,
            "timeout": sc.timeout,
            "retry": sc.retry,
            "poll_interval": sc.poll_interval,
            "max_polls": sc.max_polls,
        }

    return {
        "ok": True,
        "server": {
            "host": settings.host,
            "port": settings.port,
            "max_concurrent_chains": settings.max_concurrent_chains,
            "thread_pool_size": settings.thread_pool_size,
            "chain_mode": settings.chain_mode,
            "mock_success_rate": settings.mock_success_rate,
            "mock_stage_min": settings.mock_stage_min,
            "mock_stage_max": settings.mock_stage_max,
        },
        "chain": {
            "require_zero": settings.require_zero,
            "auto_billing": settings.auto_billing,
            "token_min_interval_ms": settings.token_min_interval_ms,
            "fail_cooldown_sec": settings.fail_cooldown_sec,
            "stages": stages,
            "branches": {name: settings.branch_dict(name) for name in settings.branch_names},
        },
        "stripe": {
            **settings.stripe,
        },
        "tls": {
            **settings.tls,
        },
        "proxy": {
            "default_pool": settings.default_pool_name,
            "health_check_interval": settings.health_check_interval,
            "max_concurrent_per_node": settings.max_concurrent_per_node,
            "qg_super_pool": _mask_pool(settings.qg_pool("qg_super_pool")),
            "qg_resi_pool": _mask_pool(settings.qg_pool("qg_resi_pool")),
            "proxy_711": settings.proxy_cfg.get("proxy_711", {}),
        },
        "momo": {
            "enabled": settings.momo_cfg.get("enabled", False),
            "patches": [
                {"name": "connect_intercept", "desc": "L1: 拦截 api.stripe.com CONNECT",
                 "enabled": settings.momo_cfg.get("connect_intercept", True)},
                {"name": "dns_fix", "desc": "L2: Clash fake-ip DoH 重解析",
                 "enabled": settings.momo_cfg.get("dns_fix", True)},
                {"name": "pm_inject", "desc": "L3: payment_method 注入",
                 "enabled": settings.momo_cfg.get("pm_inject", True)},
                {"name": "confirm_build", "desc": "L4: confirm payload 构造",
                 "enabled": settings.momo_cfg.get("confirm_build", True)},
                {"name": "resolve_regex", "desc": "L5: MoMo 支付 URL 正则",
                 "enabled": settings.momo_cfg.get("resolve_regex", True)},
            ],
        },
        "paypal": {
            "ba_url_pattern": "https://www.paypal.com/agreements/approve?ba_token=...",
            "pm_redirect_pattern": "https://pm-redirects.stripe.com/authorize/...",
            "blocked_countries": sorted(PAYPAL_BLOCKED),
            "success_criteria": [
                "init.invoice.amount_due == 0 (零金额)",
                "redirect 匹配 pm-redirects.stripe.com/authorize/",
                "最终 URL 匹配 paypal.com/agreements/approve?ba_token=",
            ],
        },
    }


def _mask_pool(pool: dict) -> dict:
    """掩码代理池凭据。"""
    if not pool:
        return {}
    return {
        "host": pool.get("host", ""),
        "port": pool.get("port", 0),
        "auth_key": _mask(pool.get("auth_key", "")),
        "auth_pwd": _mask(pool.get("auth_pwd", "")),
    }


def _mask(s: str) -> str:
    if not s or len(s) <= 4:
        return "****"
    return s[:2] + "****" + s[-2:]


@router.get("/billing/templates")
async def get_billing_templates():
    """返回全部账单模板列表。"""
    templates = []
    for country in sorted(BILLING_TEMPLATES.keys()):
        t = BILLING_TEMPLATES[country]
        geo = GEO.get(country, ("", "", ""))
        templates.append({
            "country": country,
            "name": t.get("name", ""),
            "city": t.get("city", ""),
            "state": t.get("state", ""),
            "postal_code": t.get("postal_code", ""),
            "line1": t.get("line1", ""),
            "currency": geo[2] if geo else "",
            "area_code": AREA_CODES.get(country, 0),
        })
    return {"ok": True, "templates": templates, "total": len(templates)}


@router.get("/billing/countries")
async def get_billing_countries():
    """返回支持的国家列表（排除黑名单）。"""
    countries = []
    for c in ALL_COUNTRIES:
        geo = GEO.get(c, ("", "", ""))
        countries.append({
            "code": c,
            "capital": geo[0] if geo else "",
            "currency": geo[2] if geo else "",
            "area_code": AREA_CODES.get(c, 0),
        })
    return {"ok": True, "countries": countries, "blocked": sorted(PAYPAL_BLOCKED)}


@router.post("/config/stage")
async def update_stage_config(body: dict):
    """更新单段配置（写入 config.yaml）。"""
    stage_name = body.get("stage")
    if not stage_name or stage_name not in settings.stage_names:
        return {"ok": False, "error": f"无效段名: {stage_name}"}

    import yaml
    config_path = settings.path
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    chain_cfg = config_data.setdefault("chain", {})
    stages_cfg = chain_cfg.setdefault("stages", {})
    stage_cfg = stages_cfg.setdefault(stage_name, {})

    if "countries" in body:
        countries = body["countries"]
        if isinstance(countries, str):
            countries = [c.strip().upper() for c in countries.split(",") if c.strip()]
        stage_cfg["countries"] = countries
    if "timeout" in body:
        stage_cfg["timeout"] = int(body["timeout"])
    if "retry" in body:
        stage_cfg["retry"] = int(body["retry"])
    if "poll_interval" in body:
        stage_cfg["poll_interval"] = float(body["poll_interval"])
    if "max_polls" in body:
        stage_cfg["max_polls"] = int(body["max_polls"])

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    settings.reload()
    return {"ok": True, "stage": stage_name, "config": stage_cfg}


@router.post("/config/branch")
async def update_branch_config(body: dict):
    """更新提链分支整体配置（开关 + 七段），写入 config.yaml。"""
    from core.config import BRANCH_NAMES, StageConfig

    branch_name = body.get("branch")
    if not branch_name or branch_name not in BRANCH_NAMES:
        return {"ok": False, "error": f"无效分支: {branch_name}, 可选 {BRANCH_NAMES}"}

    import yaml
    config_path = settings.path
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    chain_cfg = config_data.setdefault("chain", {})
    branches_cfg = chain_cfg.setdefault("branches", {})
    branch_cfg = branches_cfg.setdefault(branch_name, {})
    if not isinstance(branch_cfg, dict):
        branch_cfg = {}
        branches_cfg[branch_name] = branch_cfg

    # oaics 五段子配置
    # 2026-08-13 废弃: 链路已改为跟随七段映射 (oaics 不再参与决策), 保留写入仅为
    # 旧前端/API 回显兼容; 新前端 OAICS 卡片为只读展示, 不会调用此分支。
    if "oaics" in body:
        from core.config import OAICS_STAGE_NAMES

        oaics = body["oaics"]
        if isinstance(oaics, dict):
            oaics_cfg = branch_cfg.setdefault("oaics", {})
            if not isinstance(oaics_cfg, dict):
                oaics_cfg = {}
                branch_cfg["oaics"] = oaics_cfg
            for key in ("label", "channel", "token_source"):
                if key in oaics:
                    oaics_cfg[key] = str(oaics[key])
            for key in ("require_zero", "channel_check", "follow_checkout"):
                if key in oaics:
                    oaics_cfg[key] = bool(oaics[key])
            if "billing_country" in oaics:
                bc = str(oaics["billing_country"] or "auto").strip().upper()
                oaics_cfg["billing_country"] = "auto" if bc in ("AUTO", "") else bc
            if "attempts" in oaics:
                oaics_cfg["attempts"] = max(1, int(oaics["attempts"]))
            stages = oaics.get("stages")
            if isinstance(stages, dict):
                cur_stages = oaics_cfg.setdefault("stages", {})
                if not isinstance(cur_stages, dict):
                    cur_stages = {}
                    oaics_cfg["stages"] = cur_stages
                for stage_name, patch in stages.items():
                    if stage_name not in OAICS_STAGE_NAMES or not isinstance(patch, dict):
                        continue
                    sc = cur_stages.setdefault(stage_name, {})
                    if "countries" in patch:
                        val = patch["countries"]
                        if isinstance(val, str):
                            val = [c.strip().upper() for c in val.split(",") if c.strip()]
                        sc["countries"] = list(val or [])
                    for key in ("timeout", "retry"):
                        if key in patch:
                            sc[key] = int(patch[key])
                    if stage_name == "poll":
                        if "poll_interval" in patch:
                            sc["poll_interval"] = float(patch["poll_interval"])
                        if "max_polls" in patch:
                            sc["max_polls"] = int(patch["max_polls"])

    # 标量开关
    for key in ("label", "channel", "token_source"):
        if key in body:
            branch_cfg[key] = str(body[key])
    for key in ("require_zero", "channel_check", "dual_init", "follow_checkout"):
        if key in body:
            branch_cfg[key] = bool(body[key])
    for key in ("init0_ccs", "init1_ccs", "init_t_ccs"):
        if key in body:
            val = body[key]
            if isinstance(val, str):
                val = [c.strip().upper() for c in val.split(",") if c.strip()]
            branch_cfg[key] = list(val or [])
    if "billing_country" in body:
        bc = str(body["billing_country"] or "auto").strip().upper()
        branch_cfg["billing_country"] = "auto" if bc in ("AUTO", "") else bc
    if "attempts" in body:
        branch_cfg["attempts"] = max(1, int(body["attempts"]))

    # 七段配置
    stages = body.get("stages")
    if isinstance(stages, dict):
        cur_stages = branch_cfg.setdefault("stages", {})
        if not isinstance(cur_stages, dict):
            cur_stages = {}
            branch_cfg["stages"] = cur_stages
        for stage_name, patch in stages.items():
            if stage_name not in settings.stage_names or not isinstance(patch, dict):
                continue
            sc = cur_stages.setdefault(stage_name, {})
            if "countries" in patch:
                val = patch["countries"]
                if isinstance(val, str):
                    val = [c.strip().upper() for c in val.split(",") if c.strip()]
                sc["countries"] = list(val or [])
            for key in ("timeout", "retry"):
                if key in patch:
                    sc[key] = int(patch[key])
            if stage_name == "poll":
                if "poll_interval" in patch:
                    sc["poll_interval"] = float(patch["poll_interval"])
                if "max_polls" in patch:
                    sc["max_polls"] = int(patch["max_polls"])

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    settings.reload()
    return {"ok": True, "branch": branch_name, "config": settings.branch_dict(branch_name)}
