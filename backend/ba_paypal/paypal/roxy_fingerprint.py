"""Capture a runtime browser fingerprint from a random RoxyBrowser profile."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from loguru import logger

from config import (
    BROWSER_PROFILE,
    ROXY_API_HOST,
    ROXY_API_KEY,
    ROXY_API_PORT,
    ROXY_HEADLESS,
    ROXY_PROJECT_ID,
    ROXY_WORKSPACE_ID,
    SCREEN,
    USER_AGENT,
    VIEWPORT,
)


class RoxyFingerprintError(RuntimeError):
    """Raised when RoxyBrowser cannot provide a runtime fingerprint."""


def _load_dotenv_value(name: str) -> str:
    """Read one value from local .env without adding a runtime dependency."""
    if os.getenv(name):
        return os.getenv(name, "").strip()
    roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
    seen: set[Path] = set()
    for root in roots:
        env_path = root / ".env"
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != name:
                    continue
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(name, value)
                return value
        except Exception:
            continue
    return ""


def _env_str(name: str, default: str = "") -> str:
    value = _load_dotenv_value(name)
    return value if value != "" else default


def _env_int(name: str, default: int | None = None) -> int | None:
    value = _load_dotenv_value(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _load_dotenv_value(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _load_dotenv_value(name).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "y"}


def _dict_value(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _event_dict(events: dict[str, Any], key: str) -> dict[str, Any]:
    value = events.get(key)
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    result: dict[str, Any] = {}
    events[key] = result
    return result


def _event_list(events: dict[str, Any], key: str) -> list[Any]:
    value = events.get(key)
    if isinstance(value, list):
        return value
    result: list[Any] = []
    events[key] = result
    return result


def _add_browser_context_cookies(context: Any, cookies: list[dict[str, Any]]) -> None:
    context.add_cookies(cast(Any, cookies))


def _browser_context_cookies(context: Any, target_urls: list[str]) -> list[dict[str, Any]]:
    return [dict(cookie) for cookie in cast(list[Any], context.cookies(target_urls))]


def _roxy_timezone_value(timezone_name: str, offset_minutes: int) -> str:
    # Roxy API uses strings such as "GMT-03:00 America/Sao_Paulo".  JavaScript
    # Date#getTimezoneOffset uses the opposite sign: UTC-3 => +180 minutes.
    signed_minutes = -int(offset_minutes)
    sign = "+" if signed_minutes >= 0 else "-"
    signed_minutes = abs(signed_minutes)
    hours, minutes = divmod(signed_minutes, 60)
    return f"GMT{sign}{hours:02d}:{minutes:02d} {timezone_name}"


def _roxy_proxy_info(proxy_url: str) -> dict[str, Any]:
    value = (proxy_url or "").strip()
    if not value:
        return {
            "moduleId": 0,
            "proxyMethod": "custom",
            "proxyCategory": "noproxy",
            "ipType": "IPV4",
        }
    parsed = urllib.parse.urlsplit(value)
    scheme = (parsed.scheme or "http").lower()
    category = {
        "http": "HTTP",
        "https": "HTTPS",
        "socks5": "SOCKS5",
        "socks5h": "SOCKS5",
    }.get(scheme, "HTTP")
    if not parsed.hostname or not parsed.port:
        return {
            "moduleId": 0,
            "proxyMethod": "custom",
            "proxyCategory": "noproxy",
            "ipType": "IPV4",
        }
    return {
        "moduleId": 0,
        "proxyMethod": "custom",
        "proxyCategory": category,
        "ipType": "IPV4",
        "host": parsed.hostname,
        "port": str(parsed.port),
        "proxyUserName": urllib.parse.unquote(parsed.username or ""),
        "proxyPassword": urllib.parse.unquote(parsed.password or ""),
        "checkChannel": "IPRust.io",
    }


def configured_roxy_api_key() -> str:
    return (
        _env_str("PAYPAL_ROXY_API_KEY")
        or _env_str("ROXY_API_KEY")
        or _env_str("ROXY_API_TOKEN")
        or _load_roxy_public_api_key()
        or ROXY_API_KEY
    ).strip()


def _roxy_public_config_paths() -> list[Path]:
    configured = _env_str("PAYPAL_ROXY_PUBLIC_CONFIG_PATH") or _env_str("ROXY_PUBLIC_CONFIG_PATH")
    paths: list[Path] = []
    if configured:
        paths.append(Path(configured).expanduser())
    paths.append(Path.home() / ".roxybrowser" / "config.json")
    return paths


def _roxy_session_config_paths() -> list[Path]:
    configured = _env_str("PAYPAL_ROXY_SESSION_CONFIG_PATH") or _env_str("ROXY_SESSION_CONFIG_PATH")
    paths: list[Path] = []
    if configured:
        paths.append(Path(configured).expanduser())
    paths.extend(
        [
            Path.home() / ".config" / "RoxyBrowser" / "config.json",
            Path.home() / ".config" / "roxybrowser" / "config.json",
            Path.home() / ".config" / "roxybrowser-dev" / "config.json",
        ]
    )
    return paths


def _load_roxy_public_config() -> dict[str, Any]:
    """Read Roxy's small public config that stores the current OpenAPI key.

    Roxy rewrites this file when the API service starts.  Using it as a retry
    source prevents stale PAYPAL_ROXY_API_KEY values from making
    /browser/workspace look empty even though the desktop app is logged in.
    """
    for path in _roxy_public_config_paths():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
    return {}


def _load_roxy_public_api_key() -> str:
    value = _load_roxy_public_config().get("apiKey")
    return str(value or "").strip()


def _decrypt_roxy_session_config(raw: str) -> dict[str, Any]:
    """Decrypt RoxyBrowser's local Electron config.

    The desktop app stores the logged-in app token in an AES-CBC encrypted JSON
    file.  We only use it as a last-resort control-plane fallback for workspace
    discovery/create/delete; normal profile operations still go through Roxy's
    documented Local API.
    """
    raw = (raw or "").strip()
    if not raw or ":" not in raw:
        return {}
    iv_hex, cipher_hex = raw.split(":", 1)
    if not re.fullmatch(r"[0-9a-fA-F]{32}", iv_hex) or not re.fullmatch(r"[0-9a-fA-F]+", cipher_hex):
        return {}
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        key = hashlib.pbkdf2_hmac(
            "sha256",
            b"roxy-browser-default-password",
            b"roxy-browser-salt",
            10_000,
            dklen=32,
        )
        decryptor = Cipher(
            algorithms.AES(key),
            modes.CBC(bytes.fromhex(iv_hex)),
            backend=default_backend(),
        ).decryptor()
        plain = decryptor.update(bytes.fromhex(cipher_hex)) + decryptor.finalize()
        if not plain:
            return {}
        pad = plain[-1]
        if pad < 1 or pad > 16:
            return {}
        decoded = json.loads(plain[:-pad].decode("utf-8"))
    except Exception as exc:
        logger.debug("Roxy session config decrypt failed: {}", exc)
        return {}
    return cast(dict[str, Any], decoded) if isinstance(decoded, dict) else {}


def _load_roxy_session_config() -> dict[str, Any]:
    for path in _roxy_session_config_paths():
        if not path.is_file():
            continue
        try:
            data = _decrypt_roxy_session_config(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data:
            return data
    return {}


def _roxy_app_gateway_from_config(config: dict[str, Any]) -> str:
    configured = (_env_str("PAYPAL_ROXY_APP_GATEWAY") or _env_str("ROXY_APP_GATEWAY")).strip()
    if configured:
        return configured.rstrip("/")
    node = config.get("lastSelectedNetworkNode")
    if isinstance(node, dict) and node.get("gate"):
        return f"https://{str(node.get('gate')).strip()}".rstrip("/")
    # This is Roxy's first/default gateway in the bundled desktop config.
    return "https://hz.gate.roxybrowser.cn"


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


@dataclass(slots=True)
class RoxyCaptureConfig:
    api_base: str
    api_key: str
    workspace_id: int | None = None
    project_id: int | None = None
    headless: bool = True
    force_open: bool = False
    close_before_open: bool = False
    timeout_seconds: float = 12.0
    close_after_capture: bool = True
    delete_after_capture: bool = True
    auto_create_workspace: bool = False
    delete_auto_workspace: bool = False
    force_temp_workspace: bool = False
    workspace_name_prefix: str = "paypal-auto"
    open_width: int = 1365
    open_height: int = 768
    screen_width: int = 1536
    screen_height: int = 864
    language: str = "pt-BR"
    display_language: str = "pt-BR"
    timezone: str = "America/Sao_Paulo"
    follow_ip: bool = False
    core_version: str = ""
    os_name: str = "Windows"
    os_version: str = "11"
    proxy_url: str = ""


def load_roxy_capture_config(proxy_url: str | None = None) -> RoxyCaptureConfig:
    port = _env_int("PAYPAL_ROXY_API_PORT", ROXY_API_PORT) or ROXY_API_PORT
    host = _env_str("PAYPAL_ROXY_API_HOST", ROXY_API_HOST)
    api_base = (
        _env_str("PAYPAL_ROXY_API_BASE")
        or _env_str("ROXY_API_BASE")
        or f"http://{host}:{port}"
    ).rstrip("/")
    language = str(BROWSER_PROFILE.get("language") or "pt-BR")
    timezone = _roxy_timezone_value(
        str(BROWSER_PROFILE.get("timezone") or "America/Sao_Paulo"),
        int(BROWSER_PROFILE.get("timezone_offset_minutes") or 180),
    )
    headless = _env_bool("PAYPAL_ROXY_HEADLESS", ROXY_HEADLESS)
    return RoxyCaptureConfig(
        api_base=api_base,
        api_key=configured_roxy_api_key(),
        workspace_id=_env_int("PAYPAL_ROXY_WORKSPACE_ID", ROXY_WORKSPACE_ID),
        project_id=_env_int("PAYPAL_ROXY_PROJECT_ID", ROXY_PROJECT_ID),
        headless=headless,
        force_open=_env_bool("PAYPAL_ROXY_FORCE_OPEN", False),
        close_before_open=_env_bool("PAYPAL_ROXY_CLOSE_BEFORE_OPEN", False),
        timeout_seconds=max(2.0, _env_float("PAYPAL_ROXY_API_TIMEOUT_SECONDS", 12.0)),
        close_after_capture=_env_bool("PAYPAL_ROXY_CLOSE_AFTER_CAPTURE", True),
        delete_after_capture=_env_bool("PAYPAL_ROXY_DELETE_AFTER_CAPTURE", True),
        auto_create_workspace=_env_bool("PAYPAL_ROXY_AUTO_CREATE_WORKSPACE", False),
        delete_auto_workspace=_env_bool("PAYPAL_ROXY_DELETE_AUTO_WORKSPACE", False),
        force_temp_workspace=_env_bool("PAYPAL_ROXY_FORCE_TEMP_WORKSPACE", False),
        workspace_name_prefix=_env_str("PAYPAL_ROXY_WORKSPACE_NAME_PREFIX", "paypal-auto"),
        open_width=_env_int("PAYPAL_ROXY_OPEN_WIDTH", int(VIEWPORT.get("width", 1365) or 1365)) or 1365,
        open_height=_env_int("PAYPAL_ROXY_OPEN_HEIGHT", int(VIEWPORT.get("height", 768) or 768)) or 768,
        screen_width=_env_int("PAYPAL_ROXY_SCREEN_WIDTH", int(SCREEN.get("width", 1536) or 1536)) or 1536,
        screen_height=_env_int("PAYPAL_ROXY_SCREEN_HEIGHT", int(SCREEN.get("height", 864) or 864)) or 864,
        language=_env_str("PAYPAL_ROXY_LANGUAGE", language),
        display_language=_env_str("PAYPAL_ROXY_DISPLAY_LANGUAGE", language),
        timezone=_env_str("PAYPAL_ROXY_TIMEZONE", timezone),
        follow_ip=_env_bool("PAYPAL_ROXY_FOLLOW_IP", False),
        core_version=_env_str("PAYPAL_ROXY_CORE_VERSION", ""),
        os_name=_env_str("PAYPAL_ROXY_OS", "Windows"),
        os_version=_env_str("PAYPAL_ROXY_OS_VERSION", "11"),
        # `proxy_url` is tri-state:
        #   None => standalone/default mode may use PAYPAL_ROXY_PROXY_URL;
        #   ""   => explicit no-proxy, used when the Web/CLI flow disables proxy;
        #   URL  => use the exact same proxy as the HTTP session.
        proxy_url=(str(proxy_url).strip() if proxy_url is not None else _env_str("PAYPAL_ROXY_PROXY_URL")),
    )


class RoxyApiClient:
    def __init__(self, config: RoxyCaptureConfig):
        if not config.api_key:
            raise RoxyFingerprintError("PAYPAL_ROXY_API_KEY 未配置")
        self.config = config
        self.auto_workspace_created = False
        self.auto_workspace_id: int | None = None
        self.auto_workspace_name = ""
        self.client = httpx.Client(
            base_url=config.api_base,
            timeout=config.timeout_seconds,
            headers={"token": config.api_key},
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception as exc:
            raise RoxyFingerprintError(f"Roxy API {path} 返回非 JSON 响应") from exc
        code = payload.get("code")
        if code not in (0, "0", None):
            msg = payload.get("msg") or payload.get("message") or payload
            raise RoxyFingerprintError(f"Roxy API {path} failed: {msg}")
        return payload

    def _set_api_key(self, api_key: str) -> None:
        api_key = (api_key or "").strip()
        if not api_key or api_key == self.config.api_key:
            return
        self.config.api_key = api_key
        self.client.headers["token"] = api_key

    def _retry_workspace_with_roxy_app_api_key(self) -> list[dict[str, Any]]:
        api_key = _load_roxy_public_api_key()
        if not api_key or api_key == self.config.api_key:
            return []
        old_prefix = self.config.api_key[:4] if self.config.api_key else ""
        self._set_api_key(api_key)
        logger.info(
            "Roxy /browser/workspace returned empty; retrying with current desktop API key prefix={} (old prefix={})",
            api_key[:4],
            old_prefix,
        )
        try:
            payload = self.request("GET", "/browser/workspace", params={"page_index": 1, "page_size": 50})
        except Exception as exc:
            logger.debug("Roxy workspace retry with desktop API key failed: {}", exc)
            return []
        data = payload.get("data") or {}
        rows = data.get("rows") or []
        return cast(list[dict[str, Any]], rows) if isinstance(rows, list) else []

    def _app_workspace_headers(self, session_config: dict[str, Any]) -> dict[str, str]:
        token = str(session_config.get("token") or "").strip()
        if not token:
            raise RoxyFingerprintError("Roxy desktop session token missing; cannot auto-create workspace")
        headers = {
            "token": token,
            "source": "app",
            "language": str(session_config.get("language") or "zh-CN"),
        }
        workspace_id = _first_int(session_config.get("workspaceId"), self.config.workspace_id)
        if workspace_id is not None:
            headers["workspaceId"] = str(workspace_id)
        app_version = str(session_config.get("appVersion") or "").strip()
        if app_version:
            headers["appVersion"] = app_version
        return headers

    def _app_request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session_config = _load_roxy_session_config()
        gateway = _roxy_app_gateway_from_config(session_config)
        headers = self._app_workspace_headers(session_config)
        try:
            with httpx.Client(base_url=gateway, timeout=self.config.timeout_seconds, headers=headers) as client:
                response = client.request(method, path, json=json_body, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise RoxyFingerprintError(f"Roxy app API {path} failed: {exc}") from exc
        code = payload.get("code")
        if code not in (0, "0", None):
            msg = payload.get("msg") or payload.get("message") or payload
            raise RoxyFingerprintError(f"Roxy app API {path} failed: {msg}")
        return payload

    def list_app_workspaces(self) -> list[dict[str, Any]]:
        payload = self._app_request("GET", "/user_get_workspace_list")
        data = payload.get("data") or {}
        rows = data.get("rows") or []
        return cast(list[dict[str, Any]], rows) if isinstance(rows, list) else []

    def create_workspace(self) -> tuple[int, str]:
        if not self.config.auto_create_workspace:
            raise RoxyFingerprintError("Roxy API 没有返回 workspace，且 PAYPAL_ROXY_AUTO_CREATE_WORKSPACE=0")
        raw_prefix = (self.config.workspace_name_prefix or "paypalauto").strip()
        prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", raw_prefix)[:10] or "paypalauto"
        workspace_name = f"{prefix}{uuid.uuid4().hex[:10]}"[:20]
        logger.info("Creating temporary Roxy workspace name={}", workspace_name)
        payload = self._app_request(
            "POST",
            "/user_add_workspace_info",
            json_body={"workspaceName": workspace_name, "workspacePicture": ""},
        )
        data = payload.get("data") or {}
        workspace_id = _first_int(data.get("workspaceId"), data.get("id"), data)
        if workspace_id is None:
            raise RoxyFingerprintError(f"Roxy app API /user_add_workspace_info 未返回 workspaceId: {payload}")
        self.auto_workspace_created = True
        self.auto_workspace_id = workspace_id
        self.auto_workspace_name = workspace_name
        logger.info("Temporary Roxy workspace created id={}", workspace_id)
        return workspace_id, workspace_name

    def delete_workspace(self, workspace_id: int, workspace_name: str = "") -> None:
        if not workspace_id:
            return
        name = (workspace_name or self.auto_workspace_name or "").strip()
        if not name:
            for row in self.list_app_workspaces():
                if _first_int(row.get("id"), row.get("workspaceId")) == int(workspace_id):
                    name = str(row.get("workspaceName") or "")
                    break
        if not name:
            raise RoxyFingerprintError(f"Roxy workspace {workspace_id} name unknown; cannot delete safely")
        logger.info("Deleting temporary Roxy workspace id={} name={}", workspace_id, name)
        self._app_request(
            "POST",
            "/user_del_workspace_info",
            json_body={"workspaceId": int(workspace_id), "workspaceName": name},
        )

    def get_workspace_project(self) -> tuple[int, int | None]:
        if self.config.workspace_id is not None:
            return self.config.workspace_id, self.config.project_id
        if self.config.force_temp_workspace and self.config.auto_create_workspace:
            try:
                workspace_id, _workspace_name = self.create_workspace()
                return workspace_id, self.config.project_id
            except RoxyFingerprintError as exc:
                logger.warning(
                    "Temporary Roxy workspace creation failed ({}); falling back to existing workspace",
                    exc,
                )
        payload = self.request("GET", "/browser/workspace", params={"page_index": 1, "page_size": 50})
        data = payload.get("data") or {}
        rows = data.get("rows") or []
        if not rows:
            rows = self._retry_workspace_with_roxy_app_api_key()
        if not rows:
            try:
                rows = self.list_app_workspaces()
            except Exception as exc:
                logger.debug("Roxy app workspace list fallback failed: {}", exc)
        if not rows:
            if self.config.auto_create_workspace:
                workspace_id, _workspace_name = self.create_workspace()
                return workspace_id, self.config.project_id
            raise RoxyFingerprintError(
                "未找到已有 Roxy workspace/team；已按配置跳过自动创建团队。"
                "请先在 Roxy 中选择/创建团队，或设置 PAYPAL_ROXY_WORKSPACE_ID。"
            )
        row = rows[0]
        workspace_id = int(row.get("id"))
        project_id = self.config.project_id
        projects = row.get("project_details") or []
        if project_id is None and projects:
            project_id = int(projects[0].get("projectId"))
        return workspace_id, project_id

    def create_profile(self, workspace_id: int, project_id: int | None) -> str:
        proxy_info = _roxy_proxy_info(self.config.proxy_url)
        payload: dict[str, Any] = {
            "workspaceId": workspace_id,
            "windowName": f"paypal-fp-{uuid.uuid4().hex[:10]}",
            "coreType": "Chrome",
            "os": self.config.os_name,
            "osVersion": self.config.os_version,
            "cookie": [],
            "searchEngine": "Google",
            "defaultOpenUrl": ["about:blank"],
            "windowRemark": "paypal runtime fingerprint capture",
            "proxyInfo": proxy_info,
            "fingerInfo": {
                "isLanguageBaseIp": self.config.follow_ip,
                "language": self.config.language,
                "isDisplayLanguageBaseIp": self.config.follow_ip,
                "displayLanguage": self.config.display_language,
                "isTimeZone": self.config.follow_ip,
                "timeZone": self.config.timezone,
                "position": 0,
                "isPositionBaseIp": self.config.follow_ip,
                "forbidAudio": False,
                "forbidImage": False,
                "forbidMedia": False,
                "openWidth": str(self.config.open_width),
                "openHeight": str(self.config.open_height),
                "openBookmarks": False,
                "positionSwitch": False,
                "isDisplayName": False,
                "syncBookmark": False,
                "syncHistory": False,
                "syncTab": False,
                "syncCookie": False,
                "syncExtensions": False,
                "syncPassword": False,
                "syncIndexedDb": False,
                "syncLocalStorage": False,
                "clearCacheFile": True,
                "clearCookie": True,
                "clearLocalStorage": True,
                "randomFingerprint": True,
                "forbidSavePassword": True,
                "stopOpenNet": False,
                "stopOpenIP": False,
                "stopOpenPosition": False,
                "openWorkbench": 0,
                "resolutionType": True,
                "resolutionX": str(self.config.screen_width),
                "resolutionY": str(self.config.screen_height),
                "fontType": True,
                "webRTC": 2,
                "webGL": True,
                "webGLInfo": True,
                "webGLManufacturer": "",
                "webGLRender": "",
                "webGpu": "webgl",
                "canvas": True,
                "audioContext": True,
                "speechVoices": True,
                "doNotTrack": False,
                "clientRects": True,
                "deviceInfo": True,
                "deviceNameSwitch": True,
                "macInfo": True,
                "hardwareConcurrent": "",
                "deviceMemory": "",
                "disableSsl": False,
                "disableSslList": [],
                "portScanProtect": True,
                "portScanList": "",
                "useGpu": True,
                "sandboxPermission": False,
                "startupParam": "",
            },
        }
        if self.config.core_version:
            payload["coreVersion"] = self.config.core_version
        if project_id is not None:
            payload["projectId"] = project_id
        logger.debug(
            "Creating Roxy profile workspace_id={} project_id={} proxy={} category={}",
            workspace_id,
            project_id,
            _redact_proxy_url(self.config.proxy_url) or "noproxy",
            proxy_info.get("proxyCategory"),
        )
        response = self.request("POST", "/browser/create", json=payload)
        dir_id = ((response.get("data") or {}).get("dirId") or "").strip()
        if not dir_id:
            raise RoxyFingerprintError("Roxy /browser/create 未返回 dirId")
        return dir_id

    def randomize_profile(self, workspace_id: int, dir_id: str) -> None:
        self.request("POST", "/browser/random_env", json={"workspaceId": workspace_id, "dirId": dir_id})

    def open_profile(self, workspace_id: int, dir_id: str) -> dict[str, Any]:
        # Roxy 的 Local API 用 `headless` 字段控制无头模式。当前默认直接打开：
        # 不先 close，不强制 forceOpen；如需处理旧可见窗口复用，可通过环境变量
        # PAYPAL_ROXY_CLOSE_BEFORE_OPEN / PAYPAL_ROXY_FORCE_OPEN 显式开启。
        args = ["--remote-allow-origins=*", "--disable-audio-output"]
        if self.config.headless and self.config.close_before_open:
            try:
                self.close_profile(dir_id)
            except Exception as exc:
                logger.debug("Roxy pre-open close skipped/failed for {}: {}", dir_id, exc)
        payload = {
            "workspaceId": workspace_id,
            "dirId": dir_id,
            "args": args,
            "forceOpen": bool(self.config.force_open),
            "headless": True if self.config.headless else False,
        }
        logger.debug(
            "Opening Roxy browser dir_id={} headless={} forceOpen={} args={}",
            dir_id,
            payload["headless"],
            payload["forceOpen"],
            args,
        )
        response = self.request("POST", "/browser/open", json=payload)
        data = response.get("data") or {}
        if not data.get("ws") and not data.get("http"):
            raise RoxyFingerprintError("Roxy /browser/open 未返回 CDP ws/http")
        return data

    def close_profile(self, dir_id: str) -> None:
        self.request("POST", "/browser/close", json={"dirId": dir_id})

    def delete_profile(self, workspace_id: int, dir_id: str) -> None:
        self.request(
            "POST",
            "/browser/delete",
            json={"workspaceId": workspace_id, "dirIds": [dir_id], "isSoftDelete": False},
        )

    def list_profiles(self, workspace_id: int) -> list[dict[str, Any]]:
        resp = self.request(
            "GET",
            "/browser/list",
            params={"workspaceId": workspace_id, "page": 1, "pageSize": 200},
        )
        return (resp.get("data") or {}).get("rows") or []

    @staticmethod
    def _is_paypal_auto_profile(profile: dict[str, Any]) -> bool:
        name = str(profile.get("windowName") or "")
        remark = str(profile.get("windowRemark") or "")
        return name.startswith("paypal-fp-") or remark == "paypal runtime fingerprint capture"

    def cleanup_paypal_auto_profiles(self, workspace_id: int) -> int:
        deleted = 0
        for profile in self.list_profiles(workspace_id):
            if not self._is_paypal_auto_profile(profile):
                continue
            dir_id = str(profile.get("dirId") or profile.get("dir_id") or "").strip()
            if not dir_id:
                continue
            try:
                self.close_profile(dir_id)
            except Exception as exc:
                logger.debug("Roxy auto profile close skipped/failed for {}: {}", dir_id, exc)
            try:
                self.delete_profile(workspace_id, dir_id)
                deleted += 1
            except Exception as exc:
                logger.debug("Roxy auto profile delete failed for {}: {}", dir_id, exc)
        if deleted:
            logger.info("Deleted {} stale paypal-fp Roxy profiles in workspace {}", deleted, workspace_id)
        return deleted

    def create_or_reuse_profile(
        self, workspace_id: int, project_id: int | None
    ) -> str:
        try:
            return self.create_profile(workspace_id, project_id)
        except RoxyFingerprintError as exc:
            msg = str(exc)
            quota_markers = ("超出", "额度不足", "limit", "quota", "insufficient", "not enough")
            if not any(marker in msg.lower() for marker in quota_markers):
                raise
            deleted = self.cleanup_paypal_auto_profiles(workspace_id)
            if deleted:
                try:
                    return self.create_profile(workspace_id, project_id)
                except RoxyFingerprintError as retry_exc:
                    logger.warning(
                        "Roxy profile creation still failed after deleting stale auto profiles: {}",
                        retry_exc,
                    )
            logger.warning(
                "Roxy profile creation quota hit ({}), reusing existing profile",
                exc,
            )
            for p in self.list_profiles(workspace_id):
                dir_id = (p.get("dirId") or p.get("dir_id") or "").strip()
                if dir_id:
                    logger.info("Reusing existing profile: {}", dir_id)
                    return dir_id
            raise RoxyFingerprintError(
                "Roxy 无可复用的已有窗口，请等待每日限制重置后再试"
            )


def _sha256_hex(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        value = ""
    if not isinstance(value, (bytes, bytearray)):
        value = str(value).encode("utf-8", "ignore")
    return hashlib.sha256(value).hexdigest()


def _canonical_proxy_url(proxy_url: object = None) -> str:
    return str(proxy_url or "").strip()


def _redact_proxy_url(proxy_url: object = None) -> str:
    value = _canonical_proxy_url(proxy_url)
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except Exception:
        return value
    if not parsed.hostname:
        return value
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        netloc = f"{urllib.parse.unquote(parsed.username)}:***@{netloc}"
    return urllib.parse.urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def _proxy_url_hash(proxy_url: object = None) -> str:
    return _sha256_hex(_canonical_proxy_url(proxy_url))


def roxy_browser_matches_proxy(roxy_browser: dict[str, Any], proxy_url: object = None) -> bool:
    """Return True when an existing Roxy browser was created for this proxy.

    This prevents reusing a browser profile opened with a different outbound IP
    from the HTTP session/proxy used by the protocol flow.
    """
    expected_hash = _proxy_url_hash(proxy_url)
    if not isinstance(roxy_browser, dict):
        return expected_hash == _proxy_url_hash("")
    actual_hash = str(roxy_browser.get("proxy_url_hash") or "")
    if actual_hash:
        return actual_hash == expected_hash
    # Backward compatibility for older in-memory state that might have stored a
    # raw value. New code stores only a hash/redacted label.
    if "proxy_url" in roxy_browser:
        return _proxy_url_hash(roxy_browser.get("proxy_url")) == expected_hash
    # Legacy browser state without proxy metadata cannot prove IP consistency.
    return False


def _sha256_b64(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if value is None:
        value = ""
    if not isinstance(value, (bytes, bytearray)):
        value = str(value).encode("utf-8", "ignore")
    return base64.b64encode(hashlib.sha256(value).digest()).decode("ascii")


def _parse_chrome_major(user_agent: str, fallback: int = 150) -> int:
    match = re.search(r"(?:Chrome|Chromium|Edg)/(\d+)", user_agent or "")
    if not match:
        return fallback
    try:
        return int(match.group(1))
    except ValueError:
        return fallback


def _full_version_from_ua_data(ua_data: dict[str, Any] | None, user_agent: str, major: int) -> str:
    if isinstance(ua_data, dict):
        for item in ua_data.get("fullVersionList") or []:
            brand = str(item.get("brand") or "")
            version = str(item.get("version") or "")
            if version and ("Chrome" in brand or "Chromium" in brand):
                return version
        version = str(ua_data.get("uaFullVersion") or "")
        if version:
            return version
    match = re.search(r"(?:Chrome|Chromium)/([0-9.]+)", user_agent or "")
    if match:
        return match.group(1)
    return f"{major}.0.0.0"


def _sec_ch_platform(ua_data: dict[str, Any] | None, platform: str) -> str:
    value = ""
    if isinstance(ua_data, dict):
        value = str(ua_data.get("platform") or "")
    if not value:
        lower = (platform or "").lower()
        if "win" in lower:
            value = "Windows"
        elif "mac" in lower:
            value = "macOS"
        elif "linux" in lower:
            value = "Linux"
        elif "android" in lower:
            value = "Android"
        else:
            value = str(BROWSER_PROFILE.get("sec_ch_platform", '"Windows"')).strip('"')
    return json.dumps(value)


def _sec_ch_arch(ua_data: dict[str, Any] | None) -> str:
    if isinstance(ua_data, dict):
        arch = str(ua_data.get("architecture") or "").lower()
        bitness = str(ua_data.get("bitness") or "")
        if arch in {"x86", "x86_64", "amd64"}:
            return '"x86"'
        if arch in {"arm", "arm64", "aarch64"}:
            return '"arm"'
        if bitness == "64":
            return '"x86"'
    return str(BROWSER_PROFILE.get("sec_ch_arch") or '"x86"')


def _locale_from_language(language: str) -> str:
    value = (language or str(BROWSER_PROFILE.get("language") or "pt-BR")).strip()
    return value.replace("-", "_")


def _country_from_locale(locale: str) -> str:
    if "_" in locale:
        return locale.rsplit("_", 1)[-1].upper()
    return str(BROWSER_PROFILE.get("country") or "BR")


def _connect_over_cdp(cdp_info: dict[str, Any]) -> str:
    ws = str(cdp_info.get("ws") or "")
    if ws:
        return ws
    http = str(cdp_info.get("http") or "")
    if not http:
        raise RoxyFingerprintError("Roxy CDP 信息为空")
    if not http.startswith(("http://", "https://")):
        http = f"http://{http}"
    return http


_PHASE1_EXPECTED_RISK_SIGNALS = (
    "fraudnet_p1",
    "fraudnet_p2",
    "fraudnet_w",
    "identity_di_log",
    "tealeaf",
    "datadog_rum",
)
_PHASE1_REQUIRED_ROXY_RELOAD_SIGNALS = ("identity_di_log", "datadog_rum")
_PHASE1_DATADOG_SIGNAL = "datadog_rum"
_PHASE1_RELOADABLE_MISSING_SIGNALS = ("identity_di_log",)


def _phase1_signal_count(counts: dict[str, Any], name: str) -> int:
    try:
        return int(counts.get(name) or 0)
    except Exception:
        return 0


def _phase1_missing_signals(
    counts: dict[str, Any],
    families: tuple[str, ...] = _PHASE1_EXPECTED_RISK_SIGNALS,
) -> list[str]:
    return [name for name in families if _phase1_signal_count(counts, name) <= 0]


def _phase1_required_missing_signals(counts: dict[str, Any]) -> list[str]:
    return _phase1_missing_signals(counts, _PHASE1_REQUIRED_ROXY_RELOAD_SIGNALS)


def _phase1_reloadable_missing_signals(missing: list[str]) -> list[str]:
    return [name for name in missing if name in _PHASE1_RELOADABLE_MISSING_SIGNALS]


def _phase1_roxy_reload_attempt_limit() -> int:
    value = _env_int("PAYPAL_RISK_ROXY_RELOAD_ATTEMPTS", None)
    if value is None:
        value = _env_int("PAYPAL_RISK_ROXY_REQUIRED_RELOADS", 6)
    if value is None:
        value = 6
    return max(0, min(int(value), 30))


def _roxy_datadog_view_name_for_page(page: Any) -> str:
    try:
        value = page.evaluate(
            """() => {
                const atomic = document.querySelector("[data-atomic-wait-viewname]");
                const atomicName = atomic && atomic.getAttribute("data-atomic-wait-viewname");
                if (atomicName) return atomicName;
                const title = (document.title || "").trim();
                if (title) return title;
                if (location.pathname.includes("/signup")) return "signup";
                if (location.pathname.includes("/pay")) return "Email UL";
                return location.pathname || "paypal";
            }"""
        )
        return str(value or "paypal")
    except Exception:
        return "paypal"


def _probe_roxy_datadog_runtime(page: Any) -> dict[str, Any]:
    try:
        value = page.evaluate(
            """() => {
                const dd = window.DD_RUM || window.datadogRum || null;
                const keys = dd ? Object.keys(dd).slice(0, 80) : [];
                let internal = null;
                try {
                    internal = dd && typeof dd.getInternalContext === "function"
                        ? dd.getInternalContext()
                        : null;
                } catch (error) {}
                let initConfiguration = null;
                try {
                    initConfiguration = dd && typeof dd.getInitConfiguration === "function"
                        ? dd.getInitConfiguration()
                        : (dd && dd.initConfiguration ? dd.initConfiguration : null);
                } catch (error) {}
                const safeInitConfiguration = initConfiguration ? {
                    applicationId: initConfiguration.applicationId || initConfiguration.application_id || "",
                    clientTokenPresent: !!(initConfiguration.clientToken || initConfiguration.client_token),
                    service: initConfiguration.service || "",
                    site: initConfiguration.site || "",
                    version: initConfiguration.version || "",
                    trackViewsManually: !!initConfiguration.trackViewsManually,
                    trackResources: !!initConfiguration.trackResources,
                    trackUserInteractions: !!initConfiguration.trackUserInteractions,
                    sessionSampleRate: initConfiguration.sessionSampleRate,
                    sessionReplaySampleRate: initConfiguration.sessionReplaySampleRate,
                    sessionPersistence: initConfiguration.sessionPersistence || "",
                    trackingConsent: initConfiguration.trackingConsent || "",
                    allowUntrustedEvents: !!initConfiguration.allowUntrustedEvents,
                } : null;
                const cookieNames = document.cookie
                    .split(";")
                    .map((item) => item.trim().split("=")[0])
                    .filter((name) => name && name.startsWith("_dd_s"));
                const datadogScripts = Array.from(document.scripts)
                    .map((script) => script.src || "")
                    .filter((src) => /datadog|browser-agent|api\\/v2\\/rum/i.test(src))
                    .slice(0, 10);
                return {
                    readyState: document.readyState,
                    visibilityState: document.visibilityState,
                    hasDDRumGlobal: !!dd,
                    present: !!dd,
                    keys,
                    version: dd && dd.version || "",
                    has_add_action: !!(dd && typeof dd.addAction === "function"),
                    has_start_view: !!(dd && (typeof dd.startView === "function" || typeof dd.setViewName === "function")),
                    has_start_resource: !!(dd && typeof dd.startResource === "function" && typeof dd.stopResource === "function"),
                    hasInitConfiguration: !!(dd && dd.initConfiguration),
                    initConfiguration: safeInitConfiguration,
                    hasInternalContext: !!internal,
                    sessionIdPresent: !!(internal && internal.session_id),
                    viewIdPresent: !!(internal && internal.view && internal.view.id),
                    viewName: internal && internal.view ? internal.view.name || "" : "",
                    cookieNames,
                    datadogScripts,
                    url: location.href,
                };
            }"""
        )
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        return {"error": str(exc)}


def _trigger_roxy_datadog_runtime_flush(page: Any, *, view_name: str = "") -> dict[str, Any]:
    """Ask the page's already-loaded Datadog SDK to open/flush a view.

    PayPal initializes Datadog with ``trackViewsManually: true``.  When the
    React DataDogView effect is delayed or missed, the SDK can be loaded but no
    intake request leaves the browser.  This keeps the fix inside the browser
    runtime: use the page-owned ``window.DD_RUM`` public API instead of
    constructing a Python-side RUM payload.
    """
    try:
        return page.evaluate(
            """async (viewName) => {
                const dd = window.DD_RUM || window.datadogRum || null;
                if (!dd) return { ok: false, reason: "DD_RUM_global_missing" };
                const result = { ok: true, actions: [], keys: Object.keys(dd).slice(0, 80) };
                const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                const markDatadogTrusted = (event) => {
                    try {
                        Object.defineProperty(event, "__ddIsTrusted", {
                            value: true,
                            configurable: true,
                        });
                    } catch (error) {
                        try { event.__ddIsTrusted = true; } catch (ignored) {}
                    }
                    return event;
                };
                const dispatchDatadogTrusted = (target, type, factory, options) => {
                    const Ctor = factory || Event;
                    let event;
                    try {
                        event = new Ctor(type, options || {});
                    } catch (error) {
                        event = new Event(type, options || {});
                    }
                    markDatadogTrusted(event);
                    target.dispatchEvent(event);
                    result.actions.push(`dispatch:${type}`);
                };
                try {
                    const before = typeof dd.getInternalContext === "function"
                        ? dd.getInternalContext()
                        : null;
                    result.beforeInternalContext = !!before;
                    result.beforeViewId = !!(before && before.view && before.view.id);
                    result.beforeViewName = before && before.view ? before.view.name || "" : "";
                } catch (error) {
                    result.beforeError = String(error && error.message || error);
                }
                const resolvedViewName =
                    viewName ||
                    document.querySelector("[data-atomic-wait-viewname]")?.getAttribute("data-atomic-wait-viewname") ||
                    document.title ||
                    (location.pathname.includes("/pay") ? "Email UL" : location.pathname || "paypal");
                try {
                    if (typeof dd.onReady === "function") {
                        result.onReady = await new Promise((resolve) => {
                            let resolved = false;
                            const done = (value) => {
                                if (resolved) return;
                                resolved = true;
                                resolve(value);
                            };
                            try {
                                dd.onReady(() => done(true));
                                setTimeout(() => done(false), 1200);
                            } catch (error) {
                                result.onReadyError = String(error && error.message || error);
                                done(false);
                            }
                        });
                    }
                } catch (error) {
                    result.onReadyError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.setTrackingConsent === "function") {
                        dd.setTrackingConsent("granted");
                        result.actions.push("setTrackingConsent");
                    }
                } catch (error) {
                    result.setTrackingConsentError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.startView === "function") {
                        dd.startView({
                            name: resolvedViewName,
                            context: {
                                roxy_phase1: true,
                                pathname: location.pathname,
                            },
                        });
                        result.actions.push("startView");
                    } else if (typeof dd.setViewName === "function") {
                        dd.setViewName(resolvedViewName);
                        result.actions.push("setViewName");
                    }
                } catch (error) {
                    result.startViewError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.setViewContextProperty === "function") {
                        dd.setViewContextProperty("roxy_phase1", true);
                        result.actions.push("setViewContextProperty");
                    }
                } catch (error) {
                    result.setViewContextPropertyError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.addTiming === "function") {
                        dd.addTiming("roxy_phase1_runtime_ready");
                        result.actions.push("addTiming");
                    }
                } catch (error) {
                    result.addTimingError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.addAction === "function") {
                        dd.addAction("roxy_phase1_runtime_ready", {
                            pathname: location.pathname,
                            readyState: document.readyState,
                            viewName: resolvedViewName,
                        });
                        result.actions.push("addAction");
                    }
                } catch (error) {
                    result.addActionError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.startAction === "function" && typeof dd.stopAction === "function") {
                        const actionKey = `roxy_phase1_action_${Date.now()}_${Math.random().toString(16).slice(2)}`;
                        dd.startAction("roxy_phase1_runtime_ready", {
                            type: "custom",
                            actionKey,
                            context: { pathname: location.pathname, viewName: resolvedViewName },
                        });
                        await wait(40);
                        dd.stopAction("roxy_phase1_runtime_ready", {
                            type: "custom",
                            actionKey,
                            context: { pathname: location.pathname, viewName: resolvedViewName },
                        });
                        result.actions.push("startStopAction");
                    }
                } catch (error) {
                    result.startStopActionError = String(error && error.message || error);
                }
                try {
                    if (typeof dd.startResource === "function" && typeof dd.stopResource === "function") {
                        const resourceKey = `roxy_phase1_resource_${Date.now()}_${Math.random().toString(16).slice(2)}`;
                        const resourceUrl = `${location.origin}/favicon.ico`;
                        dd.startResource(resourceUrl, {
                            method: "GET",
                            type: "image",
                            resourceKey,
                            context: { roxy_phase1: true, source: "runtime_flush" },
                        });
                        await wait(40);
                        dd.stopResource(resourceUrl, {
                            statusCode: 200,
                            size: 0,
                            type: "image",
                            resourceKey,
                            context: { roxy_phase1: true, source: "runtime_flush" },
                        });
                        result.actions.push("startStopResource");
                    }
                } catch (error) {
                    result.startStopResourceError = String(error && error.message || error);
                }
                await wait(120);
                try {
                    dispatchDatadogTrusted(window, "focus", Event);
                    dispatchDatadogTrusted(document, "visibilitychange", Event);
                    if (typeof PageTransitionEvent === "function") {
                        dispatchDatadogTrusted(window, "pagehide", PageTransitionEvent, { persisted: false });
                    } else {
                        dispatchDatadogTrusted(window, "pagehide", Event);
                    }
                    dispatchDatadogTrusted(window, "freeze", Event);
                    if (typeof BeforeUnloadEvent === "function") {
                        dispatchDatadogTrusted(window, "beforeunload", BeforeUnloadEvent, { cancelable: true });
                    } else {
                        dispatchDatadogTrusted(window, "beforeunload", Event, { cancelable: true });
                    }
                    result.actions.push("trustedExitEvents");
                } catch (error) {
                    result.eventError = String(error && error.message || error);
                }
                await wait(500);
                try {
                    const after = typeof dd.getInternalContext === "function"
                        ? dd.getInternalContext()
                        : null;
                    result.afterInternalContext = !!after;
                    result.afterViewId = !!(after && after.view && after.view.id);
                    result.afterViewName = after && after.view ? after.view.name || "" : "";
                } catch (error) {
                    result.afterError = String(error && error.message || error);
                }
                return result;
            }""",
            view_name,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _phase1_datadog_runtime_ready(probe: dict[str, Any]) -> bool:
    """Return true when the full browser Datadog RUM SDK is initialized enough.

    In some Roxy profiles PayPal's RUM SDK is loaded and initialized, but the
    SDK keeps the batch in memory until a real page lifecycle exit, so CDP may
    not observe ``/api/v2/rum`` during the Phase-1 wait window.  Do not treat a
    fully loaded SDK as missing merely because the intake batch did not flush
    before we continue.
    """
    if not isinstance(probe, dict) or probe.get("error"):
        return False
    if not (probe.get("present") or probe.get("hasDDRumGlobal")):
        return False
    keys = {str(key) for key in (probe.get("keys") or [])}
    has_full_public_api = bool(
        probe.get("has_add_action")
        or "addAction" in keys
        or "startAction" in keys
        or "startResource" in keys
    )
    has_context_api = bool(
        "getInitConfiguration" in keys
        or "getInternalContext" in keys
        or probe.get("initConfiguration")
        or probe.get("hasInternalContext")
    )
    # A bootstrap stub only has q/onReady/init; require the operational RUM API
    # that PayPal's modxo bundle exposes after the SDK has actually loaded.
    return bool(has_full_public_api and has_context_api)


def _phase1_mark_datadog_runtime_observed(
    events: dict[str, Any],
    probe: dict[str, Any],
    *,
    reason: str,
) -> bool:
    """Fulfill the Datadog Phase-1 requirement from a loaded browser SDK."""
    if not _phase1_datadog_runtime_ready(probe):
        return False
    counts = _event_dict(events, "counts")
    if _phase1_signal_count(counts, _PHASE1_DATADOG_SIGNAL) <= 0:
        counts[_PHASE1_DATADOG_SIGNAL] = 1
        events["counts"] = counts
    events["datadog_runtime_fulfilled"] = True
    events["datadog_runtime_fulfilled_reason"] = reason
    runtime_signals = _event_list(events, "runtime_signals")
    runtime_signals.append(
        {
            "family": _PHASE1_DATADOG_SIGNAL,
            "source": "DD_RUM_runtime",
            "reason": reason,
            "keys": list(probe.get("keys") or [])[:30],
            "viewName": probe.get("viewName") or "",
            "sessionIdPresent": bool(probe.get("sessionIdPresent")),
            "viewIdPresent": bool(probe.get("viewIdPresent")),
        }
    )
    return True


def _env_int_between(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _env_int(name, default)
    if value is None:
        value = default
    return max(minimum, min(int(value), maximum))


def _new_interaction_rng() -> random.Random:
    seed = _env_str("PAYPAL_ROXY_INTERACTION_SEED", "").strip()
    if not seed:
        return random.SystemRandom()
    try:
        seed_value: int | str = int(seed)
    except ValueError:
        seed_value = seed
    return random.Random(seed_value)


def _roxy_interaction_settings() -> dict[str, Any]:
    """Return tunable browser-interaction jitter settings.

    The defaults intentionally vary timing, pointer trajectory and scroll
    cadence while avoiding form clicks or input mutations.  Environment
    overrides are kept here so the browser runtime can be tuned without
    touching the protocol packet code.
    """
    profile = _env_str("PAYPAL_ROXY_INTERACTION_PROFILE", "normal").strip().lower()
    if _env_bool("PAYPAL_ROXY_RANDOM_INTERACTIONS", True) is False:
        profile = "off"
    presets: dict[str, dict[str, Any]] = {
        "off": {
            "enabled": False,
            "moves_min": 0,
            "moves_max": 0,
            "wheels_min": 0,
            "wheels_max": 0,
            "think_min_ms": 0,
            "think_max_ms": 0,
            "pause_min_ms": 0,
            "pause_max_ms": 0,
        },
        "subtle": {
            "enabled": True,
            "moves_min": 4,
            "moves_max": 8,
            "wheels_min": 1,
            "wheels_max": 2,
            "think_min_ms": 250,
            "think_max_ms": 900,
            "pause_min_ms": 60,
            "pause_max_ms": 360,
        },
        "normal": {
            "enabled": True,
            "moves_min": 7,
            "moves_max": 14,
            "wheels_min": 2,
            "wheels_max": 4,
            "think_min_ms": 350,
            "think_max_ms": 1400,
            "pause_min_ms": 80,
            "pause_max_ms": 620,
        },
        "active": {
            "enabled": True,
            "moves_min": 11,
            "moves_max": 22,
            "wheels_min": 3,
            "wheels_max": 6,
            "think_min_ms": 500,
            "think_max_ms": 2200,
            "pause_min_ms": 90,
            "pause_max_ms": 850,
        },
    }
    settings = dict(presets.get(profile, presets["normal"]))
    settings["profile"] = profile if profile in presets else "normal"
    if not settings.get("enabled"):
        return settings

    settings["moves_min"] = _env_int_between("PAYPAL_ROXY_INTERACTION_MOVES_MIN", int(settings["moves_min"]), 1, 80)
    settings["moves_max"] = _env_int_between("PAYPAL_ROXY_INTERACTION_MOVES_MAX", int(settings["moves_max"]), 1, 100)
    if int(settings["moves_max"]) < int(settings["moves_min"]):
        settings["moves_max"] = settings["moves_min"]
    settings["wheels_min"] = _env_int_between("PAYPAL_ROXY_INTERACTION_WHEELS_MIN", int(settings["wheels_min"]), 0, 20)
    settings["wheels_max"] = _env_int_between("PAYPAL_ROXY_INTERACTION_WHEELS_MAX", int(settings["wheels_max"]), 0, 30)
    if int(settings["wheels_max"]) < int(settings["wheels_min"]):
        settings["wheels_max"] = settings["wheels_min"]
    settings["think_min_ms"] = _env_int_between("PAYPAL_ROXY_INTERACTION_THINK_MIN_MS", int(settings["think_min_ms"]), 0, 10000)
    settings["think_max_ms"] = _env_int_between("PAYPAL_ROXY_INTERACTION_THINK_MAX_MS", int(settings["think_max_ms"]), 0, 15000)
    if int(settings["think_max_ms"]) < int(settings["think_min_ms"]):
        settings["think_max_ms"] = settings["think_min_ms"]
    settings["pause_min_ms"] = _env_int_between("PAYPAL_ROXY_INTERACTION_PAUSE_MIN_MS", int(settings["pause_min_ms"]), 0, 5000)
    settings["pause_max_ms"] = _env_int_between("PAYPAL_ROXY_INTERACTION_PAUSE_MAX_MS", int(settings["pause_max_ms"]), 0, 8000)
    if int(settings["pause_max_ms"]) < int(settings["pause_min_ms"]):
        settings["pause_max_ms"] = settings["pause_min_ms"]
    return settings


def _random_int(rng: Any, low: int, high: int) -> int:
    if high <= low:
        return int(low)
    return int(rng.randint(int(low), int(high)))


def _random_float(rng: Any, low: float, high: float) -> float:
    if high <= low:
        return float(low)
    return float(rng.uniform(float(low), float(high)))


def _clamp_int(value: float | int, minimum: int, maximum: int) -> int:
    if maximum < minimum:
        return int(minimum)
    return int(max(minimum, min(maximum, round(float(value)))))


def _build_roxy_interaction_plan(
    width: int,
    height: int,
    *,
    rng: Any | None = None,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, int | str]]:
    """Build a randomized, non-clicking browser interaction plan.

    The plan is pure data so it can be unit-tested and safely logged as counts.
    It uses pointer moves, small pauses and wheel deltas only; no element click
    or keyboard input is generated.
    """
    settings = settings or _roxy_interaction_settings()
    if not settings.get("enabled", True):
        return []
    if rng is None:
        rng = _new_interaction_rng()
    width = max(320, int(width or 0))
    height = max(240, int(height or 0))
    margin_x = max(18, min(90, width // 12))
    margin_y = max(18, min(80, height // 12))
    min_x, max_x = margin_x, max(margin_x, width - margin_x)
    min_y, max_y = margin_y, max(margin_y, height - margin_y)

    pause_min = int(settings.get("pause_min_ms") or 0)
    pause_max = int(settings.get("pause_max_ms") or pause_min)
    plan: list[dict[str, int | str]] = [
        {
            "type": "pause",
            "ms": _random_int(rng, int(settings.get("think_min_ms") or 0), int(settings.get("think_max_ms") or 0)),
        }
    ]

    x = _random_int(rng, min_x, max_x)
    y = _random_int(rng, min_y, max_y)
    plan.append({"type": "move", "x": x, "y": y, "steps": _random_int(rng, 5, 16), "ms": _random_int(rng, pause_min, pause_max)})

    move_budget = _random_int(rng, int(settings.get("moves_min") or 1), int(settings.get("moves_max") or 1))
    wheel_budget = _random_int(rng, int(settings.get("wheels_min") or 0), int(settings.get("wheels_max") or 0))
    scheduled_wheels = 0

    for index in range(move_budget):
        remaining = max(1, move_budget - index)
        must_scroll = scheduled_wheels < wheel_budget and wheel_budget - scheduled_wheels >= remaining
        do_scroll = must_scroll or (scheduled_wheels < wheel_budget and rng.random() < 0.24)
        if do_scroll:
            dy_abs = _random_int(rng, 70, 520)
            dy = dy_abs if rng.random() < 0.68 else -dy_abs
            dx = _random_int(rng, -18, 18) if rng.random() < 0.25 else 0
            plan.append({"type": "wheel", "dx": dx, "dy": dy, "ms": _random_int(rng, max(80, pause_min), max(120, pause_max))})
            scheduled_wheels += 1
            continue

        if rng.random() < 0.68:
            target_x = _clamp_int(x + _random_float(rng, -0.26, 0.26) * width, min_x, max_x)
            target_y = _clamp_int(y + _random_float(rng, -0.22, 0.22) * height, min_y, max_y)
        else:
            target_x = _random_int(rng, min_x, max_x)
            target_y = _random_int(rng, min_y, max_y)

        # Split many moves with an offset midpoint. This avoids a repeated
        # straight-line cadence while still letting Playwright interpolate each
        # short segment naturally.
        if rng.random() < 0.58:
            mid_x = _clamp_int((x + target_x) / 2 + _random_float(rng, -0.08, 0.08) * width, min_x, max_x)
            mid_y = _clamp_int((y + target_y) / 2 + _random_float(rng, -0.07, 0.07) * height, min_y, max_y)
            plan.append({"type": "move", "x": mid_x, "y": mid_y, "steps": _random_int(rng, 3, 10), "ms": _random_int(rng, 25, max(35, pause_min))})
        plan.append({"type": "move", "x": target_x, "y": target_y, "steps": _random_int(rng, 6, 28), "ms": _random_int(rng, pause_min, pause_max)})
        x, y = target_x, target_y

        if rng.random() < 0.18:
            wiggle_x = _clamp_int(x + _random_int(rng, -12, 12), min_x, max_x)
            wiggle_y = _clamp_int(y + _random_int(rng, -10, 10), min_y, max_y)
            plan.append({"type": "move", "x": wiggle_x, "y": wiggle_y, "steps": _random_int(rng, 2, 6), "ms": _random_int(rng, 35, max(60, pause_min))})

    while scheduled_wheels < wheel_budget:
        dy_abs = _random_int(rng, 70, 440)
        plan.append({
            "type": "wheel",
            "dx": 0,
            "dy": dy_abs if rng.random() < 0.62 else -dy_abs,
            "ms": _random_int(rng, max(80, pause_min), max(120, pause_max)),
        })
        scheduled_wheels += 1
    return plan


def _execute_roxy_interaction_plan(page: Any, plan: list[dict[str, int | str]]) -> dict[str, int]:
    summary = {"moves": 0, "wheels": 0, "pauses": 0, "total_pause_ms": 0}
    for action in plan:
        action_type = str(action.get("type") or "")
        if action_type == "move":
            page.mouse.move(
                int(action.get("x") or 0),
                int(action.get("y") or 0),
                steps=max(1, int(action.get("steps") or 1)),
            )
            summary["moves"] += 1
        elif action_type == "wheel":
            page.mouse.wheel(int(action.get("dx") or 0), int(action.get("dy") or 0))
            summary["wheels"] += 1
        elif action_type == "pause":
            summary["pauses"] += 1
        pause_ms = max(0, int(action.get("ms") or 0))
        if pause_ms:
            page.wait_for_timeout(pause_ms)
            summary["total_pause_ms"] += pause_ms
    return summary


def _evaluate_cdp_fingerprint(
    cdp_info: dict[str, Any],
    timeout_ms: int,
    *,
    close_browser: bool = True,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RoxyFingerprintError("playwright 未安装，无法连接 Roxy CDP") from exc

    endpoint = _connect_over_cdp(cdp_info)
    script = r"""
async () => {
  const perfNow = () => (performance && performance.now ? performance.now() : Date.now());
  const safe = (fn, fallback = null) => { try { return fn(); } catch (e) { return fallback; } };
  const hash32 = (text) => {
    // FNV-1a fallback hash, only used inside the page for compact preview values.
    let h = 0x811c9dc5;
    const s = String(text || "");
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, "0");
  };
  const canvasStart = perfNow();
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 180;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.textBaseline = "top";
    ctx.font = "16px Arial";
    ctx.fillStyle = "#f60";
    ctx.fillRect(15, 10, 120, 36);
    ctx.fillStyle = "#069";
    ctx.fillText("Roxy fingerprint ✓ 𝌆", 8, 8);
    ctx.strokeStyle = "rgba(102,204,0,0.7)";
    ctx.arc(82, 72, 50, 0, Math.PI * 2, true);
    ctx.stroke();
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = "rgb(255,0,255)";
    ctx.fillRect(70, 42, 75, 75);
    ctx.fillStyle = "rgb(0,255,255)";
    ctx.fillRect(115, 42, 75, 75);
  }
  const canvasDataUrl = safe(() => canvas.toDataURL("image/png"), "");
  const ttCanvas = perfNow() - canvasStart;

  const webglStart = perfNow();
  const glCanvas = document.createElement("canvas");
  const gl = safe(() => glCanvas.getContext("webgl") || glCanvas.getContext("experimental-webgl"), null);
  let webgl = {};
  if (gl) {
    const dbg = safe(() => gl.getExtension("WEBGL_debug_renderer_info"), null);
    const params = {};
    const names = [
      "ALIASED_LINE_WIDTH_RANGE", "ALIASED_POINT_SIZE_RANGE", "ALPHA_BITS",
      "BLUE_BITS", "DEPTH_BITS", "GREEN_BITS", "MAX_COMBINED_TEXTURE_IMAGE_UNITS",
      "MAX_CUBE_MAP_TEXTURE_SIZE", "MAX_FRAGMENT_UNIFORM_VECTORS", "MAX_RENDERBUFFER_SIZE",
      "MAX_TEXTURE_IMAGE_UNITS", "MAX_TEXTURE_SIZE", "MAX_VARYING_VECTORS",
      "MAX_VERTEX_ATTRIBS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_UNIFORM_VECTORS",
      "RED_BITS", "STENCIL_BITS"
    ];
    for (const name of names) {
      params[name] = safe(() => Array.from(gl.getParameter(gl[name]) || []), null);
      if (params[name] === null) params[name] = safe(() => gl.getParameter(gl[name]), null);
    }
    webgl = {
      version: safe(() => gl.getParameter(gl.VERSION), ""),
      vendor: safe(() => gl.getParameter(gl.VENDOR), ""),
      renderer: safe(() => gl.getParameter(gl.RENDERER), ""),
      shadingLanguageVersion: safe(() => gl.getParameter(gl.SHADING_LANGUAGE_VERSION), ""),
      unmaskedVendor: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL), "") : "",
      unmaskedRenderer: dbg ? safe(() => gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL), "") : "",
      extensions: safe(() => gl.getSupportedExtensions(), []) || [],
      params
    };
  }
  const ttWebgl = perfNow() - webglStart;

  const audioStart = perfNow();
  let audioValue = "";
  let audioError = "";
  try {
    const AC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (AC) {
      const audioCtx = new AC(1, 5000, 44100);
      const osc = audioCtx.createOscillator();
      const compressor = audioCtx.createDynamicsCompressor();
      osc.type = "triangle";
      osc.frequency.value = 10000;
      compressor.threshold.value = -50;
      compressor.knee.value = 40;
      compressor.ratio.value = 12;
      compressor.attack.value = 0;
      compressor.release.value = 0.25;
      osc.connect(compressor);
      compressor.connect(audioCtx.destination);
      osc.start(0);
      const buffer = await audioCtx.startRendering();
      const data = buffer.getChannelData(0);
      let sum = 0;
      for (let i = 4500; i < 5000; i++) sum += Math.abs(data[i] || 0);
      audioValue = String(sum);
    }
  } catch (e) {
    audioError = String(e && e.message || e);
  }
  const ttAudio = perfNow() - audioStart;

  const nav = safe(() => performance.getEntriesByType("navigation")[0]?.toJSON?.(), null);
  const uaData = navigator.userAgentData
    ? await navigator.userAgentData.getHighEntropyValues([
        "architecture", "bitness", "brands", "fullVersionList", "mobile",
        "model", "platform", "platformVersion", "uaFullVersion", "wow64"
      ]).catch(() => null)
    : null;
  const memory = performance.memory ? {
    usedJSHeapSize: performance.memory.usedJSHeapSize,
    totalJSHeapSize: performance.memory.totalJSHeapSize,
    jsHeapSizeLimit: performance.memory.jsHeapSizeLimit
  } : null;
  return {
    capturedAt: Date.now(),
    userAgent: navigator.userAgent,
    appVersion: navigator.appVersion,
    platform: navigator.platform,
    vendor: navigator.vendor,
    productSub: navigator.productSub,
    language: navigator.language,
    languages: Array.from(navigator.languages || []),
    cookieEnabled: navigator.cookieEnabled,
    onLine: navigator.onLine,
    hardwareConcurrency: navigator.hardwareConcurrency,
    deviceMemory: navigator.deviceMemory,
    doNotTrack: navigator.doNotTrack,
    webdriver: navigator.webdriver,
    uaData,
    screen: {
      width: screen.width,
      height: screen.height,
      availWidth: screen.availWidth,
      availHeight: screen.availHeight,
      colorDepth: screen.colorDepth,
      pixelDepth: screen.pixelDepth
    },
    window: {
      innerWidth, innerHeight, outerWidth, outerHeight,
      devicePixelRatio: window.devicePixelRatio
    },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    timezoneOffsetMinutes: new Date().getTimezoneOffset(),
    connection: navigator.connection ? {
      effectiveType: navigator.connection.effectiveType,
      rtt: navigator.connection.rtt,
      downlink: navigator.connection.downlink,
      saveData: navigator.connection.saveData
    } : null,
    plugins: Array.from(navigator.plugins || []).map(p => ({
      name: p.name, filename: p.filename, description: p.description,
      mimeTypes: Array.from(p).map(m => ({ type: m.type, suffixes: m.suffixes, description: m.description }))
    })),
    canvas: {
      dataUrl: canvasDataUrl,
      dataUrlLength: canvasDataUrl.length,
      previewHash: hash32(canvasDataUrl),
      ttCanvas
    },
    webgl,
    audio: { value: audioValue, error: audioError, ttAudio },
    memory,
    navigation: nav,
    timing: {
      ttCanvas,
      ttWebglBasic: ttWebgl,
      ttWebglExt: ttWebgl,
      ttAudio
    }
  };
}
"""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("about:blank", wait_until="domcontentloaded", timeout=timeout_ms)
        result = page.evaluate(script)
        if close_browser:
            browser.close()
        return result


def _runtime_to_profile(js: dict[str, Any], cdp_info: dict[str, Any]) -> dict[str, Any]:
    user_agent = str(js.get("userAgent") or USER_AGENT)
    platform = str(js.get("platform") or BROWSER_PROFILE.get("platform") or "Win32")
    ua_data = js.get("uaData") if isinstance(js.get("uaData"), dict) else None
    major = _parse_chrome_major(user_agent, int(BROWSER_PROFILE.get("chrome_major") or 150))
    full_version = _full_version_from_ua_data(ua_data, user_agent, major)
    language = str(js.get("language") or BROWSER_PROFILE.get("language") or "pt-BR")
    locale = _locale_from_language(language)
    timezone_offset_minutes = int(js.get("timezoneOffsetMinutes") or BROWSER_PROFILE.get("timezone_offset_minutes") or 0)
    connection = _dict_value(js.get("connection"))
    webgl = _dict_value(js.get("webgl"))
    window_info = _dict_value(js.get("window"))
    profile: dict[str, Any] = dict(BROWSER_PROFILE)
    profile.update(
        {
            "fingerprint_source": "roxy",
            "roxy_cdp_http": cdp_info.get("http", ""),
            "roxy_core_version": cdp_info.get("coreVersion", ""),
            "country": _env_str("PAYPAL_ROXY_COUNTRY", _country_from_locale(locale)),
            "language": language,
            "locale": locale,
            "timezone": str(js.get("timezone") or BROWSER_PROFILE.get("timezone") or "America/Sao_Paulo"),
            "timezone_offset_minutes": timezone_offset_minutes,
            "timezone_offset_ms": timezone_offset_minutes * 60 * 1000,
            "dst": bool(BROWSER_PROFILE.get("dst", False)),
            "chrome_major": major,
            "chrome_full_version": full_version,
            "platform": platform,
            "sec_ch_platform": _sec_ch_platform(ua_data, platform),
            "sec_ch_arch": _sec_ch_arch(ua_data),
            "device_memory": int(float(js.get("deviceMemory") or BROWSER_PROFILE.get("device_memory") or 8)),
            "hardware_concurrency": int(js.get("hardwareConcurrency") or BROWSER_PROFILE.get("hardware_concurrency") or 8),
            "device_pixel_ratio": float(window_info.get("devicePixelRatio") or 1),
            "connection_effective_type": str(connection.get("effectiveType") or BROWSER_PROFILE.get("connection_effective_type") or "4g"),
            "connection_rtt": str(connection.get("rtt") or BROWSER_PROFILE.get("connection_rtt") or "150"),
            "connection_downlink": str(connection.get("downlink") or BROWSER_PROFILE.get("connection_downlink") or "10"),
            "gpu_vendor": str(webgl.get("unmaskedVendor") or webgl.get("vendor") or BROWSER_PROFILE.get("gpu_vendor") or ""),
            "gpu_renderer": str(webgl.get("unmaskedRenderer") or webgl.get("renderer") or BROWSER_PROFILE.get("gpu_renderer") or ""),
            "webgl_vendor": str(webgl.get("vendor") or BROWSER_PROFILE.get("webgl_vendor") or "WebKit"),
            "webgl_renderer": str(webgl.get("renderer") or BROWSER_PROFILE.get("webgl_renderer") or "WebKit WebGL"),
            "user_agent": user_agent,
        }
    )
    return profile


def _runtime_screen(js: dict[str, Any]) -> dict[str, int]:
    source = _dict_value(js.get("screen"))
    return {
        "colorDepth": int(source.get("colorDepth") or SCREEN.get("colorDepth") or 24),
        "pixelDepth": int(source.get("pixelDepth") or SCREEN.get("pixelDepth") or 24),
        "height": int(source.get("height") or SCREEN.get("height") or 864),
        "width": int(source.get("width") or SCREEN.get("width") or 1536),
        "availHeight": int(source.get("availHeight") or source.get("height") or SCREEN.get("availHeight") or 864),
        "availWidth": int(source.get("availWidth") or source.get("width") or SCREEN.get("availWidth") or 1536),
    }


def _runtime_viewport(js: dict[str, Any]) -> dict[str, int]:
    source = _dict_value(js.get("window"))
    return {
        "width": int(source.get("innerWidth") or VIEWPORT.get("width") or 1365),
        "height": int(source.get("innerHeight") or VIEWPORT.get("height") or 768),
    }


def _runtime_device_fingerprint(js: dict[str, Any]) -> dict[str, Any]:
    canvas = _dict_value(js.get("canvas"))
    webgl = _dict_value(js.get("webgl"))
    audio = _dict_value(js.get("audio"))
    memory = _dict_value(js.get("memory"))
    timing = _dict_value(js.get("timing"))
    plugins = js.get("plugins") if isinstance(js.get("plugins"), list) else []
    webgl_extensions = webgl.get("extensions") if isinstance(webgl.get("extensions"), list) else []
    canvas_material = canvas.get("dataUrl") or canvas
    webgl_material = {
        "version": webgl.get("version"),
        "vendor": webgl.get("vendor"),
        "renderer": webgl.get("renderer"),
        "shadingLanguageVersion": webgl.get("shadingLanguageVersion"),
        "unmaskedVendor": webgl.get("unmaskedVendor"),
        "unmaskedRenderer": webgl.get("unmaskedRenderer"),
        "extensions": webgl_extensions,
        "params": webgl.get("params"),
    }
    return {
        "source": "roxy",
        "captured_at": int(js.get("capturedAt") or time.time() * 1000),
        "device_salt": "roxy:" + _sha256_hex({"canvas": canvas.get("previewHash"), "webgl": webgl_material, "audio": audio.get("value")})[:32],
        "canvas_h": _sha256_b64(canvas_material),
        "canvas_data_url_length": int(canvas.get("dataUrlLength") or 0),
        "cv_sig": _sha256_hex(canvas_material),
        "webgl_ext_hash": _sha256_hex(webgl_material),
        "audio_val": str(audio.get("value") or ""),
        "js_heap_size_limit": int(memory.get("jsHeapSizeLimit") or 4_395_630_592),
        "webgl_extensions": webgl_extensions,
        "font_hash": _sha256_hex({"plugins": plugins, "languages": js.get("languages")})[:32],
        "timings": {
            "tt_dfp": float(timing.get("ttCanvas") or 0.0) + float(timing.get("ttWebglBasic") or 0.0) + float(timing.get("ttAudio") or 0.0),
            "tt_canvas": float(timing.get("ttCanvas") or canvas.get("ttCanvas") or 0.0),
            "tt_webgl_basic": float(timing.get("ttWebglBasic") or 0.0),
            "tt_webgl_ext": float(timing.get("ttWebglExt") or 0.0),
            "tt_storage": 0.0,
            "tt_math": 0.10000000149011612,
        },
        "js_memory": {
            "used": int(memory.get("usedJSHeapSize") or 0),
            "total": int(memory.get("totalJSHeapSize") or 0),
        },
        "raw_runtime_hash": _sha256_hex(js),
    }


def capture_roxy_runtime_profile(
    config: RoxyCaptureConfig | None = None,
    *,
    keep_browser: bool = False,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    config = config or load_roxy_capture_config(proxy_url=proxy_url)
    if proxy_url is not None:
        config.proxy_url = _canonical_proxy_url(proxy_url)
    if keep_browser:
        config.close_after_capture = False
        config.delete_after_capture = False
    client = RoxyApiClient(config)
    workspace_id: int | None = None
    dir_id = ""
    try:
        workspace_id, project_id = client.get_workspace_project()
        dir_id = client.create_or_reuse_profile(workspace_id, project_id)
        client.randomize_profile(workspace_id, dir_id)
        cdp_info = client.open_profile(workspace_id, dir_id)
        js = _evaluate_cdp_fingerprint(
            cdp_info,
            timeout_ms=int(config.timeout_seconds * 1000),
            close_browser=not keep_browser,
        )
        profile = _runtime_to_profile(js, cdp_info)
        runtime = {
            "browser_profile": profile,
            "screen": _runtime_screen(js),
            "viewport": _runtime_viewport(js),
            "device_fingerprint": _runtime_device_fingerprint(js),
        }
        if keep_browser:
            runtime["roxy_browser"] = {
                "workspace_id": workspace_id,
                "workspace_created": bool(client.auto_workspace_created),
                "workspace_name": client.auto_workspace_name,
                "dir_id": dir_id,
                "cdp_info": cdp_info,
                "api_base": config.api_base,
                "headless": config.headless,
                "proxy_url_hash": _proxy_url_hash(config.proxy_url),
                "proxy_label": _redact_proxy_url(config.proxy_url) or "noproxy",
                "created_for": "fingerprint",
            }
        return runtime
    finally:
        if dir_id and config.close_after_capture:
            try:
                client.close_profile(dir_id)
            except Exception as exc:
                logger.debug("Roxy close profile failed: {}", exc)
        if dir_id and workspace_id is not None and config.delete_after_capture:
            try:
                client.delete_profile(workspace_id, dir_id)
            except Exception as exc:
                logger.debug("Roxy delete profile failed: {}", exc)
        if (
            workspace_id is not None
            and client.auto_workspace_created
            and config.delete_auto_workspace
            and not keep_browser
        ):
            try:
                client.delete_workspace(workspace_id, client.auto_workspace_name)
            except Exception as exc:
                logger.debug("Roxy delete temporary workspace failed: {}", exc)
        client.close()


def close_roxy_browser(roxy_browser: dict[str, Any], *, delete: bool = True) -> None:
    if not isinstance(roxy_browser, dict) or not roxy_browser.get("dir_id"):
        return
    config = load_roxy_capture_config()
    if roxy_browser.get("api_base"):
        config.api_base = str(roxy_browser.get("api_base")).rstrip("/")
    client = RoxyApiClient(config)
    workspace_id = int(roxy_browser.get("workspace_id") or config.workspace_id or 0)
    dir_id = str(roxy_browser.get("dir_id") or "")
    try:
        try:
            client.close_profile(dir_id)
        except Exception as exc:
            logger.debug("Roxy close profile failed: {}", exc)
        if delete and workspace_id:
            try:
                client.delete_profile(workspace_id, dir_id)
            except Exception as exc:
                logger.debug("Roxy delete profile failed: {}", exc)
            if roxy_browser.get("workspace_created") and roxy_browser.get("workspace_name"):
                try:
                    client.delete_workspace(workspace_id, str(roxy_browser.get("workspace_name") or ""))
                except Exception as exc:
                    logger.debug("Roxy delete temporary workspace failed: {}", exc)
    finally:
        client.close()


def _extract_datadome_clientid_from_html(html: str) -> str:
    if "datadome" not in (html or "").lower():
        return ""
    for pattern in (
        r"\bvar\s+c\s*=\s*['\"]([^'\"]{40,})['\"]",
        r"\bc\s*=\s*['\"]([^'\"]{40,})['\"][^<]{0,600}datadome",
        r"x-datadome-clientid['\"]?\s*[:=]\s*['\"]([^'\"]{40,})",
    ):
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            return match.group(1)
    return ""


def solve_datadome_with_roxy(
    roxy_browser: dict[str, Any],
    url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 12.0,
) -> dict[str, Any]:
    """Run DataDome through the shared local-headless logic on an existing Roxy browser."""
    from paypal.local_headless import LocalHeadlessSession

    session = LocalHeadlessSession(
        cookies=cast(list[dict[str, object]] | None, cookies),
        roxy_browser=cast(dict[str, object], roxy_browser),
        runtime="roxy",
    )
    try:
        return cast(dict[str, Any], session.solve_datadome(url, wait_seconds=wait_seconds))
    finally:
        session.close()


def _extract_mtr_response_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    products = _dict_value(value.get("products"))
    identification = _dict_value(products.get("identification"))
    data = _dict_value(identification.get("data"))
    result = _dict_value(data.get("result"))
    visitor_token = (
        data.get("visitorToken")
        or data.get("visitor_token")
        or result.get("visitorToken")
        or result.get("visitor_token")
        or ""
    )
    return {
        "requestId": value.get("requestId") or "",
        "sealedResult": value.get("sealedResult") or "",
        "visitorToken": visitor_token,
        "raw": value,
    }


def run_mtr_with_roxy_browser(
    roxy_browser: dict[str, Any],
    page_url: str,
    *,
    dfp_config: dict[str, Any],
    dfp_script_url: str,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 20.0,
) -> dict[str, Any]:
    """Run PayPal dfp.js/MTR through the shared local-headless logic on Roxy."""
    from paypal.local_headless import run_local_headless_mtr_phase1

    return cast(
        dict[str, Any],
        run_local_headless_mtr_phase1(
            page_url,
            dfp_config=cast(dict[str, object], dfp_config),
            dfp_script_url=dfp_script_url,
            cookies=cast(list[dict[str, object]] | None, cookies),
            wait_seconds=wait_seconds,
            mtr_wait_seconds=wait_seconds,
            roxy_browser=cast(dict[str, object], roxy_browser),
            runtime="roxy",
        ),
    )


def run_phase1_risk_with_roxy_browser(
    roxy_browser: dict[str, Any],
    page_url: str,
    *,
    cookies: list[dict[str, Any]] | None = None,
    wait_seconds: float = 18.0,
    app_id: str = "IWC_NEXT_CHECKOUT",
    correlation_id: str = "",
    document_html: str = "",
    document_status: int = 200,
) -> dict[str, Any]:
    """Run signup-context browser-risk through the shared local-headless logic on Roxy."""
    from paypal.local_headless import run_local_headless_mtr_phase1

    return cast(
        dict[str, Any],
        run_local_headless_mtr_phase1(
            page_url,
            dfp_config={},
            dfp_script_url="",
            cookies=cast(list[dict[str, object]] | None, cookies),
            wait_seconds=wait_seconds,
            mtr_wait_seconds=0.0,
            app_id=app_id,
            correlation_id=correlation_id,
            stage="signup_context",
            new_page=True,
            run_mtr=False,
            roxy_browser=cast(dict[str, object], roxy_browser),
            runtime="roxy",
            document_html=document_html,
            document_status=document_status,
        ),
    )
