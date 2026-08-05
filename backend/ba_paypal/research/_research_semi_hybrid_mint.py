# -*- coding: utf-8 -*-
"""RESEARCH semi-hybrid mint: Chrome hsw(req) n → pure Node encrypt → curl → decrypt.

Proves pure encrypt is green when n is browser-class.
Not product runtime (product requires pure n too).
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests as creq
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SITEKEY = "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"
# Live asset V from G11 2026-07-16 (was stale 7d2138a… → pack/host mismatch risk)
V = os.environ.get("MIN_BA_HCAPTCHA_ASSET_V") or "ced1647459f073cc025a1281baafa600680d7f3e"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
NODE_PATH = (
    str(ROOT / "ba_fp_helpers" / "node_modules")
    + os.pathsep
    + r"C:\Users\Administrator\Desktop\GPT_PLUS_PP纯协议版\webui\frontend\node_modules"
)

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
    getRandomValues: (a) => {
      a.set(nc.randomBytes(a.length));
      return a;
    },
  };
  let mp = null;
  try {
    Object.defineProperty(w, 'msgpack', {
      configurable: true,
      get: () => mp,
      set: (v) => {
        mp = v;
        if (v && v.encode) global.__mp = v;
      },
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
  await page
    .goto(
      `https://newassets.hcaptcha.com/captcha/v1/${V}/static/hcaptcha.html#frame=challenge&host=${input.host}&sitekey=${input.sitekey}`,
      { timeout: 40000 }
    )
    .catch(() => {});
  await page.waitUntilComplete({ timeout: 20000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 2500));
  const w = page.mainFrame.window;
  inject(w);
  for (let i = 0; i < 50 && !global.__mp; i++) {
    await new Promise((r) => setTimeout(r, 100));
    if (w.msgpack && w.msgpack.encode) global.__mp = w.msgpack;
  }
  // Fallback: Node @msgpack/msgpack (page may not expose msgpack on happy-dom)
  if (!global.__mp) {
    try {
      global.__mp = require('@msgpack/msgpack');
    } catch (_) {}
  }
  if (!global.__mp) throw new Error('no mp');
  const code = fs.readFileSync(input.hswPath, 'utf8');
  const s = w.document.createElement('script');
  s.textContent = code;
  w.document.body.appendChild(s);
  if (typeof w.hsw !== 'function') throw new Error('no hsw');
  // Prefer official page msgpack; fallback npm WITH ExtData type 18 (required — else HTTP 415).
  let encode = global.__mp && global.__mp.encode && global.__mp.encode.bind(global.__mp);
  let ExtData = global.__mp && global.__mp.ExtData;
  let encodeSource = 'official';
  if (!encode) {
    const npmMp = require('@msgpack/msgpack');
    encode = npmMp.encode.bind(npmMp);
    ExtData = npmMp.ExtData;
    encodeSource = 'npm';
  }
  function asExt18(enc) {
    if (enc && typeof enc === 'object' && Number(enc.type) === 18 && enc.data) {
      return ExtData ? new ExtData(18, enc.data) : enc;
    }
    if (ExtData && enc instanceof ExtData) return enc;
    let u8;
    if (enc instanceof Uint8Array) u8 = enc;
    else if (enc && enc.buffer)
      u8 = new Uint8Array(enc.buffer, enc.byteOffset || 0, enc.byteLength || enc.length);
    else throw new Error('enc not bytes: ' + typeof enc);
    return ExtData ? new ExtData(18, u8) : u8;
  }
  const now = Date.now();
  const body = {
    v: V,
    sitekey: input.sitekey,
    host: input.host,
    hl: input.hl || 'es',
    n: String(input.n),
    motionData: JSON.stringify({
      st: now - 1400,
      dct: now - 1300,
      mm: Array.from({ length: 100 }, (_, i) => [30 + i * 3, 50 + (i % 20), i * 12]),
      'mm-mp': 12.5,
      md: [[100, 80, 500]],
      mu: [[100, 80, 540]],
      topLevel: {
        st: now - 2000,
        sc: {
          width: 1440,
          height: 900,
          availWidth: 1440,
          availHeight: 875,
          colorDepth: 24,
        },
        nv: {
          userAgent: input.userAgent,
          platform: 'MacIntel',
          webdriver: false,
          hardwareConcurrency: 10,
          deviceMemory: 8,
        },
        dr: 'https://www.paypal.com/',
        inv: true,
        exec: true,
      },
      v: 1,
    }),
    pem: JSON.stringify({
      csc: 180,
      csch: 'api.hcaptcha.com',
      cscrt: 40,
      cscft: 200,
    }),
    pst: false,
    p_e: JSON.stringify({
      st: now - 2500,
      sc: { width: 1440, height: 900 },
      nv: {
        userAgent: input.userAgent,
        platform: 'MacIntel',
        webdriver: false,
      },
      dr: '',
      inv: false,
      exec: false,
    }),
  };
  const enc = await w.hsw(1, encode(body));
  // Do NOT strip ExtType 18 — API returns 415 if second array element is plain bin.
  const packed = encode([JSON.stringify(input.cObj), asExt18(enc)]);
  const out = Buffer.from(packed instanceof Uint8Array ? packed : new Uint8Array(packed));
  process.stdout.write(
    JSON.stringify({
      ok: true,
      packed_b64: out.toString('base64'),
      solved_len: String(input.n).length,
      packed_len: out.length,
      encode_source: encodeSource,
      packed_head: out.slice(0, 12).toString('hex'),
      enc_type: enc && enc.constructor && enc.constructor.name,
    })
  );
  await browser.close().catch(() => {});
})().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && (e.stack || e)) }));
  process.exit(1);
});
"""


def mint_semi_hybrid(region: str = "MX") -> dict:
    import proxy_711 as p

    proxy = p.build_residential_proxy(
        region=region, session="sh" + str(int(time.time()) % 10000), sess_time=40
    )
    s = creq.Session(impersonate="chrome146")
    headers = {
        "User-Agent": UA,
        "Origin": "https://newassets.hcaptcha.com",
        "Referer": "https://newassets.hcaptcha.com/",
        "Accept": "application/json",
    }
    csc = s.post(
        f"https://api.hcaptcha.com/checksiteconfig?v={V}&host=www.paypalobjects.com"
        f"&sitekey={SITEKEY}&sc=1&swa=1&spst=0",
        headers=headers,
        proxy=proxy,
        timeout=40,
    )
    if csc.status_code != 200:
        return {"ok": False, "error": f"csc {csc.status_code}"}
    c = csc.json()["c"]
    parts = c["req"].split(".")
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    pl = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    hsw_url = "https://newassets.hcaptcha.com" + pl["l"] + "/hsw.js"
    hsw_path = ROOT / "_hsw_semi.js"
    hsw_path.write_bytes(s.get(hsw_url, proxy=proxy, timeout=60).content)

    u = urlparse(proxy)
    pw = {
        "server": f"http://{u.hostname}:{u.port}",
        "username": u.username or "",
        "password": u.password or "",
    }
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    with sync_playwright() as pwt:
        browser = pwt.chromium.launch(
            headless=True,
            executable_path=chrome if os.path.isfile(chrome) else None,
            proxy=pw,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page.goto(
            f"https://newassets.hcaptcha.com/captcha/v1/{V}/static/hcaptcha.html"
            f"#frame=challenge&host=www.paypalobjects.com&sitekey={SITEKEY}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(1500)
        page.evaluate(
            """
            (url) => new Promise((resolve, reject) => {
              const s = document.createElement('script');
              s.src = url;
              s.onload = () => resolve(typeof hsw);
              s.onerror = () => reject(new Error('hsw load fail'));
              document.head.appendChild(s);
            })
            """,
            hsw_url,
        )
        page.wait_for_timeout(400)
        solved = page.evaluate(
            "async (req) => String(await hsw(req))",
            c["req"],
        )
        browser.close()
    print("browser_n", len(solved), flush=True)

    enc_path = ROOT / "_tmp_semi_enc.js"
    enc_path.write_text(ENC_JS, encoding="utf-8")
    env = os.environ.copy()
    env["NODE_PATH"] = NODE_PATH
    proc = subprocess.run(
        ["node", str(enc_path)],
        input=json.dumps(
            {
                "n": solved,
                "cObj": c,
                "sitekey": SITEKEY,
                "host": "www.paypalobjects.com",
                "userAgent": UA,
                "hswPath": str(hsw_path),
                "v": V,
                "hl": "es",
            }
        ),
        capture_output=True,
        text=True,
        timeout=150,
        env=env,
        cwd=str(ROOT),
    )
    d = json.loads(proc.stdout or "{}")
    if not d.get("ok"):
        return {"ok": False, "error": f"enc:{d.get('error')}", "n_len": len(solved)}
    packed = base64.b64decode(d["packed_b64"])
    print("packed", d.get("packed_len"), flush=True)
    r = s.post(
        f"https://api.hcaptcha.com/getcaptcha/{SITEKEY}",
        headers={
            **headers,
            "Accept": "application/json, application/octet-stream",
            "Content-Type": "application/octet-stream",
        },
        data=packed,
        proxy=proxy,
        timeout=45,
    )
    ct = r.headers.get("content-type") or ""
    print("gc", r.status_code, ct, len(r.content), flush=True)
    if r.status_code != 200 or "octet-stream" not in ct:
        return {
            "ok": False,
            "error": f"gc:{r.status_code}:{ct}",
            "n_len": len(solved),
            "packed": d.get("packed_len"),
        }
    resp_path = ROOT / "_semi_resp.bin"
    resp_path.write_bytes(r.content)
    proc2 = subprocess.run(
        ["node", str(ROOT / "ba_fp_helpers" / "hsw_decrypt_resp_node.js")],
        input=json.dumps(
            {
                "respPath": str(resp_path.resolve()),
                "hswPath": str(hsw_path.resolve()),
                "sitekey": SITEKEY,
                "host": "www.paypalobjects.com",
            }
        ),
        capture_output=True,
        text=True,
        timeout=150,
        env=env,
        cwd=str(ROOT),
    )
    raw = (proc2.stdout or "").strip().lstrip("\ufeff")
    out = json.loads(raw)
    tok = out.get("token") or ""
    if tok:
        (ROOT / "_token_success.txt").write_text(tok, encoding="utf-8")
        (ROOT / "_token_source.txt").write_text(
            "semi_hybrid_browser_n_pure_enc", encoding="utf-8"
        )
        (ROOT / "_hybrid_plain_obj.json").write_text(
            json.dumps(
                {
                    "pass": out.get("pass"),
                    "expiration": out.get("expiration"),
                    "token_len": len(tok),
                    "source": "semi_hybrid_browser_n_pure_enc",
                    "n_len": len(solved),
                    "packed": d.get("packed_len"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print("TOKEN", len(tok), flush=True)
        return {
            "ok": True,
            "token": tok,
            "token_len": len(tok),
            "n_len": len(solved),
            "packed": d.get("packed_len"),
        }
    return {"ok": False, "error": out.get("error") or "no_token", "n_len": len(solved)}


def main() -> None:
    res = mint_semi_hybrid()
    print(json.dumps({k: res[k] for k in res if k != "token"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
