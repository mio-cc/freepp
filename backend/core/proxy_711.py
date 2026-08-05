# -*- coding: utf-8 -*-
"""711 住宅代理链式中继：curl_cffi / requests → 本机 relay → Clash → 711 → 目标。

根因（见 PROXY_FIX.md）：
  711（global.rotgb.711proxy.com:10000）对「本机直连」的 HTTP CONNECT 直接掐断
  （空响应 / Proxy CONNECT aborted），但经 Clash 日本出口再连 711 时 CONNECT 正常。
  Camoufox 能用，是因为系统侧 Clash 链路；curl_cffi 显式指定 711 时走直连被拒。

本模块启动一个仅监听 127.0.0.1 的 HTTP CONNECT 中继：
  1) 客户端 → relay：CONNECT target:443
  2) relay → Clash(7897)：CONNECT 711:10000
  3) relay → 711：CONNECT target:443 + Proxy-Authorization
  4) 双向 pipe

用法：
  from proxy_711 import ensure_proxy
  proxy = ensure_proxy("http://USER:PASS@global.rotgb.711proxy.com:10000")
  # → "http://USER:PASS@127.0.0.1:18794"（默认端口，被占则自动换）
"""
from __future__ import annotations

import atexit
import base64
import os
import re
import select
import socket
import threading
import time
from typing import Optional, Tuple
from urllib.parse import quote, unquote, urlparse

# temporary one-shot BR probe hook (noop after flag)
try:
    import _force_br_probe_hook  # noqa: F401
except Exception:
    pass

# 711 网关（可被环境变量覆盖）
DEFAULT_711_HOST = os.environ.get("PROXY_711_HOST", "global.rotgb.711proxy.com")
DEFAULT_711_PORT = int(os.environ.get("PROXY_711_PORT", "10000"))
DEFAULT_711_USER = os.environ.get("PROXY_711_USER", "YOUR_711_USER")
DEFAULT_711_PASS = os.environ.get("PROXY_711_PASS", "YOUR_711_PASS")

# Clash / mihomo mixed-port 候选（FlClash 默认 7890；历史 Clash Verge 7897）
CLASH_CANDIDATES = (
    os.environ.get("CLASH_PROXY", ""),
    "127.0.0.1:7890",
    "127.0.0.1:7897",
    "127.0.0.1:17897",
)

RELAY_HOST = "127.0.0.1"
# 默认 18794，避开 codex 旧版 18792（仅 CONNECT、明文 HTTP 会 405）
RELAY_PORT = int(os.environ.get("PROXY_711_RELAY_PORT", "18794"))
_RELAY_PORT_CANDIDATES = (
    RELAY_PORT,
    18794,
    18793,
    18792,
    18795,
)

_711_HINTS = (
    "711proxy.com",
    "rotgb.711",
    "711proxy",
)

# OpenAI/ChatGPT 支持且 711 住宅代理可达的国家白名单（注册代理国家框定）
# 已实测可达：US、BR（见项目记忆）；其余为常见住宅国家且 OpenAI 支持。
SUPPORTED_COUNTRIES = ["US", "GB", "CA", "AU", "DE", "FR", "JP", "SG", "NL", "BR"]

_relay_lock = threading.Lock()
_relay: Optional["ChainRelay"] = None
_clash_addr: Optional[Tuple[str, int]] = None
_active_relay_port: int = RELAY_PORT

# ---- 711 对部分主机名的 CONNECT 会空响应掐断（2026-07-17 实测：
#      www.paypal.com → empty；同 IP CONNECT 200 + TLS SNI 正常）。
#      中继把这些 host 改写为 IP 以绕过拦截。名单可经环境变量扩展。 ----
_CONNECT_IP_REWRITE_HOSTS = frozenset(
    h.strip().lower()
    for h in (
        os.environ.get("PROXY_711_CONNECT_REWRITE_HOSTS")
        or "www.paypal.com"
    ).split(",")
    if h.strip()
)

_dns_lock = threading.Lock()
_dns_cache: dict = {}


def _is_fake_ip(ip: str) -> bool:
    """Clash/mihomo fake-ip 池段（198.18.0.0/15）。"""
    try:
        parts = [int(x) for x in ip.split(".")]
        return len(parts) == 4 and parts[0] == 198 and 18 <= parts[1] <= 19
    except ValueError:
        return False


def _resolve_host_ip(host: str, ttl: float = 300.0) -> Optional[str]:
    """解析主机真实 IP：缓存优先 → 系统 DNS → 经 Clash DoH(1.1.1.1) 回退。

    系统 DNS 若被 Clash fake-ip 接管会返回 198.18.x.x（711 连不上），
    此处识别 fake-ip 后经 Clash 走 DoH JSON 拿真实 IP 并写回缓存。
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return None
    now = time.time()
    with _dns_lock:
        hit = _dns_cache.get(host)
        if hit and hit[1] > now:
            return hit[0]
    ip: Optional[str] = None
    try:
        ip = socket.gethostbyname(host)
        if _is_fake_ip(ip):
            ip = None
    except OSError:
        ip = None
    if ip is None:
        clash = _probe_clash()
        if clash:
            try:
                s = socket.create_connection(clash, timeout=6)
                try:
                    s.settimeout(6)
                    req = (
                        f"GET https://1.1.1.1/dns-query?name={host}&type=A HTTP/1.1\r\n"
                        f"Host: 1.1.1.1\r\n"
                        f"Accept: application/dns-json\r\n"
                        f"Connection: close\r\n\r\n"
                    )
                    s.sendall(req.encode("latin1"))
                    raw = b""
                    while True:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        raw += chunk
                        if len(raw) > 65536:
                            break
                    body = raw.split(b"\r\n\r\n", 1)[-1].decode("utf-8", "replace")
                    for m in re.finditer(r'"data"\s*:\s*"(\d+\.\d+\.\d+\.\d+)"', body):
                        real_ip = m.group(1)
                        if not _is_fake_ip(real_ip):
                            ip = real_ip
                            break
                finally:
                    try:
                        s.close()
                    except OSError:
                        pass
            except OSError:
                ip = None
    if ip:
        with _dns_lock:
            _dns_cache[host] = (ip, time.time() + ttl)
    return ip


def _rewrite_connect_target(raw: str) -> str:
    """命中改写名单的 CONNECT 目标 host:port → ip:port（绕过 711 主机名拦截）。"""
    if ":" not in raw:
        return raw
    host, _, port = raw.rpartition(":")
    host_l = host.strip().lower().strip("[]")
    if host_l not in _CONNECT_IP_REWRITE_HOSTS:
        return raw
    ip = _resolve_host_ip(host_l)
    if not ip:
        return raw
    if os.environ.get("PROXY_711_DEBUG", "0") == "1":
        print(f"[proxy_711] rewrite CONNECT {raw} → {ip}:{port}")
    return f"{ip}:{port}"


def is_711_proxy(url: Optional[str]) -> bool:
    """判断是否为 711 住宅代理 URL / host:port 串。"""
    if not url:
        return False
    low = url.strip().lower()
    return any(h in low for h in _711_HINTS)


def parse_proxy_url(url: str) -> dict:
    """解析 http://user:pass@host:port 或 host:port:user:pass。"""
    raw = (url or "").strip()
    if not raw:
        return {
            "host": DEFAULT_711_HOST,
            "port": DEFAULT_711_PORT,
            "user": DEFAULT_711_USER,
            "password": DEFAULT_711_PASS,
        }
    if "://" not in raw and raw.count(":") == 3:
        # host:port:user:pass
        host, port, user, password = raw.split(":", 3)
        return {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
        }
    if "://" not in raw:
        raw = "http://" + raw
    p = urlparse(raw)
    return {
        "host": p.hostname or DEFAULT_711_HOST,
        "port": p.port or DEFAULT_711_PORT,
        "user": unquote(p.username) if p.username else DEFAULT_711_USER,
        "password": unquote(p.password) if p.password else DEFAULT_711_PASS,
    }


def _probe_clash() -> Tuple[str, int]:
    """探测可用的 Clash mixed-port。"""
    global _clash_addr
    if _clash_addr:
        return _clash_addr
    for item in CLASH_CANDIDATES:
        if not item:
            continue
        item = item.replace("http://", "").replace("https://", "").strip("/")
        if ":" not in item:
            continue
        host, port_s = item.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            continue
        try:
            s = socket.create_connection((host, port), timeout=1.5)
            s.close()
            _clash_addr = (host, port)
            return _clash_addr
        except OSError:
            continue
    raise RuntimeError(
        "未找到可用的 Clash/mihomo 本地端口（试过 7897/17897/7890）。"
        "请先启动 Clash Verge，或设置环境变量 CLASH_PROXY=127.0.0.1:端口"
    )


def _http_connect(sock: socket.socket, hostport: str, headers: Optional[dict] = None, timeout: float = 20.0) -> bytes:
    """在已连接的 socket 上发 HTTP CONNECT，返回响应头（含 \\r\\n\\r\\n）。"""
    sock.settimeout(timeout)
    lines = [f"CONNECT {hostport} HTTP/1.1", f"Host: {hostport}"]
    if headers:
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("")
    sock.sendall("\r\n".join(lines).encode("latin1"))
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    return data


def _pipe(a: socket.socket, b: socket.socket, idle: float = 120.0) -> None:
    sockets = [a, b]
    try:
        while True:
            r, _, x = select.select(sockets, [], sockets, idle)
            if x or not r:
                break
            for s in r:
                other = b if s is a else a
                try:
                    data = s.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    other.sendall(data)
                except OSError:
                    return
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


class ChainRelay:
    """本机 HTTP CONNECT 中继：client → Clash → 711 → target。"""

    def __init__(
        self,
        listen_host: str = RELAY_HOST,
        listen_port: int = RELAY_PORT,
        upstream_host: str = DEFAULT_711_HOST,
        upstream_port: int = DEFAULT_711_PORT,
        default_user: str = DEFAULT_711_USER,
        default_pass: str = DEFAULT_711_PASS,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.default_user = default_user
        self.default_pass = default_pass
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def url_base(self) -> str:
        return f"http://{self.listen_host}:{self.listen_port}"

    def start(self) -> None:
        global _active_relay_port
        if self._thread and self._thread.is_alive():
            return
        clash = _probe_clash()
        self._clash = clash
        # 依次尝试候选端口，绑定成功即自建中继（避免复用仅支持 CONNECT 的旧进程）
        last_err: Optional[OSError] = None
        sock = None
        for port in _RELAY_PORT_CANDIDATES:
            port = int(port)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((self.listen_host, port))
                sock = s
                self.listen_port = port
                _active_relay_port = port
                break
            except OSError as e:
                last_err = e
                try:
                    s.close()
                except OSError:
                    pass
        if sock is None:
            raise RuntimeError(
                f"无法绑定 711 链式中继端口（试过 {_RELAY_PORT_CANDIDATES}）: {last_err}"
            )
        sock.listen(128)
        sock.settimeout(1.0)
        self._sock = sock
        self._stop.clear()
        self._external = False
        self._thread = threading.Thread(target=self._loop, name="proxy711-relay", daemon=True)
        self._thread.start()
        atexit.register(self.stop)
        if os.environ.get("PROXY_711_DEBUG", "0") == "1":
            print(
                f"[proxy_711] relay 已启动 {self.listen_host}:{self.listen_port} "
                f"→ Clash {clash[0]}:{clash[1]} → {self.upstream_host}:{self.upstream_port}"
            )

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(client,), daemon=True)
            t.start()

    def _handle(self, client: socket.socket) -> None:
        up: Optional[socket.socket] = None
        try:
            client.settimeout(30)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = client.recv(4096)
                if not chunk:
                    return
                buf += chunk
                if len(buf) > 65536:
                    break
            head, _, rest = buf.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            first = lines[0].decode("latin1", "replace")
            parts = first.split(" ")
            if len(parts) < 2:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            method = parts[0].upper()
            is_connect = method == "CONNECT"

            user, password = self.default_user, self.default_pass
            for line in lines[1:]:
                if line.lower().startswith(b"proxy-authorization: basic "):
                    try:
                        token = line.split(None, 2)[2]
                        cred = base64.b64decode(token).decode("utf-8", "replace")
                        if ":" in cred:
                            user, password = cred.split(":", 1)
                    except Exception:
                        pass

            # 1) 经 Clash 打通到 711 网关
            up = socket.create_connection(self._clash, timeout=15)
            up.settimeout(30)
            resp1 = _http_connect(up, f"{self.upstream_host}:{self.upstream_port}")
            if b"200" not in resp1.split(b"\r\n", 1)[0]:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return

            auth = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
            auth_hdr = f"Proxy-Authorization: Basic {auth}".encode()

            if is_connect:
                # 2a) HTTPS：在 711 上 CONNECT 目标站，再双向 pipe
                target = _rewrite_connect_target(parts[1])  # host:port → 名单内改写为 ip:port
                resp2 = _http_connect(
                    up,
                    target,
                    headers={"Proxy-Authorization": f"Basic {auth}"},
                )
                if b"200" not in resp2.split(b"\r\n", 1)[0]:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if rest:
                    up.sendall(rest)
                client.settimeout(None)
                up.settimeout(None)
                _pipe(client, up)
                up = None
            else:
                # 2b) 明文 HTTP：把 absolute-form 请求转给 711（含 Proxy-Authorization）
                out_lines = [lines[0]]
                for ln in lines[1:]:
                    low = ln.lower()
                    if low.startswith(b"proxy-authorization:"):
                        out_lines.append(auth_hdr)
                    elif low.startswith(b"proxy-connection:") or low.startswith(b"connection:"):
                        continue
                    else:
                        out_lines.append(ln)
                out_lines.append(b"Connection: close")
                out_req = b"\r\n".join(out_lines) + b"\r\n\r\n" + rest
                up.sendall(out_req)
                client.settimeout(None)
                up.settimeout(None)
                _pipe(client, up)
                up = None
        except Exception:
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            try:
                client.close()
            except OSError:
                pass
            if up is not None:
                try:
                    up.close()
                except OSError:
                    pass


def start_relay(
    upstream_host: str = DEFAULT_711_HOST,
    upstream_port: int = DEFAULT_711_PORT,
    default_user: str = DEFAULT_711_USER,
    default_pass: str = DEFAULT_711_PASS,
    listen_port: int = RELAY_PORT,
) -> ChainRelay:
    """启动（或复用）全局 711 链式中继（本进程内 daemon 线程）。"""
    global _relay
    with _relay_lock:
        alive = _relay is not None and _relay._thread and _relay._thread.is_alive()
        if alive:
            # 更新默认凭据（session 用户名可能每次不同）
            _relay.default_user = default_user
            _relay.default_pass = default_pass
            _relay.upstream_host = upstream_host
            _relay.upstream_port = upstream_port
            return _relay
        r = ChainRelay(
            listen_port=listen_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            default_user=default_user,
            default_pass=default_pass,
        )
        r.start()
        _relay = r
        return r


def rewrite_to_relay(proxy_url: str) -> str:
    """把 711 URL 改写为指向本机 relay 的 URL（保留 user/pass）。

    输入: http://USER:PASS@global.rotgb.711proxy.com:10000
    输出: http://USER:PASS@127.0.0.1:<relay_port>
    """
    info = parse_proxy_url(proxy_url)
    r = start_relay(
        upstream_host=info["host"],
        upstream_port=info["port"],
        default_user=info["user"],
        default_pass=info["password"],
    )
    user = quote(info["user"], safe="")
    password = quote(info["password"], safe="")
    port = getattr(r, "listen_port", None) or _active_relay_port or RELAY_PORT
    return f"http://{user}:{password}@{RELAY_HOST}:{port}"


def ensure_proxy(proxy_url: Optional[str]) -> Optional[str]:
    """若是 711 代理则改写为链式中继 URL；否则原样返回。

    非 711、None 均透传。直连 711 在 curl_cffi 下会 Proxy CONNECT aborted。
    """
    if not proxy_url:
        return proxy_url
    if not is_711_proxy(proxy_url):
        return proxy_url
    rewritten = rewrite_to_relay(proxy_url)
    if os.environ.get("PROXY_711_DEBUG", "0") == "1":
        print(f"[proxy_711] 711 链式中继: ...@{proxy_url.split('@')[-1]} → {rewritten.split('@')[-1]}")
    return rewritten


def pick_region(exclude=None):
    """从 SUPPORTED_COUNTRIES 选一个国家（可排除已知不可用国家）。"""
    import random as _rand

    pool = [c for c in SUPPORTED_COUNTRIES if not exclude or c not in exclude]
    if not pool:
        pool = list(SUPPORTED_COUNTRIES)
    return _rand.choice(pool)


def build_711_proxy(
    region: str = "US",
    session: Optional[str] = None,
    sess_time: int = 30,
    sticky: bool = True,
) -> str:
    """构造一条 711 代理 URL，并自动改写到本地链式中继。"""
    import random
    import string

    user = DEFAULT_711_USER
    if sticky:
        sid = session or "".join(random.choices(string.ascii_lowercase + string.digits, k=11))
        user = f"{DEFAULT_711_USER}-session-{sid}-sessTime-{sess_time}-region-{region}"
    elif region:
        user = f"{DEFAULT_711_USER}-region-{region}"
    raw = f"http://{quote(user, safe='')}:{quote(DEFAULT_711_PASS, safe='')}@{DEFAULT_711_HOST}:{DEFAULT_711_PORT}"
    return ensure_proxy(raw) or raw


def smoke_test(proxy_url: Optional[str] = None) -> bool:
    """快速自检：经链式中继拿出口 IP。"""
    from curl_cffi import requests as creq

    url = ensure_proxy(
        proxy_url
        or f"http://{DEFAULT_711_USER}:{DEFAULT_711_PASS}@{DEFAULT_711_HOST}:{DEFAULT_711_PORT}"
    )
    try:
        r = creq.get(
            "https://api.ipify.org?format=json",
            proxies={"http": url, "https": url},
            timeout=25,
            impersonate="chrome131",
        )
        print(f"[proxy_711] smoke status={r.status_code} body={r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"[proxy_711] smoke FAIL: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    import sys

    ok = smoke_test(sys.argv[1] if len(sys.argv) > 1 else None)
    sys.exit(0 if ok else 1)
