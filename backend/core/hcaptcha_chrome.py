"""Chrome semi-hybrid hCaptcha mint (Stripe invisible)。

突破链路 (2026-08-04 验证):
  Chrome 加载 Stripe hCaptcha invisible 页面 → hcaptcha.render + execute
  → 自动 PoW + getcaptcha (octet-stream) → token 经 getResponse 轮询拿到。

关键发现:
  - checksiteconfig v 参数是 hCaptcha 版本 hash (ba51eebd...) 不是 UUID
  - execute 必须传 widgetId (字符串), 传 div 元素会抛 invalid-captcha-id
  - callback 可能不触发, 需轮询 hcaptcha.getResponse(widgetId)
"""
from __future__ import annotations

import time
from typing import Any

SITEKEY = "463b917e-e264-403f-ad34-34af0ee10294"
IFRAME_URL = (
    "https://b.stripecdn.com/stripethirdparty-srv/assets/v33.5/"
    f"HCaptchaInvisible.html?siteKey={SITEKEY}"
)
DEFAULT_PROXY = "http://127.0.0.1:7890"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
RENDER_JS = """
() => new Promise((resolve, reject) => {
  if (!window.hcaptcha) return reject(new Error('no hcaptcha object'));
  const div = document.createElement('div');
  div.id = 'hcap-mint-widget';
  document.body.appendChild(div);
  let done = false;
  try {
    const widgetId = window.hcaptcha.render(div, {
      sitekey: __hcapSitekey,
      size: 'invisible',
      'error-callback': () => { done = true; resolve(null); },
    });
    let cbToken = null;
    window.hcaptcha.execute(widgetId, (tok) => { cbToken = tok; });
    window.hcaptcha.execute(widgetId);
    const poll = setInterval(() => {
      try {
        const t = window.hcaptcha.getResponse(widgetId);
        const tok = (t && t.length > 20) ? t : (cbToken && cbToken.length > 20 ? cbToken : null);
        if (tok && !done) { done = true; clearInterval(poll); resolve(tok); }
      } catch (e) {}
    }, 1000);
    setTimeout(() => { if (!done) { done = true; clearInterval(poll); resolve(null); } }, 60000);
  } catch (e) {
    done = true;
    resolve(null);
  }
})
"""


def mint_hcaptcha_chrome(
    proxy: str = DEFAULT_PROXY,
    sitekey: str = SITEKEY,
    timeout: float = 90,
    headless: bool = True,
) -> dict[str, Any]:
    """用真实 Chrome 拿 Stripe hCaptcha passive token。

    Returns:
        {ok, token, token_len, elapsed_ms, error}
    """
    t0 = time.time()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "playwright 未安装 (pip install playwright)"}

    try:
        with sync_playwright() as pwt:
            browser = pwt.chromium.launch(
                headless=headless,
                executable_path=CHROME,
                proxy={"server": proxy},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
                )
            )
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            try:
                page.goto(IFRAME_URL, wait_until="domcontentloaded", timeout=min(timeout, 60) * 1000)
            except Exception as e:
                browser.close()
                return {"ok": False, "error": f"goto: {e}"}
            page.wait_for_timeout(2500)
            has_hcap = page.evaluate("() => typeof window.hcaptcha")
            if has_hcap == "undefined":
                browser.close()
                return {"ok": False, "error": "no hcaptcha object on page"}
            page.evaluate(f"() => {{ window.__hcapSitekey = '{sitekey}'; }}")
            token = page.evaluate(RENDER_JS)
            browser.close()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    elapsed = int((time.time() - t0) * 1000)
    if token:
        return {"ok": True, "token": token, "token_len": len(token), "elapsed_ms": elapsed}
    return {"ok": False, "error": "no token in 60s (soft-reject or challenge)", "elapsed_ms": elapsed}


def save_token(token: str, path: str = "_hcap_chrome_token.txt") -> str:
    import os
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    return os.path.abspath(path)


if __name__ == "__main__":
    import json
    res = mint_hcaptcha_chrome()
    print(json.dumps({k: v[:90] + "..." if k == "token" else v for k, v in res.items()}, indent=2))
    if res.get("ok"):
        save_token(res["token"])
