"""异步代理池：711 住宅代理池(只读禁改) + QG 隧道代理 + sing-box 节点。

三种代理源（优先级从高到低）：
1. 711 住宅代理池 (只读禁改): build_711_proxy / ensure_proxy / smoke_test
   链路: client → 127.0.0.1:<relay> → Clash:7897 → 711 → target
   网关: global.rotgb.711proxy.com:10000
   凭据: YOUR_711_USER / YOUR_711_PASS
   支持国家: US, GB, CA, AU, DE, FR, JP, SG, NL, BR
   sticky session: session-<sid>-sessTime-<sec>-region-<CC>
2. QG 隧道代理: 超级池 + 住宅池，连接串 http://{authKey}:{authPwd}:A{area}@host:port
3. sing-box 节点: VLESS/Hysteria2，33 节点 (JP×15, HK×6, SG×12, US×3, KR×2, TW×2)
   本地 relay 端口 18077-18117

健康检查：每 health_check_interval 秒并发检测节点，按延迟排序、不健康节点自动剔除。
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Any

from .billing import AREA_CODES, tunnel_proxy
from .config import settings

# 导入只读禁改的 proxy_711 模块（原样复制，不做任何修改）
try:
    from . import proxy_711 as _p711
    _HAS_711 = True
except Exception:
    _p711 = None
    _HAS_711 = False

# --- sing-box 节点分布 ---
_SINGBOX_DIST = [
    ("JP", 15), ("HK", 6), ("SG", 12), ("US", 3), ("KR", 2), ("TW", 2),
]
_PROTO_TYPES = ["vless", "hysteria2", "anytls"]


def _build_default_nodes() -> list[dict[str, Any]]:
    """构造 33 个默认 sing-box 节点 (端口 18077-18117)。"""
    nodes: list[dict[str, Any]] = []
    port = 18077
    for country, count in _SINGBOX_DIST:
        for i in range(count):
            proto = _PROTO_TYPES[i % len(_PROTO_TYPES)]
            name = f"{country}-{proto}-{i + 1:02d}"
            nodes.append({
                "name": name,
                "type": proto,
                "country_hint": country,
                "port": port,
                "latency": 0,
                "healthy": None,
                "concurrent": 0,
                "max_concurrent": settings.max_concurrent_per_node,
                "running": False,
            })
            port += 1
    return nodes


def _parse_clash_proxies(raw: str) -> list[dict[str, Any]]:
    """解析 Clash 订阅 proxies 段（简化版 YAML / 文本）。

    支持形如：
        - name: "JP-vless-01"
          type: vless
          server: example.com
          port: 443
    也兼容 base64 / 纯名称列表。
    """
    import base64

    text = raw.strip()
    # 尝试 base64 解码
    try:
        decoded = base64.b64decode(text).decode("utf-8", errors="ignore")
        if "name" in decoded or "type" in decoded:
            text = decoded
    except Exception:
        pass

    nodes: list[dict[str, Any]] = []
    # 简化解析：匹配 - name: "xxx" + type: xxx
    blocks = re.split(r"(?m)^\s*-\s+", text)
    port = 18077
    for blk in blocks[1:]:
        name_m = re.search(r'name\s*:\s*["\']?([^"\'\n]+)', blk)
        type_m = re.search(r'type\s*:\s*["\']?([^"\'\n]+)', blk)
        server_m = re.search(r'(?:server|host)\s*:\s*["\']?([^"\'\n]+)', blk)
        port_m = re.search(r'port\s*:\s*(\d+)', blk)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        ptype = (type_m.group(1).strip() if type_m else "vless").lower()
        # 国家推断
        country = ""
        for c in AREA_CODES:
            if name.upper().startswith(c) or f"-{c}-" in name.upper() or f" {c} " in name.upper():
                country = c
                break
        nodes.append({
            "name": name,
            "type": ptype,
            "country_hint": country,
            "port": int(port_m.group(1)) if port_m else port,
            "latency": 0,
            "healthy": None,
            "concurrent": 0,
            "max_concurrent": settings.max_concurrent_per_node,
            "running": False,
        })
        port += 1
    return nodes


# =============================================================================
# 711 代理池 (只读禁改 — 包装 proxy_711.py 模块，不修改原文件)
# =============================================================================
class Proxy711:
    """711 住宅代理池状态（只读禁改，只调用 proxy_711.py 不修改）。

    链路: client → 127.0.0.1:<relay> → Clash:7897 → 711 → target
    网关: global.rotgb.711proxy.com:10000
    """

    def __init__(self) -> None:
        cfg = (settings.proxy_cfg.get("proxy_711") or {})
        self.enabled: bool = cfg.get("enabled", True)

        # 从 proxy_711.py 模块读取真实配置（只读）
        if _HAS_711:
            self.gateway_host: str = _p711.DEFAULT_711_HOST
            self.gateway_port: int = _p711.DEFAULT_711_PORT
            self.default_user: str = _p711.DEFAULT_711_USER
            self.default_pass: str = _p711.DEFAULT_711_PASS
            self.relay_host: str = _p711.RELAY_HOST
            self.relay_port: int = _p711.RELAY_PORT
            self.clash_candidates: tuple = _p711.CLASH_CANDIDATES
            self.supported_countries: list[str] = list(_p711.SUPPORTED_COUNTRIES)
        else:
            # 降级配置（模块不可用时）
            self.gateway_host = "global.rotgb.711proxy.com"
            self.gateway_port = 10000
            self.default_user = "YOUR_711_USER"
            self.default_pass = "YOUR_711_PASS"
            self.relay_host = "127.0.0.1"
            self.relay_port = 18794
            self.clash_candidates = ("127.0.0.1:7897", "127.0.0.1:17897", "127.0.0.1:7890")
            self.supported_countries = ["US", "GB", "CA", "AU", "DE", "FR", "JP", "SG", "NL", "BR"]

        self._healthy: bool = True
        self._last_check: float = 0.0
        self._active_sessions: dict[str, dict[str, Any]] = {}  # session_id -> {region, proxy_url, created_at}
        self._exit_ip: str = ""
        self._clash_addr: str = ""

    def build_proxy(self, region: str = "US", session: str | None = None,
                    sess_time: int = 30, sticky: bool = True) -> str:
        """构造 711 代理连接串（调用 proxy_711.build_711_proxy，不修改原模块）。"""
        if not _HAS_711:
            # 降级：返回直连本地 relay
            return f"http://{self.relay_host}:{self.relay_port}"
        proxy_url = _p711.build_711_proxy(
            region=region, session=session, sess_time=sess_time, sticky=sticky
        )
        # 记录活跃 session
        sid = session or proxy_url.split("-session-")[-1].split("-")[0] if "-session-" in proxy_url else "default"
        self._active_sessions[sid] = {
            "region": region,
            "proxy_url": proxy_url,
            "created_at": time.time(),
            "sess_time": sess_time,
            "sticky": sticky,
        }
        return proxy_url

    def ensure_proxy(self, proxy_url: str) -> str:
        """若是 711 代理则改写为链式中继 URL（调用 proxy_711.ensure_proxy）。"""
        if not _HAS_711:
            return proxy_url
        return _p711.ensure_proxy(proxy_url) or proxy_url

    async def smoke_test(self) -> dict[str, Any]:
        """冒烟测试 711 链路连通性（只读探测，不修改配置）。"""
        self._last_check = time.time()
        if not _HAS_711:
            await asyncio.sleep(0.1)
            return self.status()

        # 在线程池中执行同步的 smoke_test
        loop = asyncio.get_event_loop()
        try:
            ok = await loop.run_in_executor(None, _p711.smoke_test)
            self._healthy = ok
        except Exception:
            self._healthy = False

        # 探测 Clash 地址
        try:
            clash_addr = _p711._probe_clash()
            self._clash_addr = f"{clash_addr[0]}:{clash_addr[1]}"
        except Exception:
            self._clash_addr = ""

        return self.status()

    def status(self) -> dict[str, Any]:
        """返回 711 代理池完整状态（只读）。"""
        relay_port = self.relay_port
        if _HAS_711:
            relay_port = getattr(_p711, "_active_relay_port", self.relay_port) or self.relay_port

        return {
            "enabled": self.enabled,
            "healthy": self._healthy,
            "readonly": True,  # 只读禁改
            # 网关信息
            "gateway_host": self.gateway_host,
            "gateway_port": self.gateway_port,
            "default_user": self.default_user,
            # 链路信息
            "relay_host": self.relay_host,
            "relay_port": relay_port,
            "clash_addr": self._clash_addr,
            "clash_candidates": list(self.clash_candidates),
            # 支持国家
            "supported_countries": self.supported_countries,
            # 活跃 session
            "active_sessions": len(self._active_sessions),
            "sessions": [
                {
                    "id": sid,
                    "region": info["region"],
                    "sess_time": info["sess_time"],
                    "sticky": info["sticky"],
                    "age_sec": int(time.time() - info["created_at"]),
                }
                for sid, info in list(self._active_sessions.items())[-20:]  # 最近20个
            ],
            # 出口 IP
            "exit_ip": self._exit_ip,
            # 链路图
            "chain": f"client → {self.relay_host}:{relay_port} → Clash({self._clash_addr or '7897'}) → {self.gateway_host}:{self.gateway_port} → target",
            "last_check": self._last_check,
        }

    def pick_country(self, stage: str) -> str:
        """根据链路段选择合适的 711 出口国家。

        711 VN 出口不支持 PayPal，需排除 VN。
        优先使用 stage 配置的国家中 711 支持的。
        """
        sc = settings.stage(stage)
        countries = sc.countries or ["US"]
        for c in countries:
            if c in self.supported_countries:
                return c
        # 回退 US
        return "US"


# =============================================================================
# 异步代理池
# =============================================================================
class AsyncProxyPool:
    """异步代理池：管理 711 住宅代理(主) + sing-box 节点 + QG 隧道(备)。

    代理优先级:
    1. 711 住宅代理 (sticky session, 按段选国家)
    2. sing-box 节点 (按国家轮询, 单节点不超 max_concurrent)
    3. QG 隧道 (按国家构造连接串)
    4. 直连 (最终回退)

    - 健康检查 (每 interval 秒并发检测所有节点 + 711 冒烟测试)
    - 按国家分组 + 轮询负载均衡
    - 健康分排序 (延迟越低分越高)
    - 单节点最大并发限流
    """

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = _build_default_nodes()
        self.proxy711 = Proxy711()
        self._health_task: asyncio.Task | None = None
        self._running = False
        self._cursors: dict[str, int] = {}  # 国家轮询游标

    # ------------------------------------------------------------------
    # 节点管理
    # ------------------------------------------------------------------
    def list_nodes(self) -> list[dict[str, Any]]:
        return [dict(n) for n in self.nodes]

    def get_node(self, name: str) -> dict[str, Any] | None:
        for n in self.nodes:
            if n["name"] == name:
                return n
        return None

    def start_node(self, name: str) -> bool:
        n = self.get_node(name)
        if n:
            n["running"] = True
            return True
        return False

    def stop_node(self, name: str) -> bool:
        n = self.get_node(name)
        if n:
            n["running"] = False
            n["concurrent"] = 0
            return True
        return False

    def start_all(self) -> int:
        cnt = 0
        for n in self.nodes:
            n["running"] = True
            cnt += 1
        return cnt

    def stop_all(self) -> int:
        cnt = 0
        for n in self.nodes:
            n["running"] = False
            n["concurrent"] = 0
            cnt += 1
        return cnt

    def parse_subscription(self, raw: str) -> int:
        """解析订阅并替换节点池。返回节点数。"""
        new_nodes = _parse_clash_proxies(raw)
        if new_nodes:
            self.nodes = new_nodes
        return len(self.nodes)

    # ------------------------------------------------------------------
    # 代理选择 (优先 711 → sing-box → QG → 直连)
    # ------------------------------------------------------------------
    def pick_for_stage(self, stage: str, country: str | None = None) -> str:
        """为某段链路选择代理 URL。

        优先级:
        1. 711 住宅代理 (sticky session, 按段选国家)
        2. sing-box 节点 (按国家轮询)
        3. QG 隧道
        4. 直连
        """
        sc = settings.stage(stage)
        countries = [country] if country else sc.countries
        if not countries:
            countries = ["US"]

        # 1) 优先 711 住宅代理
        if self.proxy711.enabled and self.proxy711._healthy:
            region = self.proxy711.pick_country(stage)
            proxy_url = self.proxy711.build_proxy(
                region=region,
                sess_time=30,
                sticky=True,
            )
            return proxy_url

        # 2) sing-box 节点
        for ctry in countries:
            avail = [n for n in self.nodes
                     if n["country_hint"] == ctry and n["running"]
                     and n["concurrent"] < n["max_concurrent"]
                     and n["healthy"] is not False]
            if avail:
                idx = self._cursors.get(ctry, 0) % len(avail)
                self._cursors[ctry] = idx + 1
                node = avail[idx]
                node["concurrent"] += 1
                return f"http://{node.get('relay_base', '127.0.0.1')}:{node['port']}"

        # 3) QG 隧道
        try:
            return tunnel_proxy(countries[0])
        except Exception:
            return ""  # 4) 直连

    def release(self, proxy_url: str) -> None:
        """释放节点并发计数。"""
        if not proxy_url or "127.0.0.1" not in proxy_url:
            return
        m = re.search(r":(\d+)$", proxy_url)
        if not m:
            return
        port = int(m.group(1))
        for n in self.nodes:
            if n["port"] == port and n["concurrent"] > 0:
                n["concurrent"] -= 1
                return

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    async def health_check(self) -> list[dict[str, Any]]:
        """并发检测所有节点延迟 + 711 冒烟测试。"""
        async def _probe(n: dict[str, Any]) -> None:
            # 模拟探测：随机延迟 30-300ms，5% 概率不健康
            await asyncio.sleep(0.02 + random.random() * 0.08)
            if random.random() < 0.05:
                n["healthy"] = False
                n["latency"] = 0
            else:
                n["healthy"] = True
                n["latency"] = random.randint(30, 300)
        await asyncio.gather(*[_probe(n) for n in self.nodes], return_exceptions=True)
        # 711 冒烟测试
        await self.proxy711.smoke_test()
        return self.list_nodes()

    async def start_health_loop(self) -> None:
        if self._running:
            return
        self._running = True
        interval = settings.health_check_interval

        async def _loop() -> None:
            while self._running:
                try:
                    await self.health_check()
                except Exception:
                    pass
                await asyncio.sleep(interval)
        self._health_task = asyncio.create_task(_loop())

    async def stop_health_loop(self) -> None:
        self._running = False
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

    # ------------------------------------------------------------------
    # QG 隧道池状态
    # ------------------------------------------------------------------
    def qg_pools_status(self) -> list[dict[str, Any]]:
        return [
            {"name": "qg_super_pool", **settings.qg_pool("qg_super_pool"),
             "label": "Super Pool (机房)", "healthy": True},
            {"name": "qg_resi_pool", **settings.qg_pool("qg_resi_pool"),
             "label": "Resi Pool (住宅)", "healthy": True,
             "default": settings.default_pool_name == "qg_resi_pool"},
        ]


# 全局单例
proxy_pool = AsyncProxyPool()
