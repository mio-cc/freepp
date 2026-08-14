from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx
from loguru import logger


SMSBOWER_API_URL = "https://smsbower.page/stubs/handler_api.php"
SMSBOWER_DEFAULT_SERVICE = "ts"
SMSBOWER_DEFAULT_COUNTRY = "73"
SMSBOWER_DEFAULT_WAIT_SECONDS = 30.0
SMSBOWER_DEFAULT_POLL_INTERVAL_SECONDS = 2.0
SMSBOWER_DEFAULT_MAX_CHANNEL_FAILURES = 3
SMSBOWER_DEFAULT_ACTIVATION_TTL_SECONDS = 20 * 60
SMSBOWER_DEFAULT_MAX_ATTEMPTS = 12


class SMSBowerApiError(RuntimeError):
    pass


@dataclass
class SMSBowerProviderPrice:
    provider_id: str
    price: float
    count: int


@dataclass
class SMSBowerActivation:
    activation_id: str
    phone_number: str
    provider_id: str
    price: float
    expires_at: float
    reused: bool = False


class SMSBowerClientProtocol(Protocol):
    def get_provider_prices(self, service: str, country: str) -> list[SMSBowerProviderPrice] | list[dict[str, object]]: ...

    def get_number_v2(
        self,
        *,
        service: str,
        country: str,
        provider_id: str,
        max_price: float,
    ) -> dict[str, object]: ...

    def get_status(self, activation_id: str) -> str: ...

    def set_status(self, activation_id: str, status: int) -> str: ...


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_value(name: str) -> str:
    if os.getenv(name):
        return os.getenv(name, "").strip()
    for env_path in (Path.cwd() / ".env", _project_root() / ".env"):
        try:
            if not env_path.exists():
                continue
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return ""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _load_dotenv_value(name)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "smsbower"}


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    raw = _load_dotenv_value(name)
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = _load_dotenv_value(name)
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


def _digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def normalize_brazil_phone(value: object) -> str:
    return normalize_phone(value, "+55")


def normalize_phone(value: object, cc: str = "+55") -> str:
    """按国家码归一回填 +CC 前缀 (cc 如 "+66" / "+1")。"""
    digits = _digits(value)
    if not digits:
        raise SMSBowerApiError("SMSBower returned an empty phone number")
    cc_digits = str(cc or "+55").lstrip("+")
    if digits.startswith(cc_digits):
        return f"+{digits}"
    return f"+{cc_digits}{digits}"


def _parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _parse_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


class SMSBowerClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = SMSBOWER_API_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise SMSBowerApiError("SMSBower API key is not configured")
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def _request_text(self, action: str, params: dict[str, object] | None = None) -> str:
        query: dict[str, str] = {"api_key": self.api_key, "action": action}
        for key, value in (params or {}).items():
            query[key] = str(value)
        with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds), trust_env=False) as client:
            response = client.get(self.base_url, params=query)
            response.raise_for_status()
        text = (response.text or "").strip()
        if text in {"BAD_KEY", "BAD_ACTION", "BAD_SERVICE", "BAD_COUNTRY"}:
            raise SMSBowerApiError(text)
        return text

    def _request_json(self, action: str, params: dict[str, object] | None = None) -> object:
        text = self._request_text(action, params)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SMSBowerApiError(text) from exc

    def get_provider_prices(self, service: str, country: str) -> list[SMSBowerProviderPrice]:
        data = self._request_json("getPricesV3", {"service": service, "country": country})
        providers = self._extract_price_nodes(data, service, country)
        prices = [
            SMSBowerProviderPrice(
                provider_id=str(item.get("provider_id") or key),
                price=_parse_float(item.get("price")),
                count=_parse_int(item.get("count")),
            )
            for key, item in providers
            if isinstance(item, dict)
        ]
        return sorted(
            [item for item in prices if item.provider_id and item.price > 0 and item.count > 0],
            key=lambda item: (item.price, item.provider_id),
        )

    def _extract_price_nodes(
        self,
        data: object,
        service: str,
        country: str,
    ) -> list[tuple[str, dict[str, object]]]:
        if not isinstance(data, dict):
            raise SMSBowerApiError(f"Unexpected getPricesV3 response: {data!r}")
        country_node = data.get(country)
        if isinstance(country_node, dict):
            service_node = country_node.get(service)
            if isinstance(service_node, dict):
                return [
                    (str(key), value)
                    for key, value in service_node.items()
                    if isinstance(value, dict)
                ]
        for maybe_country in data.values():
            if not isinstance(maybe_country, dict):
                continue
            for maybe_service in maybe_country.values():
                if not isinstance(maybe_service, dict):
                    continue
                matches = [
                    (str(key), value)
                    for key, value in maybe_service.items()
                    if isinstance(value, dict) and "price" in value and "count" in value
                ]
                if matches:
                    return matches
        raise SMSBowerApiError(f"No provider prices for service={service} country={country}")

    def get_number_v2(
        self,
        *,
        service: str,
        country: str,
        provider_id: str,
        max_price: float,
    ) -> dict[str, object]:
        data = self._request_json(
            "getNumberV2",
            {
                "service": service,
                "country": country,
                "providerIds": provider_id,
                "maxPrice": max_price,
            },
        )
        if not isinstance(data, dict) or not data.get("activationId") or not data.get("phoneNumber"):
            raise SMSBowerApiError(f"Unexpected getNumberV2 response: {data!r}")
        return data

    def get_status(self, activation_id: str) -> str:
        return self._request_text("getStatus", {"id": activation_id})

    def set_status(self, activation_id: str, status: int) -> str:
        return self._request_text("setStatus", {"id": activation_id, "status": status})


class SMSBowerActivationStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else _project_root() / "cache" / "smsbower_numbers.json"

    def _empty(self) -> dict[str, object]:
        return {"activations": [], "provider_failures": {}}

    def load(self) -> dict[str, object]:
        try:
            if not self.path.exists():
                return self._empty()
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("activations", [])
                data.setdefault("provider_failures", {})
                return data
        except Exception as exc:
            logger.warning("SMSBower cache read failed: {}", exc)
        return self._empty()

    def save(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def reusable_activation(self, now: float | None = None) -> SMSBowerActivation | None:
        now = time.time() if now is None else now
        data = self.load()
        activations = data.get("activations")
        if not isinstance(activations, list):
            return None
        fresh_rows: list[dict[str, object]] = []
        selected: SMSBowerActivation | None = None
        for row in activations:
            if not isinstance(row, dict):
                continue
            expires_at = _parse_float(row.get("expires_at"))
            if expires_at <= now:
                continue
            fresh_rows.append(row)
            if selected is None:
                selected = SMSBowerActivation(
                    activation_id=str(row.get("activation_id") or ""),
                    phone_number=str(row.get("phone_number") or ""),
                    provider_id=str(row.get("provider_id") or ""),
                    price=_parse_float(row.get("price")),
                    expires_at=expires_at,
                    reused=True,
                )
        if len(fresh_rows) != len(activations):
            data["activations"] = fresh_rows
            self.save(data)
        if selected and selected.activation_id and selected.phone_number:
            return selected
        return None

    def remember_success(
        self,
        *,
        activation_id: str,
        phone_number: str,
        provider_id: str,
        price: float,
        expires_at: float,
    ) -> None:
        data = self.load()
        activations = data.get("activations")
        rows = [row for row in activations if isinstance(row, dict)] if isinstance(activations, list) else []
        rows = [row for row in rows if str(row.get("activation_id") or "") != activation_id]
        rows.insert(
            0,
            {
                "activation_id": activation_id,
                "phone_number": phone_number,
                "provider_id": provider_id,
                "price": price,
                "expires_at": expires_at,
            },
        )
        data["activations"] = rows[:20]
        failures = data.get("provider_failures")
        if isinstance(failures, dict):
            failures[str(provider_id)] = 0
        self.save(data)

    def abandon(self, activation_id: str) -> None:
        data = self.load()
        activations = data.get("activations")
        if isinstance(activations, list):
            data["activations"] = [
                row
                for row in activations
                if not isinstance(row, dict) or str(row.get("activation_id") or "") != activation_id
            ]
            self.save(data)

    def provider_failure_count(self, provider_id: str) -> int:
        failures = self.load().get("provider_failures")
        if not isinstance(failures, dict):
            return 0
        return _parse_int(failures.get(str(provider_id)))

    def record_failure(self, provider_id: str) -> None:
        data = self.load()
        failures = data.get("provider_failures")
        if not isinstance(failures, dict):
            failures = {}
            data["provider_failures"] = failures
        key = str(provider_id)
        failures[key] = _parse_int(failures.get(key)) + 1
        self.save(data)


class SMSBowerOtpProvider:
    def __init__(
        self,
        *,
        client: SMSBowerClientProtocol,
        store: SMSBowerActivationStore | None = None,
        service: str = SMSBOWER_DEFAULT_SERVICE,
        country: str = SMSBOWER_DEFAULT_COUNTRY,
        phone_cc: str = "+55",
        max_price: float | None = None,
        wait_seconds: float = SMSBOWER_DEFAULT_WAIT_SECONDS,
        poll_interval_seconds: float = SMSBOWER_DEFAULT_POLL_INTERVAL_SECONDS,
        max_channel_failures: int = SMSBOWER_DEFAULT_MAX_CHANNEL_FAILURES,
        activation_ttl_seconds: int = SMSBOWER_DEFAULT_ACTIVATION_TTL_SECONDS,
        max_attempts: int = SMSBOWER_DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.client = client
        self.store = store or SMSBowerActivationStore()
        self.service = service
        self.country = country
        self.phone_cc = str(phone_cc or "+55")
        self.max_price = float(max_price) if max_price else None
        self.wait_seconds = max(1.0, float(wait_seconds)) if wait_seconds >= 1 else float(wait_seconds)
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.max_channel_failures = max(1, int(max_channel_failures))
        self.activation_ttl_seconds = max(60, int(activation_ttl_seconds))
        self.max_attempts = max(1, int(max_attempts))

    def reserve_number(self) -> SMSBowerActivation:
        reusable = self.store.reusable_activation()
        if reusable is not None:
            logger.info("Reusing active SMSBower phone from provider {}", reusable.provider_id)
            self._set_status(reusable.activation_id, 3)
            return reusable
        return self._purchase_new_number()

    def _purchase_new_number(self) -> SMSBowerActivation:
        prices = self._get_provider_prices()
        if not prices:
            raise SMSBowerApiError(f"SMSBower has no PayPal {self.phone_cc} providers with available numbers")
        last_error: Exception | None = None
        for price in prices:
            if self.store.provider_failure_count(price.provider_id) >= self.max_channel_failures:
                logger.info("Skipping SMSBower provider {} after repeated failures", price.provider_id)
                continue
            if self.max_price is not None and price.price > self.max_price:
                logger.info(
                    "Skipping SMSBower provider {} price={} over budget={}",
                    price.provider_id, price.price, self.max_price,
                )
                continue
            try:
                data = self._get_number_v2(price)
                activation = SMSBowerActivation(
                    activation_id=str(data["activationId"]),
                    phone_number=normalize_phone(data["phoneNumber"], self.phone_cc),
                    provider_id=str(data.get("activationOperator") or data.get("provider_id") or price.provider_id),
                    price=_parse_float(data.get("activationCost"), price.price),
                    expires_at=time.time() + self.activation_ttl_seconds,
                    reused=False,
                )
                logger.info(
                    "Reserved SMSBower PayPal {} number provider={} price={}",
                    self.phone_cc,
                    activation.provider_id,
                    activation.price,
                )
                return activation
            except Exception as exc:
                last_error = exc
                self.store.record_failure(price.provider_id)
                logger.warning("SMSBower provider {} failed: {}", price.provider_id, exc)
        if last_error is not None:
            raise SMSBowerApiError(
                f"SMSBower could not reserve a PayPal {self.phone_cc} number: {last_error}"
            ) from last_error
        raise SMSBowerApiError("SMSBower providers are all blocked by failure thresholds")

    def mark_sms_sent(self, activation: SMSBowerActivation) -> None:
        if activation.reused:
            return
        self._set_status(activation.activation_id, 1)

    def wait_for_code(self, activation: SMSBowerActivation, timeout_seconds: float | None = None) -> str | None:
        deadline = time.time() + (self.wait_seconds if timeout_seconds is None else float(timeout_seconds))
        while time.time() <= deadline:
            status = self._get_status(activation.activation_id)
            code = self._code_from_status(status)
            if code:
                self.store.remember_success(
                    activation_id=activation.activation_id,
                    phone_number=activation.phone_number,
                    provider_id=activation.provider_id,
                    price=activation.price,
                    expires_at=activation.expires_at,
                )
                return code
            if status in {"STATUS_CANCEL", "NO_ACTIVATION"}:
                self.store.abandon(activation.activation_id)
                return None
            time.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.time())))
        return None

    def abandon(self, activation: SMSBowerActivation, reason: str) -> None:
        logger.warning(
            "Abandoning SMSBower activation provider={} reused={} reason={}",
            activation.provider_id,
            activation.reused,
            reason,
        )
        try:
            self._set_status(activation.activation_id, 8)
        except Exception as exc:
            logger.warning("SMSBower activation cancel failed: {}", exc)
        self.store.abandon(activation.activation_id)
        self.store.record_failure(activation.provider_id)

    def register_confirmation_result(self, activation: SMSBowerActivation, confirmed: bool) -> None:
        if confirmed:
            self.store.remember_success(
                activation_id=activation.activation_id,
                phone_number=activation.phone_number,
                provider_id=activation.provider_id,
                price=activation.price,
                expires_at=activation.expires_at,
            )
            return
        self.abandon(activation, "paypal_rejected_code")

    def _get_provider_prices(self) -> list[SMSBowerProviderPrice]:
        values = self.client.get_provider_prices(self.service, self.country)
        prices: list[SMSBowerProviderPrice] = []
        for value in values:
            if isinstance(value, SMSBowerProviderPrice):
                prices.append(value)
            elif isinstance(value, dict):
                prices.append(
                    SMSBowerProviderPrice(
                        provider_id=str(value.get("provider_id") or ""),
                        price=_parse_float(value.get("price")),
                        count=_parse_int(value.get("count")),
                    )
                )
        return sorted(
            [item for item in prices if item.provider_id and item.count > 0],
            key=lambda item: (item.price, item.provider_id),
        )

    def _get_number_v2(self, price: SMSBowerProviderPrice) -> dict[str, object]:
        result = self.client.get_number_v2(
            service=self.service,
            country=self.country,
            provider_id=price.provider_id,
            max_price=price.price,
        )
        if isinstance(result, dict):
            return result
        raise SMSBowerApiError(f"Unexpected getNumberV2 response: {result!r}")

    def _get_status(self, activation_id: str) -> str:
        return str(self.client.get_status(activation_id))

    def _set_status(self, activation_id: str, status: int) -> str:
        return str(self.client.set_status(activation_id, status))

    @staticmethod
    def _code_from_status(status: str) -> str:
        if not status.startswith("STATUS_OK:"):
            return ""
        code = status.split(":", 1)[1].strip().strip("'").strip('"')
        match = re.search(r"\d{4,8}", code)
        return match.group(0) if match else code


def smsbower_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    provider = (_load_dotenv_value("PAYPAL_SMS_PROVIDER") or _load_dotenv_value("SMS_PROVIDER")).strip().lower()
    if provider:
        return provider == "smsbower"
    return _env_bool("PAYPAL_SMSBOWER_ENABLED") or _env_bool("SMSBOWER_ENABLED")


def build_smsbower_provider(
    *,
    enabled: bool | None = None,
    api_key: str | None = None,
    country: str = SMSBOWER_DEFAULT_COUNTRY,
    phone_cc: str = "+55",
    max_price: float | None = None,
    service: str = SMSBOWER_DEFAULT_SERVICE,
) -> SMSBowerOtpProvider | None:
    if not smsbower_enabled(enabled):
        return None
    resolved_key = (
        api_key
        or _load_dotenv_value("SMSBOWER_API_KEY")
        or _load_dotenv_value("PAYPAL_SMSBOWER_API_KEY")
    )
    client = SMSBowerClient(resolved_key)
    return SMSBowerOtpProvider(
        client=client,
        country=country,
        phone_cc=phone_cc,
        max_price=max_price,
        service=service,
        wait_seconds=_env_float("SMSBOWER_WAIT_SECONDS", SMSBOWER_DEFAULT_WAIT_SECONDS, 1.0, 300.0),
        poll_interval_seconds=_env_float(
            "SMSBOWER_POLL_INTERVAL_SECONDS",
            SMSBOWER_DEFAULT_POLL_INTERVAL_SECONDS,
            0.2,
            30.0,
        ),
        max_channel_failures=_env_int(
            "SMSBOWER_MAX_CHANNEL_FAILURES",
            SMSBOWER_DEFAULT_MAX_CHANNEL_FAILURES,
            1,
            20,
        ),
        activation_ttl_seconds=_env_int(
            "SMSBOWER_ACTIVATION_TTL_SECONDS",
            SMSBOWER_DEFAULT_ACTIVATION_TTL_SECONDS,
            60,
            24 * 60 * 60,
        ),
        max_attempts=_env_int("SMSBOWER_MAX_ATTEMPTS", SMSBOWER_DEFAULT_MAX_ATTEMPTS, 1, 100),
    )


# ---- 报价查询 (只读, TTL 缓存; 批量同国只打一次 smsbower) ----

QUOTE_TTL_SECONDS = 60.0
_QUOTE_LOCK = threading.Lock()
_quote_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
_SERVICE_FALLBACKS = ("ts", "pp")
_SUPPORTED_SERVICES: set[str] = set(_SERVICE_FALLBACKS)


def _quote_key(sms_country_id: str, service: str) -> str:
    return f"{sms_country_id}|{service.lower()}"


def _resolve_sms_country_id(country: str) -> str:
    from paypal.country_profile import smsbower_country_id

    return smsbower_country_id(country)


def build_provider_for_quote(country: str, service: str | None = None) -> list[dict[str, object]]:
    """返回某国最低价可用供应商列表 (quote API 用途, 只读不激活)。"""

    sms_id = _resolve_sms_country_id(country)
    services = [service] if service and service.lower() in _SUPPORTED_SERVICES else list(_SERVICE_FALLBACKS)

    resolved_key = (
        _load_dotenv_value("SMSBOWER_API_KEY")
        or _load_dotenv_value("PAYPAL_SMSBOWER_API_KEY")
    )
    if not resolved_key:
        return []

    with _QUOTE_LOCK:
        key = _quote_key(sms_id, services[0])
        hit = _quote_cache.get(key)
        if hit and hit[0] > time.time():
            return list(hit[1])

    client = SMSBowerClient(resolved_key)
    quotes: list[dict[str, object]] = []
    last_error: Exception | None = None
    for svc in services:
        try:
            prices = client.get_provider_prices(svc, sms_id)
        except Exception as exc:
            last_error = exc
            continue
        for price in prices:
            quotes.append({
                "provider_id": price.provider_id,
                "price": price.price,
                "count": price.count,
                "currency": "USD",
                "service": svc,
            })
        if quotes:
            break
    quotes.sort(key=lambda item: (float(item.get("price") or 0), str(item.get("provider_id") or "")))
    with _QUOTE_LOCK:
        _quote_cache[key] = (time.time() + QUOTE_TTL_SECONDS, list(quotes))
    if not quotes and last_error is not None:
        logger.warning("sms quote failed for country={} sms_id={}: {}", country, sms_id, last_error)
    return quotes


def activation_to_public_dict(activation: SMSBowerActivation) -> dict[str, object]:
    payload = asdict(activation)
    payload["phone_number"] = "*" * max(0, len(activation.phone_number) - 4) + activation.phone_number[-4:]
    return payload
