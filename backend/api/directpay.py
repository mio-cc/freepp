"""直卡支付 API 路由：绑卡 / 免税地址 / 订阅流程。

提供:
- POST /api/directpay/subscribe  - 完整流程 (提链→绑卡→重新提链→免税地址)
- POST /api/directpay/bind      - 仅绑卡
- GET  /api/directpay/cards     - 卡片库列表
- POST /api/directpay/cards     - 新增卡
- DELETE /api/directpay/cards/{id}
- GET  /api/directpay/taxfree   - 免税地址库 (生成/州列表)
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/directpay", tags=["directpay"])

# 内存中的直卡支付记录
_dp_records: list[dict[str, Any]] = []


class SubscribeRequest(BaseModel):
    token_id: str | None = None
    access_token: str = ""
    account_id: str = ""
    card_id: int | None = None
    taxfree_state: str = "DE"
    rebind_recheckout: bool = True
    hcaptcha_token: str = ""


class BindRequest(BaseModel):
    token_id: str | None = None
    access_token: str = ""
    account_id: str = ""
    card_id: int | None = None
    checkout_id: str = ""
    processor: str = "openai_llc"
    use_cdp_fallback: bool = False
    hcaptcha_token: str = ""


class BindAndPayRequest(BaseModel):
    token_id: str | None = None
    access_token: str = ""
    account_id: str = ""
    card_id: int | None = None
    checkout_id: str = ""
    processor: str = "openai_llc"
    taxfree_state: str = "DE"
    currency: str = "USD"
    use_cdp_fallback: bool = False
    hcaptcha_token: str = ""


class AddCardRequest(BaseModel):
    number: str
    exp_month: str
    exp_year: str
    cvc: str
    name: str = ""
    max_uses: int = 10


def _record_to_dict(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id", ""),
        "status": r.get("status", ""),
        "step": r.get("step", ""),
        "email": r.get("email", ""),
        "card_last4": r.get("card_last4", ""),
        "taxfree_state": r.get("taxfree_state", ""),
        "short_link": r.get("short_link_2") or r.get("short_link_1") or "",
        "error": r.get("error", ""),
        "created_at": r.get("created_at", 0),
        "updated_at": r.get("updated_at", 0),
    }


def _ensure_hcaptcha(hcaptcha_token: str = "") -> str:
    """未提供 hcaptcha_token 时自动用 Chrome mint; 失败返回空串 (不阻塞主流程)。"""
    if hcaptcha_token:
        return hcaptcha_token
    try:
        from core.hcaptcha_chrome import mint_hcaptcha_chrome
        r = mint_hcaptcha_chrome()
        if r.get("ok"):
            import logging
            logging.getLogger("directpay").info("hcaptcha auto-mint ok len=%d", r["token_len"])
            return r["token"]
    except Exception as e:
        import logging
        logging.getLogger("directpay").warning("hcaptcha auto-mint failed: %s", e)
    return ""


async def _resolve_token(token_id: str | None, access_token: str) -> tuple[str, str, str]:
    """取 access_token / session_token / account_id (chatgpt_account_id UUID)。"""
    at = access_token
    st = ""
    account_id = ""
    if not at and token_id:
        from core.token_store import token_store
        tok = await token_store.get_token(token_id)
        if tok:
            at = tok.get("access_token") or tok.get("raw") or ""
            st = tok.get("session_token") or ""
    if not at:
        raise ValueError("需要 access_token 或 token_id")
    # account_id = JWT 的 https://api.openai.com/auth.chatgpt_account_id (UUID)
    import base64, json
    try:
        payload_b64 = at.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        jwt = json.loads(base64.urlsafe_b64decode(payload_b64))
        auth_claims = jwt.get("https://api.openai.com/auth") or {}
        account_id = str(auth_claims.get("chatgpt_account_id") or "")
    except Exception:
        account_id = ""
    return at, st, account_id


@router.post("/subscribe")
async def subscribe(req: SubscribeRequest) -> dict[str, Any]:
    """完整直卡支付: 提链 → 绑卡 → 重新提链 → 免税地址 → 订阅。"""
    from core.direct_pay import bind_and_subscribe

    record: dict[str, Any] = {
        "id": f"dp_{int(time.time() * 1000)}",
        "status": "running", "step": "checkout",
        "email": "", "card_last4": "", "taxfree_state": req.taxfree_state,
        "error": "", "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
    }
    _dp_records.append(record)

    import asyncio
    async def _run() -> None:
        try:
            at, st, account_id = await _resolve_token(req.token_id, req.access_token)
            hcaptcha_token = _ensure_hcaptcha(req.hcaptcha_token)
            result = await asyncio.to_thread(
                bind_and_subscribe, at, st,
                account_id=req.account_id or account_id,
                card_id=req.card_id, taxfree_state=req.taxfree_state,
                rebind_recheckout=req.rebind_recheckout,
                hcaptcha_token=hcaptcha_token,
            )
            if result.get("ok"):
                record["status"] = "success"
                record["step"] = "done"
                record["card_last4"] = result.get("card_last4", "")
                record["short_link_1"] = result.get("short_link_1", "")
                record["short_link_2"] = result.get("short_link_2", "")
                record["taxfree_address"] = result.get("taxfree_address", {})
                record["bind"] = result.get("bind", {})
            else:
                record["status"] = "failed"
                record["step"] = result.get("step", "failed")
                record["error"] = result.get("error", "")
        except Exception as e:
            record["status"] = "failed"
            record["error"] = f"{type(e).__name__}: {e}"
        finally:
            record["updated_at"] = int(time.time() * 1000)

    asyncio.create_task(_run())
    return {"ok": True, "record": _record_to_dict(record)}


@router.post("/bind")
async def bind(req: BindRequest) -> dict[str, Any]:
    """纯协议绑卡: SetupIntent confirm 内联卡数据 (零浏览器)。

    需要 checkout_id (oaics_*) + processor (从提链阶段获取)。
    如纯协议失败且 use_cdp_fallback=True, 回退到 CDP 浏览器绑卡。
    """
    from core.bind_card import bind_card
    from core.card_store import card_store
    from core.proxy_pool import proxy_pool

    try:
        at, st, account_id = await _resolve_token(req.token_id, req.access_token)
        account_id = req.account_id or account_id
        if not account_id:
            return {"ok": False, "error": "无法确定 account_id"}
        card = card_store.get_card(req.card_id) if req.card_id else card_store.pickup_card()
        if not card:
            return {"ok": False, "error": "卡片库无可用卡"}
        proxy = proxy_pool.pick_for_stage("checkout", "US")

        # hCaptcha token (可选, 未传自动 mint)
        hcaptcha_token = _ensure_hcaptcha(req.hcaptcha_token)

        # 纯协议绑卡
        import asyncio
        result = await asyncio.to_thread(
            bind_card, proxy, at, account_id, card, st,
            checkout_id=req.checkout_id, processor=req.processor,
            hcaptcha_token=hcaptcha_token,
        )

        # 纯协议失败, 可选 CDP 回退
        if not result.get("ok") and req.use_cdp_fallback:
            from core.cdp_bind import cdp_bind_card
            try:
                cdp_result = await cdp_bind_card(at, account_id, card, st)
                result["cdp_fallback"] = cdp_result
                if cdp_result.get("ok"):
                    result["ok"] = True
                    result["method"] = "cdp_fallback"
            except Exception as e:
                result["cdp_fallback_error"] = f"{type(e).__name__}: {e}"

        result["method"] = result.get("method", "pure_protocol")
        return {"ok": result.get("ok"), "result": result}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/bind-and-pay")
async def bind_and_pay(req: BindAndPayRequest) -> dict[str, Any]:
    """纯协议完整流程: 绑卡 → 支付确认 → 订阅验证 (零浏览器)。

    需要 checkout_id (oaics_*) + processor。
    返回 subscription_plan="plus" 即成功。
    """
    from core.bind_card import bind_and_pay
    from core.card_store import card_store
    from core.proxy_pool import proxy_pool

    record: dict[str, Any] = {
        "id": f"bp_{int(time.time() * 1000)}",
        "status": "running", "step": "bind",
        "card_last4": "", "error": "",
        "created_at": int(time.time() * 1000),
        "updated_at": int(time.time() * 1000),
    }
    _dp_records.append(record)

    import asyncio
    async def _run() -> None:
        try:
            at, st, account_id = await _resolve_token(req.token_id, req.access_token)
            account_id = req.account_id or account_id
            if not account_id:
                raise ValueError("无法确定 account_id")
            card = card_store.get_card(req.card_id) if req.card_id else card_store.pickup_card()
            if not card:
                raise ValueError("卡片库无可用卡")
            proxy = proxy_pool.pick_for_stage("checkout", "US")
            record["card_last4"] = str(card.get("number", ""))[-4:]

            hcaptcha_token = _ensure_hcaptcha(req.hcaptcha_token)

            result = await asyncio.to_thread(
                bind_and_pay, proxy, at, account_id, card, st,
                checkout_id=req.checkout_id, processor=req.processor,
                currency=req.currency, fast_verify=True, timeout=30,
                hcaptcha_token=hcaptcha_token,
            )
            record["result"] = result
            if result.get("ok"):
                record["status"] = "success"
                record["step"] = "done"
                record["subscription_plan"] = result.get("subscription_plan", "")
                record["pm_id"] = result.get("pm_id", "")
                record["method"] = "pure_protocol"
            else:
                record["status"] = "failed"
                record["step"] = result.get("step", "failed")
                record["error"] = result.get("error", "")

                # CDP 回退 (仅绑卡, 不走支付确认)
                if req.use_cdp_fallback:
                    from core.cdp_bind import cdp_bind_card
                    try:
                        cdp_result = await cdp_bind_card(at, account_id, card, st)
                        if cdp_result.get("ok"):
                            record["status"] = "partial"
                            record["step"] = "done_bind_only"
                            record["pm_id"] = cdp_result.get("pm_id", "")
                            record["method"] = "cdp_fallback"
                    except Exception as e:
                        record["cdp_fallback_error"] = str(e)
        except Exception as e:
            record["status"] = "failed"
            record["error"] = f"{type(e).__name__}: {e}"
        finally:
            record["updated_at"] = int(time.time() * 1000)

    asyncio.create_task(_run())
    return {"ok": True, "record": _record_to_dict(record)}


@router.get("/cards")
async def list_cards() -> dict[str, Any]:
    from core.card_store import card_store
    cards = card_store.list_cards()
    for c in cards:
        c["number"] = f"****{str(c.get('number', ''))[-4:]}"
    return {"ok": True, "cards": cards, "total": len(cards)}


@router.post("/cards")
async def add_card(req: AddCardRequest) -> dict[str, Any]:
    from core.card_store import card_store
    cid = card_store.add_card(req.model_dump())
    return {"ok": True, "card_id": cid}


@router.delete("/cards/{card_id}")
async def delete_card(card_id: int) -> dict[str, Any]:
    from core.card_store import card_store
    ok = card_store.delete_card(card_id)
    return {"ok": ok}


@router.get("/taxfree")
async def taxfree(state: str = "DE") -> dict[str, Any]:
    """免税地址: 生成指定州地址 + 州列表。"""
    from core.taxfree_store import taxfree_store
    addr = taxfree_store["generate"](state=state)
    return {"ok": True, "address": addr, "states": taxfree_store["list_states"]()}


@router.get("/records")
async def get_records() -> dict[str, Any]:
    return {"ok": True, "records": [_record_to_dict(r) for r in _dp_records],
            "total": len(_dp_records)}
