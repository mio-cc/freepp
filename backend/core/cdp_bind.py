# -*- coding: utf-8 -*-
"""CDP 浏览器绑卡服务 (Stripe Elements tokenization 唯一可行通道)。

背景 (2026-08-01 研究结论):
  - Stripe 卡 tokenization 纯 HTTP 被硬限制: /v1/tokens /v1/payment_methods + pk
    均返回 400 "integration surface unsupported" (该 account 禁止 pk 直连 tokenization)
  - 必须走 Elements iframe (fingerprinted JS + postMessage 协议 + 真实浏览器指纹)
  - 非 headless 反检测 Chrome (.ba_chrome_profile) 下 hCaptcha 自动通过
  - happy-dom 等 DOM 桥会被 Stripe 指纹检测识别, 不可行

流程:
  1. (HTTP) POST /backend-api/payments/payment_method -> SetupIntent client_secret
  2. (CDP) 加载 Stripe.js + Card Element, 真实键盘输入卡号/有效期/cvc
  3. (CDP) confirmCardSetup -> hCaptcha 自动过 -> SetupIntent succeeded
  4. (HTTP) GET /backend-api/payments/payment_methods 验证绑卡
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any

from .bind_card import create_setup_intent, list_payment_methods
from .card_store import card_store
from .proxy_pool import proxy_pool

log = logging.getLogger(__name__)

CDP_HOST = os.environ.get("MIN_CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("MIN_CDP_PORT", "9223"))
CHROME_PATH = os.environ.get(
    "MIN_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
CHROME_PROFILE = os.environ.get(
    "MIN_CHROME_PROFILE", r"D:\整理\min-implant\.ba_chrome_profile")


class CdpError(RuntimeError):
    pass


class CdpClient:
    """最小 CDP 客户端 (websockets + pending future)。"""

    def __init__(self, ws_url: str):
        import websockets
        self.ws_url = ws_url
        self.ws = None
        self.id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self._recv_task = None
        self._connected = False

    async def connect(self) -> None:
        import websockets
        self.ws = await websockets.connect(self.ws_url, max_size=50 * 1024 * 1024)
        self._connected = True
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        try:
            while self._connected:
                try:
                    msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=1.0))
                except asyncio.TimeoutError:
                    continue
                if msg.get("id") in self.pending:
                    f = self.pending.pop(msg["id"])
                    if "error" in msg:
                        f.set_exception(RuntimeError(msg["error"]))
                    else:
                        f.set_result(msg.get("result", {}))
                else:
                    # 事件消息: 分发到 on_event 回调 (Network.requestWillBeSent 等)
                    handler = getattr(self, "on_event", None)
                    if handler is not None:
                        try:
                            handler(msg)
                        except Exception:
                            pass
        except Exception:
            self._connected = False

    async def send(self, method: str, params: dict | None = None, sid: str | None = None) -> dict:
        if not self._connected:
            raise CdpError("CDP 未连接")
        self.id += 1
        mid = self.id
        msg = {"id": mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=30)

    async def ev(self, expr: str, sid: str | None = None) -> Any:
        r = await self.send("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": True},
                            sid=sid)
        rr = r.get("result") or {}
        if rr.get("subtype") == "error":
            raise CdpError(str(rr.get("description", ""))[:200])
        return rr.get("value")

    async def close(self) -> None:
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


def ensure_chrome() -> str:
    """确保 CDP Chrome 在跑, 返回 ws url。"""
    import urllib.request
    url = f"http://{CDP_HOST}:{CDP_PORT}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            d = json.loads(resp.read().decode("utf-8"))
            return d.get("webSocketDebuggerUrl", "")
    except Exception:
        pass
    # 启动 Chrome (非 headless + 反检测 profile)
    subprocess.Popen([
        CHROME_PATH, f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={CHROME_PROFILE}",
        "--no-first-run", "--no-default-browser-check",
        "--window-size=1280,900", "about:blank",
    ], creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
    for _ in range(15):
        time.sleep(1)
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                return d.get("webSocketDebuggerUrl", "")
        except Exception:
            continue
    raise CdpError("无法启动 CDP Chrome")


async def _type_card(cdp: CdpClient, card_sid: str, card: dict[str, Any]) -> None:
    """真实键盘输入卡信息到 Elements iframe。"""
    async def type_into(sel: str, text: str) -> None:
        await cdp.ev(f"document.querySelector('{sel}').focus()", card_sid)
        await asyncio.sleep(0.15)
        for ch in text:
            await cdp.send("Input.dispatchKeyEvent",
                           {"type": "char", "text": ch, "key": ch, "code": ""}, sid=card_sid)
            await asyncio.sleep(0.08)
        await asyncio.sleep(0.3)

    await type_into("input[name=cardnumber]", str(card.get("number", "")).replace(" ", ""))
    await type_into("input[name=exp-date]", f"{card.get('exp_month', '')}/{card.get('exp_year', '')}")
    await type_into("input[name=cvc]", str(card.get("cvc", "")))


async def _find_card_iframe(cdp: CdpClient, main_sid: str) -> str:
    """找 Card Element iframe (OOPIF target), 返回其 sessionId。"""
    for _ in range(20):
        targets = await cdp.send("Target.getTargets")
        for t in targets.get("targetInfos", []):
            u = t.get("url", "")
            if "elements-inner-card" in u:
                try:
                    sess = await cdp.send("Target.attachToTarget",
                                          {"targetId": t["targetId"], "flatten": True})
                    tsid = sess["sessionId"]
                    await cdp.send("Runtime.enable", sid=tsid)
                    has = await cdp.ev("!!document.querySelector('input[name=cardnumber]')", tsid)
                    if has:
                        return tsid
                except Exception:
                    continue
        await asyncio.sleep(1.5)
    raise CdpError("未找到 Card Element iframe")


async def _apply_us_fingerprint(cdp: CdpClient, sid: str) -> None:
    """指纹环境对齐美国免税账单地理 (时区/locale/language)。

    用户要求: 指纹环境要与填的账单地理位置接近。免税地址用美国州 (DE 等),
    故指纹对齐 US: 时区 America/New_York, locale en-US, 语言 en-US。
    """
    try:
        # 时区 + locale + 语言 (Emulation.setTimezoneOverride / setLocaleOverride)
        await cdp.send("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"}, sid=sid)
        await cdp.send("Emulation.setLocaleOverride", {"locale": "en-US"}, sid=sid)
        await cdp.send("Emulation.setLanguageOverride", {"language": "en-US"}, sid=sid)
        # 页面 navigator 层面 (userAgent 保留 Chrome 真实指纹, 不改)
        log.info("CDP 指纹已对齐美国 (America/New_York / en-US)")
    except Exception as e:
        log.debug("CDP 指纹设置 soft-fail: %s", e)


async def cdp_bind_card(
    access_token: str,
    account_id: str,
    card: dict[str, Any],
    session_token: str = "",
    proxy: str = "",
) -> dict[str, Any]:
    """CDP 绑卡: SetupIntent(HTTP) -> Elements 填卡(CDP) -> confirm(CDP) -> 验证(HTTP)。"""
    out: dict[str, Any] = {"ok": False, "step": ""}
    # 1. SetupIntent (HTTP)
    proxy = proxy or proxy_pool.pick_for_stage("checkout", "US")
    si = create_setup_intent(proxy, access_token, account_id, session_token)
    if not si.get("ok"):
        out.update(step="setup_intent", error=f"status={si.get('status')} {str(si.get('body'))[:200]}")
        return out
    cs = si["client_secret"]; pk = si["pk"]
    out["setup_intent"] = si.get("setup_intent_id")

    # 2. CDP 绑卡
    ws_url = ensure_chrome()
    cdp = CdpClient(ws_url)
    try:
        await cdp.connect()
        # 关闭旧 pay.153 页面
        targets = await cdp.send("Target.getTargets")
        for t in targets.get("targetInfos", []):
            if t.get("type") == "page" and "pay.153" in t.get("url", ""):
                try:
                    await cdp.send("Target.closeTarget", {"targetId": t["targetId"]})
                except Exception:
                    pass
        await asyncio.sleep(1.5)
        res = await cdp.send("Target.createTarget", {"url": "https://pay.153.ink/"})
        tid = res["targetId"]
        sess = await cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        main_sid = sess["sessionId"]
        await cdp.send("Page.enable", sid=main_sid)
        await cdp.send("Runtime.enable", sid=main_sid)
        await asyncio.sleep(3)

        # 指纹环境对齐美国免税账单地理 (时区/locale)
        await _apply_us_fingerprint(cdp, main_sid)

        html = """<!doctype html><html><head><meta charset=utf-8></head><body>
        <div id="ce"></div>
        <script src="https://js.stripe.com/v3/"></script>
        <script>
        window.__cs = %CS%;
        setTimeout(() => {
          window.__stripe = Stripe(%PK%);
          window.__card = window.__stripe.elements().create('card', {hidePostalCode: true});
          window.__card.mount('#ce');
        }, 1200);
        </script>
        </body></html>"""
        html = html.replace("%CS%", json.dumps(cs)).replace("%PK%", json.dumps(pk))
        await cdp.ev(f"document.open(); document.write({json.dumps(html)}); document.close();", main_sid)
        await asyncio.sleep(6)

        card_sid = await _find_card_iframe(cdp, main_sid)
        await _type_card(cdp, card_sid, card)

        # confirm (45s 超时)
        result = await cdp.ev("""
        new Promise((resolve) => {
          window.__stripe.confirmCardSetup(window.__cs, {
            payment_method: {card: window.__card, billing_details: {name: %NAME%}},
            set_as_default_payment_method: true
          }).then(r => resolve(r.error ? 'ERR:' + JSON.stringify(r.error).slice(0, 300) : 'OK:' + r.setupIntent.status + ' pm=' + (r.setupIntent.payment_method || '')))
            .catch(e => resolve('EXC:' + e.message));
          setTimeout(() => resolve('TIMEOUT-45s'), 45000);
        })
        """.replace("%NAME%", json.dumps(str(card.get("name") or "Simon Test"))), main_sid)
        out["confirm"] = result
        if not str(result).startswith("OK:"):
            out.update(step="confirm", error=str(result))
            return out
        pm = str(result).split("pm=")[-1].strip()
        out["pm_id"] = pm
        out["ok"] = True
        out["step"] = "done"
    finally:
        await cdp.close()

    # 3. 验证 (HTTP)
    try:
        lst = list_payment_methods(proxy, access_token, account_id, session_token)
        out["cards"] = lst.get("cards") if lst.get("ok") else []
    except Exception:
        pass
    return out


async def cdp_bind_from_store(
    access_token: str,
    account_id: str,
    card_id: int | None = None,
    session_token: str = "",
) -> dict[str, Any]:
    """从卡片库取卡并 CDP 绑卡。"""
    card = card_store.get_card(card_id) if card_id else card_store.pickup_card()
    if not card:
        return {"ok": False, "step": "card", "error": "卡片库无可用卡"}
    result = await cdp_bind_card(access_token, account_id, card, session_token)
    result["card_last4"] = str(card.get("number", ""))[-4:]
    return result
