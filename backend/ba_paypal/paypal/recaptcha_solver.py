# -*- coding: utf-8 -*-
"""PayPal authchallenge reCAPTCHA 本地解法 (Playwright)。

移植自 GPT-Register-Tool captcha_solver.py, 两条路径:
  1. real-page: Playwright 带协议会话 cookies 打开真实 challenge 页,
     recaptcha v2/v3 widget 自动触发 (execute / 点 checkbox), 取 token;
     同时回灌浏览器内新 cookie。
  2. bridge: data URI 内嵌 api.js render + grecaptcha.execute (v3 invisible)。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.parse
from typing import Any


def extract_recaptcha_context(challenge_html: str) -> dict[str, Any]:
    """从 PayPal authchallenge HTML 提取 recaptcha 上下文。

    返回 {site_key, anchor_url, enterprise, v2_checkbox}。
    anchor_url 优先 Enterprise anchor (PayPal 用 Enterprise 常见), 否则 api2。
    """
    html = challenge_html or ""
    out: dict[str, Any] = {
        "site_key": "",
        "anchor_url": "",
        "enterprise": False,
        "v2_checkbox": False,
    }

    def _first_query(url: str, name: str) -> str:
        try:
            qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True))
            return str(qs.get(name) or "")
        except Exception:
            return ""

    anchors = re.findall(r'https://www\.google\.com/recaptcha/([a-z0-9]+)/anchor\?[^"\'\s<>]+', html)
    for anchor in anchors:
        url = f"https://www.google.com/recaptcha/{anchor}"
        out["anchor_url"] = url
        out["enterprise"] = "enterprise" in anchor
        k = _first_query(url, "k") or _first_query(url, "sitekey")
        if k:
            out["site_key"] = k
        break

    if not out["site_key"]:
        m = re.search(r'data-sitekey=["\']([A-Za-z0-9_-]{20,})["\']', html, re.I)
        if m:
            out["site_key"] = m.group(1)

    # PayPal authchallenge: _adsRecaptchaSiteKey 隐藏字段
    if not out["site_key"]:
        m = re.search(r'name="_adsRecaptchaSiteKey"\s+value="([A-Za-z0-9_-]{20,})"', html, re.I)
        if m:
            out["site_key"] = m.group(1)

    if not out["site_key"]:
        m = re.search(r'6L[A-Za-z0-9_-]{20,}', html)
        if m:
            out["site_key"] = m.group(0)

    if not out["anchor_url"] and out["site_key"]:
        out["anchor_url"] = (
            f"https://www.google.com/recaptcha/enterprise/anchor?ar=1&k={out['site_key']}"
            f"&co=aHR0cHM6Ly93d3cucGF5cGFsLmNvbTo0NDM.&hl=en&v=2&size=invisible&cb=freecaptcha"
        )
        out["enterprise"] = True

    if "g-recaptcha" in html and "data-sitekey" in html:
        out["v2_checkbox"] = True
    return out


def _enterprise_anchor_url(site_key: str, hl: str = "pt_BR") -> str:
    """PayPal 挑战实测可用参数: co=paypalobjects.com + invisible + submit。

    2026-08-14 实测: 该组合 anchor 直接携带 recaptcha-token,
    reload 出 17xx 位 Enterprise token (纯 HTTP, 无需点击)。
    """
    co = "aHR0cHM6Ly93d3cucGF5cGFsb2JqZWN0cy5jb206NDQz"  # base64("https://www.paypalobjects.com:443")
    return (
        f"https://www.google.com/recaptcha/enterprise/anchor"
        f"?ar=1&k={site_key}&co={co}&hl={hl}&v=17G9E0q0CsYLPipQ1uSP1jCv"
        f"&size=invisible&sa=submit&useg=3"
    )


def solve_recaptcha_from_html(
    *,
    challenge_html: str = "",
    site_key: str = "",
    anchor_url: str = "",
    proxy: str = "",
    timeout: int = 30,
) -> str:
    """统一入口: 纯 HTTP enterprise anchor->reload (freecaptcha 移植) 优先, Playwright bridge 兜底。"""
    ctx = extract_recaptcha_context(challenge_html)
    key = site_key or ctx["site_key"] or ""
    anchor = anchor_url or ctx["anchor_url"] or ""
    if anchor and "size=invisible" not in anchor:
        # 探测实证: PayPal challenge 用 invisible+paypalobjects 参数才出 token
        anchor = ""
    if not anchor and key:
        anchor = _enterprise_anchor_url(key)
    if anchor:
        token = solve_recaptcha_v3_anchor_reload(
            anchor_url=anchor,
            site_key=key,
            proxy=proxy,
            timeout=timeout,
        )
        if token:
            return str(token)
    if key:
        token = solve_recaptcha_bridge(
            site_key=key,
            action="submit",
            proxy=proxy,
            headless=True,
            timeout_ms=90000,
        )
        if token:
            return str(token)
    return ""


def _pw_proxy(proxy: str) -> dict[str, str] | None:
    if not proxy:
        return None
    if proxy.lower().startswith(("socks5h://", "socks5://")):
        return {"server": proxy}
    parsed = urllib.parse.urlsplit(proxy)
    if parsed.scheme in ("http", "https"):
        settings: dict[str, str] = {"server": f"http://{parsed.hostname}:{parsed.port or 80}"}
        if parsed.username:
            settings["username"] = urllib.parse.unquote(parsed.username)
        if parsed.password:
            settings["password"] = urllib.parse.unquote(parsed.password)
        return settings
    return {"server": proxy}


def _pw_launch_kwargs(proxy: str = "") -> dict[str, Any]:
    """Playwright launch 参数: 优先使用系统 Chrome (免下载), 否则默认 bundled。"""
    kwargs: dict[str, Any] = {
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        pw_proxy = _pw_proxy(proxy)
        if pw_proxy:
            kwargs["proxy"] = pw_proxy
    chrome = os.environ.get("RECAPTCHA_CHROME_PATH", "").strip()
    if not chrome:
        candidates = (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        )
        chrome = next((p for p in candidates if os.path.exists(p)), "")
    if chrome:
        kwargs["executable_path"] = chrome
    return kwargs


def _pw_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in cookies or []:
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "")
        if not name or not domain:
            continue
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": str(item.get("path") or "/") or "/",
            "secure": bool(item.get("secure", True)),
            "httpOnly": bool(item.get("httpOnly", False)),
        }
        expires = item.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = float(expires)
        out.append(cookie)
    return out


def _build_bridge_html(site_key: str, action: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>reCAPTCHA Bridge</title>
<script src="https://www.google.com/recaptcha/api.js?render={site_key}"></script>
</head><body><div id="status">loading</div>
<script>
  grecaptcha.ready(function() {{
    document.getElementById('status').textContent = 'ready';
    grecaptcha.execute('{site_key}', {{action: '{action or "submit"}'}}).then(function(token) {{
      document.getElementById('status').textContent = 'solved';
      window._captchaToken = token;
    }}).catch(function(err) {{
      document.getElementById('status').textContent = 'error: ' + (err && err.message || err);
    }});
  }});
</script></body></html>"""


def solve_recaptcha_bridge(
    *,
    site_key: str,
    action: str = "submit",
    proxy: str = "",
    headless: bool = True,
    timeout_ms: int = 90000,
) -> str:
    """v3 invisible bridge 解法: 返回 token 或空串。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    html_content = _build_bridge_html(site_key, action)
    pw_proxy = _pw_proxy(proxy)
    try:
        from paypal.pw_shared import shared_playwright
        with shared_playwright() as playwright:
            launch_kwargs = _pw_launch_kwargs(pw_proxy and proxy or "")
            browser = playwright.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 960},
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
                    locale="en-US",
                )
                page = context.new_page()
                page.set_content(html_content, wait_until="domcontentloaded")
                deadline = time.time() + timeout_ms / 1000
                while time.time() < deadline:
                    try:
                        status = page.evaluate("document.getElementById('status').textContent")
                    except Exception:
                        status = ""
                    if status == "solved":
                        token = page.evaluate("window._captchaToken || ''")
                        if token:
                            return str(token)
                        return ""
                    if status.startswith("error"):
                        return ""
                    time.sleep(0.5)
                return ""
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception:
        return ""


def solve_recaptcha_on_page(
    *,
    page_url: str,
    cookies: list[dict[str, Any]] | None = None,
    proxy: str = "",
    headless: bool = True,
    timeout_ms: int = 120000,
    log=print,
) -> str:
    """打开真实 challenge 页, 触发 recaptcha v2/v3 widget, 返回 token。

    优先尝试自动 execute (v3); 若页面是 v2 checkbox, 尝试点击 iframe 内 checkbox。
    浏览器内产生的 cookies 也会一并返回(经 import 由调用方回灌)。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""

    pw_proxy = _pw_proxy(proxy)
    try:
        from paypal.pw_shared import shared_playwright
        with shared_playwright() as playwright:
            launch_kwargs: dict[str, Any] = {
                "headless": headless,
                "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            }
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy
            browser = playwright.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    viewport={"width": 1280, "height": 960},
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
                    locale="en-US",
                )
                pw_cookies = _pw_cookies(cookies)
                if pw_cookies:
                    try:
                        context.add_cookies(pw_cookies)
                    except Exception as e:
                        log("recaptcha add_cookies warn: %s" % str(e)[:100])
                page = context.new_page()
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log("recaptcha goto warn: %s" % str(e)[:100])

                deadline = time.time() + timeout_ms / 1000
                token = ""

                # 路径1: v3 execute (页面上有 grecaptcha.ready 可用)
                try:
                    has_api = page.evaluate(
                        "typeof window.grecaptcha !== 'undefined' && typeof window.grecaptcha.execute === 'function'"
                    )
                    if has_api:
                        site_key = page.evaluate(
                            "(() => { const el = document.querySelector('[data-sitekey]'); "
                            "return el ? el.getAttribute('data-sitekey') : ''; })()"
                        )
                        if site_key:
                            token = page.evaluate(
                                """(key) => new Promise((resolve) => {
                                    try {
                                        grecaptcha.execute(key, {action: 'submit'}).then(resolve).catch(() => resolve(''));
                                    } catch (e) { resolve(''); }
                                })""",
                                site_key,
                            )
                            if token:
                                return str(token)
                except Exception:
                    pass

                # 路径2: v2 checkbox iframe 点击
                while time.time() < deadline and not token:
                    try:
                        for frame in page.frames:
                            if "recaptcha" not in (frame.url or "").lower():
                                continue
                            try:
                                frame.click("#recaptcha-anchor", timeout=2000)
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    time.sleep(1.5)
                    try:
                        token = page.evaluate(
                            "(() => { const ta = document.querySelector('textarea[name=g-recaptcha-response]'); "
                            "return ta && ta.value ? ta.value : ''; })()"
                        )
                    except Exception:
                        token = ""
                return str(token or "")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception:
        return ""


def solve_recaptcha_v3_anchor_reload(
    *,
    anchor_url: str,
    site_key: str = "",
    proxy: str = "",
    timeout: int = 30,
) -> str:
    """纯 HTTP reCAPTCHA v3 解法 (移植 freecaptcha: anchor -> recaptcha-token -> reload)。

    仅适用于标准 v3 anchor (https://www.google.com/recaptcha/api2/anchor?k=...)。
    Enterprise 时可用 /recaptcha/enterprise/anchor 与 /recaptcha/enterprise/reload。
    """
    if not anchor_url:
        return ""
    try:
        import urllib.parse
        import urllib.request

        if proxy:
            from curl_cffi import requests as creq

            r = creq.get(anchor_url, timeout=timeout, impersonate="chrome", proxies={"https": proxy, "http": proxy})
            anchor_response = r.text or ""
        else:
            with urllib.request.urlopen(anchor_url, timeout=timeout) as req:
                anchor_response = req.read().decode("utf-8", errors="replace")
    except Exception:
        return ""

    m = re.search(r'id="recaptcha-token"\s*value="([^"]*)"', anchor_response)
    if not m:
        return ""
    recap_token = m.group(1)
    parsed = urllib.parse.urlsplit(anchor_url)
    anchor_data = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    k = site_key or anchor_data.get("k") or ""
    if not k:
        return ""
    data = {**anchor_data, "reason": "q", "c": recap_token, "chr": "", "vh": "", "bg": ""}
    host = "https://www.google.com/recaptcha/enterprise" if "enterprise" in anchor_url else "https://www.google.com/recaptcha/api2"
    reload_url = f"{host}/reload?k={k}"
    try:
        if proxy:
            from curl_cffi import requests as creq

            r = creq.post(reload_url, data=urllib.parse.urlencode(data).encode("utf-8"),
                          timeout=timeout, impersonate="chrome",
                          proxies={"https": proxy, "http": proxy},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
            reload_response = r.text or ""
        else:
            import urllib.request

            req = urllib.request.Request(reload_url, data=urllib.parse.urlencode(data).encode("utf-8"),
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                reload_response = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        if reload_response.startswith(")]}'"):
            reload_data = json.loads(reload_response[5:])
            token = reload_data[1]
            return str(token or "")
    except Exception:
        pass
    return ""


def solve_recaptcha(
    *,
    challenge_html: str = "",
    site_key: str = "",
    action: str = "submit",
    page_url: str = "",
    cookies: list[dict[str, Any]] | None = None,
    proxy: str = "",
    headless: bool = True,
    timeout_ms: int = 120000,
    log=print,
) -> str:
    """统一入口: 有 page_url 走真实页, 否则走 bridge。"""
    if page_url:
        token = solve_recaptcha_on_page(
            page_url=page_url, cookies=cookies, proxy=proxy,
            headless=headless, timeout_ms=timeout_ms, log=log,
        )
        if token:
            log("recaptcha solved via page token_len=%d" % len(token))
            return token
    if site_key:
        token = solve_recaptcha_bridge(
            site_key=site_key, action=action, proxy=proxy,
            headless=headless, timeout_ms=min(timeout_ms, 90000),
        )
        if token:
            log("recaptcha solved via bridge token_len=%d" % len(token))
            return token
    return ""


__all__ = ["solve_recaptcha", "solve_recaptcha_bridge", "solve_recaptcha_on_page"]
