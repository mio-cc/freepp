"""Optional protocol traffic recorder for offline diffing against browser captures."""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


_global_recorder: "TrafficRecorder | None" = None
_GLOBAL_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def traffic_recording_enabled() -> bool:
    return _env_bool("PAYPAL_TRAFFIC_RECORD", False) or _env_bool(
        "PAYPAL_TEST_TRAFFIC_RECORD",
        False,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_name(value: str, max_len: int = 140) -> str:
    name = re_sub(r"^https?://", "", value or "")
    name = re_sub(r"[^a-zA-Z0-9._-]+", "_", name)
    name = name.strip("_")[:max_len]
    return name or "item"


def re_sub(pattern: str, repl: str, value: str) -> str:
    import re

    return re.sub(pattern, repl, value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prepare_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def _touch_private_file(path: Path) -> None:
    _prepare_private_dir(path.parent)
    path.touch(exist_ok=True)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _write_private_text(path: Path, text: str) -> None:
    _prepare_private_dir(path.parent)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _is_text_content_type(content_type: str = "", url: str = "") -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if (
        ct.startswith("text/")
        or "json" in ct
        or "javascript" in ct
        or "xml" in ct
        or "graphql" in ct
        or ct in {"application/x-www-form-urlencoded", "application/graphql"}
    ):
        return True
    path = urllib.parse.urlparse(url or "").path.lower()
    return path.endswith((".txt", ".json", ".js", ".mjs", ".html", ".htm", ".css", ".xml"))


def _extension_for(content_type: str = "", url: str = "", default: str = ".bin") -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct == "application/json" or "json" in ct:
        return ".json"
    if ct == "application/x-www-form-urlencoded":
        return ".txt"
    if "javascript" in ct or "ecmascript" in ct:
        return ".js"
    if ct in {"text/html", "application/xhtml+xml"}:
        return ".html"
    if ct == "text/css":
        return ".css"
    if ct.startswith("text/") or "xml" in ct:
        return ".txt" if "xml" not in ct else ".xml"
    guessed = mimetypes.guess_extension(ct or "")
    if guessed and len(guessed) <= 10:
        return guessed
    suffix = Path(urllib.parse.urlparse(url or "").path).suffix
    return suffix if suffix and len(suffix) <= 10 else default


_SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "securitycode",
    "cvv",
    "pin",
    "clientkey",
    "accesstoken",
    "euat",
    "cardnumber",
    "encryptednumber",
    "cpf",
    "identitydocument",
    "document",
)


def _redact_scalar(value: Any, key: str = "") -> Any:
    if not isinstance(value, str):
        return value
    compact = key.lower().replace("_", "").replace("-", "")
    if any(part in compact for part in _SENSITIVE_KEY_PARTS):
        if compact in {"cardnumber", "encryptednumber"}:
            digits = "".join(ch for ch in value if ch.isdigit())
            return f"{'*' * max(0, len(digits) - 4)}{digits[-4:]}" if len(digits) > 4 else "<redacted>"
        return "<redacted>"
    if compact in {"token", "batoken", "ectoken", "billingagreementid"} and len(value) > 12:
        return f"{value[:6]}...{value[-4:]}"
    return value


def redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return _redact_scalar(value, key)


def _headers_to_dict(headers: Any) -> dict[str, str]:
    if not headers:
        return {}
    try:
        if hasattr(headers, "multi_items"):
            return {str(k): str(v) for k, v in headers.multi_items()}
        return {str(k): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def _url_with_params(url: str, params: Any) -> str:
    if not params:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        old = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if isinstance(params, dict):
            new = list(params.items())
        else:
            new = list(params)
        query = urllib.parse.urlencode(old + new, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=query))
    except Exception:
        return url


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request_body_from_kwargs(kwargs: dict[str, Any]) -> tuple[bytes | None, str, dict[str, Any]]:
    """Best-effort serialization of outbound request payloads."""
    meta: dict[str, Any] = {}
    if "json" in kwargs and kwargs.get("json") is not None:
        return _json_bytes(kwargs.get("json")), "application/json", {"source": "json"}

    if "content" in kwargs and kwargs.get("content") is not None:
        content = kwargs.get("content")
        if isinstance(content, bytes):
            data = content
        elif isinstance(content, bytearray):
            data = bytes(content)
        else:
            data = str(content).encode("utf-8")
        return data, "", {"source": "content"}

    if "data" in kwargs and kwargs.get("data") is not None:
        data = kwargs.get("data")
        if data is None:
            return None, "", meta
        if isinstance(data, bytes):
            return data, "", {"source": "data.bytes"}
        if isinstance(data, bytearray):
            return bytes(data), "", {"source": "data.bytearray"}
        if isinstance(data, str):
            return data.encode("utf-8"), "", {"source": "data.str"}
        try:
            return urllib.parse.urlencode(cast(Any, data), doseq=True).encode("utf-8"), (
                "application/x-www-form-urlencoded"
            ), {"source": "data.form"}
        except Exception:
            return _json_bytes(redact(data)), "application/json", {"source": "data.repr"}

    if "files" in kwargs and kwargs.get("files") is not None:
        files = kwargs.get("files")
        if files is None:
            return None, "", meta
        fields = []
        try:
            iterable: Any = files.items() if isinstance(files, dict) else files
            for name, value in iterable:
                item: dict[str, Any] = {"name": str(name)}
                if isinstance(value, tuple):
                    item["filename"] = value[0]
                    item["value"] = value[1] if len(value) > 1 else ""
                    if len(value) > 2:
                        item["content_type"] = value[2]
                else:
                    item["value"] = value
                fields.append(redact(item, item.get("name", "")))
        except Exception as exc:
            fields = [{"error": str(exc)}]
        meta["source"] = "files.summary"
        meta["note"] = "multipart boundary is client-generated; this file stores field summary"
        return _json_bytes({"multipart": fields}), "application/json", meta

    return None, "", meta


class TrafficRecorder:
    """Write request/response records in a roxy-like directory layout."""

    def __init__(self, root: str | Path | None = None):
        configured = (
            str(root or "").strip()
            or os.getenv("PAYPAL_TRAFFIC_RECORD_DIR", "").strip()
            or os.getenv("PAYPAL_TEST_TRAFFIC_DIR", "").strip()
        )
        if configured:
            self.root = Path(configured).expanduser().resolve()
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.root = (Path.cwd() / "captures" / f"program-paypal-{stamp}").resolve()
        self.network_dir = self.root / "network"
        self.requests_dir = self.network_dir / "requests"
        self.bodies_dir = self.network_dir / "bodies"
        self.events_file = self.network_dir / "events.jsonl"
        self.requests_tsv = self.network_dir / "requests.tsv"
        self.summary_file = self.root / "summary.json"
        self.meta_file = self.root / "metadata.json"
        self.raw_bodies = _env_bool("PAYPAL_TRAFFIC_RECORD_RAW", False)
        self.response_bodies = _env_bool("PAYPAL_TRAFFIC_RECORD_RESPONSES", False)
        self.max_preview = int(os.getenv("PAYPAL_TRAFFIC_PREVIEW_BYTES", "4000") or "4000")
        self._lock = threading.Lock()
        self._seq = 0
        self._response_seq = 0
        self._started = time.time()
        self._closed = False
        self._request_body_paths: dict[int, str] = {}
        self._last_event: dict[str, Any] = {}
        for path in (self.root, self.network_dir, self.requests_dir, self.bodies_dir):
            _prepare_private_dir(path)
        _touch_private_file(self.events_file)
        if not self.requests_tsv.exists():
            _write_private_text(
                self.requests_tsv,
                "id\ttime\tmethod\tstatus\turl\trequestBody\tresponseBody\tcontentType\tsynthetic\n",
            )
        _write_private_text(
            self.meta_file,
            json.dumps(
                {
                    "startedAt": _now(),
                    "root": str(self.root),
                    "recorder": "paypal.traffic_recorder",
                    "rawBodies": self.raw_bodies,
                    "responseBodies": self.response_bodies,
                    "note": "Program-side PayPalSession/CapSolver request recorder for offline diffing against Roxy CDP captures.",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        self._write_summary("recording")

    def _next_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _append_jsonl(self, obj: dict[str, Any]) -> None:
        with self._lock:
            _touch_private_file(self.events_file)
            with self.events_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _append_tsv(self, fields: list[Any]) -> None:
        line = "\t".join(str(v or "").replace("\t", " ").replace("\n", "\\n") for v in fields)
        with self._lock:
            _touch_private_file(self.requests_tsv)
            with self.requests_tsv.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _write_summary(self, status: str = "recording") -> None:
        payload = {
            "status": status,
            "startedAt": datetime.fromtimestamp(self._started, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "updatedAt": _now(),
            "finishedAt": _now() if status == "closed" else "",
            "root": str(self.root),
            "requests": self._seq,
            "responses": self._response_seq,
            "elapsedSeconds": round(time.time() - self._started, 3),
            "eventsFile": str(self.events_file),
            "requestsTsv": str(self.requests_tsv),
            "requestsDir": str(self.requests_dir),
            "bodiesDir": str(self.bodies_dir),
            "lastEvent": self._last_event,
        }
        tmp = self.summary_file.with_suffix(".json.tmp")
        with self._lock:
            _prepare_private_dir(tmp.parent)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
            tmp.replace(self.summary_file)
            try:
                os.chmod(self.summary_file, 0o600)
            except Exception:
                pass

    def _save_bytes(
        self,
        directory: Path,
        prefix: str,
        url: str,
        data: bytes | None,
        content_type: str = "",
    ) -> dict[str, Any] | None:
        if not data:
            return None
        ext = _extension_for(content_type, url)
        path = directory / f"{prefix}_{_safe_name(url)}_{_sha256(data)[:10]}{ext}"
        _prepare_private_dir(directory)
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        text = _is_text_content_type(content_type, url)
        preview = ""
        if text:
            preview = data[: self.max_preview].decode("utf-8", errors="replace")
        else:
            preview = base64.b64encode(data[: min(self.max_preview, 256)]).decode("ascii")
        return {
            "path": str(path),
            "bytes": len(data),
            "sha256": _sha256(data),
            "text": text,
            "preview": preview,
        }

    def record_request(
        self,
        method: str,
        url: str,
        kwargs: dict[str, Any] | None = None,
        *,
        headers: dict[str, Any] | None = None,
        synthetic: bool = False,
        note: str = "",
    ) -> int:
        req_id = self._next_id()
        kwargs = dict(kwargs or {})
        full_url = _url_with_params(url, kwargs.get("params"))
        merged_headers = dict(headers or {})
        body, body_content_type, body_meta = request_body_from_kwargs(kwargs)
        content_type = (
            body_content_type
            or merged_headers.get("Content-Type")
            or merged_headers.get("content-type")
            or ""
        )
        saved_body = None
        if self.raw_bodies and body:
            saved_body = self._save_bytes(
                self.requests_dir,
                f"{req_id:05d}_{method.upper()}",
                full_url,
                body,
                content_type,
            )
            if saved_body:
                self._request_body_paths[req_id] = str(saved_body.get("path") or "")
        rec = {
            "id": req_id,
            "time": _now(),
            "type": "request",
            "method": method.upper(),
            "url": full_url,
            "headers": redact(_headers_to_dict(merged_headers)),
            "synthetic": synthetic,
            "note": note,
        }
        if saved_body:
            rec["postData"] = {
                k: v
                for k, v in saved_body.items()
                if k in {"path", "bytes", "sha256", "text", "preview"}
            }
            rec["postData"]["meta"] = body_meta
        elif body:
            rec["postData"] = {
                "bytes": len(body),
                "sha256": _sha256(body),
                "text": _is_text_content_type(content_type, full_url),
                "meta": body_meta,
            }
        self._append_jsonl(rec)
        self._last_event = {
            "type": "request",
            "id": req_id,
            "time": rec["time"],
            "method": method.upper(),
            "url": full_url,
        }
        self._write_summary("recording")
        return req_id

    def record_response(
        self,
        req_id: int,
        method: str,
        url: str,
        response: Any = None,
        *,
        error: str = "",
        synthetic: bool = False,
    ) -> None:
        status = ""
        headers: dict[str, str] = {}
        body = b""
        try:
            if response is not None:
                status = str(getattr(response, "status_code", "") or "")
                headers = _headers_to_dict(getattr(response, "headers", {}) or {})
                body = bytes(getattr(response, "content", b"") or b"")
        except Exception:
            pass
        content_type = headers.get("content-type") or headers.get("Content-Type") or ""
        saved_body = None
        if self.response_bodies and body:
            saved_body = self._save_bytes(
                self.bodies_dir,
                f"{req_id:05d}_resp_{status or 'ERR'}",
                url,
                body,
                content_type,
            )
        rec = {
            "id": req_id,
            "time": _now(),
            "type": "response" if not error else "requestfailed",
            "method": method.upper(),
            "url": url,
            "status": int(status) if str(status).isdigit() else status,
            "headers": redact(headers),
            "synthetic": synthetic,
        }
        if error:
            rec["error"] = error
        self._response_seq += 1
        if saved_body:
            rec["responseBody"] = {
                k: v
                for k, v in saved_body.items()
                if k in {"path", "bytes", "sha256", "text", "preview"}
            }
        self._append_jsonl(rec)
        request_body = self._request_body_paths.get(req_id, "")
        self._append_tsv(
            [
                req_id,
                _now(),
                method.upper(),
                status,
                url,
                request_body,
                saved_body.get("path", "") if saved_body else "",
                content_type,
                "1" if synthetic else "",
            ]
        )
        self._last_event = {
            "type": rec["type"],
            "id": req_id,
            "time": rec["time"],
            "method": method.upper(),
            "url": url,
            "status": rec["status"],
            "synthetic": synthetic,
        }
        self._write_summary("recording")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._write_summary("closed")


def get_global_traffic_recorder() -> TrafficRecorder | None:
    current = getattr(_THREAD_LOCAL, "recorder", None)
    if current is not None:
        return current
    global _global_recorder
    if not traffic_recording_enabled():
        return None
    with _GLOBAL_LOCK:
        if _global_recorder is None:
            _global_recorder = TrafficRecorder()
        return _global_recorder


def reset_global_traffic_recorder(root: str | Path | None = None) -> TrafficRecorder:
    global _global_recorder
    os.environ["PAYPAL_TRAFFIC_RECORD"] = "1"
    if root:
        os.environ["PAYPAL_TRAFFIC_RECORD_DIR"] = str(root)
    with _GLOBAL_LOCK:
        if _global_recorder is not None:
            try:
                _global_recorder.close()
            except Exception:
                pass
        _global_recorder = TrafficRecorder(root)
        return _global_recorder


def set_current_traffic_recorder(recorder: TrafficRecorder | None) -> None:
    _THREAD_LOCAL.recorder = recorder


def clear_current_traffic_recorder() -> None:
    if hasattr(_THREAD_LOCAL, "recorder"):
        delattr(_THREAD_LOCAL, "recorder")


def close_global_traffic_recorder() -> None:
    global _global_recorder
    with _GLOBAL_LOCK:
        if _global_recorder is not None:
            _global_recorder.close()
            _global_recorder = None
