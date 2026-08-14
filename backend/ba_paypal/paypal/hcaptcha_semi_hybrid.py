# -*- coding: utf-8 -*-
"""semi-hybrid hCaptcha passive 求解器 (适配自 7/16 _research_semi_hybrid_mint.py)。

流程: curl_cffi checksiteconfig -> 浏览器(headless) 算 n -> happy-dom 官方 msgpack 加密
      -> curl_cffi getcaptcha -> hsw_decrypt_resp_node.js 解密 -> token

结论依据 (docs/HCAPTCHA_PURE_PROTOCOL_ARCHIVE_20260812.md):
  - 纯协议算 n 永远 soft-reject (host_sum 哨兵 4778)
  - Chrome 算 n + 纯协议加密 = 唯一已验证出 token 的路径 (7/16: token ~2134)
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse as _up
from pathlib import Path

from curl_cffi import requests as creq

from paypal.proxy import ProxyConfig

_HERE = Path(__file__).resolve().parent
_BA_FP = _HERE.parent / "ba_fp_helpers"
_NODE = os.environ.get("NODE_BIN", "node")

ENC_JS = r"""
const fs = require('fs');
const nc = require('crypto');
const { Browser } = require('happy-dom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
function inject(w) {
  w.WebAssembly = globalThis.WebAssembly;
  w.BigInt = globalThis.BigInt;
  w.crypto = {
    subtle: nc.webcrypto.subtle,
    getRandomValues: (a) => { a.set(nc.randomBytes(a.length)); return a; },
  };
  let mp = null;
  try {
    Object.defineProperty(w, 'msgpack', {
      configurable: true,
      get: () => mp,
      set: (v) => { mp = v; if (v && v.encode) global.__mp = v; },
    });
  } catch (_) {}
}
(async () => {
  const browser = new Browser({
    settings: {
      enableJavaScriptEvaluation: true,
      suppressInsecureJavaScriptEnvironmentWarning: true,
      suppressCodeGenerationFromStringsWarning: true,
      fetch: { disableSameOriginPolicy: true, disableStrictSSL: true },
      navigation: { crossOriginPolicy: 'anyOrigin', beforeContentCallback: inject },
      timer: { maxTimeout: 90000, maxIntervalIterations: 3e5 },
      navigator: { userAgent: input.userAgent },
    },
  });
  const page = browser.newPage();
  const V = input.v;
  await page.goto(
    `https://newassets.hcaptcha.com/captcha/v1/${V}/static/hcaptcha.html#frame=challenge&host=${input.host}&sitekey=${input.sitekey}`,
    { timeout: 40000 }
  ).catch(() => {});
  await page.waitUntilComplete({ timeout: 20000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 2500));
  const w = page.mainFrame.window;
  inject(w);
  for (let i = 0; i < 50 && !global.__mp; i++) {
    await new Promise((r) => setTimeout(r, 100));
    if (w.msgpack && w.msgpack.encode) global.__mp = w.msgpack;
  }
  if (!global.__mp) {
    try { global.__mp = require('@msgpack/msgpack'); } catch (_) {}
  }
  if (!global.__mp) throw new Error('no mp');
  const code = fs.readFileSync(input.hswPath, 'utf8');
  const s = w.document.createElement('script');
  s.textContent = code;
  w.document.body.appendChild(s);
  if (typeof w.hsw !== 'function') throw new Error('no hsw');
  let encode = global.__mp && global.__mp.encode && global.__mp.encode.bind(global.__mp);
  let ExtData = global.__mp && global.__mp.ExtData;
  if (!encode) {
    const npmMp = require('@msgpack/msgpack');
    encode = npmMp.encode.bind(npmMp);
    ExtData = npmMp.ExtData;
  }
  function asExt18(enc) {
    if (enc && typeof enc === 'object' && Number(enc.type) === 18 && enc.data) {
      return ExtData ? new ExtData(18, enc.data) : enc;
    }
    if (ExtData && enc instanceof ExtData) return enc;
    let u8;
    if (enc instanceof Uint8Array) u8 = enc;
    else if (enc && enc.buffer) u8 = new Uint8Array(enc.buffer, enc.byteOffset || 0, enc.byteLength || enc.length);
    else throw new Error('enc not bytes: ' + typeof enc);
    return ExtData ? new ExtData(18, u8) : u8;
  }
  const now = Date.now();
  const body = {
    v: V,
    sitekey: input.sitekey,
    host: input.host,
    hl: input.hl || 'en',
    n: String(input.n),
    motionData: JSON.stringify({
      st: now - 1400, dct: now - 1300,
      mm: Array.from({ length: 100 }, (_, i) => [30 + i * 3, 50 + (i % 20), i * 12]),
      'mm-mp': 12.5,
      md: [[100, 80, 500]],
      mu: [[100, 80, 540]],
      topLevel: {
        st: now - 2000,
        sc: { width: 1440, height: 900, availWidth: 1440, availHeight: 875, colorDepth: 24 },
        nv: {
          userAgent: input.userAgent, platform: 'Win32', webdriver: false,
          hardwareConcurrency: 8, deviceMemory: 8,
        },
        dr: 'https://www.paypal.com/', inv: true, exec: true,
      },
      v: 1,
    }),
    pem: JSON.stringify({ csc: 180, csch: 'api.hcaptcha.com', cscrt: 40, cscft: 200 }),
    pst: false,
    p_e: JSON.stringify({
      st: now - 2500,
      sc: { width: 1440, height: 900 },
      nv: { userAgent: input.userAgent, platform: 'Win32', webdriver: false },
      dr: '', inv: false, exec: false,
    }),
  };
  const enc = await w.hsw(1, encode(body));
  const packed = encode([JSON.stringify(input.cObj), asExt18(enc)]);
  const out = Buffer.from(packed instanceof Uint8Array ? packed : new Uint8Array(packed));
  process.stdout.write(JSON.stringify({
    ok: true,
    packed_b64: out.toString('base64'),
    solved_len: String(input.n).length,
    packed_len: out.length,
    enc_type: enc && enc.constructor && enc.constructor.name,
  }));
  await browser.close().catch(() => {});
})().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
  process.exit(1);
});
"""

DECRYPT_JS = r"""
const fs = require('fs');
const path = require('path');
const nc = require('crypto');
const { Browser } = require('happy-dom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ROOT = input.helpersRoot;
const hswCode = fs.readFileSync(input.hswPath, 'utf8');
const respBytes = fs.readFileSync(input.respPath);
function inject(w) {
  w.WebAssembly = globalThis.WebAssembly;
  w.BigInt = globalThis.BigInt;
  w.crypto = {
    subtle: nc.webcrypto.subtle,
    getRandomValues: (a) => { a.set(nc.randomBytes(a.length)); return a; },
  };
}
(async () => {
  const browser = new Browser({
    settings: {
      enableJavaScriptEvaluation: true,
      suppressInsecureJavaScriptEnvironmentWarning: true,
      suppressCodeGenerationFromStringsWarning: true,
      navigation: { crossOriginPolicy: 'anyOrigin', beforeContentCallback: inject },
      timer: { maxTimeout: 90000, maxIntervalIterations: 3e5 },
    },
  });
  const page = browser.newPage();
  await page.goto('https://newassets.hcaptcha.com/', { timeout: 40000 }).catch(() => {});
  const w = page.mainFrame.window;
  inject(w);
  const mp = require(path.join(ROOT, 'node_modules', '@msgpack', 'msgpack'));
  const decode = mp.decode.bind(mp);
  const s = w.document.createElement('script');
  s.textContent = hswCode;
  w.document.body.appendChild(s);
  await new Promise((r) => setTimeout(r, 50));
  if (typeof w.hsw !== 'function') throw new Error('no hsw after load');
  const out = await Promise.race([
    Promise.resolve(w.hsw(0, new Uint8Array(respBytes))),
    new Promise((_, rej) => setTimeout(() => rej(new Error('hsw decrypt timeout')), 60000)),
  ]);
  if (out == null) throw new Error('hsw(0) returned null');
  const u8 = out instanceof Uint8Array ? out : new Uint8Array(out);
  let j = null;
  try { j = decode(u8); }
  catch (e) {
    try {
      const arr = decode(u8);
      const first = arr && arr[0];
      if (first && typeof first === 'object' && Number(first.type) === 18) {
        const inner = new Uint8Array(first.data);
        const plain2 = await w.hsw(0, inner);
        j = decode(plain2 instanceof Uint8Array ? plain2 : new Uint8Array(plain2));
      } else throw new Error('decoded non-ext18');
    } catch (e2) { throw new Error('decode fail: ' + e2.message); }
  }
  const tok = String(j.generated_pass_UUID || j.token || '').trim();
  process.stdout.write(JSON.stringify({
    ok: !!tok, token: tok, pass: j.pass, expiration: j.expiration,
    success: j.success, error_codes: j['error-codes'] || j.error_codes || null,
    keys: Object.keys(j).slice(0, 20),
  }));
  await browser.close().catch(() => {});
})().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
  process.exit(1);
});
"""


def _pw_proxy(proxy: str):
    if not proxy:
        return None
    u = _up.urlsplit(proxy)
    pw = {"server": f"http://{u.hostname}:{u.port or 80}"}
    if u.username:
        pw["username"] = _up.unquote(u.username)
    if u.password:
        pw["password"] = _up.unquote(u.password)
    return pw


def _node_env():
    env = os.environ.copy()
    env["NODE_PATH"] = str(_BA_FP / "node_modules")
    return env


def mint_semi_hybrid(
    *,
    sitekey: str,
    host: str = "www.paypalobjects.com",
    asset_v: str = "",
    proxy: str = "",
    user_agent: str = "",
    hl: str = "en",
    workdir: str | Path | None = None,
    headless: bool = True,
    timeout: float = 150,
) -> dict:
    """Chrome 算 n + happy-dom 加密 + 纯协议提交 → token。

    Returns: {ok, token, n_len, packed_len, error?}
    """
    wd = Path(workdir or tempfile.mkdtemp(prefix="hcap_sh_"))
    wd.mkdir(parents=True, exist_ok=True)
    UA = user_agent or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )

    # 0. 预热 (curl_cffi, 同代理, 拿 __cf_bm)
    pm = {"https": proxy, "http": proxy} if proxy else {}
    sess = creq.Session(impersonate="chrome", proxies=pm)
    try:
        sess.get("https://newassets.hcaptcha.com/",
                 headers={"User-Agent": UA, "Accept": "text/html,*/*"}, timeout=30)
    except Exception:
        pass
    headers = {
        "User-Agent": UA,
        "Origin": "https://newassets.hcaptcha.com",
        "Referer": "https://newassets.hcaptcha.com/",
        "Accept": "application/json",
    }

    # 1. 浏览器: 一次完成 V 获取 + checksiteconfig + 算 n
    from paypal.pw_shared import shared_playwright
    import re as _re
    n = ""
    req = ""
    real_v = asset_v or ""
    with shared_playwright() as pw:
        launch_kwargs = {"headless": headless,
                         "executable_path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                         "args": ["--disable-blink-features=AutomationControlled"]}
        pw_proxy = _pw_proxy(proxy)
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy
        browser = pw.chromium.launch(**launch_kwargs)
        page = browser.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page.goto(
            "https://www.paypalobjects.com/web/res/dec/735e9fd9ebf4231e1be1b853ad922/"
            "hcaptcha/hcaptchapassive_eval.html?siteKey=" + sitekey,
            wait_until="domcontentloaded", timeout=60000,
        )
        frame = None
        deadline = time.time() + 45
        while time.time() < deadline:
            page.wait_for_timeout(1500)
            for f in page.frames:
                if "hcaptcha" in (f.url or "") and "hcaptcha_passive_eval" not in (f.url or ""):
                    try:
                        if f.evaluate("typeof window.hsw") == "function":
                            frame = f
                            break
                    except Exception:
                        continue
            if frame:
                break
        if frame is None:
            browser.close()
            return {"ok": False, "error": "no widget frame / hsw"}
        m = _re.search(r"/captcha/v1/([a-f0-9]{20,})/", frame.url or "")
        if m:
            real_v = m.group(1)
        req = frame.evaluate(
            """async ({host, sitekey}) => {
                const url = 'https://api.hcaptcha.com/checksiteconfig?host=' + host
                    + '&sitekey=' + sitekey + '&sc=1&swa=1&spst=0';
                const resp = await fetch(url, {method: 'POST',
                    headers: {'content-type': 'application/json'}, body: '{}'});
                const j = await resp.json();
                return (j.c && j.c.req) || '';
            }""",
            {"host": host, "sitekey": sitekey},
        )
        if not req:
            browser.close()
            return {"ok": False, "error": "no req from csc"}
        n = frame.evaluate("async (r) => String(await window.hsw(r))", req)
        browser.close()
    if not n:
        return {"ok": False, "error": "browser n empty"}
    asset_v = real_v or asset_v
    c = {"type": "hsw", "req": req}
    parts = req.split(".")
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    pl = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    hsw_url = "https://newassets.hcaptcha.com" + pl["l"] + "/hsw.js"
    hsw_path = wd / "_hsw.js"
    r = sess.get(hsw_url, headers={"User-Agent": UA, "Accept": "*/*",
                                   "Referer": "https://newassets.hcaptcha.com/"}, timeout=60)
    if r.status_code != 200 or (r.text or "").strip().startswith("<"):
        return {"ok": False, "error": "hsw dl %d" % r.status_code}
    hsw_path.write_bytes(r.content)

    if not n:
        return {"ok": False, "error": "browser n empty"}
    asset_v = real_v or asset_v

    # 2. happy-dom 加密
    enc_js_path = wd / "_enc.js"
    enc_js_path.write_text(ENC_JS, encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(enc_js_path)],
        input=json.dumps({
            "n": n, "cObj": c, "sitekey": sitekey, "host": host,
            "userAgent": UA, "hswPath": str(hsw_path), "v": asset_v, "hl": hl,
        }),
        capture_output=True, text=True, timeout=timeout,
        env=_node_env(), cwd=str(_BA_FP),
    )
    d = json.loads(proc.stdout or "{}")
    if not d.get("ok"):
        return {"ok": False, "error": f"enc:{d.get('error','')}", "n_len": len(n)}
    packed = base64.b64decode(d["packed_b64"])

    # 3. getcaptcha
    r = sess.post(
        f"https://api.hcaptcha.com/getcaptcha/{sitekey}",
        headers={**headers, "Accept": "application/json, application/octet-stream",
                 "Content-Type": "application/octet-stream"},
        data=packed, timeout=45,
    )
    ct = r.headers.get("content-type") or ""
    if r.status_code != 200 or "octet-stream" not in ct:
        return {"ok": False, "error": f"gc:{r.status_code}:{ct}", "n_len": len(n),
                "packed": d.get("packed_len")}

    # 4. 解密
    resp_path = wd / "_resp.bin"
    resp_path.write_bytes(r.content)
    dec_js_path = wd / "_dec.js"
    dec_js_path.write_text(DECRYPT_JS, encoding="utf-8")
    proc2 = subprocess.run(
        [_NODE, str(dec_js_path)],
        input=json.dumps({"respPath": str(resp_path), "hswPath": str(hsw_path),
                          "helpersRoot": str(_BA_FP)}),
        capture_output=True, text=True, timeout=timeout, env=_node_env(), cwd=str(_BA_FP),
    )
    raw = (proc2.stdout or "").strip().lstrip("\ufeff")
    out = json.loads(raw)
    tok = out.get("token") or ""
    if tok:
        return {"ok": True, "token": tok, "token_len": len(tok),
                "n_len": len(n), "packed": d.get("packed_len"), "pass": out.get("pass")}
    return {"ok": False, "error": out.get("error") or "no_token", "n_len": len(n),
            "packed": d.get("packed_len")}


def _probe_asset_v(*, sess, ua: str, proxy: str) -> str:
    """获取当前 asset V: 优先 eval 页 iframe 引用, 其次 widget 页面跳转头, 最后 hsw 目录探测。"""
    import re
    candidates = []
    try:
        r = sess.get(
            "https://www.paypalobjects.com/web/res/dec/735e9fd9ebf4231e1be1b853ad922/"
            "hcaptcha/hcaptchapassive_eval.html?siteKey=884d15d9-b649-4bbb-8d1c-2d6f0eed75eb",
            headers={"User-Agent": ua, "Accept": "text/html,*/*"}, timeout=30,
        )
        for m in re.finditer(r"/captcha/v1/([a-f0-9]{20,})/", r.text or ""):
            candidates.append(m.group(1))
        # eval 页动态加载 js.hcaptcha.com api.js; widget iframe 常出现在 data 属性
        m2 = re.search(r"newassets\.hcaptcha\.com/captcha/v1/([a-f0-9]{20,})", r.text or "")
        if m2:
            candidates.append(m2.group(1))
    except Exception:
        pass
    # widget 页面 (hcaptcha.html 根路径) 会 302 到 /captcha/v1/<V>/static/hcaptcha.html
    if not candidates:
        try:
            r = sess.get("https://newassets.hcaptcha.com/captcha/v1/static/hcaptcha.html",
                         headers={"User-Agent": ua, "Accept": "text/html,*/*"},
                         timeout=30, allow_redirects=False)
            loc = r.headers.get("Location", "") or ""
            m = re.search(r"/captcha/v1/([a-f0-9]{20,})/", loc)
            if m:
                candidates.append(m.group(1))
        except Exception:
            pass
    if not candidates:
        try:
            r = sess.get("https://newassets.hcaptcha.com/captcha/v1/",
                         headers={"User-Agent": ua, "Accept": "text/html,*/*"},
                         timeout=30, allow_redirects=False)
            loc = r.headers.get("Location", "") or ""
            m = re.search(r"v1/([a-f0-9]{20,})", loc)
            if m:
                candidates.append(m.group(1))
        except Exception:
            pass
    for c in candidates:
        if c:
            return c
    return ""


__all__ = ["mint_semi_hybrid"]


if __name__ == "__main__":
    sys.path.insert(0, str(_HERE.parent))
    from core.proxy_pool import proxy_pool

    px = proxy_pool.pick_for_stage("checkout", "US") or ""
    res = mint_semi_hybrid(
        sitekey="884d15d9-b649-4bbb-8d1c-2d6f0eed75eb",
        proxy=px,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "token"}, ensure_ascii=False, indent=2))
    if res.get("token"):
        print("TOKEN_PREFIX:", res["token"][:40])