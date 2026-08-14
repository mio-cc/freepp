"""PayPal BA 支付授权 API 路由。

提供 BA 授权记录查询、单条/批量授权启动、授权配置管理等接口。
授权队列由 core.ba_queue 维护: 提链成功后自动导入, 重启后从
success_inventory (paypal 渠道) 种子回填。

国家联动 (BA_COUNTRY_ALIGN_PLAN_20260812):
  - 授权段国家默认跟随队列 record.country (提链段 billing_country)
  - config.identity_country / exit_country 可覆盖; follow_chain_country=False 时用显式国家
  - 每条授权任务独立 country_context, per-task config 深拷贝快照
  - 代理按国家从 proxy_pool 选 (711/sing-box/QG), 启动前 geo 实测校验
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.ba_queue import (
    add as ba_add,
    bulk_remove as ba_bulk_remove,
    clear as ba_clear,
    count as ba_count,
    extract_ba_token,
    get as ba_get,
    import_from_url,
    list_records,
    mark_stale as ba_mark_stale,
    remove as ba_remove,
    retry as ba_retry,
    try_start as ba_try_start,
    update as ba_update,
)
from core.token_store import token_store

router = APIRouter(prefix="/api/paypal", tags=["paypal"])

_BA_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ba_config.json"
)

_ba_config: dict[str, Any] = {
    "sms_provider": "smsbower",
    "sms_price": "0.5",
    "sms_price_min": "0",
    "sms_max_attempts": 12,
    "sms_timeout": 15,
    "exit_country": "BR",
    "identity_country": "",
    "sms_country": "",
    "proxy_type": "711_sticky",
    "captcha_strategy": "frontend_disable",
    "buyer_mode": "elevation",
    "max_retries": 3,
    "max_flow_attempts": 2,
    "follow_chain_country": True,
    "fail_fast_geo": True,
    "max_concurrent": 3,
    "flow_timeout_s": 120,
}


def _load_ba_config() -> None:
    """启动/模块加载时从 ba_config.json 恢复上次配置 (不存在则用默认)。"""
    global _ba_config
    try:
        if not os.path.exists(_BA_CONFIG_FILE):
            return
        with open(_BA_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _ba_config.update({k: v for k, v in data.items() if v is not None})
    except Exception:
        pass


def _save_ba_config() -> None:
    """配置变更立即落盘, 下次启动/刷新自动恢复。"""
    try:
        tmp = _BA_CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ba_config, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _BA_CONFIG_FILE)
    except OSError:
        pass


_load_ba_config()

# ---- 授权段并发闸门 (独立于提链段 max_concurrent_chains) ----
_ba_throttle_lock = threading.Lock()
_ba_running_count = 0


def _merged_ba_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """授权 config 合并: 显式字段覆盖, 缺省回落 _ba_config 默认值。

    历史问题: `req.config or _ba_config` 在前端只传部分字段时不合并默认,
    导致 buyer_mode 等回落硬编码旧值 (original / 0.02 等)。
    """
    merged = dict(_ba_config)
    if isinstance(raw, dict):
        merged.update(raw)
    return merged


def _ba_concurrency_cap(cfg: dict[str, Any] | None = None) -> int:
    """并发上限: per-task config 优先, 其次全局 _ba_config, 默认 3。"""
    try:
        if cfg is not None and cfg.get("max_concurrent"):
            return max(1, int(cfg.get("max_concurrent")))
    except Exception:
        pass
    try:
        return max(1, int(_ba_config.get("max_concurrent") or 3))
    except Exception:
        return 3


def _ba_acquire_slot(cap: int | None = None) -> bool:
    """尝试占用一个并发槽。cap 为空时按 _ba_config 计算。"""
    global _ba_running_count
    limit = cap if cap is not None else _ba_concurrency_cap()
    with _ba_throttle_lock:
        if _ba_running_count >= limit:
            return False
        _ba_running_count += 1
        return True


def _ba_release_slot() -> None:
    global _ba_running_count
    with _ba_throttle_lock:
        _ba_running_count = max(0, _ba_running_count - 1)


class BAAuthorizeRequest(BaseModel):
    ba_token: str
    config: dict[str, Any] | None = None


class BABatchRequest(BaseModel):
    ba_tokens: list[str]
    config: dict[str, Any] | None = None


class BAConfigUpdate(BaseModel):
    sms_provider: str | None = None
    sms_api_key: str | None = None
    sms_price: str | None = None
    sms_price_min: str | None = None
    sms_max_attempts: int | None = None
    sms_timeout: int | None = None
    exit_country: str | None = None
    identity_country: str | None = None
    sms_country: str | None = None
    proxy_type: str | None = None
    captcha_strategy: str | None = None
    buyer_mode: str | None = None
    max_retries: int | None = None
    max_flow_attempts: int | None = None
    follow_chain_country: bool | None = None
    fail_fast_geo: bool | None = None
    max_concurrent: int | None = None
    flow_timeout_s: int | None = None


class BAImportRequest(BaseModel):
    text: str | None = None
    urls: list[str] | None = None
    email: str = ""
    country: str = ""
    source: str = "manual"


class BARetryRequest(BaseModel):
    ba_tokens: list[str]
    config: dict[str, Any] | None = None


class BADeleteRequest(BaseModel):
    ba_tokens: list[str]


class BAClearRequest(BaseModel):
    status: str | None = None


def _extract_ba_token(url: str) -> str:
    return extract_ba_token(url)


def _record_to_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "ba_token": r.get("ba_token", ""),
        "email": r.get("email", ""),
        "approve_url": r.get("approve_url", ""),
        "status": r.get("status", "pending"),
        "step": r.get("step", "submit_email"),
        "country": r.get("country", ""),
        "identity_country": r.get("identity_country", ""),
        "proxy_country": r.get("proxy_country", ""),
        "geo_country": r.get("geo_country", ""),
        "chain_id": r.get("chain_id", ""),
        "source": r.get("source", ""),
        "captcha_type": r.get("captcha_type", ""),
        "sms_phone": r.get("sms_phone", ""),
        "sms_price": r.get("sms_price", 0),
        "sms_provider_id": r.get("sms_provider_id", ""),
        "last_msg": r.get("last_msg", ""),
        "last_level": r.get("last_level", ""),
        "error": r.get("error", ""),
        "created_at": r.get("created_at", 0),
        "updated_at": r.get("updated_at", 0),
    }


def _resolve_auth_country(record: dict[str, Any], cfg: dict[str, Any]) -> str:
    """授权段国家解析: follow_chain_country(默认) 取 record.country, 否则显式 config。"""
    rec_country = str((record or {}).get("country") or "").strip().upper()
    explicit = str(
        cfg.get("identity_country") or cfg.get("exit_country") or ""
    ).strip().upper()
    follow = bool(cfg.get("follow_chain_country", True))
    if follow and rec_country:
        return rec_country
    return explicit or rec_country or "BR"


def _pick_ba_proxy(country: str, cfg: dict[str, Any]) -> tuple[str, str]:
    """代理选择: 显式 proxy > proxy_pool.pick_for_stage(country) > 空(直连, 会被 geo 校验拦)。

    proxy_type 配置项映射到代理源偏好:
      - "711" / "711_sticky" (默认): proxy_pool 内部 711 优先
      - "qg": QG 隧道优先 (proxy_pool 无对应开关时回落默认)
      - "singbox": sing-box 节点优先 (回落默认)
    显式 proxy URL 时忽略 proxy_type。
    """
    explicit = str(cfg.get("proxy") or os.environ.get("MIN_BA_PROXY", "") or "").strip()
    if explicit:
        return explicit, "explicit"
    try:
        from core.proxy_pool import proxy_pool
        proxy_type = str(cfg.get("proxy_type") or "711_sticky").strip().lower()
        url = proxy_pool.pick_for_stage("resolve", country, source=proxy_type)
        if url:
            return url, "auto"
    except Exception:
        pass
    return "", "none"


def _geo_precheck(proxy: str, country: str) -> dict[str, Any]:
    """授权启动前实测代理出口国家。返回 {ok, country, confidence, error}。"""
    if not proxy:
        return {"ok": False, "country": "", "confidence": 0.0, "error": "no_proxy"}
    try:
        from core.geo_probe import probe_country
        probe = probe_country(proxy=proxy)
    except Exception as exc:
        return {"ok": False, "country": "", "confidence": 0.0, "error": f"geo_probe_exc:{exc}"}
    actual = str(probe.get("country") or "").upper()
    confidence = probe.get("confidence") or 0.0
    if not probe.get("ok") or not actual:
        return {"ok": False, "country": actual, "confidence": confidence,
                "error": str(probe.get("error") or "geo_probe_failed")}
    return {
        "ok": actual == country,
        "country": actual,
        "confidence": confidence,
        "error": "" if actual == country else "geo_mismatch",
    }


def _resolve_sms_country_code(raw: str, ctx) -> tuple[str, str]:
    """接码国家解析: 字母国家码 → SMSBower 数字国家码 + 手机国码。

    sms_country 可能来自前端 (字母 ISO2, 如 "TH") 或历史配置 (数字码, 如 "34"):
    - 数字码: 原样透传, phone_cc 跟随授权国上下文
    - 字母码: 走 country_profile 转数字码, phone_cc 跟随该接码国 (号码前缀须与接码国一致)
    - 空: 默认跟随授权国上下文
    """
    from ba_paypal.paypal.country_profile import country_context as _ctx
    from ba_paypal.paypal.country_profile import smsbower_country_id as _sms_id
    raw = str(raw or "").strip()
    if not raw:
        return ctx.sms_country_id, ctx.phone_country
    if raw.isdigit():
        return raw, ctx.phone_country
    cc = raw.upper()
    try:
        sub_ctx = _ctx(cc)
        return sub_ctx.sms_country_id, sub_ctx.phone_country
    except Exception:
        return _sms_id(cc), ctx.phone_country


def _build_sms_provider(country: str, cfg: dict[str, Any]):
    """按国家上下文构造 SMSBower provider (接码国家 + 手机国码 + 价格上限)。

    目前仅实现 smsbower (sms_activate/5sim 等选项前端将置灰禁用)。
    """
    provider_name = str(cfg.get("sms_provider") or "smsbower").strip().lower()
    if provider_name not in ("smsbower", "sms_bower", ""):
        return None, f"unsupported_sms_provider:{provider_name} (仅支持 smsbower)"
    from ba_paypal.paypal.country_profile import country_context as _ctx
    ctx = _ctx(country)
    try:
        from ba_paypal.paypal.smsbower import build_smsbower_provider as _build
        api_key = str(cfg.get("sms_api_key") or "").strip() or None
        provider = _build(enabled=True, api_key=api_key)
        if provider is None:
            return None, "smsbower_disabled"
        provider.country, provider.phone_cc = _resolve_sms_country_code(
            cfg.get("sms_country"), ctx
        )
        try:
            price = float(str(cfg.get("sms_price") or "0.5"))
            if price > 0:
                provider.max_price = price
        except Exception:
            pass
        try:
            price_min = float(str(cfg.get("sms_price_min") or "0"))
            if price_min > 0:
                provider.min_price = price_min
        except Exception:
            pass
        try:
            max_attempts = int(cfg.get("sms_max_attempts") or 12)
            if max_attempts > 0:
                provider.max_attempts = max(1, max_attempts)
        except Exception:
            pass
        try:
            sms_timeout = float(str(cfg.get("sms_timeout") or "15"))
            if sms_timeout >= 1:
                provider.wait_seconds = sms_timeout
        except Exception:
            pass
        return provider, ""
    except Exception as exc:
        return None, f"sms_provider_exc:{exc}"


async def _run_authorize_task(ba_token: str, cfg: dict[str, Any]) -> None:
    """单条授权后台任务 (country 随 record / ctx 组装)。"""
    from ba_paypal import BAAuthorizer

    record = ba_get(ba_token)
    if record is None:
        return
    country = _resolve_auth_country(record, cfg)
    try:
        from ba_paypal.paypal.country_profile import country_context
        ctx = country_context(country)
    except Exception as exc:
        ba_update(ba_token, status="failed", error=f"unsupported_country:{exc}")
        return

    proxy, proxy_source = _pick_ba_proxy(country, cfg)
    # 显式代理优先跳 geo 校验; 自动代理按要求校验 (fail_fast_geo 默认 true)
    fail_fast = bool(cfg.get("fail_fast_geo", True))
    geo = None
    if proxy_source == "auto" or not proxy:
        geo = _geo_precheck(proxy, country)
        if not geo.get("ok"):
            ba_update(
                ba_token,
                status="failed",
                error=geo.get("error") or "geo_mismatch",
                proxy_country=geo.get("country") or "",
            )
            return
    ba_update(
        ba_token,
        country=record.get("country") or country,
        identity_country=country,
        proxy_country=proxy or "",
    )

    sms_provider, sms_err = _build_sms_provider(country, cfg)
    if sms_err:
        ba_update(ba_token, status="failed", error=sms_err)
        return

    # 超时后后台线程可能仍在跑 (to_thread 不会真正取消), 需屏蔽其后续写回
    timed_out = threading.Event()

    def _on_flow_progress(idx: int, name: str, status: str, kw: dict) -> None:
        """flow progress_cb -> 队列实时写回 (3s 轮询前端可见)。

        只写 step + 最近一条 msg; sms_price/sms_phone 由 SMS 段回调带出。
        超时后返回, 不再覆盖超时分支写入的 status/step/error。
        """
        if timed_out.is_set():
            return
        try:
            detail = str(kw.get("detail") or "")
            level = str(kw.get("level") or "info")
            step = str(name or "")
            if step == "sms" and detail:
                # 从 detail 提取取号价格 (格式: 已取号 provider=xxx price=$0.1234 phone=+66...)
                import re as _re
                m_price = _re.search(r"price=\$([0-9.]+)", detail)
                m_prov = _re.search(r"provider=([0-9]+)", detail)
                m_phone = _re.search(r"phone=(\+\d[\d\s]*)", detail)
                if m_price:
                    ba_update(ba_token, sms_price=float(m_price.group(1)))
                if m_prov:
                    ba_update(ba_token, sms_provider_id=m_prov.group(1))
                if m_phone:
                    ba_update(ba_token, sms_phone=m_phone.group(1).strip())
            ba_update(ba_token, step=step, last_msg=detail, last_level=level)
        except Exception:
            pass

    def _run() -> dict:
        auth = BAAuthorizer(proxy=proxy or None, fp_country=country)
        return auth.authorize(
            f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
            phone=str(cfg.get("phone") or "").strip() or None,
            buyer_mode=str(cfg.get("buyer_mode") or "elevation").strip().lower(),
            country=country,
            identity=cfg.get("identity"),
            sms_provider=sms_provider,
            max_card_attempts=int(cfg.get("max_retries") or 3) + 2,
            max_flow_attempts=int(cfg.get("max_flow_attempts") or 2),
            max_authorize_attempts=int(cfg.get("max_retries") or 3),
            on_step=_on_flow_progress,
        )

    try:
        # 超时兜底: flow 卡死 (如 Playwright 清理挂起) 时强制收尾写队列, 不无限 running
        timeout_s = float(cfg.get("flow_timeout_s") or 480)
        result = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_s)
        ok = result.get("status") == "success"
        ba_update(
            ba_token,
            status="success" if ok else "failed",
            step="done" if ok else str(result.get("reason") or "failed"),
            error="" if ok else str(result.get("error") or result.get("reason") or ""),
            email=(
                (result.get("user") or {}).get("email", "")
                if ok and isinstance(result.get("user"), dict)
                else record.get("email", "")
            ),
        )
    except asyncio.TimeoutError:
        timed_out.set()
        ba_update(
            ba_token,
            status="failed",
            step="failed",
            error=f"flow_timeout (授权流程超过 {timeout_s:.0f}s 未结束)",
        )
        _logging.getLogger("api.paypal").warning(
            "BA authorize flow timed out after %ss: %s", timeout_s, ba_token,
        )
    except Exception as exc:
        import logging as _logging
        import traceback as _tb

        _logging.getLogger("api.paypal").error(
            "BA authorize task crashed: %s\n%s",
            exc,
            _tb.format_exc(),
        )
        ba_update(ba_token, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        _ba_release_slot()


_seeded_once = False


async def _seed_from_inventory() -> None:
    """队列为空时, 从成功库存 (paypal 渠道) 回填 BA 记录 (每个进程仅一次)。

    保证后端重启后, 之前提链成功的 BA 仍出现在授权队列。
    手动清空队列后不会再次被种满 (本进程内 _seeded_once 已置位)。
    """
    global _seeded_once
    if _seeded_once:
        return
    _seeded_once = True
    if ba_count() > 0:
        return
    try:
        recs = await token_store.list_success(limit=500, channel="paypal")
    except Exception:
        return
    for r in recs:
        url = r.get("paypal_approve_url") or ""
        if "ba_token=" in url:
            import_from_url(
                url,
                email=r.get("email") or "",
                country=r.get("exit_country") or r.get("billing_country") or "",
                chain_id=r.get("ba") or "",
                source="inventory",
            )


@router.get("/ba/records")
async def get_ba_records() -> dict[str, Any]:
    """获取所有 BA 授权记录。"""
    await _seed_from_inventory()
    ba_mark_stale()
    records = list_records()
    return {
        "ok": True,
        "records": [_record_to_dict(r) for r in records],
        "total": len(records),
    }


@router.get("/ba/pending")
async def get_pending_ba() -> dict[str, Any]:
    """获取待授权的 BA 记录（从成功链路中提取）。"""
    await _seed_from_inventory()
    pending = [r for r in list_records() if r.get("status") == "pending"]
    return {
        "ok": True,
        "records": [_record_to_dict(r) for r in pending],
        "count": len(pending),
    }


@router.post("/ba/authorize")
async def authorize_ba(req: BAAuthorizeRequest) -> dict[str, Any]:
    """启动单条 BA 授权流程 (国家跟随 queue record.country 或 config 覆盖)。"""
    ba_token = req.ba_token.strip()
    if not ba_token:
        return {"ok": False, "error": "ba_token is required"}

    # 查找或创建记录
    record = ba_get(ba_token)
    if record is None:
        cfg0 = req.config or {}
        country0 = str(
            cfg0.get("identity_country") or cfg0.get("exit_country") or ""
        ).upper()
        record = {
            "ba_token": ba_token,
            "email": "",
            "approve_url": f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
            "status": "pending",
            "step": "submit_email",
            "country": country0,
            "chain_id": "",
            "captcha_type": "",
            "sms_phone": "",
            "error": "",
            "source": "manual",
            "created_at": int(time.time() * 1000),
            "updated_at": int(time.time() * 1000),
        }
        ba_add(**record)

    cfg = _merged_ba_config(req.config)

    # pending -> running 原子转移 (重复启动拒绝)
    ok, lock_err = ba_try_start(ba_token)
    if not ok:
        return {"ok": False, "error": lock_err}
    if not _ba_acquire_slot(cap=_ba_concurrency_cap(cfg)):
        ba_update(ba_token, status="pending", error="")
        return {"ok": False, "error": "concurrency_limit"}

    asyncio.create_task(_run_authorize_task(ba_token, cfg))

    return {
        "ok": True,
        "ba_token": ba_token,
        "status": "running",
        "step": "submit_email",
        "message": "BA 授权流程已启动 (ba_paypal)",
    }


@router.post("/ba/batch")
async def batch_authorize(req: BABatchRequest) -> dict[str, Any]:
    """批量启动 BA 授权 (逐条真实起任务, 每条 record 各自国家分发)。"""
    tokens = [t.strip() for t in (req.ba_tokens or []) if t and t.strip()]
    if not tokens:
        return {"ok": False, "error": "ba_tokens list is empty"}

    started: list[str] = []
    skipped: dict[str, str] = {}
    for ba_token in tokens:
        record = ba_get(ba_token)
        if record is None:
            cfg0 = req.config or {}
            country0 = str(
                cfg0.get("identity_country") or cfg0.get("exit_country") or ""
            ).upper()
            ba_add(
                ba_token,
                approve_url=f"https://www.paypal.com/agreements/approve?ba_token={ba_token}",
                status="pending",
                step="submit_email",
                country=country0,
                source="manual",
            )
        ok, err = ba_try_start(ba_token)
        if not ok:
            skipped[ba_token] = err
            continue
        cfg = _merged_ba_config(req.config)
        if not _ba_acquire_slot(cap=_ba_concurrency_cap(cfg)):
            ba_update(ba_token, status="pending", error="")
            skipped[ba_token] = "concurrency_limit"
            break
        asyncio.create_task(_run_authorize_task(ba_token, cfg))
        started.append(ba_token)

    return {
        "ok": True,
        "started": len(started),
        "skipped": skipped,
        "total": len(tokens),
        "message": f"已启动 {len(started)}/{len(tokens)} 条 BA 授权",
    }


@router.post("/ba/import")
async def import_ba_manual(req: BAImportRequest) -> dict[str, Any]:
    """手动导入 BA 链接/裸 token 到授权队列 (支持多行文本 / 数组 / 单条)。

    兼容旧调用: body 含 paypal_approve_url 时按单条导入。
    """
    # 兼容旧格式: {paypal_approve_url, email, country, chain_id}
    body_items: list[str] = []
    if isinstance(req.urls, list):
        body_items.extend(u for u in req.urls if u and str(u).strip())
    if req.text and req.text.strip():
        for line in req.text.splitlines():
            for part in re.split(r"[,\s;]+", line.strip()):
                if part:
                    body_items.append(part)

    if not body_items:
        return {"ok": False, "error": "text/urls is empty"}

    email = (req.email or "").strip()
    country = (req.country or "").strip().upper()
    source = (req.source or "manual").strip() or "manual"

    seen: set[str] = set()
    imported: list[str] = []
    exists: list[str] = []
    invalid: list[str] = []
    for item in body_items:
        tok = extract_ba_token(item)
        if not tok:
            invalid.append(item)
            continue
        if tok in seen:
            continue
        seen.add(tok)
        if ba_get(tok) is not None:
            exists.append(tok)
            continue
        ba_add(
            tok,
            email=email,
            approve_url=f"https://www.paypal.com/agreements/approve?ba_token={tok}",
            status="pending",
            step="submit_email",
            country=country,
            chain_id="manual",
            source=source,
        )
        imported.append(tok)

    return {
        "ok": True,
        "imported": imported,
        "exists": exists,
        "invalid": invalid,
        "total": len(imported) + len(exists),
        "message": f"导入 {len(imported)} 条, 重复 {len(exists)} 条, 无效 {len(invalid)} 条",
    }


@router.post("/ba/import_url")
async def import_ba_from_chain(request: Request) -> dict[str, Any]:
    """从成功链路中导入 BA URL 到授权队列 (保留旧端点兼容)。

    接收链路成功结果中的 paypal_approve_url，提取 ba_token。
    (orchestrator 已在链路成功时自动导入, 此端点保留供手动调用)
    """
    body = await request.json()
    url = body.get("paypal_approve_url", "")
    email = body.get("email", "")
    country = body.get("country", "")
    chain_id = body.get("chain_id", "")

    ba_token = _extract_ba_token(url)
    if not ba_token:
        return {"ok": False, "error": "No ba_token found in URL"}

    if ba_get(ba_token) is not None:
        return {"ok": True, "exists": True, "ba_token": ba_token}

    imported = import_from_url(url, email=email, country=country, chain_id=chain_id)
    return {"ok": True, "imported": imported, "ba_token": ba_token}


@router.post("/ba/retry")
async def retry_ba_batch(req: BARetryRequest) -> dict[str, Any]:
    """批量重试 BA: failed -> pending (allow_success=true 时已授权也可重跑) -> 启动。"""
    tokens = [t.strip() for t in (req.ba_tokens or []) if t and t.strip()]
    if not tokens:
        return {"ok": False, "error": "ba_tokens list is empty"}

    started: list[str] = []
    skipped: dict[str, str] = {}
    allow_success = bool((req.config or {}).get("allow_success_retry", False))
    for ba_token in tokens:
        if not ba_retry(ba_token, allow_success=allow_success):
            skipped[ba_token] = "not_retryable_or_not_found"
            continue
        # retry 只把 failed->pending; 启动前必须 try_start 转 running,
        # 否则任务在跑但队列 status 停留 pending: 前端不显示"授权启动"、
        # 且 batch/authorize 会把它当 pending 重复启动
        ok, lock_err = ba_try_start(ba_token)
        if not ok:
            skipped[ba_token] = lock_err
            continue
        cfg = _merged_ba_config(req.config)
        if not _ba_acquire_slot(cap=_ba_concurrency_cap(cfg)):
            ba_update(ba_token, status="pending", error="")
            skipped[ba_token] = "concurrency_limit"
            break
        asyncio.create_task(_run_authorize_task(ba_token, cfg))
        started.append(ba_token)

    return {
        "ok": True,
        "started": len(started),
        "skipped": skipped,
        "total": len(tokens),
        "message": f"已重试 {len(started)}/{len(tokens)} 条 BA 授权",
    }


@router.post("/ba/delete")
async def delete_ba_records(req: BADeleteRequest) -> dict[str, Any]:
    """批量删除 BA 授权记录。"""
    tokens = [t.strip() for t in (req.ba_tokens or []) if t and t.strip()]
    if not tokens:
        return {"ok": False, "error": "ba_tokens list is empty"}
    deleted = ba_bulk_remove(tokens)
    return {"ok": True, "deleted": deleted, "total": len(tokens)}


@router.post("/ba/clear")
async def clear_ba_records(req: BAClearRequest) -> dict[str, Any]:
    """清空队列: status 缺省清全部; 指定时只清该状态 (pending/running/success/failed)。"""
    status = (req.status or "").strip().lower()
    if status in ("pending", "running", "success", "failed"):
        removed = ba_clear(status=status)
    else:
        removed = ba_clear()
        status = "all"
    return {"ok": True, "removed": removed, "status": status}


@router.get("/ba/config")
async def get_ba_config() -> dict[str, Any]:
    """获取 BA 授权配置。"""
    return {"ok": True, "config": _ba_config}


@router.post("/ba/config")
async def update_ba_config(req: BAConfigUpdate) -> dict[str, Any]:
    """更新 BA 授权配置 (立即落盘, 重启后自动恢复)。

    captcha_strategy: frontend_disable / manual_required — 同时写入环境变量
    供 flow 的 paypal_captcha_bypass_mode() 读取 (原配置项从不被 flow 消费)。
    """
    updates = req.model_dump(exclude_none=True)
    strategy = str(updates.get("captcha_strategy") or "").strip().lower()
    if strategy:
        if strategy in ("frontend_disable", "manual_required"):
            os.environ["PAYPAL_CAPTCHA_BYPASS_MODE"] = strategy
        else:
            updates["captcha_strategy"] = ""
    _ba_config.update(updates)
    _save_ba_config()
    return {"ok": True, "config": _ba_config}


@router.get("/ba/stats")
async def get_ba_stats() -> dict[str, Any]:
    """获取 BA 授权统计。"""
    await _seed_from_inventory()
    records = list_records()
    total = len(records)
    pending = sum(1 for r in records if r.get("status") == "pending")
    running = sum(1 for r in records if r.get("status") == "running")
    success = sum(1 for r in records if r.get("status") == "success")
    failed = sum(1 for r in records if r.get("status") == "failed")

    iq_count = sum(1 for r in records if r.get("captcha_type") == "iq")
    pi_count = sum(1 for r in records if r.get("captcha_type") == "pi")

    # 国家分布 (新)
    by_country: dict[str, int] = {}
    for r in records:
        cc = (r.get("identity_country") or r.get("country") or "UNKNOWN").upper()
        by_country[cc] = by_country.get(cc, 0) + 1

    return {
        "ok": True,
        "stats": {
            "total": total,
            "pending": pending,
            "running": running,
            "success": success,
            "failed": failed,
            "success_rate": round(success / (success + failed) * 100, 1) if (success + failed) > 0 else 0,
            "captcha_iq": iq_count,
            "captcha_pi": pi_count,
            "by_country": by_country,
        },
    }


@router.delete("/ba/{ba_token}")
async def delete_ba_record(ba_token: str) -> dict[str, Any]:
    """删除 BA 授权记录。"""
    deleted = ba_remove(ba_token)
    return {"ok": True, "deleted": 1 if deleted else 0}


class IdentityRequest(BaseModel):
    country: str = "US"
    count: int = 1


@router.get("/identity/countries")
async def identity_countries() -> dict[str, Any]:
    """列出身份库支持的全部国家 (含 sms/proxy 可用标记, 供前端置灰)。"""
    try:
        from ba_paypal.paypal.country_profile import available
        items = []
        for cc in available():
            try:
                from ba_paypal.paypal.country_profile import (
                    country_context,
                    sms_supported,
                    proxy_supported,
                )
                ctx = country_context(cc)
                items.append({
                    "code": cc,
                    "sms_supported": sms_supported(cc),
                    "proxy_supported": proxy_supported(cc),
                    "sms_country_id": ctx.sms_country_id,
                })
            except Exception:
                items.append({"code": cc, "sms_supported": False, "proxy_supported": False, "sms_country_id": ""})
        return {"ok": True, "countries": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/identity/fields/{country}")
async def identity_fields(country: str) -> dict[str, Any]:
    """返回指定国家的表单字段配置 (kycFields / id_types)。"""
    try:
        from ba_paypal.paypal.identity_lib import profile_summary
        return {"ok": True, "profile": profile_summary(country.upper())}
    except KeyError:
        return {"ok": False, "error": f"unsupported country: {country.upper()}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/identity/generate")
async def identity_generate(req: IdentityRequest) -> dict[str, Any]:
    """按国家生成注册身份数据 (姓名/生日/证件号等, 校验位均有效)。"""
    try:
        from ba_paypal.paypal.identity_lib import generate_country_data
        items = generate_country_data(req.country.upper(), req.count)
        return {"ok": True, "country": req.country.upper(), "items": items}
    except KeyError:
        return {"ok": False, "error": f"unsupported country: {req.country.upper()}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/sms/quote")
async def sms_quote(country: str, service: str | None = None) -> dict[str, Any]:
    """接码报价: 该国最低价可用供应商列表 (带 TTL 缓存, 批量同国只查一次)。"""
    cc = (country or "").strip().upper()
    try:
        from ba_paypal.paypal.smsbower import build_provider_for_quote
        quotes = build_provider_for_quote(cc, service=service)
        return {"ok": True, "country": cc, "quotes": quotes,
                "service": quotes[0].get("service") if quotes else ""}
    except Exception as exc:
        return {"ok": False, "country": cc, "error": str(exc), "quotes": []}