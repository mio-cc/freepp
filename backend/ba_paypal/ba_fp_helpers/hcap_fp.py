"""纯 Python hCaptcha 指纹构建器 (zero-browser)。

参考 Implex-ltd/hcaptcha-reverse fp_build: hsw(jwt, fp_json_b64) 双参数签名,
指纹由外部构建为明文 JSON, base64 后作为 hsw 第二参数传入。
"""
import base64
import json
import random
import time


def _b64u_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.replace("_", "/").replace("-", "+"))


def parse_req(req_jwt: str) -> dict:
    h, p, _ = req_jwt.split(".")
    header = json.loads(_b64u_decode(h))
    payload = json.loads(_b64u_decode(p))
    return {"header": header, "payload": payload}


def _crc32_rev(data: bytes) -> int:
    table = [0] * 256
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        table[i] = crc
    crc = 0xFFFFFFFF
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def rand_hash(data: bytes) -> float:
    crc = _crc32_rev(data)
    return crc * 2.3283064365386963e-10


def build_fp(req_jwt: str, *, user_agent: str | None = None,
             with_events: bool = False) -> dict:
    pl = parse_req(req_jwt)["payload"]
    s = pl["s"]
    f = pl.get("f", 0)
    t = pl.get("t", "w")
    d = pl["d"]
    l = pl.get("l", "")
    c = pl.get("c", 1000)
    location = ("https://newassets.hcaptcha.com" + l) if not l.startswith("http") else l

    ua = user_agent or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36")
    lang = "en-US"
    langs = ["en-US", "en"]

    proof_spec = {
        "difficulty": s,
        "fingerprint_type": f,
        "_type": t,
        "data": d,
        "_location": location,
        "timeout_value": c,
    }

    components = {
        "navigator": {
            "user_agent": ua,
            "language": lang,
            "languages": langs,
            "platform": "Win32",
            "max_touch_points": 0,
            "webdriver": False,
            "notification_query_permission": None,
            "plugins_undefined": False,
        },
        "screen": {
            "color_depth": 24,
            "pixel_depth": 24,
            "width": 1920,
            "height": 1080,
            "avail_width": 1920,
            "avail_height": 1040,
        },
        "device_pixel_ratio": 1,
        "has_session_storage": True,
        "has_local_storage": True,
        "has_indexed_db": True,
        "web_gl_hash": "-1",
        "canvas_hash": "9373501784251927131",
        "has_touch": False,
        "notification_api_permission": "Denied",
        "chrome": True,
        "to_string_length": 33,
        "err_firefox": None,
        "r_bot_score": 0,
        "r_bot_score_suspicious_keys": [],
        "r_bot_score_2": 0,
        "audio_hash": "-1",
        "extensions": [False],
        "parent_win_hash": "9751511312137185722",
        "webrtc_hash": "-1",
        "performance_hash": "4140103483592612201",
        "unique_keys": "0,IntlPolyfill,hcaptcha,__SECRET_EMOTION__,grecaptcha,platform,1,regeneratorRuntime,hcaptchaOnLoad",
        "inv_unique_keys": "__wdata,image_label_binary,_sharedLibs,text_free_entry,sessionStorage,hsw,localStorage",
        "features": {
            "performance_entries": True,
            "web_audio": True,
            "web_rtc": True,
            "canvas_2d": True,
            "fetch": True,
        },
    }

    events = []
    if with_events:
        events = [
            [107, "[1920,1080,1920,1040,24,24,false,0,1,1920,1080,true,true,true,false]"],
            [1302, "[1,2,3,4]"],
            [1401, '"UTC"'],
            [3503, str(int(time.time()))],
        ]

    fp = {
        "proof_spec": proof_spec,
        "rand": [0.33912900034451066, "_rand"],
        "components": components,
        "fingerprint_events": events,
    }

    s1 = json.dumps(fp, separators=(",", ":"))
    s_no_rand = s1.replace('"_rand"', "")
    s_no_rand = s_no_rand.replace(":,_", ":_")
    s_no_rand = s_no_rand.replace(",_", "")
    r = rand_hash(s_no_rand.encode("utf-8"))
    s2 = s1.replace('"_rand"', repr(round(r, 16)))
    return json.loads(s2)


def build_fp_b64(req_jwt: str, **kw) -> str:
    fp = build_fp(req_jwt, **kw)
    return base64.b64encode(json.dumps(fp, separators=(",", ":")).encode()).decode()


if __name__ == "__main__":
    import sys
    req = sys.argv[1] if len(sys.argv) > 1 else ""
    if not req:
        print("usage: python hcap_fp.py <req_jwt>")
        sys.exit(1)
    fp = build_fp(req)
    print(json.dumps(fp, indent=1)[:800])
    print("---b64---")
    print(base64.b64encode(json.dumps(fp, separators=(",", ":")).encode()).decode()[:200])
