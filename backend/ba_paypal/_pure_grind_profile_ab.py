# -*- coding: utf-8 -*-
"""A/B pure getcaptcha under device profiles (Power-to-Device Ratio hypothesis).

Zero browser. Arms: high_mac_m1 (over-claim) | mid_mac_intel | lowend_mac | matched_host.
Writes _pure_grind_profile_ab_out.json
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from curl_cffi import requests as creq

ROOT = Path(__file__).resolve().parent
SITEKEY = "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"
V = os.environ.get("MIN_BA_HCAPTCHA_ASSET_V") or "ced1647459f073cc025a1281baafa600680d7f3e"
HOST = "www.paypalobjects.com"
NODE_PATH = (
    str(ROOT / "ba_fp_helpers" / "node_modules")
    + os.pathsep
    + r"C:\Users\Administrator\Desktop\GPT_PLUS_PP纯协议版\webui\frontend\node_modules"
)
OUT = ROOT / "_pure_grind_profile_ab_out.json"
PROFILES = [
    "high_mac_m1",
    "mid_mac_intel",
    "lowend_mac",
    "matched_host",
]
POW_JS = ROOT / "_hsw_happy_dom_window_force_pow.js"
if not POW_JS.exists():
    POW_JS = ROOT / "_pure_grind_napi_pow.js"
PACK_JS = ROOT / "_pure_grind_pack_ext18.js"


def node_env(profile: str) -> dict:
    env = os.environ.copy()
    env["NODE_PATH"] = NODE_PATH + os.pathsep + env.get("NODE_PATH", "")
    env["MIN_BA_POW_DEVICE_PROFILE"] = profile
    return env


def jwt_l(req: str) -> str:
    parts = req.split(".")
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    pl = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    return pl.get("l", "")


def main() -> int:
    arms = os.environ.get("AB_PROFILES", ",".join(PROFILES)).split(",")
    arms = [a.strip() for a in arms if a.strip()]
    sess = creq.Session(impersonate="chrome146")
    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )
    h = {
        "User-Agent": ua,
        "Origin": "https://newassets.hcaptcha.com",
        "Referer": "https://newassets.hcaptcha.com/",
        "Accept": "application/json",
    }
    csc = (
        f"https://api.hcaptcha.com/checksiteconfig?v={V}&host={HOST}"
        f"&sitekey={SITEKEY}&sc=1&swa=1&spst=0"
    )
    r = sess.post(csc, headers=h, timeout=45)
    r.raise_for_status()
    c = r.json()["c"]
    req = c["req"]
    hsw_url = "https://newassets.hcaptcha.com" + jwt_l(req) + "/hsw.js"
    hsw_path = ROOT / "_hsw_protocol_live.js"
    hsw_path.write_bytes(sess.get(hsw_url, timeout=60).content)
    print("csc ok hsw", hsw_path.stat().st_size, "v", V[:12], flush=True)

    results = []
    for profile in arms:
        print(f"\n=== arm {profile} ===", flush=True)
        t0 = time.time()
        proc = subprocess.run(
            ["node", str(POW_JS)],
            input=json.dumps(
                {
                    "req": req,
                    "hswPath": str(hsw_path),
                    "userAgent": ua,
                    "forceMode": "window",
                    "deviceProfile": profile,
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=150,
            env=node_env(profile),
            cwd=str(ROOT),
        )
        line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else "{}"
        try:
            pow_data = json.loads(line)
        except Exception:
            pow_data = {"ok": False, "error": f"json:{line[:200]}", "stderr": (proc.stderr or "")[:300]}
        n = str(pow_data.get("n") or pow_data.get("proof") or "")
        row = {
            "profile": profile,
            "pow_ok": bool(pow_data.get("ok")),
            "n_len": len(n),
            "host_sum": pow_data.get("host_sum"),
            "host_unique": pow_data.get("host_unique"),
            "ms": pow_data.get("ms") or pow_data.get("elapsedMs") or round((time.time() - t0) * 1000),
            "deviceProfile": pow_data.get("deviceProfile"),
            "hardwareConcurrency": pow_data.get("hardwareConcurrency"),
            "error": pow_data.get("error"),
            "green": False,
            "token_len": 0,
            "gc": None,
        }
        print(
            f"  pow n_len={row['n_len']} host_sum={row['host_sum']} ms={row['ms']}",
            flush=True,
        )
        if not n or len(n) < 100:
            results.append(row)
            continue
        if not PACK_JS.exists():
            row["error"] = "pack_missing"
            results.append(row)
            continue
        proc2 = subprocess.run(
            ["node", str(PACK_JS)],
            input=json.dumps(
                {
                    "n": n,
                    "cObj": c,
                    "sitekey": SITEKEY,
                    "host": HOST,
                    "userAgent": ua,
                    "hswPath": str(hsw_path),
                    "v": V,
                    "hl": "pt",
                }
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=node_env(profile),
            cwd=str(ROOT),
        )
        pack = json.loads((proc2.stdout or "").strip() or "{}")
        if not pack.get("ok") or not pack.get("packed_b64"):
            row["error"] = f"pack:{pack.get('error')}"
            results.append(row)
            continue
        packed = base64.b64decode(pack["packed_b64"])
        row["pack_len"] = len(packed)
        gr = sess.post(
            f"https://api.hcaptcha.com/getcaptcha/{SITEKEY}",
            headers={
                **h,
                "Accept": "application/json, application/octet-stream",
                "Content-Type": "application/octet-stream",
            },
            data=packed,
            timeout=45,
        )
        ct = (gr.headers.get("content-type") or "").lower()
        gc = {
            "status": gr.status_code,
            "ct": ct[:40],
            "len": len(gr.content or b""),
        }
        if "json" in ct or (gr.content or b"").lstrip()[:1] == b"{":
            try:
                j = gr.json()
            except Exception:
                j = {}
            tok = str(j.get("generated_pass_UUID") or j.get("token") or "")
            gc.update(
                {
                    "success": j.get("success"),
                    "pass": j.get("pass"),
                    "token_len": len(tok),
                    "keys": list(j.keys())[:8],
                }
            )
            if tok and len(tok) > 20:
                row["green"] = True
                row["token_len"] = len(tok)
        else:
            gc["binary"] = True
            # try not decrypt here — binary often means pass
            if gr.status_code == 200 and len(gr.content or b"") > 500:
                row["green"] = True  # provisional; may need decrypt
                row["token_len"] = -1
                gc["maybe_green_binary"] = True
        row["gc"] = gc
        print(f"  gc {gc}", flush=True)
        results.append(row)

    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "v": V,
        "pow_js": POW_JS.name,
        "green_count": sum(1 for r in results if r.get("green")),
        "results": results,
    }
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== SUMMARY ===", flush=True)
    print(json.dumps(summary, indent=2)[:2000], flush=True)
    print("wrote", OUT, flush=True)
    return 0 if summary["green_count"] else 1


if __name__ == "__main__":
    sys.exit(main())
