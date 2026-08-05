USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.7871.46 Safari/537.36"
)

SCREEN = {
    "colorDepth": 24,
    "pixelDepth": 24,
    "height": 864,
    "width": 1536,
    "availHeight": 864,
    "availWidth": 1536,
}

VIEWPORT = {"width": 1365, "height": 768}

# Keep every synthetic browser signal on the same regional/device profile.
# The checkout flow is hard-coded for Brazil, so timezone, locale, analytics,
# FraudNet and Client-Hints must all agree.
BROWSER_PROFILE = {
    "country": "BR",
    "language": "pt-BR",
    "locale": "pt_BR",
    "timezone": "America/Sao_Paulo",
    # JavaScript Date#getTimezoneOffset for UTC-3 is +180 minutes.
    "timezone_offset_minutes": 180,
    # FraudNet p1 uses millisecond offset in the same sign as getTimezoneOffset.
    "timezone_offset_ms": 180 * 60 * 1000,
    "dst": False,
    "chrome_major": 150,
    "chrome_full_version": "150.0.7871.46",
    "platform": "Linux x86_64",
    "sec_ch_platform": '"Linux"',
    "sec_ch_platform_version": '""',
    "sec_ch_arch": '"x86"',
    "device_memory": 8,
    "hardware_concurrency": 12,
    "device_pixel_ratio": 1,
    "connection_effective_type": "4g",
    "connection_rtt": "150",
    "connection_downlink": "10",
    "gpu_vendor": "Google Inc. (Google)",
    "gpu_renderer": (
        "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) "
        "(0x0000C0DE)), SwiftShader driver)"
    ),
    "webgl_vendor": "WebKit",
    "webgl_renderer": "WebKit WebGL",
}

TEALEAF_APP_KEY = "76938917d7504ff7a962174c021690bd"
HCAPTCHA_SITEKEY = "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"


# 浏览器指纹来源：
#   "random" 继续使用程序内合成的随机/模板指纹；
#   "roxy"   通过 RoxyBrowser Local API 创建随机指纹窗口，再从真实 Chromium
#            runtime 读取 canvas/WebGL/audio/heap/screen/UA 等信号；
#   "headless" 使用本机 Playwright headless Chromium 读取 runtime 信号；
#   "auto"   当配置了 Roxy API key 时优先 Roxy，否则回退 random。
# 运行时可用 PAYPAL_FINGERPRINT_SOURCE 覆盖。
FINGERPRINT_SOURCE = "random"

# RoxyBrowser Local API。不要把真实 API key 提交到仓库，优先放 .env：
#   PAYPAL_FINGERPRINT_SOURCE=roxy
#   PAYPAL_ROXY_API_KEY=xxxx
#   PAYPAL_ROXY_API_PORT=50000
ROXY_API_HOST = "127.0.0.1"
ROXY_API_PORT = 50000
ROXY_API_KEY = ""
# Roxy Local API 的 /browser/open 支持 headless 字段；Roxy 模式默认使用无头模式。
ROXY_HEADLESS = True

# 可选：固定 workspace/project；为空时自动读取 /browser/workspace 第一项。
ROXY_WORKSPACE_ID: int | None = None
ROXY_PROJECT_ID: int | None = None


# DataDome 处理模式：
#   "protocol" 保留原来的协议边缘模拟：提取 datadome client id，并在无
#              datadome cookie 时注入 x-datadome-clientid；
#   "roxy"     使用上面同一个 Roxy 指纹浏览器加载 PayPal/DataDome，让真实
#              Chrome runtime 执行 ddbm2/paypal challenge 链并回灌 cookie；
#   "headless" 使用本机 Playwright headless Chromium 执行 DataDome 链；
#   "auto"     遇到 403/authchallenge 或缺 cookie 时优先 Roxy，失败后回退 protocol；
#   "off"      不主动处理 DataDome。
# 运行时可用 PAYPAL_DATADOME_MODE 覆盖。
DATADOME_MODE = "protocol"
DATADOME_ROXY_WAIT_SECONDS = 12.0


# MTR sealedResult 来源：
#   "python_generated" 保留原来的协议模板提交；
#   "roxy"             使用同一个 Roxy 指纹浏览器加载 PayPal 页面/dfp.js，
#                      监听真实 `/mtr/.../x0` 和 `/mtr/...` POST 响应并回灌
#                      requestId/sealedResult/cookies；
#   "headless"         使用本机 Playwright headless Chromium 执行 dfp.js；
#   "auto"             优先 roxy，失败后回退 python_generated；
#   "off"              不发送 MTR。
# 运行时可用 PAYPAL_MTR_RUNTIME 覆盖。
MTR_RUNTIME_MODE = "python_generated"
MTR_ROXY_WAIT_SECONDS = 20.0

# MTR dfpconfig fallback.  新版 PayPal 页面有时不再直接在首屏 HTML
# 输出 `<script id="dfpconfig">`，但 dfp.js 仍需要 channel/cmid/api key。
# 运行时优先读取页面/RSC/浏览器 DOM 中的 dfpconfig；下面的 API key 只作为
# 用户显式手动覆盖，不再内置固定值：
#   PAYPAL_MTR_CHANNEL=iwc-mxo
#   PAYPAL_MTR_API_KEY=...
MTR_CHANNEL = "iwc-mxo"
MTR_API_KEY = ""


# signup-context browser risk 来源。主流程已移除独立 Phase 1 风控信号步骤。
#   "roxy"     使用同一个 Roxy 指纹浏览器执行 signup-context browser risk；
#   "headless" 使用本机 Playwright headless Chromium；
#   "auto"     优先 roxy，Roxy 不可用时回退 headless。
# 运行时可用 PAYPAL_RISK_SIGNALS_MODE 覆盖。
# PAYPAL_ENABLE_SIGNUP_CONTEXT_RISK 是旧开关，不再允许关闭 Step 3 风控。
RISK_SIGNALS_MODE = "protocol"
RISK_ROXY_WAIT_SECONDS = 18.0
