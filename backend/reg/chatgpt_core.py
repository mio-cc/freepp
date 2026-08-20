import json
import os
import re
import sys
import time
import uuid
import random
import string
import secrets
import hashlib
import base64
import argparse
import threading
from pathlib import Path
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request
import urllib.error

# Windows 默认控制台/管道常为 GBK：print emoji 会 UnicodeEncodeError 直接崩。
# ops.bat 重定向到 logs\*.log 时尤其如此。启动早期把 stdout/stderr 切到 UTF-8。
def _force_utf8_stdio() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(_stream, "reconfigure"):
                _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

# --- end temporary hook ---

# 用带重试的 curl_cffi Session 垫片（修经 mihomo 代理的 intermittent TLS 'invalid library'）
from .cf_shim import requests, Session
# OpenAI 官方 Sentinel SDK（Camoufox 加载，生成 Sentinel-Token / SO-Token）
from . import sentinel_sdk
# 按邮箱域名统计注册成功率，停用低成功率域名
from . import provider_stats
# 711 住宅代理：curl_cffi 直连会 CONNECT aborted，需经本机 relay→Clash→711
from core import proxy_711  # noqa: E402
# 邮箱渠道: 全部由调用方 (reg/engine) 自定义渠道注册表注入
import atexit
atexit.register(lambda: sentinel_sdk.close_browser())

# 配置输出目录和请求UA（默认值 = 指纹池首项 chrome131 Win；run() 内每号独立选用）
OUT_DIR = Path(__file__).parent.resolve()

# 注册兜底：单阶段（如 authorize）最大重试；外层 while True 总尝试上限（防代理/池全挂时空耗）
MAX_STAGE_RETRY = 5
MAX_TOTAL_ATTEMPTS = 30

# 登录取 cookie：authorize 落 email-verification 时走邮箱 OTP（两阶段等待）
LOGIN_OTP_PHASE1_SEC = 45   # 先等 openai 自动下发的验证码，超时 resend
LOGIN_OTP_PHASE2_SEC = 90   # resend 后只收新码

# 注册流程 IMAP OTP 两阶段时长 (秒)
IMAP_OTP_PHASE_SEC = 60        # 默认/密码流: 单阶段等待
IMAP_OTP_WEB_PHASE_SEC = 90     # web 流 phase1: authorize 自动下发 OTP, IMAP 轮询有延迟
IMAP_OTP_WEB_PHASE2_SEC = 90    # web 流 phase2: resend 后再等
IMAP_OTP_ICLOUD_PHASE_SEC = 120  # icloud HME 转发延迟更大
IMAP_OTP_DATE_SKEW_SEC = 30     # not_before 时间容差 (防 clock skew)


def _build_chrome_fp(
    ua: str,
    major: str,
    *,
    platform: str = "Windows",
    platform_version: str = "10.0.0",
    accept_language: str = "en-US,en;q=0.9",
) -> Dict[str, str]:
    """构造与 UA / impersonate 版本自洽的 Client Hints 头包。"""
    sec_ch_ua = (
        f'"Not:A-Brand";v="99", "Google Chrome";v="{major}", "Chromium";v="{major}"'
    )
    full_ver = (
        f'"Not:A-Brand";v="99.0.0.0", "Google Chrome";v="{major}.0.0.0", '
        f'"Chromium";v="{major}.0.0.0"'
    )
    return {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": accept_language,
        "priority": "u=1, i",
        "sec-ch-ua": sec_ch_ua,
        "sec-ch-ua-full-version-list": full_ver,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{platform}"',
        "sec-ch-ua-platform-version": f'"{platform_version}"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": ua,
        "upgrade-insecure-requests": "1",
    }


def _fp_profile(
    *,
    pid: str,
    major: str,
    impersonate: str,
    ua: str,
    platform: str,
    nav_platform: str,
    platform_version: str,
    languages: tuple,
    screen_w: int,
    screen_h: int,
    px_ratio: float,
    canvas_seed: str,
    webgl_vendor: str,
    webgl_renderer: str,
    hardware_concurrency: int = 8,
    device_memory: float = 8.0,
    accept_language: str = "en-US,en;q=0.9",
) -> Dict[str, Any]:
    """单个配套指纹 profile：HTTP 头 + TLS impersonate + sentinel VM 字段同一套。"""
    ch_platform = "Windows" if platform.lower().startswith("win") else (
        "macOS" if platform.lower().startswith("mac") else platform
    )
    chrome_fp = _build_chrome_fp(
        ua, major,
        platform=ch_platform,
        platform_version=platform_version,
        accept_language=accept_language,
    )
    return {
        "id": pid,
        "ua": ua,
        "impersonate": impersonate,
        "chrome_fp": chrome_fp,
        "platform": nav_platform,          # navigator.platform e.g. Win32 / MacIntel
        "os_platform": ch_platform,        # sec-ch-ua-platform e.g. Windows / macOS
        "languages": list(languages),
        "screen": {
            "width": screen_w,
            "height": screen_h,
            "px_ratio": px_ratio,
        },
        "canvas_seed": canvas_seed,
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "hardware_concurrency": hardware_concurrency,
        "device_memory": device_memory,
        "chrome_major": major,
    }


# ≥12 个真实 Chrome 指纹 profile（impersonate 须 curl_cffi 支持；UA/sec-ch-ua/major 严格绑定）
# curl_cffi 0.15 桌面 Chrome：100/101/104/107/110/116/119/120/123/124/131/133a/136/142/145/146
FP_POOL: list = [
    # --- Chrome 131 Windows 多分辨率 ---
    _fp_profile(
        pid="chrome131_win_fhd",
        major="131", impersonate="chrome131",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en"),
        screen_w=1920, screen_h=1080, px_ratio=1.0,
        canvas_seed="win-fhd-gtx1060-a1",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=8, device_memory=8.0,
    ),
    _fp_profile(
        pid="chrome131_win_qhd",
        major="131", impersonate="chrome131",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=2560, screen_h=1440, px_ratio=1.0,
        canvas_seed="win-qhd-rtx3060-b2",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=12, device_memory=16.0,
    ),
    _fp_profile(
        pid="chrome131_win_hd",
        major="131", impersonate="chrome131",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en", "zh-CN"),
        screen_w=1366, screen_h=768, px_ratio=1.0,
        canvas_seed="win-hd-uhd620-c3",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=4, device_memory=8.0,
        accept_language="en-US,en;q=0.9,zh-CN;q=0.8",
    ),
    _fp_profile(
        pid="chrome131_win_uhd",
        major="131", impersonate="chrome131",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=3840, screen_h=2160, px_ratio=1.5,
        canvas_seed="win-uhd-rtx4070-c4",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=16, device_memory=32.0,
    ),
    _fp_profile(
        pid="chrome131_mac_retina",
        major="131", impersonate="chrome131",
        ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        platform="macOS", nav_platform="MacIntel", platform_version="14.2.1",
        languages=("en-US", "en"),
        screen_w=1440, screen_h=900, px_ratio=2.0,
        canvas_seed="mac-retina-applem1-d4",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M1, OpenGL 4.1)",
        hardware_concurrency=8, device_memory=8.0,
    ),
    # --- Chrome 133 / 136 ---
    _fp_profile(
        pid="chrome133_win_fhd",
        major="133", impersonate="chrome133a",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en"),
        screen_w=1920, screen_h=1080, px_ratio=1.0,
        canvas_seed="win-fhd-rtx4060-e5",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=16, device_memory=16.0,
    ),
    _fp_profile(
        pid="chrome133_win_laptop",
        major="133", impersonate="chrome133a",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en"),
        screen_w=1600, screen_h=900, px_ratio=1.25,
        canvas_seed="win-1600-irisxe-e6",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=8, device_memory=16.0,
    ),
    _fp_profile(
        pid="chrome136_win_qhd",
        major="136", impersonate="chrome136",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=2560, screen_h=1440, px_ratio=1.25,
        canvas_seed="win-qhd-rx7600-f6",
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer="ANGLE (AMD, AMD Radeon RX 7600 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=12, device_memory=32.0,
    ),
    _fp_profile(
        pid="chrome136_win_fhd",
        major="136", impersonate="chrome136",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en", "es"),
        screen_w=1920, screen_h=1080, px_ratio=1.0,
        canvas_seed="win-fhd-gtx1660-f7",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=6, device_memory=16.0,
        accept_language="en-US,en;q=0.9,es;q=0.8",
    ),
    _fp_profile(
        pid="chrome136_mac_studio",
        major="136", impersonate="chrome136",
        ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
        platform="macOS", nav_platform="MacIntel", platform_version="15.1.0",
        languages=("en-US", "en"),
        screen_w=1680, screen_h=1050, px_ratio=2.0,
        canvas_seed="mac-studio-m2pro-h8",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M2 Pro, OpenGL 4.1)",
        hardware_concurrency=10, device_memory=16.0,
    ),
    _fp_profile(
        pid="chrome136_mac_mba",
        major="136", impersonate="chrome136",
        ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
        platform="macOS", nav_platform="MacIntel", platform_version="14.5.0",
        languages=("en-US", "en"),
        screen_w=2560, screen_h=1600, px_ratio=2.0,
        canvas_seed="mac-mba-m3-h9",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M3, OpenGL 4.1)",
        hardware_concurrency=8, device_memory=16.0,
    ),
    # --- 较旧 / 较新 major（分散 JA3 池） ---
    _fp_profile(
        pid="chrome124_win_fhd",
        major="124", impersonate="chrome124",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-GB", "en-US", "en"),
        screen_w=1536, screen_h=864, px_ratio=1.25,
        canvas_seed="win-1536-irisxe-g7",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=8, device_memory=16.0,
        accept_language="en-GB,en-US;q=0.9,en;q=0.8",
    ),
    _fp_profile(
        pid="chrome120_win_hdplus",
        major="120", impersonate="chrome120",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en"),
        screen_w=1680, screen_h=1050, px_ratio=1.0,
        canvas_seed="win-1680-rx580-i1",
        webgl_vendor="Google Inc. (AMD)",
        webgl_renderer="ANGLE (AMD, AMD Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=8, device_memory=16.0,
    ),
    _fp_profile(
        pid="chrome123_win_fhd",
        major="123", impersonate="chrome123",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="10.0.0",
        languages=("en-US", "en"),
        screen_w=1920, screen_h=1200, px_ratio=1.0,
        canvas_seed="win-1920x1200-uhd770-i2",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=12, device_memory=32.0,
    ),
    _fp_profile(
        pid="chrome142_win_qhd",
        major="142", impersonate="chrome142",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=2560, screen_h=1440, px_ratio=1.0,
        canvas_seed="win-qhd-rtx4070ti-j1",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Ti Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=16, device_memory=32.0,
    ),
    _fp_profile(
        pid="chrome142_mac_retina",
        major="142", impersonate="chrome142",
        ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"),
        platform="macOS", nav_platform="MacIntel", platform_version="15.2.0",
        languages=("en-US", "en"),
        screen_w=1512, screen_h=982, px_ratio=2.0,
        canvas_seed="mac-14m3pro-j2",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M3 Pro, OpenGL 4.1)",
        hardware_concurrency=12, device_memory=18.0,
    ),
    _fp_profile(
        pid="chrome145_win_fhd",
        major="145", impersonate="chrome145",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=1920, screen_h=1080, px_ratio=1.0,
        canvas_seed="win-fhd-arc-a770-k1",
        webgl_vendor="Google Inc. (Intel)",
        webgl_renderer="ANGLE (Intel, Intel(R) Arc(TM) A770 Graphics Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=16, device_memory=32.0,
    ),
    _fp_profile(
        pid="chrome146_win_uwqhd",
        major="146", impersonate="chrome146",
        ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"),
        platform="Windows", nav_platform="Win32", platform_version="15.0.0",
        languages=("en-US", "en"),
        screen_w=3440, screen_h=1440, px_ratio=1.0,
        canvas_seed="win-uwqhd-rtx4080-k2",
        webgl_vendor="Google Inc. (NVIDIA)",
        webgl_renderer="ANGLE (NVIDIA, NVIDIA GeForce RTX 4080 Direct3D11 vs_5_0 ps_5_0)",
        hardware_concurrency=24, device_memory=64.0,
    ),
    _fp_profile(
        pid="chrome146_mac_studio",
        major="146", impersonate="chrome146",
        ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"),
        platform="macOS", nav_platform="MacIntel", platform_version="15.3.0",
        languages=("en-US", "en"),
        screen_w=3008, screen_h=1692, px_ratio=2.0,
        canvas_seed="mac-studio-m2max-k3",
        webgl_vendor="Google Inc. (Apple)",
        webgl_renderer="ANGLE (Apple, Apple M2 Max, OpenGL 4.1)",
        hardware_concurrency=12, device_memory=32.0,
    ),
]

# 向后兼容：默认全局常量指向池中首项（非 OpenAI 路径可继续用）
_DEFAULT_FP = FP_POOL[0]
UA = _DEFAULT_FP["ua"]
IMPERSONATE = _DEFAULT_FP["impersonate"]
SEC_CH_UA = _DEFAULT_FP["chrome_fp"]["sec-ch-ua"]
CHROME_FP = dict(_DEFAULT_FP["chrome_fp"])


def choose_fp(seed: Optional[str] = None) -> Dict[str, Any]:
    """每号选用一套配套指纹。

    - 环境变量 ANTI_FUZZ_FP_ID：固定某 profile（id 或 0-based 下标，调试用）
    - 传入 seed/email：SHA256 稳定映射到池内一项
    - 否则 random.choice
    返回深拷贝，避免会话间互相污染 chrome_fp dict。
    """
    force = (os.environ.get("ANTI_FUZZ_FP_ID") or "").strip()
    picked = None
    if force:
        if force.isdigit():
            idx = int(force) % len(FP_POOL)
            picked = FP_POOL[idx]
        else:
            for p in FP_POOL:
                if p.get("id") == force:
                    picked = p
                    break
            if picked is None:
                # 允许 chrome131 / chrome136 等按 major 或 impersonate 匹配首项
                for p in FP_POOL:
                    if force in (p.get("impersonate"), p.get("chrome_major"), f"chrome{p.get('chrome_major')}"):
                        picked = p
                        break
        if picked is None:
            print(f"[anti-fuzz] ANTI_FUZZ_FP_ID={force!r} 未匹配，回退随机")
    if picked is None and seed:
        h = hashlib.sha256(str(seed).encode("utf-8", errors="replace")).hexdigest()
        idx = int(h[:8], 16) % len(FP_POOL)
        picked = FP_POOL[idx]
    if picked is None:
        picked = random.choice(FP_POOL)
    # 深拷贝可变子结构
    out = dict(picked)
    out["chrome_fp"] = dict(picked["chrome_fp"])
    out["screen"] = dict(picked["screen"])
    out["languages"] = list(picked["languages"])
    return out


def _fp_summary(fp: Dict[str, Any]) -> str:
    scr = fp.get("screen") or {}
    return (
        f"id={fp.get('id')} chrome={fp.get('chrome_major')} "
        f"impersonate={fp.get('impersonate')} platform={fp.get('platform')} "
        f"screen={scr.get('width')}x{scr.get('height')}@{scr.get('px_ratio')}"
    )


def _bind_session_fp(session, fp: Dict[str, Any]) -> None:
    """把本号配套指纹挂到 Session，供 next-auth 等辅助函数读取。"""
    try:
        session._anti_fuzz_fp = fp  # type: ignore[attr-defined]
        session._anti_fuzz_ua = fp.get("ua") or UA  # type: ignore[attr-defined]
        session._anti_fuzz_impersonate = fp.get("impersonate") or IMPERSONATE  # type: ignore[attr-defined]
    except Exception:
        pass


def _session_ua(session=None, default: str = "") -> str:
    if session is not None:
        ua = getattr(session, "_anti_fuzz_ua", None)
        if ua:
            return ua
        try:
            h = session.headers.get("user-agent") or session.headers.get("User-Agent")
            if h:
                return h
        except Exception:
            pass
    return default or UA


def _session_impersonate(session=None, default: str = "") -> str:
    if session is not None:
        imp = getattr(session, "_anti_fuzz_impersonate", None)
        if imp:
            return imp
        imp = getattr(session, "impersonate", None)
        if imp:
            return str(imp)
    return default or IMPERSONATE


def _make_trace_headers():
    """生成 Datadog APM trace headers（和真实浏览器的 RUM SDK 一致）"""
    trace_id = random.randint(10**17, 10**18 - 1)
    parent_id = random.randint(10**17, 10**18 - 1)
    tp = f"00-{uuid.uuid4().hex}-{format(parent_id, '016x')}-01"
    return {
        "traceparent": tp, "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum", "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": str(trace_id), "x-datadog-parent-id": str(parent_id),
    }


# ---------- 防风控：随机时间差 + 每号新设备（见 ANTI_FUZZING.md）----------
# ANTI_FUZZ=0 可关闭步骤抖动（调试）；号间冷却仍建议保留。
def _anti_fuzz_enabled() -> bool:
    return os.environ.get("ANTI_FUZZ", "1").strip() not in ("0", "false", "False", "no", "off")


def _new_oai_did() -> str:
    """每次注册强制新设备 ID（UUID）。同一次 run() 内全程复用，禁止跨号复用。"""
    return str(uuid.uuid4())


# 线程本地取消回调：并发注册时每线程独立，避免全局列表互踩。
# 仍保留 list 形态兼容旧引用，但读写走 TLS。
_CANCEL_TLS = threading.local()
_CANCEL_HOLDER = [None]  # 兼容：单线程/旧路径仍可写 [0]；_interruptible_sleep 优先 TLS


def _set_cancel_check(fn):
    _CANCEL_TLS.fn = fn
    _CANCEL_HOLDER[0] = fn


def _get_cancel_check():
    fn = getattr(_CANCEL_TLS, "fn", None)
    if fn is not None:
        return fn
    return _CANCEL_HOLDER[0]


def _interruptible_sleep(t: float) -> None:
    """可被取消信号中断的 sleep（0.2s 粒度）。取消回调返回真则立即返回。"""
    end = time.time() + t
    while True:
        ck = _get_cancel_check()
        if ck and ck():
            return
        remaining = end - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))

def _human_delay(lo: float = 0.4, hi: float = 1.8, label: str = "") -> float:
    """步骤间真人节奏抖动：均匀分布 + 偶发长停顿。返回实际 sleep 秒数。"""
    if not _anti_fuzz_enabled():
        return 0.0
    try:
        lo_e = float(os.environ.get("ANTI_FUZZ_STEP_LO", lo))
        hi_e = float(os.environ.get("ANTI_FUZZ_STEP_HI", hi))
        lo, hi = lo_e, hi_e
    except Exception:
        pass
    if hi < lo:
        lo, hi = hi, lo
    t = random.uniform(lo, hi)
    # 约 15% 概率「发呆」一下，打散机械节奏
    if random.random() < 0.15:
        t += random.uniform(0.5, 2.5)
    if label:
        print(f"[anti-fuzz] delay {t:.2f}s ({label})")
    _interruptible_sleep(t)
    return t


def _batch_cooldown_seconds() -> int:
    """账号与账号之间的冷却（秒）。环境变量可覆盖上下限。"""
    try:
        lo = int(os.environ.get("ANTI_FUZZ_BATCH_LO", "8"))
        hi = int(os.environ.get("ANTI_FUZZ_BATCH_HI", "25"))
    except Exception:
        lo, hi = 8, 25
    if hi < lo:
        lo, hi = hi, lo
    return random.randint(lo, hi)


def _retry_backoff(attempt: int, base: float = 0.8) -> float:
    """失败重试：指数退避 + 抖动 base * 2^(n-1) + U(0,1)。attempt 从 1 起。"""
    if not _anti_fuzz_enabled():
        t = float(attempt)
        _interruptible_sleep(t)
        return t
    n = max(1, int(attempt))
    t = float(base) * (2 ** (n - 1)) + random.uniform(0.0, 1.0)
    print(f"[anti-fuzz] retry backoff {t:.2f}s (attempt={n})")
    _interruptible_sleep(t)
    return t


def _bind_oai_did(session, did: str) -> None:
    """把 oai-did 写入 chatgpt.com + .openai.com，保证 authorize / sentinel 同源。"""
    if not did:
        return
    try:
        session.cookies.set("oai-did", did, domain=".openai.com", path="/")
        session.cookies.set("oai-did", did, domain="chatgpt.com", path="/")
    except Exception:
        pass


def _msg_date_ts(msg):
    """解析邮件 Date 头为 unix timestamp；失败返回 None。"""
    try:
        raw = msg.get("Date") if msg is not None else None
        if not raw:
            return None
        dt = parsedate_to_datetime(str(raw))
        if dt is None:
            return None
        return float(dt.timestamp())
    except Exception:
        return None


def _imap_fetch_message_bytes(conn, mid):
    """拉取完整邮件原始字节。

    iCloud (imap.mail.me.com) 对 (RFC822) 常返回空体 ``N ()``，BODY.PEEK[] 正常。
    163/126 两种均可；优先 BODY.PEEK[]（不改 \\Seen），失败再回退 RFC822。
    """
    for spec in ("(BODY.PEEK[])", "(RFC822)"):
        try:
            _typ, data = conn.fetch(mid, spec)
        except Exception:
            continue
        if not data:
            continue
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                if item[1]:
                    return bytes(item[1])
    return None


def imap_get_otp(email_alias=None, cancel_check=None, timeout_min=None, timeout_sec=None,
                 seen_ids=None, not_before=None, imap_label=None):
    """连接转发邮箱（IMAP），遍历 IMAP_ACCOUNTS 在各收件箱查找 OpenAI 验证码。
    email_alias: 注册用的 @duck.com 别名，用于过滤 DDG 转发过来的邮件。
    timeout_sec: 优先；给定时按秒截止（两阶段 OTP 用）。
    timeout_min: 兼容旧调用；timeout_sec 未给时用分钟（默认 IMAP_TIMEOUT_MIN）。
    seen_ids: 可选 set，跨阶段复用已见邮件 id（带账户前缀），避免 resend 后重复扫旧信。
    not_before: 可选 unix ts；只接受 Date >= not_before - SKEW 的 OTP，挡历史旧码 / resend 前 OTP1。
    """
    accounts = list(IMAP_ACCOUNTS) if IMAP_ACCOUNTS else []
    if imap_label:
        _filtered = [a for a in accounts if (a.get("label") or "") == imap_label]
        if _filtered:
            accounts = _filtered
        # 指定 label 且存在才过滤；否则保持全部（兼容 duck/163 不传 label）
    if not accounts:
        # 向后兼容：无列表时回退单账户 IMAP_* 常量
        if IMAP_USER:
            accounts = [{
                "host": IMAP_HOST, "port": IMAP_PORT,
                "user": IMAP_USER, "auth": IMAP_AUTH, "label": "default",
            }]
    if not accounts or not any(a.get("user") for a in accounts):
        print("  [!] 未配置 IMAP_ACCOUNTS / IMAP_USER（转发邮箱地址）")
        return None
    if timeout_sec is not None:
        total_sec = max(1, int(timeout_sec))
    else:
        total_sec = int((timeout_min or IMAP_TIMEOUT_MIN) * 60)
    import imaplib
    import email as _email

    deadline = time.time() + total_sec
    # 短超时（两阶段 10s）用 1s 轮询、缩短基线后等待；长超时保持原 5s 节奏
    short_mode = total_sec <= 45
    baseline_sleep = 1 if short_mode else 5
    poll_sleep = 1 if short_mode else 5
    if seen_ids is None:
        seen_ids = set()
    # 每账户独立基线；跨阶段复用 seen 时全部跳过基线重建
    skip_baseline = bool(seen_ids)
    baseline_done = {
        (a.get("label") or a.get("user") or str(i)): skip_baseline
        for i, a in enumerate(accounts)
    }
    alias = (email_alias or "").lower()
    skew = IMAP_OTP_DATE_SKEW_SEC
    nb = float(not_before) if not_before is not None else None

    def _seen_key(label, mid):
        """跨邮箱 mid 可能冲突，用 label 前缀隔离。"""
        mid_s = mid.decode("utf-8", errors="replace") if isinstance(mid, (bytes, bytearray)) else str(mid)
        return f"{label}:{mid_s}"

    while time.time() < deadline:
        if cancel_check and cancel_check():
            return None

        any_baseline_this_round = False

        for acc_i, acc in enumerate(accounts):
            if cancel_check and cancel_check():
                return None
            if time.time() >= deadline:
                return None

            label = acc.get("label") or acc.get("user") or str(acc_i)
            host = acc.get("host") or IMAP_HOST
            port = int(acc.get("port") or IMAP_PORT or 993)
            user = acc.get("user") or ""
            auth = acc.get("auth") or ""
            if not user or not auth:
                continue

            # 163/126/yeah.net 必须先发 IMAP ID 命令，否则登录被拒
            need_id = any(h in host for h in ("163.com", "126.com", "yeah.net"))

            conn = None
            try:
                conn = imaplib.IMAP4_SSL(host, port, timeout=15)
                if need_id:
                    imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED')
                    conn._simple_command('ID', '("name" "IMAPClient" "version" "1.0")')
                conn.login(user, auth)
                conn.select("INBOX", readonly=True)

                _, msg_nums = conn.search(None, "ALL")
                ids = msg_nums[0].split() if msg_nums and msg_nums[0] else []

                # 首次轮询建立基线：只把「非 OTP」的历史邮件标记为已见。
                # 关键修复：若验证码在首次轮询前就已到达（OpenAI 通常 5s 内送达），
                # 不能把它一并计入基线，否则会被永久跳过 → 卡在等待 OTP。
                # 因此基线只跳过真正的旧邮件，OTP 邮件即使早于轮询也保留待提取。
                # not_before：OTP 若 Date 早于窗口（或无 Date）也标 seen，挡历史旧码。
                if not baseline_done.get(label):
                    skipped = 0
                    for mid in ids:
                        sk = _seen_key(label, mid)
                        try:
                            raw0 = _imap_fetch_message_bytes(conn, mid)
                            if not raw0:
                                seen_ids.add(sk)
                                skipped += 1
                                continue
                            m0 = _email.message_from_bytes(raw0)
                            fr0 = str(m0.get("From", "") or "").lower()
                            sj0 = str(m0.get("Subject", "") or "").lower()
                            is_otp = (("openai" in fr0 or "noreply" in fr0)
                                        and "verification code" in sj0)
                        except Exception:
                            is_otp = False
                            m0 = None
                        if not is_otp:
                            seen_ids.add(sk)
                            skipped += 1
                        elif nb is not None:
                            dts = _msg_date_ts(m0) if m0 is not None else None
                            if dts is None or dts < (nb - skew):
                                # 无 Date 的 OTP 保守标 seen；Date 过旧则挡历史码
                                seen_ids.add(sk)
                                skipped += 1
                    baseline_done[label] = True
                    any_baseline_this_round = True
                    print(f"  [*] IMAP[{label}] 基线：跳过 {skipped} 封非 OTP/旧 OTP 邮件，等待新验证邮件..."
                          + (f" not_before={nb:.0f}" if nb is not None else ""))
                    conn.logout()
                    conn = None
                    continue  # 下一账户；本轮若有基线则最后统一 sleep

                for mid in reversed(ids[-40:]):
                    sk = _seen_key(label, mid)
                    if sk in seen_ids:
                        continue
                    seen_ids.add(sk)
                    raw = _imap_fetch_message_bytes(conn, mid)
                    if not raw:
                        continue
                    msg = _email.message_from_bytes(raw)
                    to_addr = str(msg.get("To", "") or "").lower()
                    from_addr = str(msg.get("From", "") or "").lower()
                    subject = str(msg.get("Subject", "") or "")
                    # 临时诊断：确认每封新邮件的 label/from/to 与 alias 匹配情况
                    print(f"  [debug] scan [{label}] From={from_addr[:50]} To={to_addr[:50]} alias={alias}")
                    # 按当前别名过滤：DDG 转发常把别名写进 From 改写
                    # （noreply_at_tm.openai.com_<local>@duck.com）和/或 To。
                    # 旧逻辑"或来自 openai/noreply"会跨别名提取旧 OTP → wrong_email_otp_code。
                    # +tag 应急别名：OpenAI 可能发到 base+tag，DDG 也可能只认 base；
                    # 因此同时匹配完整 alias 与 base local（去 +tag）。
                    if alias:
                        base_local = alias.split("@", 1)[0].split("+", 1)[0]
                        hay = f"{to_addr} {from_addr}"
                        if alias not in hay and base_local not in hay:
                            continue
                    if not alias and "openai" not in from_addr and "noreply" not in from_addr \
                            and "openai" not in subject.lower() and "chatgpt" not in subject.lower():
                        continue

                    # not_before 时间窗：提取 6 位码前过滤旧 OTP / 无 Date 邮件
                    msg_date_ts = _msg_date_ts(msg)
                    if nb is not None:
                        if msg_date_ts is None:
                            print(f"  [~] 跳过无 Date 邮件 [{label}] mid={mid!r} "
                                  f"From={from_addr[:80]} To={to_addr[:80]}")
                            continue
                        if msg_date_ts < (nb - skew):
                            print(f"  [~] 跳过旧 OTP [{label}] mid={mid!r} Date={msg_date_ts:.0f} "
                                  f"not_before={nb:.0f} skew={skew} From={from_addr[:60]}")
                            continue

                    body_parts = []
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct in ("text/plain", "text/html"):
                                try:
                                    payload = part.get_payload(decode=True)
                                except Exception:
                                    continue
                                if payload:
                                    charset = part.get_content_charset() or "utf-8"
                                    body_parts.append(payload.decode(charset, errors="replace"))
                    else:
                        try:
                            payload = msg.get_payload(decode=True)
                        except Exception:
                            payload = None
                        if payload:
                            charset = msg.get_content_charset() or "utf-8"
                            body_parts.append(payload.decode(charset, errors="replace"))

                    combined = subject + " " + " ".join(body_parts)
                    # 去 style/script 与所有标签，避免匹配 CSS 颜色(如 #000000)或 HTML 数字
                    combined = re.sub(r'<style[^>]*>.*?</style>', '', combined, flags=re.DOTALL | re.IGNORECASE)
                    combined = re.sub(r'<script[^>]*>.*?</script>', '', combined, flags=re.DOTALL | re.IGNORECASE)
                    combined = re.sub(r'<[^>]+>', ' ', combined)
                    m = re.search(r'(?<!\d)(\d{6})(?!\d)', combined)
                    if m:
                        code = m.group(1)
                        msgid = str(msg.get("Message-ID", "") or "")
                        date_s = f"{msg_date_ts:.0f}" if msg_date_ts is not None else "None"
                        nb_s = f"{nb:.0f}" if nb is not None else "None"
                        print(f"  [*] IMAP[{label}] OTP 命中 code={code} Date={date_s} Message-ID={msgid} "
                              f"From={from_addr[:80]} To={to_addr[:80]} not_before={nb_s}")
                        return code
                conn.logout()
                conn = None
            except (imaplib.IMAP4.error, OSError) as e:
                print(f"  [!] IMAP[{label}] 连接异常: {e}")
            finally:
                if conn:
                    try:
                        conn.logout()
                    except Exception:
                        pass

        # 本轮有账户刚建基线 → 用 baseline_sleep；否则 poll_sleep
        sleep_n = baseline_sleep if any_baseline_this_round else poll_sleep
        for _ in range(sleep_n):
            if cancel_check and cancel_check():
                return None
            if time.time() >= deadline:
                return None
            time.sleep(1)
    return None


# ========== 2. OpenAI OAuth2 授权与环境生成模块 ==========

# 关键：web 流出号抓包用的是 /api/accounts/authorize（非 Hydra /oauth/authorize）。
# /oauth/authorize 仍可能发 OTP，但会话状态机与 create_account 不对齐时
# 会 400 invalid_auth_step（见 camoufox_captured.json[3]/[4]、NOBROWSER_INVALID_AUTH_STEP.md）。
AUTH_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"   # web 流 ChatGPT Web client（非 Codex app_EMoamEEZ…）
DEFAULT_REDIRECT_URI = "https://chatgpt.com/api/auth/callback/openai"
DEFAULT_SCOPE = "openid email profile offline_access model.request model.read organization.read organization.write"

def _gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    special = "!@#$%^&*.-"
    base = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(string.digits),
        random.choice(special),
    ]
    base += [random.choice(alphabet + special) for _ in range(12)]
    random.shuffle(base)
    return "".join(base)

def _random_name() -> str:
    return ''.join(random.choice(string.ascii_lowercase) for _ in range(random.randint(5, 9))).capitalize()

def _random_birthdate() -> str:
    start = datetime(1970,1,1)
    end = datetime(1999,12,31)
    d = start + timedelta(days=random.randrange((end - start).days + 1))
    return d.strftime('%Y-%m-%d')

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())

def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)

def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)

def _parse_callback_url(callback_url: str) -> Dict[str, Any]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "","state": "","error": "","error_description": ""}
    if "://" not in candidate:
        if candidate.startswith("?"): candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate: candidate = f"http://{candidate}"
        elif "=" in candidate: candidate = f"http://localhost/?{candidate}"
    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)
    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values
    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()
    code = get1("code"); state = get1("state")
    error = get1("error"); error_description = get1("error_description")
    if code and not state and "#" in code:
        code, state = code.split("#",1)
    if not error and error_description:
        error, error_description = error_description, ""
    return {"code": code,"state": state,"error": error,"error_description": error_description}

def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2: return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii")).decode("utf-8"))
    except: return {}

def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw: return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try: return json.loads(base64.urlsafe_b64decode((raw + pad).encode("ascii")).decode("utf-8"))
    except: return {}

def _to_int(v: Any) -> int:
    try: return int(v)
    except: return 0

def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded","Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200: raise RuntimeError(f"token exchange failed: {resp.status}")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"token exchange failed: {exc.code}") from exc

@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str

def generate_oauth_url(
    *,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    scope: str = DEFAULT_SCOPE,
    login_hint: str = "",
    device_id: str = "",
    state: str = "",
) -> OAuthStart:
    """构造 web 流 authorize URL（对齐 camoufox 成功抓包参数）。

    抓包必带：device_id / ext-oai-did / auth_session_logging_id /
    ext-passkey-client-capabilities / screen_hint / login_hint。

    注意：若要拿 chatgpt.com next-auth accessToken，state 必须来自
    next-auth POST /api/auth/signin/openai 下发的 state（与
    __Secure-next-auth.state JWE cookie 绑定）。自造 state 会导致
    callback 时 OAuthCallback（state 不匹配）。
    """
    state = (state or "").strip() or _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)
    did = (device_id or "").strip() or str(uuid.uuid4())
    logging_id = str(uuid.uuid4())
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        # 浏览器 next-auth 路径无 PKCE；保留 PKCE 仅兼容 Codex /oauth/token 换票兜底
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "audience": "https://api.openai.com/v1",
        "screen_hint": "login_or_signup",
        "device_id": did,
        "ext-oai-did": did,
        "ext-passkey-client-capabilities": "01001",
        "auth_session_logging_id": logging_id,
    }
    if login_hint:
        params["login_hint"] = login_hint
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(auth_url=auth_url, state=state, code_verifier=code_verifier, redirect_uri=redirect_uri)


def start_nextauth_openai_oauth(
    session,
    *,
    callback_url: str = "https://chatgpt.com/",
    login_hint: str = "",
    device_id: str = "",
) -> OAuthStart:
    """经 next-auth 发起 openai OAuth（curl_cffi 无浏览器）。

    正确顺序（与浏览器一致）：
      1) GET chatgpt.com/auth/login  → 种 __Host-next-auth.csrf-token
      2) GET /api/auth/csrf          → csrfToken
      3) POST /api/auth/signin/openai (csrfToken + callbackUrl + json=true)
         → 200 {"url": "https://auth.openai.com/api/accounts/authorize?...&state=..."}
         → Set-Cookie __Secure-next-auth.state=<JWE>（与 state 绑定，不可伪造）
      4) 在 authorize URL 上补 login_hint/screen_hint 等注册参数（保留 next-auth state）

    错误做法：
      - 自造 state 直接 GET authorize → create_account 的 continue_url 无法通过 next-auth callback
      - 注册完成后再 POST signin/openai → 新 state，旧 code 作废，且会话已清空会落 /auth/login
      - GET continue_url 时没有匹配的 __Secure-next-auth.state → error=OAuthCallback
    """
    ua = _session_ua(session)
    impersonate = _session_impersonate(session)
    nav_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "user-agent": ua,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        **_make_trace_headers(),
    }
    try:
        # stream 模式只拿 Set-Cookie (__Host-next-auth.csrf-token), 不下载 HTML body (~976KB)
        # 该页面 body 从不被解析, 纯为种 csrf cookie, 截断可省约 1MB 下传/号。
        _warmup_login = session.get(
            "https://chatgpt.com/auth/login",
            timeout=20, stream=True,
            headers={**nav_headers, "referer": "https://chatgpt.com/"},
            impersonate=impersonate,
        )
        _warmup_login.close()
    except Exception as e:
        print(f"[~] /auth/login 预热异常: {repr(e)[:100]}")

    csrf = ""
    try:
        csrf_resp = session.get(
            "https://chatgpt.com/api/auth/csrf",
            timeout=15,
            headers={
                "Accept": "application/json",
                "user-agent": ua,
                "referer": "https://chatgpt.com/auth/login",
                **_make_trace_headers(),
            },
            impersonate=impersonate,
        )
        if csrf_resp.status_code == 200:
            csrf = str((csrf_resp.json() or {}).get("csrfToken") or "").strip()
        print(f"[debug] next-auth csrf status={csrf_resp.status_code} token={'set' if csrf else '空'}")
    except Exception as e:
        print(f"[!] next-auth csrf 异常: {repr(e)[:120]}")
        raise RuntimeError("next-auth csrf failed") from e
    if not csrf:
        raise RuntimeError("next-auth csrfToken empty")

    # json=true → 200 + {"url": authorize...}，并写入 __Secure-next-auth.state JWE
    body = urllib.parse.urlencode(
        {
            "csrfToken": csrf,
            "callbackUrl": callback_url,
            "json": "true",
        }
    )
    try:
        signin_resp = session.post(
            "https://chatgpt.com/api/auth/signin/openai",
            timeout=30,
            allow_redirects=False,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/auth/login",
                "user-agent": ua,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                **_make_trace_headers(),
            },
            data=body,
            impersonate=impersonate,
        )
    except Exception as e:
        print(f"[!] POST signin/openai 异常: {repr(e)[:120]}")
        raise RuntimeError("next-auth signin failed") from e

    auth_url = ""
    if signin_resp.status_code in (301, 302, 303, 307, 308):
        auth_url = (signin_resp.headers.get("Location") or signin_resp.headers.get("location") or "").strip()
    else:
        try:
            auth_url = str((signin_resp.json() or {}).get("url") or "").strip()
        except Exception:
            auth_url = ""
        if not auth_url:
            auth_url = (signin_resp.headers.get("Location") or signin_resp.headers.get("location") or "").strip()

    cookie_names = _session_cookie_names(session)
    has_state_cookie = any(n == "__Secure-next-auth.state" for n in cookie_names)
    print(
        f"[debug] POST signin/openai status={signin_resp.status_code} "
        f"url={'set' if auth_url else '空'} state_cookie={has_state_cookie}"
    )
    if not auth_url or "authorize" not in auth_url:
        raise RuntimeError(
            f"next-auth signin did not return authorize url: "
            f"status={signin_resp.status_code} body={str(signin_resp.text)[:200]}"
        )
    if not has_state_cookie:
        print("[Warn] 未看到 __Secure-next-auth.state cookie — callback 极可能 OAuthCallback")

    # 保留 next-auth 下发的 state/client_id/redirect_uri，补注册用 query
    parsed = urllib.parse.urlparse(auth_url)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    params = {k: (v[0] if v else "") for k, v in qs.items()}
    state = str(params.get("state") or "").strip()
    if not state:
        raise RuntimeError("next-auth authorize url missing state")

    did = (device_id or "").strip() or str(params.get("device_id") or "").strip() or str(uuid.uuid4())
    params["device_id"] = did
    params["ext-oai-did"] = did
    params.setdefault("prompt", "login")
    params.setdefault("screen_hint", "login_or_signup")
    params.setdefault("ext-passkey-client-capabilities", "01001")
    if "auth_session_logging_id" not in params:
        params["auth_session_logging_id"] = str(uuid.uuid4())
    if login_hint:
        params["login_hint"] = login_hint
    # next-auth 默认无 PKCE；不要自加 code_challenge，否则与 server 端换票不一致

    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print(f"[debug] next-auth authorize state={state[:16]}... did={did[:8]}... hint={bool(login_hint)}")
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier="",  # next-auth server-side token exchange，无 PKCE
        redirect_uri=str(params.get("redirect_uri") or DEFAULT_REDIRECT_URI),
    )


def _extract_cookie_value(session, name: str) -> str:
    """从 Session CookieJar 取指定 cookie 的值（兼容 curl_cffi / requests）。"""
    try:
        for c in session.cookies:
            if getattr(c, "name", None) == name:
                return str(getattr(c, "value", "") or "")
    except Exception:
        pass
    try:
        return str(session.cookies.get(name) or "")
    except Exception:
        return ""


def finish_nextauth_access_token(session, continue_url: str) -> tuple[str, str]:
    """用 create_account 返回的 continue_url 完成 next-auth callback，读 accessToken。

    返回 (access_token, session_token)：
      - access_token: chatgpt.com/api/auth/session 返回的 accessToken
      - session_token: __Secure-next-auth.session-token cookie（AT 过期后用其换新 AT）
    continue_url 形如:
      https://chatgpt.com/api/auth/callback/openai?code=...&state=...
    要求 Session 里已有匹配的 __Secure-next-auth.state（signin 时下发的 JWE）。
    """
    cu = (continue_url or "").strip()
    if not cu or "code=" not in cu:
        print("[!] finish_nextauth: continue_url 无 code")
        return "", ""

    names = _session_cookie_names(session)
    has_state = any(n == "__Secure-next-auth.state" for n in names)
    print(f"[debug] callback 前 next-auth cookies: state={has_state} "
          f"csrf={any('csrf' in n for n in names)} "
          f"names={[n for n in names if 'next-auth' in n]}")

    ua = _session_ua(session)
    impersonate = _session_impersonate(session)
    nav_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "user-agent": ua,
        "referer": "https://auth.openai.com/",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "cross-site",
        **_make_trace_headers(),
    }

    # 逐步跟随重定向，便于诊断 OAuthCallback / 落 login
    current = cu
    final_url = cu
    try:
        for hop in range(8):
            resp = session.get(
                current,
                timeout=30,
                allow_redirects=False,
                stream=True,
                headers=nav_headers if hop == 0 else {
                    **nav_headers,
                    "referer": current if hop else "https://auth.openai.com/",
                    "sec-fetch-site": "same-origin" if "chatgpt.com" in current else "cross-site",
                },
                impersonate=impersonate,
            )
            loc = (resp.headers.get("Location") or resp.headers.get("location") or "").strip()
            print(f"[debug] callback hop[{hop}] status={resp.status_code} "
                  f"url={current[:90]} loc={loc[:100]}")
            final_url = str(getattr(resp, "url", None) or current)
            if resp.status_code not in (301, 302, 303, 307, 308) or not loc:
                final_url = str(getattr(resp, "url", None) or current)
                # 非重定向终点
                if resp.status_code >= 400:
                    # 错误时才下载 body 用于诊断
                    print(f"[!] callback 终点 HTTP {resp.status_code} body={resp.text[:200]}")
                else:
                    resp.close()  # 成功: 丢弃 body
                break
            next_url = urllib.parse.urljoin(current, loc)
            resp.close()  # 重定向跳: 丢弃中间响应 body
            if "error=" in next_url or "/auth/error" in next_url:
                print(f"[!] next-auth callback 失败: {next_url[:160]}")
                return "", ""
            current = next_url
            final_url = next_url
        else:
            print("[Warn] callback 重定向超过 8 跳")
    except Exception as e:
        print(f"[!] GET continue_url/callback 异常: {repr(e)[:140]}")
        return "", ""

    print(f"[debug] callback 完成 final={final_url[:100]}")

    try:
        sess_resp = session.get(
            "https://chatgpt.com/api/auth/session",
            timeout=20,
            headers={
                "Accept": "application/json",
                "user-agent": ua,
                "referer": "https://chatgpt.com/",
                **_make_trace_headers(),
            },
            impersonate=impersonate,
        )
        if sess_resp.status_code != 200:
            print(f"[!] /api/auth/session status={sess_resp.status_code} body={sess_resp.text[:300]}")
            return "", ""
        sess_json = sess_resp.json() or {}
        access_token = str(sess_json.get("accessToken") or "").strip()
        uemail = str(((sess_json.get("user") or {}).get("email")) or "")
        session_token = _extract_cookie_value(session, "__Secure-next-auth.session-token")
        print(
            f"[debug] /api/auth/session accessToken={'set' if access_token else 'EMPTY'} "
            f"session_token={'set' if session_token else 'EMPTY'} "
            f"user={uemail[:60]} keys={list(sess_json.keys())[:8]}"
        )
        return access_token, session_token
    except Exception as e:
        print(f"[!] /api/auth/session 异常: {repr(e)[:120]}")
        return "", ""


def _session_cookie_names(session) -> list:
    """列出当前 Session cookie 名（兼容 curl_cffi CookieJar）。"""
    names = []
    try:
        for c in session.cookies:
            n = getattr(c, "name", None)
            if n:
                names.append(n)
    except Exception:
        pass
    if not names:
        try:
            # RequestsCookieJar / dict-like
            names = list(session.cookies.keys())  # type: ignore[arg-type]
        except Exception:
            pass
    return names


def _auth_session_flags(session) -> Dict[str, bool]:
    """对照抓包：create_account 前应具备的关键 cookie。"""
    names = _session_cookie_names(session)
    return {
        "oai_did": any(n == "oai-did" for n in names),
        "oai_login_csrf": any(n.startswith("oai-login-csrf") for n in names),
        "login_session": "login_session" in names,
        "oai_client_auth_session": "oai-client-auth-session" in names,
        "auth_provider": "auth_provider" in names,
        "hydra_redirect": "hydra_redirect" in names,
        "cf_clearance": "cf_clearance" in names,
    }

def fetch_sentinel_token(*, flow: str, did: str, proxies: Any = None) -> Optional[str]:
    """获取 OpenAI Sentinel 信封（OpenAI-Sentinel-Token 用）。

    修正：必须返回 JSON 信封 {"p":..,"t":..,"c":<token>,"id":<did>,"flow":..}，
    不能是裸 token 串。直接复用 sentinel_sdk 的纯 Python 构造器。
    """
    try:
        return sentinel_sdk.build_sentinel_token(did, flow, proxies)
    except Exception:
        return None

def submit_callback_url(*, callback_url: str, expected_state: str, code_verifier: str, redirect_uri: str = DEFAULT_REDIRECT_URI) -> str:
    """提取重定向中的 Code 并换取最终的 Access / Refresh Token"""
    cb = _parse_callback_url(callback_url)
    if cb["error"]: raise RuntimeError(f"oauth error: {cb['error']}")
    if not cb["code"] or not cb["state"]: raise ValueError("callback missing code/state")
    if cb["state"] != expected_state: raise ValueError("state mismatch")

    token_resp = _post_form(TOKEN_URL, {
        "grant_type": "authorization_code", "client_id": CLIENT_ID,
        "code": cb["code"], "redirect_uri": redirect_uri, "code_verifier": code_verifier,
    })
    
    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0)))
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token, "access_token": access_token, "refresh_token": refresh_token,
        "account_id": account_id, "last_refresh": now_rfc3339, "email": email,
        "type": "codex", "expired": expired_rfc3339,
    }
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ========== 3. 核心注册与提取流程 ==========

def _log_sdk(flow: str, meta) -> None:
    """按规范记录 Sentinel SDK 结果：只记长度/版本/so 是否生成，绝不打印 token 明文。"""
    if not isinstance(meta, dict):
        print(f"[Sentinel:{flow}] 未返回有效结果")
        return
    print(f"[Sentinel:{flow}] mode={meta.get('mode')} "
          f"sentinel_len={meta.get('sentinel_len')} "
          f"so_len={meta.get('so_len')} "
          f"sdk_version={meta.get('sdk_version')} "
          f"so_present={meta.get('so_present')} "
          f"t_ok={meta.get('t_ok')} so_ok={meta.get('so_ok')} "
          f"observer_wait_ms={meta.get('observer_wait_ms')} "
          f"ok={meta.get('ok')}")
    if meta.get("error"):
        print(f"[Sentinel:{flow}] 备注: {meta.get('error')}")


# 模块级缓存：自动探测到的可用代理只探一次，避免主循环每轮都重探。
_probed_proxy: Optional[str] = None


def _resend_email_otp(s, did, ua, impersonate) -> bool:
    """调用 OpenAI resend OTP：GET /api/accounts/email-otp/send（与密码流/Resend 按钮同源）。

    页面 email-verification 的「Resend email」即打此接口；302/200 均视为触发成功。
    """
    headers = {
        "referer": "https://auth.openai.com/email-verification",
        "origin": "https://auth.openai.com",
        "accept": "application/json",
        "oai-device-id": did,
        "user-agent": ua,
        **_make_trace_headers(),
    }
    try:
        r = s.get(
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers=headers,
            timeout=15,
            stream=True,
            impersonate=impersonate,
            allow_redirects=False,
        )
        ok = r.status_code in (200, 302, 303, 307, 308)
        r.close()  # 只需 status_code, 不下载 body
        print(f"[*] resend OTP email-otp/send status={r.status_code} ok={ok}")
        return ok
    except Exception as e:
        print(f"[!] resend OTP 异常: {repr(e)[:160]}")
        return False


def _wait_otp_imap_two_phase(code_fetcher, s, did, ua, impersonate, cancel_check=None, *,
                             phase1_sec=None, phase2_sec=None, allow_resend=True,
                             otp_issued_at=None):
    """IMAP OTP 两阶段：phase1 等 → (可选 resend) → phase2 再等 → 仍无则 None。

    phase1_sec/phase2_sec 默认 IMAP_OTP_PHASE_SEC（password 路径 10s 两阶段保持现状）。
    web 路径由 run() 传入 IMAP_OTP_WEB_PHASE_SEC / IMAP_OTP_WEB_PHASE2_SEC。
    not_before：phase1 用 otp_issued_at（或 now）；resend 成功后 phase2 用 resend_at，
    避免 phase2 提到 OTP1 → validate invalid_state。
    seen_ids 跨阶段复用。IMAP 渠道路径不走此函数。
    """
    phase1 = IMAP_OTP_PHASE_SEC if phase1_sec is None else int(phase1_sec)
    phase2 = IMAP_OTP_PHASE_SEC if phase2_sec is None else int(phase2_sec)
    seen_ids = set()
    not_before = float(otp_issued_at) if otp_issued_at is not None else time.time()
    print(f"[*] OTP 两阶段等待：{phase1}s → resend={allow_resend} → {phase2}s "
          f"（not_before={not_before:.0f}）")

    code = code_fetcher(timeout_sec=phase1, seen_ids=seen_ids, not_before=not_before)
    if code:
        return code
    if cancel_check and cancel_check():
        return None

    if not allow_resend:
        print(f"[!] 第1阶段 {phase1}s 未收到 OTP，allow_resend=False，放弃此邮箱")
        return None

    print(f"[*] 第1阶段 {phase1}s 未收到 OTP，触发 resend...")
    resend_ok = _resend_email_otp(s, did, ua, impersonate)
    # resend 成功时刻：phase2 只收 OTP2，挡先到的旧 OTP1
    resend_at = time.time() if resend_ok else not_before
    if cancel_check and cancel_check():
        return None

    code = code_fetcher(timeout_sec=phase2, seen_ids=seen_ids, not_before=resend_at)
    if code:
        return code
    print(f"[!] 第2阶段 {phase2}s 仍未收到 OTP，放弃此邮箱")
    return None


def login_with_password(proxy, email, password, cancel_check=None, code_fetcher=None) -> Optional[tuple]:
    """用邮箱+密码走 next-auth 登录，给存量账号补抓 session_token。

    完全复用 run() 的会话/指纹基建（fp + oai-did + 711 + CF warm-up +
    next-auth state JWE），把注册中的「create-account + OTP」换成密码登录：
      authorize(login_hint=email) → login_password 页 → sentinel(password_verify)
      → POST /api/accounts/password/verify → callback → GET /api/auth/session

    返回 (email, access_token, session_token) 或 None。
    登录若要求邮箱 OTP（email-verification），返回 None 并在 stdout 打原因。
    """
    _set_cancel_check(cancel_check)
    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        print("[!] login_with_password: 缺少 email/password")
        return None

    if proxy and proxy_711.is_711_proxy(proxy):
        proxy = proxy_711.ensure_proxy(proxy)
    proxies = {"http": proxy, "https": proxy} if proxy else None

    fp = choose_fp(seed=email)
    ua = fp["ua"]
    impersonate = fp["impersonate"]
    chrome_fp = fp["chrome_fp"]
    did = _new_oai_did()
    print(f"[login] 指纹 {_fp_summary(fp)} 新设备 oai-did={did[:8]}...")

    s = requests.Session(proxies=proxies, impersonate=impersonate)
    s.headers.clear()
    s.headers.update(chrome_fp)
    _bind_session_fp(s, fp)
    _bind_oai_did(s, did)
    # 包装 session.request 以记录代理流量 (归 BLOCK_REGISTER, 由 engine.py 设置 threadlocal)
    _orig_req_login = s.request
    def _traced_req_login(*a, **kw):
        r = _orig_req_login(*a, **kw)
        if not kw.get("stream"):
            try:
                from core.traffic import record_response
                record_response(r)
            except Exception:
                pass
        return r
    s.request = _traced_req_login

    try:
        # 第零步：chatgpt.com 拿 Cloudflare cookies（同 run()，stream 省流）
        try:
            _warmup = s.get("https://chatgpt.com/", timeout=20, stream=True, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
                "Upgrade-Insecure-Requests": "1",
                "user-agent": ua, **_make_trace_headers(),
            }, impersonate=impersonate)
            _warmup.close()
            print("[login] 已访问 chatgpt.com 获取 CF cookies")
        except Exception as e:
            print(f"[login] chatgpt.com 访问异常（继续）: {repr(e)[:100]}")
        _bind_oai_did(s, did)
        _human_delay(0.8, 2.2, "after chatgpt.com warm-up")

        # 第一步：next-auth 发起 OAuth（state 绑定 __Secure-next-auth.state JWE）
        oauth_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
            "Upgrade-Insecure-Requests": "1",
            "user-agent": ua,
            "referer": "https://chatgpt.com/",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "cross-site",
            **_make_trace_headers(),
        }
        oauth = None
        for attempt in range(1, MAX_STAGE_RETRY + 1):
            if cancel_check and cancel_check():
                print("[login] 已取消（authorize 前）")
                return None
            try:
                oauth = start_nextauth_openai_oauth(
                    s, callback_url="https://chatgpt.com/", login_hint=email, device_id=did
                )
            except Exception as e:
                print(f"[login] next-auth signin 失败 ({repr(e)[:100]})，第 {attempt}/{MAX_STAGE_RETRY} 次")
                if attempt < MAX_STAGE_RETRY:
                    _retry_backoff(attempt)
                    continue
                return None

            _human_delay(0.3, 1.2, f"before authorize attempt={attempt}")
            resp = s.get(oauth.auth_url, timeout=25, stream=True, headers=oauth_headers, allow_redirects=True)
            _bind_oai_did(s, did)
            final_url = str(getattr(resp, "url", "") or "")
            print(f"[login] authorize status={resp.status_code} url={final_url[:110]} "
                  f"cookies={_auth_session_flags(s)}")
            resp.close()  # 只需 final_url + Set-Cookie, 不下载 HTML body (~62KB)

            # 若 authorize 直接跳到 chatgpt.com 回调（极少见），直接收尾
            cb = _parse_callback_url(final_url)
            if cb["code"] and cb["state"]:
                return email, *finish_nextauth_access_token(s, final_url)

            # 会话已建立（login_password 页/邮箱验证页/或已认证跳转）即可进入密码步
            if final_url and ("log-in" in final_url or "login" in final_url
                              or "password" in final_url or "email-verification" in final_url):
                break
            if attempt < MAX_STAGE_RETRY:
                print(f"[login] authorize 未到登录页（第 {attempt}/{MAX_STAGE_RETRY} 次），重试同一会话")
                _retry_backoff(attempt)
        else:
            return None

        if cancel_check and cancel_check():
            print("[login] 已取消（login 页后）")
            return None
        _human_delay(0.4, 1.5, "after authorize / before password verify")

        # OTP 优先路径：authorize 直接落 email-verification（OpenAI 对新设备登录默认发邮箱码）
        if "email-verification" in final_url:
            otp_issued_at = time.time()
            _human_delay(0.3, 1.0, "before OTP wait")
            if not code_fetcher:
                print("[login] authorize 落 email-verification（需邮箱 OTP），"
                      "但无 code_fetcher；仅 outlook 池账号支持，放弃")
                return None
            code = _wait_otp_imap_two_phase(
                code_fetcher, s, did, ua, impersonate, cancel_check=cancel_check,
                phase1_sec=LOGIN_OTP_PHASE1_SEC, phase2_sec=LOGIN_OTP_PHASE2_SEC,
                allow_resend=True, otp_issued_at=otp_issued_at,
            )
            if not code:
                print("[login] 邮箱 OTP 超时未收到，放弃本号")
                return None
            print(f"[login] 邮箱 OTP 已取到: {code}")
            validate_headers = {
                "referer": final_url or "https://auth.openai.com/email-verification",
                "origin": "https://auth.openai.com",
                "content-type": "application/json",
                "oai-device-id": did,
                "user-agent": ua,
                **_make_trace_headers(),
            }
            code_resp = s.post(
                "https://auth.openai.com/api/accounts/email-otp/validate",
                headers=validate_headers,
                data=json.dumps({"code": code}),
                impersonate=impersonate,
                allow_redirects=False,
                timeout=30,
            )
            print(f"[login] /api/accounts/email-otp/validate → {code_resp.status_code}")
            if code_resp.status_code not in (200, 302):
                print(f"[login] OTP 校验失败: {code_resp.status_code} body={code_resp.text[:300]}")
                return None
            if code_resp.status_code == 200:
                try:
                    vj = code_resp.json()
                except Exception:
                    vj = {}
                cont = str((vj or {}).get("continue_url") or "").strip()
                print(f"[login] OTP validate 200 continue_url={cont[:110]}")
            else:
                cont = (code_resp.headers.get("Location") or "").strip()
                print(f"[login] OTP validate 302 location={cont[:110]}")
            at, st = finish_nextauth_access_token(s, cont or final_url)
            if not at:
                print("[login] /api/auth/session 未返回 accessToken，登录并未真正落地")
                return None
            print(f"[login] 成功 accessToken={'有' if at else '无'} "
                  f"session_token={'有' if st else '无'} email={email}")
            return email, at, st

        # 密码路径：落 login/password 页才走 password/verify

        # 第二步：sentinel(password_verify) 防机器人，再 POST /api/accounts/password/verify
        pwd_headers = {
            "referer": final_url or "https://auth.openai.com/log-in/password",
            "origin": "https://auth.openai.com",
            "accept": "application/json",
            "content-type": "application/json",
            "oai-device-id": did,
            "user-agent": ua,
            **_make_trace_headers(),
        }
        try:
            from . import sentinel_sdk as _sdk_login
            _sl = _sdk_login.sentinel_for("password_verify", proxy=proxy, did=did, fp=fp)
            _sentinel_pwd = _sl.get("sentinel_token") if _sl.get("ok") else None
        except Exception as e:
            print(f"[login] sentinel(password_verify) 异常: {repr(e)[:100]}")
            _sentinel_pwd = None
        if _sentinel_pwd:
            pwd_headers["openai-sentinel-token"] = _sentinel_pwd
            print("[login] sentinel(password_verify) 已发放")
        else:
            print("[Warn] [login] 未拿到 sentinel(password_verify)，直接试登")

        pv = s.post(
            "https://auth.openai.com/api/accounts/password/verify",
            headers=pwd_headers,
            data=json.dumps({"password": password}),
            impersonate=impersonate,
            allow_redirects=False,
            timeout=30,
        )
        print(f"[login] /api/accounts/password/verify → {pv.status_code}")
        if pv.status_code != 200:
            print(f"[login] 密码登录失败: {pv.status_code} body={pv.text[:300]}")
            return None
        try:
            vj = pv.json()
        except Exception:
            vj = {}
        cont = str((vj or {}).get("continue_url") or "").strip()
        print(f"[login] password/verify 200 continue_url={cont[:110]}")

        if "email-verification" in (cont or final_url):
            print("[login] 该账号登录需邮箱 OTP（email-verification），当前无收信通道，放弃")
            return None

        # 第三步：GET /api/auth/session 拿 accessToken + session_token（内部跟完回调重定向）
        at, st = finish_nextauth_access_token(s, cont or final_url)
        if not at:
            print("[login] /api/auth/session 未返回 accessToken，登录并未真正落地")
            return None
        print(f"[login] 成功 accessToken={'有' if at else '无'} session_token={'有' if st else '无'} email={email}")
        return email, at, st

    except Exception as e:
        print(f"[login] 未预期异常，放弃本号: {repr(e)[:200]}")
        return None


# 自定义邮箱渠道注册表: 调用方注册 setup_email(proxies, cancel_check) -> (email, openai_password, fetch_code)
# fetch_code(timeout_sec=None, seen_ids=None, not_before=None) -> code|None
CUSTOM_EMAIL_CHANNELS: Dict[str, Any] = {}


def register_email_channel(name: str, setup_fn) -> None:
    """注册自定义邮箱渠道。name 即 run(email=name) 与面板渠道下拉的取值。"""
    name = str(name or "").strip().lower()
    if not name or not callable(setup_fn):
        raise ValueError("register_email_channel: 需要 name + setup_fn")
    CUSTOM_EMAIL_CHANNELS[name] = setup_fn


def list_email_channels() -> list:
    """已注册的自定义邮箱渠道名列表（面板下拉用）。"""
    return sorted(CUSTOM_EMAIL_CHANNELS.keys())


def run(proxy: Optional[str], email: str = "", cancel_check=None, imap_label=None) -> Optional[tuple]:
    # 返回 5-tuple: (token_json, email, password, access_token, session_token)
    #   - web 流: token_json=None, access_token+session_token 来自 chatgpt.com/api/auth/session
    #   - Codex 流: token_json=JSON串(含 refresh_token), access_token/session_token 均为 ""
    # 取消信号接入：register_loop 传入 STATE.cancel.is_set；OTP 等待循环与步骤间
    # sleep 均据此秒级响应，停止按钮可即时打断当前这一次注册。
    # TLS：并发多号注册时互不覆盖 cancel 回调。
    _set_cancel_check(cancel_check)
    # 若未显式给代理，自动从代理池挑一个能穿透 OpenAI Cloudflare 的。
    # （当前 1000 条多为数据中心 IP，多半 403；探到活的就用，探不到
    #  则 fallback 到 711 住宅链式中继——与测活侧 _resolve_relay_proxy 对齐。）
    global _probed_proxy
    # 未显式给代理时：自动建 711 住宅中继（从 OpenAI 支持国家白名单随机选 region，
    # 每号 sticky 锁地区，避免 unsupported_country 403）
    if not proxy:
            try:
                region = proxy_711.pick_region()
                proxy = proxy_711.build_711_proxy(region=region, sess_time=30)
                print(f"[*] 711 中继锁定国家: {region}")
                print(f"[*] 免费池无可用代理，自动启用 711 中继: {proxy.split('@')[-1] if proxy and '@' in proxy else proxy}")
            except Exception as e:
                print(f"[!] 711 中继自动启用失败: {e}")
    # 711 直连在 curl_cffi 下会被网关掐 CONNECT；改写为本机 Clash 链式中继。
    # Camoufox 浏览器路径可继续用原始 711 URL（系统侧 Clash/TUN 已覆盖）。
    # 每号新 711 sticky session（proxy_711.build_711_proxy 每次自动新 session id）。
    if proxy and proxy_711.is_711_proxy(proxy):
        proxy = proxy_711.ensure_proxy(proxy)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    _email = None
    _ok = False
    code_fetcher = None  # 自定义渠道取码器

    # 保存邮箱模式：后面 mail_data 会覆盖 email 变量为具体地址
    email_mode = email
    print(f"[*] 初始化请求，准备使用 {email} 邮箱注册...")
    if not email:
        chans = list_email_channels()
        if not chans:
            print("[Error] 无可用邮箱渠道（邮箱池为空，请先在邮箱池添加 IMAP 账号）")
            return None
        email = chans[0]
        print(f"[*] 未指定渠道，自动选用第一个: {email}")
    if email in CUSTOM_EMAIL_CHANNELS:
        # 调用方 (reg/engine) 注册的自定义邮箱渠道
        mail_data = CUSTOM_EMAIL_CHANNELS[email](proxies, cancel_check=cancel_check)
    else:
        print(f"[Error] 未知邮箱渠道: {email!r}（可用渠道: {list_email_channels() or '无'}）")
        return None
    if not mail_data or not mail_data[0]:
        print(f"[Error] 获取 {email} 邮箱失败")
        return None

    email, password, code_fetcher = mail_data
    _email = email
    if cancel_check and cancel_check():
        print("[*] 已取消（获取邮箱后），中止本次注册")
        return None
    print(f"[*] 成功获取邮箱: {email}")
    if password:
        # 对 duck/163/dms 路径：password 是 OpenAI 账户密码；
        # 渠道第二返回值是 OpenAI 账户密码（IMAP 收信密码另存）。
        print(f"[*] OpenAI 账户密码已生成（len={len(password)}）")
    else:
        print("[!] 未生成 OpenAI 密码，password 流程将失败")

    # §3.5 每号操作清单：①新 oai-did ②新 fp profile ③新 711 session ④TLS impersonate 随 fp
    fp = choose_fp(seed=email)
    ua = fp["ua"]
    impersonate = fp["impersonate"]
    chrome_fp = fp["chrome_fp"]
    did = _new_oai_did()
    print(f"[anti-fuzz] 本号指纹 {_fp_summary(fp)}")
    print(f"[anti-fuzz] 新设备 oai-did={did[:8]}...（本号全程固定，与 fp 配套）")

    s = requests.Session(proxies=proxies, impersonate=impersonate)
    s.headers.clear()
    s.headers.update(chrome_fp)
    _bind_session_fp(s, fp)
    _bind_oai_did(s, did)
    # 包装 session.request 以记录代理流量 (归 BLOCK_REGISTER, 由 engine.py 设置 threadlocal)
    _orig_req_run = s.request
    def _traced_req_run(*a, **kw):
        r = _orig_req_run(*a, **kw)
        # stream 请求的 body 未下载, 跳过流量统计 (避免触发 content 读取)
        if not kw.get("stream"):
            try:
                from core.traffic import record_response
                record_response(r)
            except Exception:
                pass
        return r
    s.request = _traced_req_run

    try:
        # 第零步：先访问 chatgpt.com 拿 Cloudflare cookies（cf_clearance/__cf_bm 等）。
        # oai-did 已强制写入；响应后仍以本号 did 为准（防服务端 Set-Cookie 关联设备）。
        # 用 stream 模式只拿响应头 (Set-Cookie), 不下载完整 HTML body (~500KB),
        # 首页 body 从不被解析, 纯为种 CF cookie, 截断可省 ~500KB 下传/号。
        try:
            _warmup = s.get("https://chatgpt.com/", timeout=20, stream=True, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
                "Upgrade-Insecure-Requests": "1",
                "user-agent": ua, **_make_trace_headers(),
            }, impersonate=impersonate)
            _warmup.close()
            print("[*] 已访问 chatgpt.com 获取 CF cookies")
        except Exception as e:
            print(f"[~] chatgpt.com 访问异常（继续）: {repr(e)[:100]}")
        _bind_oai_did(s, did)
        _human_delay(0.8, 2.2, "after chatgpt.com warm-up")

        # 第一步：经 next-auth 发起 OAuth，再进 /api/accounts/authorize + login_hint
        # 关键：state 必须来自 POST /api/auth/signin/openai（绑定 __Secure-next-auth.state JWE），
        # 自造 state 会导致 create_account 的 continue_url 在 callback 时 OAuthCallback。
        # 浏览器路径：csrf → POST signin/openai → authorize?...&state=<next-auth> → 注册 → callback。
        oauth_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
            "Upgrade-Insecure-Requests": "1",
            "user-agent": ua,
            "referer": "https://chatgpt.com/",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "cross-site",
            **_make_trace_headers(),
        }
        auth_ok = False
        oauth = None
        final_url = ""
        for attempt in range(1, MAX_STAGE_RETRY + 1):
            if cancel_check and cancel_check():
                print("[*] 已取消（authorize 前），中止本次注册")
                return None
            try:
                oauth = start_nextauth_openai_oauth(
                    s, callback_url="https://chatgpt.com/", login_hint=email, device_id=did
                )
            except Exception as e:
                print(f"[Warn] next-auth signin 失败 ({repr(e)[:100]})，第 {attempt}/{MAX_STAGE_RETRY} 次；"
                      f"回退自造 state（仅能出号，accessToken 大概率拿不到）")
                oauth = generate_oauth_url(login_hint=email, device_id=did)

            _human_delay(0.3, 1.2, f"before authorize attempt={attempt}")
            resp = s.get(oauth.auth_url, timeout=25, stream=True, headers=oauth_headers, allow_redirects=True)
            # 保持本号 did 固定，不采用服务端可能下发的其它 oai-did
            _bind_oai_did(s, did)
            flags = _auth_session_flags(s)
            final_url = str(getattr(resp, "url", "") or "")
            print(f"[*] authorize attempt={attempt} status={resp.status_code} "
                  f"url={final_url[:90]} cookies={flags}")
            # 有 oai-did +（csrf 或 login_session）视为会话已建立
            if did and (flags.get("oai_login_csrf") or flags.get("login_session")
                        or flags.get("oai_client_auth_session")
                        or "email-verification" in final_url):
                resp.close()  # 成功: 不需 body, 丢弃
                auth_ok = True
                break
            reason = f"HTTP {resp.status_code}"
            err_code = ""
            body_snippet = ""
            try:
                err_json = resp.json()
                err_code = err_json.get("error", {}).get("code", "")
                err_msg = err_json.get("error", {}).get("message", "")
                body_snippet = f"json err={err_code}"
                if err_code == "unsupported_country_region_territory":
                    # 出口 IP 地区不支持：重试当前号/代理必败，立即放弃换下一个邮箱
                    reason = "HTTP 403 - 出口 IP 所在地区不受 OpenAI 支持，请用美国等支持地区代理"
                    print(f"[Warn] authorize 确定性失败 ({reason})，放弃本号")
                    return None
                elif err_code:
                    reason = f"HTTP {resp.status_code} - {err_code}: {err_msg}"
            except Exception:
                # 非 JSON 响应体（典型 Cloudflare/OpenAI 反爬 403 HTML 页）
                try:
                    txt = resp.text or ""
                    body_snippet = txt[:200].replace("\n", " ").strip()
                    _low = txt.lower()
                    if "cloudflare" in _low and ("attention required" in _low or "cf-error" in _low
                                                 or "just a moment" in _low or "challenge" in _low):
                        body_snippet = f"[CF挑战页] {body_snippet[:120]}"
                    elif "access denied" in _low or "blocked" in _low:
                        body_snippet = f"[拒绝访问] {body_snippet[:120]}"
                except Exception:
                    pass
            print(f"[Warn] authorize 会话未就绪 ({reason}, 第 {attempt}/{MAX_STAGE_RETRY} 次) "
                  f"body={body_snippet}")
            if attempt < MAX_STAGE_RETRY:
                # §3.2 指数退避 + 抖动：base * 2^(n-1) + U(0,1)
                _retry_backoff(attempt, base=0.8)
                # 重试仍用本号同一套 fp / did（会话内不中途换指纹）
                s = requests.Session(proxies=proxies, impersonate=impersonate)
                s.headers.clear()
                s.headers.update(chrome_fp)
                _bind_session_fp(s, fp)
                _bind_oai_did(s, did)
                # 重建 session 后必须重做 CF 预热，否则 cf_clearance/__cf_bm 缺失，
                # authorize 越重试越容易撞 Cloudflare 403
                try:
                    _warmup = s.get("https://chatgpt.com/", timeout=20, stream=True, headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
                        "Upgrade-Insecure-Requests": "1",
                        "user-agent": ua, **_make_trace_headers(),
                    }, impersonate=impersonate)
                    _warmup.close()
                except Exception as _e:
                    print(f"[~] 重试 CF 预热异常（继续）: {repr(_e)[:100]}")
                _bind_oai_did(s, did)
        if not did:
            print("[Error] 未能获取到 OpenAI Device ID (oai-did) - 见上方具体原因")
            return None
        if not auth_ok:
            # authorize 未成功：勿再进 OTP 死等，直接放弃本号让外层换邮箱
            print("[Warn] authorize 后关键 cookie 仍不全，放弃本号 "
                  f"(cookie_names={_session_cookie_names(s)[:20]})")
            return None

        # web 流：authorize 成功时 OTP1 已发；立刻记 issued 时刻供 IMAP not_before
        otp_issued_at = time.time()

        if cancel_check and cancel_check():
            print("[*] 已取消（authorize 后），中止本次注册")
            return None
        _human_delay(0.5, 1.8, "after authorize / before OTP path")

        # 密码流程检测（订阅节点/数据中心 IP 走 password 页，需 user/register + email-otp/send）
        is_password_flow = "create-account/password" in final_url
        if is_password_flow:
            print("[*] authorize 落 create-account/password，走密码流程（user/register + email-otp/send）")
            try:
                from . import sentinel_sdk as _sdk2
                _su = _sdk2.sentinel_for("username_password_create", proxy, did=did, fp=fp)
                _sentinel_user = _su.get("sentinel_token") if _su.get("ok") else None
            except Exception:
                _sentinel_user = None
            _pwd_headers = {
                "referer": "https://auth.openai.com/create-account/password",
                "origin": "https://auth.openai.com",
                "accept": "application/json",
                "content-type": "application/json",
                "oai-device-id": did,
                "user-agent": ua,
                **_make_trace_headers(),
            }
            if _sentinel_user:
                _pwd_headers["openai-sentinel-token"] = _sentinel_user
            _pwd_resp = s.post("https://auth.openai.com/api/accounts/user/register",
                               headers=_pwd_headers,
                               data=json.dumps({"password": password, "username": email}),
                               impersonate=impersonate)
            print(f"[debug] user/register status={_pwd_resp.status_code} body={_pwd_resp.text[:300]}")
            if _pwd_resp.status_code != 200:
                print("[Error] user/register 失败")
                return None
            _otp_send = s.get("https://auth.openai.com/api/accounts/email-otp/send", headers=_pwd_headers, timeout=15, stream=True, impersonate=impersonate)
            _otp_send.close()  # 只需触发 OTP 发送, 不下载 body
            print("[*] 密码流程 user/register 成功，已触发 email-otp/send")
            # password 流：OTP 在 email-otp/send 后才发出
            otp_issued_at = time.time()
        else:
            # web 流：authorize 带 login_hint 已自动发送 OTP，无密码步、无 authorize/continue
            print(f"[*] web 流：authorize 完成 did={did[:8]}... OTP 应已发送（无密码步）")

        # 等 OTP（IMAP 直读 / duck 转发 / icloud HME）
        # duck/163：两阶段 IMAP；password 保持 10s×2，web 用更长 phase 覆盖 DDG 延迟。
        # icloud：复用 web 两阶段时长，allow_resend=False（HME 转发对 resend 支持未知，避免拿错码）。
        # IMAP / dms：保持原 code_fetcher() 逻辑不变。
        if email_mode in ("duck", "163"):
            if is_password_flow:
                code = _wait_otp_imap_two_phase(
                    code_fetcher, s, did, ua, impersonate, cancel_check=cancel_check,
                    phase1_sec=IMAP_OTP_PHASE_SEC, phase2_sec=IMAP_OTP_PHASE_SEC,
                    allow_resend=True, otp_issued_at=otp_issued_at,
                )
            else:
                code = _wait_otp_imap_two_phase(
                    code_fetcher, s, did, ua, impersonate, cancel_check=cancel_check,
                    phase1_sec=IMAP_OTP_WEB_PHASE_SEC, phase2_sec=IMAP_OTP_WEB_PHASE2_SEC,
                    allow_resend=True, otp_issued_at=otp_issued_at,
                )
        elif email_mode == "icloud":
            code = _wait_otp_imap_two_phase(
                code_fetcher, s, did, ua, impersonate, cancel_check=cancel_check,
                phase1_sec=IMAP_OTP_ICLOUD_PHASE_SEC, phase2_sec=IMAP_OTP_WEB_PHASE2_SEC,
                allow_resend=False, otp_issued_at=otp_issued_at,
            )
        elif str(email_mode).startswith("imap:"):
            # 自建 IMAP 渠道 (catch-all 收件箱): 必须走两阶段 + not_before 时间过滤,
            # 否则 code_fetcher() 无 not_before 会逆序取到收件箱里别的号的旧 OTP
            # → wrong_email_otp_code 401。allow_resend=True 兜底 phase2 重发。
            code = _wait_otp_imap_two_phase(
                code_fetcher, s, did, ua, impersonate, cancel_check=cancel_check,
                phase1_sec=IMAP_OTP_WEB_PHASE_SEC, phase2_sec=IMAP_OTP_WEB_PHASE2_SEC,
                allow_resend=True, otp_issued_at=otp_issued_at,
            )
        else:
            code = code_fetcher()
        if cancel_check and cancel_check():
            print("[*] 已取消（等待 OTP 期间），中止本次注册")
            return None
        if not code:
            print("[Error] 验证码等待超时或提取失败")
            return None
        print(f"[*] 成功提取验证码: {code}")
        _human_delay(0.4, 1.5, "before email-otp/validate")

        # 第七步：校验验证码 → 服务端推进到 about_you
        validate_headers = {
            "referer": "https://auth.openai.com/email-verification",
            "origin": "https://auth.openai.com",
            "content-type": "application/json",
            "oai-device-id": did,
            **_make_trace_headers(),
        }
        code_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers=validate_headers,
            data=json.dumps({"code": code}),
            impersonate=impersonate,
        )
        if code_resp.status_code != 200:
            print(f"[Error] 验证码校验失败: {code_resp.status_code} | used_code={code} | body={code_resp.text[:600]}")
            return None

        # 抓包：validate 200 返回 continue_url=about-you + page.type=about_you，
        # 浏览器会 GET about-you 再 POST create_account。无浏览器也跟一次，
        # 避免服务端仍停在 email_verification → create_account invalid_auth_step。
        about_url = "https://auth.openai.com/about-you"
        try:
            vj = code_resp.json() if code_resp.text else {}
            page_type = str(((vj or {}).get("page") or {}).get("type") or "")
            cont = str((vj or {}).get("continue_url") or "").strip()
            # 已注册邮箱再走 OTP 校验，OpenAI 不回 about_you，
            # 而是 page=external_url + continue_url=chatgpt.com 登录回调。
            # 此时若盲信 cont 去 GET，会直接登录旧账号污染 cookie，
            # 随后 create_account 必 400 invalid_auth_step。
            # 识别此状态 → 放弃本次，让外层批量循环换别名重试（自定义渠道已去重）。
            if page_type == "external_url" or "chatgpt.com/api/auth/callback" in cont:
                # 已注册邮箱 OTP 校验返回登录回调 = 账号已存在。
                # 不放弃，直接走 next-auth callback 补抓 accessToken/sessionToken
                # (存量账号登录链路, 供补导入 token 库)。
                _login_at, _login_st = None, None
                try:
                    if cont and "code=" in cont:
                        _login_at, _login_st = finish_nextauth_access_token(s, cont)
                except Exception as _le:
                    print(f"[~] 存量登录 callback 异常: {repr(_le)[:150]}")
                if _login_at:
                    print(f"[+] 存量账号登录成功: at_len={len(_login_at)} "
                          f"st={'set' if _login_st else 'NONE'}")
                    _ok = True
                    return None, email, password, _login_at, _login_st or ""
                print(f"[!] 邮箱已注册（OTP validate 返回 external_url 登录回调）→ "
                      f"放弃本次，换别名/换号重试")
                print(f"[!] page={page_type or '?'} continue={cont[:100]}")
                # 自定义渠道：标记已注册（若有 mark_already_registered 钩子）
                if code_fetcher is not None:
                    try:
                        _mark = getattr(code_fetcher, "mark_already_registered", None)
                        if callable(_mark):
                            _mark("already_registered_openai")
                        _lfr = getattr(code_fetcher, "last_fail_reason", None)
                        if isinstance(_lfr, dict):
                            _lfr["reason"] = "already_registered"
                    except Exception:
                        pass
                return None
            if cont:
                about_url = cont
            print(f"[*] OTP OK page={page_type or '?'} continue={about_url[:100]}")
        except Exception as e:
            print(f"[~] 解析 validate body 失败（继续 GET about-you）: {e}")
        try:
            _ay = s.get(about_url, timeout=20, stream=True, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": chrome_fp.get("accept-language", "en-US,en;q=0.9"),
                "Upgrade-Insecure-Requests": "1",
                "user-agent": ua,
                "referer": "https://auth.openai.com/email-verification",
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
                **_make_trace_headers(),
            }, impersonate=impersonate)
            _ay.close()  # 只需 Set-Cookie, 不下载 HTML body (~63KB)
            print(f"[*] 已 GET about-you 推进会话 cookies={_auth_session_flags(s)}")
        except Exception as e:
            print(f"[~] GET about-you 异常（继续 create_account）: {repr(e)[:120]}")

        # 第八步前：真实 Chrome 生成 create_account 的 Sentinel/SO（混合架构）
        # 默认 OPENAI_SENTINEL_MODE=browser → sentinel_browser.generate_tokens
        # Node 伪 DOM 虽 t_ok=so_ok=True，但 t/so 非真解，服务端不认。
        sentinel_acct, so_token = None, None
        sdk_acct = None
        try:
            # 默认 pure（sentinel_pure_vm 纯 Python t/so，无 Chrome）；可用环境变量覆盖
            os.environ.setdefault("OPENAI_SENTINEL_MODE", "pure")
            # 配套对齐：sentinel VM 的 screen/webgl/platform/languages 与 HTTP 头同一套 fp
            sdk_acct = sentinel_sdk.sentinel_for(
                "oauth_create_account", proxy, did=did, fp=fp
            )
            _log_sdk("oauth_create_account", sdk_acct)
            if sdk_acct and sdk_acct.get("ok"):
                sentinel_acct = sdk_acct.get("sentinel_token")
                so_token = sdk_acct.get("so_token")
            else:
                print(f"[!] oauth_create_account SDK 未就绪: "
                      f"{(sdk_acct or {}).get('error') or 'unknown'}")
        except Exception as e:
            print(f"[!] create_account 的 Sentinel 构造失败：{e}")
        print(f"[*] sentinel(acct)={'set' if sentinel_acct else 'NONE'} "
              f"len={len(sentinel_acct) if sentinel_acct else 0} "
              f"so_token={'set' if so_token else 'NONE'} "
              f"so_len={len(so_token) if so_token else 0} "
              f"mode={(sdk_acct or {}).get('mode')}")
        if not sentinel_acct or not so_token:
            print("[Error] create_account 缺少真 t/so（pure-vm/browser/node 均失败），"
                  "请确认 Playwright+系统 Chrome 可用（OPENAI_SENTINEL_MODE=browser）")
            return None

        # 第八步：完成账号注册填写（对齐抓包 header + cookie 会话）
        flags = _auth_session_flags(s)
        print(f"[debug] pre-create_account cookies={flags} "
              f"names={[n for n in _session_cookie_names(s) if 'oai' in n or 'session' in n or 'csrf' in n or 'auth' in n]}")
        if not flags.get("oai_login_csrf") and not flags.get("login_session"):
            print("[Warn] 缺少 oai-login-csrf_* / login_session — 高概率 invalid_auth_step")

        create_headers = {
            "accept": "application/json",
            "referer": "https://auth.openai.com/about-you",
            "origin": "https://auth.openai.com",
            "content-type": "application/json",
            "oai-device-id": did,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            **_make_trace_headers(),
        }
        create_headers["openai-sentinel-token"] = sentinel_acct
        create_headers["openai-sentinel-so-token"] = so_token
        create_data = {"name": _random_name(), "birthdate": _random_birthdate()}
        # 抓包 web 流 create_account body 仅 name+birthdate，不带 cf_turnstile_response
        if cancel_check and cancel_check():
            print("[*] 已取消（create_account 前），中止本次注册")
            return None
        _human_delay(0.6, 2.0, "before create_account (fill profile)")
        create_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers=create_headers,
            data=json.dumps(create_data),
            impersonate=impersonate,
        )
        print(f"[debug] create_account status={create_resp.status_code} body={create_resp.text[:500]}")
        if create_resp.status_code != 200:
            try:
                _e = create_resp.json().get("error", {})
                _code = _e.get("code")
                print(f"[Error] 账户信息填写失败: {create_resp.status_code} | "
                      f"code={_code} msg={_e.get('message')}")
                if _code == "invalid_auth_step":
                    print("[Hint] invalid_auth_step = 会话不在 about_you 步（检查 authorize 端点/cookie），"
                          "不是 registration_disallowed（后者才是 t/so/风控）")
                elif _code == "registration_disallowed":
                    print("[Hint] registration_disallowed = sentinel t/so 或设备/IP/邮箱风控；"
                          "确认 mode=browser 且 t/so 来自真实 Chrome")
                elif _code == "user_already_exists":
                    print("[Hint] user_already_exists = 该邮箱已注册过（DDG 别名重复），"
                          "burn 别名后外层换号重试")
            except Exception:
                print(f"[Error] 账户信息填写失败: {create_resp.status_code} | "
                      f"body={create_resp.text[:600]}")
            return None

        # web 流：create_account 200 的 continue_url 直接含 next-auth callback code+state。
        # 此 code 不能用 /oauth/token 换 Codex token（会 401）。
        # 正确收尾（勿再 POST signin/openai — 那会换新 state 并落 /auth/login）：
        #   1) GET continue_url（callback/openai?code=&state=）— 需匹配 __Secure-next-auth.state
        #   2) GET /api/auth/session → {accessToken, user, expires}
        try:
            _cj = create_resp.json()
            _cu = str(_cj.get("continue_url") or "").strip()
        except Exception:
            _cu = ""
        if _cu and "code=" in _cu:
            print("[debug] web 流 create_account 200，GET continue_url 完成 next-auth callback 取 accessToken")
            # 校验 continue_url 的 state 与 signin 时保存的 state 一致
            try:
                _cb = _parse_callback_url(_cu)
                if oauth and oauth.state and _cb.get("state") and _cb["state"] != oauth.state:
                    print(f"[Warn] continue_url state 与 next-auth state 不一致 "
                          f"cu={_cb['state'][:16]} oauth={oauth.state[:16]}")
                else:
                    print(f"[debug] continue_url state 对齐 next-auth "
                          f"state={(_cb.get('state') or '')[:16]}...")
            except Exception:
                pass
            _access_token, _session_token = finish_nextauth_access_token(s, _cu)
            _ok = True
            # web 流无 Codex token（next-auth code 由 chatgpt.com 服务端换票），
            # accessToken 来自 chatgpt.com/api/auth/session
            return None, email, password, _access_token, _session_token

        # 第九步：选择工作区 Workspace（Codex OAuth 流才需要）
        auth_cookie = s.cookies.get("oai-client-auth-session")
        print(f"[debug] auth_cookie={bool(auth_cookie)} cookie_names={list(s.cookies.keys())}")
        if not auth_cookie:
            print("[debug] 无 auth_cookie，create_account 可能没真正建号或流程变了")
            return None
        auth_json = _decode_jwt_segment(auth_cookie.split(".")[0])
        workspace_id = str((auth_json.get("workspaces") or [{}])[0].get("id") or "").strip()
        
        select_resp = s.post("https://auth.openai.com/api/accounts/workspace/select", headers={"referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent", "origin": "https://auth.openai.com", "content-type": "application/json", "oai-device-id": did, "user-agent": ua, **_make_trace_headers()}, data=json.dumps({"workspace_id": workspace_id}), impersonate=impersonate)
        print(f"[debug] workspace/select status={select_resp.status_code} body={select_resp.text[:300]}")
        if select_resp.status_code != 200: return None
        
        continue_url = str((select_resp.json() or {}).get("continue_url") or "").strip()

        # 第十步：拦截重定向，提取终极 Token
        current_url = continue_url
        for _ in range(6):
            final_resp = s.get(current_url, allow_redirects=False, timeout=15)
            location = final_resp.headers.get("Location") or ""
            print(f"[debug] redirect[{_}] status={final_resp.status_code} url={current_url[:80]} loc={location[:120]}")
            if final_resp.status_code not in [301, 302, 303, 307, 308] or not location:
                break
            next_url = urllib.parse.urljoin(current_url, location)
            if "code=" in next_url and "state=" in next_url:
                token_json = submit_callback_url(callback_url=next_url, code_verifier=oauth.code_verifier, redirect_uri=oauth.redirect_uri, expected_state=oauth.state)
                _ok = True
                # Codex OAuth 流（/oauth/token 换票成功），无 next-auth accessToken
                return token_json, email, password, "", ""
            current_url = next_url

        print("[Error] 未能在重定向链中捕获到最终 Token")
        return None

    except Exception as _e:
        print(f"[兜底] 未预期异常，放弃本号: {repr(_e)[:200]}")
        return None
    finally:
        _set_cancel_check(None)
        if _email:
            try:
                _reason = None
                if not _ok and code_fetcher is not None:
                    _lfr = getattr(code_fetcher, "last_fail_reason", None)
                    if isinstance(_lfr, dict):
                        _reason = (_lfr.get("reason") or "").strip() or None
                if not _ok and not _reason:
                    _reason = "other_fail"
                provider_stats.record(_email, _ok, reason=_reason)
            except Exception:
                pass
            try:
                print("[统计] 邮箱域名成功率:\n" + provider_stats.summary())
            except Exception:
                pass


# ========== 4. 主程序轮询与保存 ==========

def main():
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="OpenAI 完美融合自动化注册脚本 (By Gemini)")
    parser.add_argument(
        "--proxy",
        default=None,
        help="代理地址，如 http://127.0.0.1:7890 或 711: "
             "http://USER:PASS@global.rotgb.711proxy.com:10000 "
             "（711 会自动改写为 Clash 链式中继）",
    )
    parser.add_argument("--email", choices=list_email_channels(), default="", help="注册邮箱来源（留空=自动选第一个可用渠道；imap:<标签>=自建域名邮箱）")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("--count", type=int, default=0, help="总注册次数（0=无限，配合 --once 等价于1）")
    args = parser.parse_args()

    count = 0
    print("========================================")
    print("[*] OpenAI 终极注册机 (带 Token 提取及 DDG@duck.com / 163 邮箱)")
    print("========================================")
    
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        count += 1
        # 总尝试上限：最终保险，避免代理/池整体不可用时无限换号空耗
        if count > MAX_TOTAL_ATTEMPTS:
            print(f"[*] 已达总尝试上限 {MAX_TOTAL_ATTEMPTS}，停止（可能代理/池整体不可用）")
            break
        if args.count and count > args.count:
            count -= 1
            break
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 开始第 {count} 次注册流程 <<<")
        run_result = run(args.proxy, email=args.email)
        
        if run_result:
            token_json, email, password, access_token, session_token = run_result
            fname_email = email.replace("@", "_")

            # 保存机制 1：单独保存 Token JSON 文件
            tokens_dir = OUT_DIR / "tokens"
            tokens_dir.mkdir(parents=True, exist_ok=True)
            file_path = tokens_dir / f"token_{fname_email}_{int(time.time())}.json"
            if token_json:
                file_path.write_text(token_json, encoding="utf-8")
                print(f"[OK] 成功获取 Token！已保存至: {file_path}")
            elif access_token:
                save = {"accessToken": access_token, "email": email}
                if session_token:
                    save["session_token"] = session_token
                file_path.write_text(json.dumps(save, ensure_ascii=False), encoding="utf-8")
                print(f"[OK] 账号创建成功，accessToken 已保存至: {file_path}")
            else:
                print(f"[OK] 账号创建成功（create_account 200），web 流无 Codex token（next-auth code）")

            # 保存机制 2：汇总账号密码信息
            acc_file = tokens_dir / "accounts.txt"
            with open(acc_file, "a", encoding="utf-8") as f:
                f.write(f"{email}----{password}\n")
            print(f"[OK] 账号已追加至: {acc_file}")
            
        else:
            print("[-] 本次注册流程断开。")

        if args.once:
            break
            
        wait_time = _batch_cooldown_seconds()
        print(f"[*] 冷却 {wait_time} 秒（anti-fuzz 号间随机间隔）...")
        time.sleep(wait_time)

if __name__ == "__main__":
    main()
