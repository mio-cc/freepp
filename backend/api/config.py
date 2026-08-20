"""配置与账单模板路由。

REST:
- GET  /api/config             - 返回完整运行配置
- GET  /api/billing/templates  - 返回全部账单模板
- GET  /api/billing/countries  - 返回支持的国家列表
- POST /api/config/stage       - 更新单段配置 (前端可编辑)
- GET  /api/config/secrets     - 返回密钥/凭据 (secrets.json + config.yaml 标量)
- POST /api/config/secrets     - 更新密钥/凭据 (写 secrets.json + env 注入 + 热重载)
- POST /api/config/section     - 通用写回 config.yaml 顶层段 (server/stripe/tls/proxy/...)
"""
from __future__ import annotations

from fastapi import APIRouter

from core.config import settings
from core.billing import BILLING_TEMPLATES, ALL_COUNTRIES, PAYPAL_BLOCKED, AREA_CODES, GEO
from core.secrets_store import secrets_store

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
            "sess_time": settings.proxy_sess_time,
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
        # 以下为「密钥与凭据」页所需、原 GET /api/config 缺失的段 (补齐)
        "geo": {
            "enabled": settings.geo_cfg.get("enabled", True),
            "timeout": settings.geo_cfg.get("timeout", 10),
            "sources": settings.geo_cfg.get("sources", []),
        },
        "register_pool": {
            "base_url": settings.register_pool.get("base_url", ""),
            "timeout": settings.register_pool.get("timeout", 15),
        },
        "storage": {
            "db_path": settings.storage.get("db_path", "tokens.db"),
            "samples_dir": settings.storage.get("samples_dir", "samples"),
            "runs_dir": settings.storage.get("runs_dir", "runs"),
        },
        "logging": {
            "level": settings.logging_cfg.get("level", "INFO"),
            "json_logs": settings.logging_cfg.get("json_logs", False),
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
    for key in ("require_zero", "channel_check", "dual_init", "follow_checkout", "channel_probe"):
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
    if "checkout_mode" in body:
        cm = str(body["checkout_mode"] or "auto").strip().lower()
        valid_modes = ("auto", "host_inline", "host_no_inline", "cust_inline", "cust_no_inline")
        branch_cfg["checkout_mode"] = cm if cm in valid_modes else "auto"

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


# ── 密钥与凭据页 (secrets) ──────────────────────────────────────────

# POST /api/config/section 可写回的顶层段白名单 + 每段允许字段 + 类型转换
# 复用 update_stage_config 的 yaml load/dump/reload 写回模式
_SECTION_SCHEMA: dict[str, dict[str, type]] = {
    "server": {
        "host": str, "port": int, "max_concurrent_chains": int,
        "thread_pool_size": int, "chain_mode": str,
        "mock_success_rate": float, "mock_stage_min": float, "mock_stage_max": float,
    },
    "stripe": {
        "init_version": str, "runtime_version": str,
        "checkout_url": str, "approve_url": str, "pm_url": str,
        "init_url_tmpl": str, "update_url_tmpl": str, "confirm_url_tmpl": str, "poll_url_tmpl": str,
    },
    "tls": {
        "impersonate": str, "user_agent": str, "accept_language": str,
    },
    "proxy": {
        "default_pool": str, "health_check_interval": int,
        "max_concurrent_per_node": int, "sess_time": int,
    },
    "register_pool": {
        "base_url": str, "timeout": int,
    },
    "storage": {
        "db_path": str, "samples_dir": str, "runs_dir": str,
    },
    "geo": {
        "enabled": bool, "timeout": int, "sources": list,
    },
    "logging": {
        "level": str, "json_logs": bool,
    },
    "momo": {
        "enabled": bool, "connect_intercept": bool, "dns_fix": bool,
        "pm_inject": bool, "confirm_build": bool, "resolve_regex": bool,
    },
}


def _coerce(val, typ: type):
    """按 schema 类型转换入参值 (失败返回 None 表示跳过该字段)。

    注意: bool False 是合法值, 不能被当作"空"丢弃; 调用方区分 None(跳过) 与 False(写入)。
    """
    try:
        if typ is bool:
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("1", "true", "yes", "on", "y")
        if val is None or val == "":
            return None
        if typ is int:
            return int(val)
        if typ is float:
            return float(val)
        if typ is list:
            if isinstance(val, str):
                return [s.strip() for s in val.split(",") if s.strip()]
            return list(val)
        return str(val)
    except (ValueError, TypeError):
        return None


@router.post("/config/section")
async def update_config_section(body: dict):
    """通用写回 config.yaml 顶层段 (server/stripe/tls/proxy/register_pool/storage/geo/logging/momo)。

    body: {section: "server", fields: {host: "0.0.0.0", port: 8770, ...}}
    代理 QG 池凭据用 section="proxy" 下的 qg_super_pool/qg_resi_pool 子对象。
    """
    section = body.get("section")
    fields = body.get("fields")
    if not section or section not in _SECTION_SCHEMA:
        return {"ok": False, "error": f"无效段: {section}, 可选 {list(_SECTION_SCHEMA.keys())}"}
    if not isinstance(fields, dict):
        return {"ok": False, "error": "fields 必须是对象"}

    import yaml
    config_path = settings.path
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}

    sec_cfg = config_data.setdefault(section, {})
    if not isinstance(sec_cfg, dict):
        sec_cfg = {}
        config_data[section] = sec_cfg

    schema = _SECTION_SCHEMA[section]
    changed = {}
    for fld, val in fields.items():
        # proxy 段特例: qg_super_pool / qg_resi_pool 子对象 (含 auth_key/auth_pwd/host/port)
        if section == "proxy" and fld in ("qg_super_pool", "qg_resi_pool"):
            sub = val if isinstance(val, dict) else {}
            cur_sub = sec_cfg.setdefault(fld, {})
            if not isinstance(cur_sub, dict):
                cur_sub = {}
                sec_cfg[fld] = cur_sub
            for k in ("host", "port", "auth_key", "auth_pwd"):
                if k in sub and sub[k] is not None:
                    cv = sub[k]
                    if k == "port":
                        try:
                            cv = int(cv)
                        except (ValueError, TypeError):
                            continue
                    elif k != "host" and cv is not None:
                        cv = str(cv)
                    cur_sub[k] = cv
            changed[fld] = cur_sub
            continue

        if fld not in schema:
            continue
        fld_type = schema[fld]
        cv = _coerce(val, fld_type)
        # None 表示空值/转换失败: bool 跳过 (False 是合法值已返回), 其余跳过该字段
        if cv is None and not (fld_type is bool and isinstance(val, bool)):
            continue
        sec_cfg[fld] = cv
        changed[fld] = cv

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    settings.reload()
    return {"ok": True, "section": section, "changed": list(changed.keys())}


@router.get("/config/secrets")
async def get_secrets():
    """返回密钥/凭据页全部数据 (不脱敏 — 离线项目, 前端需展示原值供编辑)。

    secrets:  secrets.json 全字段 (711/api798/sms/paypal_antibot) 原值
    proxy_pools: config.yaml 代理池凭据原值 (qg_super_pool/qg_resi_pool, 不脱敏)
    """
    return {
        "ok": True,
        "secrets": secrets_store.get_all(),
        "proxy_pools": {
            "qg_super_pool": settings.qg_pool("qg_super_pool"),
            "qg_resi_pool": settings.qg_pool("qg_resi_pool"),
            "default_pool": settings.default_pool_name,
        },
    }


@router.post("/config/secrets")
async def update_secrets(body: dict):
    """更新密钥/凭据 (写 secrets.json + 注入 os.environ + 热重载模块常量)。

    body: {section: "seven11", fields: {PROXY_711_USER: "...", ...}}
    section ∈ {seven11, api798, sms, paypal_antibot}
    """
    section = body.get("section")
    fields = body.get("fields")
    if not isinstance(fields, dict):
        return {"ok": False, "error": "fields 必须是对象"}
    return secrets_store.update(section or "", fields)


@router.get("/config/email_domains")
async def get_email_domains():
    """返回邮箱域名池配置 (用户配置 + 内置默认)。"""
    from core.email_domains_store import email_domains_store
    return {"ok": True, **email_domains_store.get_all()}


@router.post("/config/email_domains")
async def update_email_domains(body: dict | None = None):
    """更新邮箱域名池配置。body: {by_country?: {US: [...]}, fallback?: [...], reset?: true}"""
    from core.email_domains_store import email_domains_store
    body = body or {}
    if body.get("reset"):
        return {"ok": True, **email_domains_store.reset()}
    return {"ok": True, **email_domains_store.update(
        by_country=body.get("by_country"),
        fallback=body.get("fallback"),
    )}
