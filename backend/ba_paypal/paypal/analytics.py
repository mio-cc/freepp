"""PayPal Analytics, XO Logger, and Datadog RUM stubs.

These systems send telemetry/tracking data alongside the main flow.
They are not strictly required for the protocol but help avoid detection.
"""
import time
import uuid
import random
import json
import urllib.parse
from loguru import logger
from config import USER_AGENT, SCREEN, VIEWPORT, BROWSER_PROFILE


def _state(session):
    return getattr(session, "state", None)


def _profile(session) -> dict[str, object]:
    state = _state(session)
    return dict((getattr(state, "browser_profile", None) if state else None) or BROWSER_PROFILE)


def _screen(session) -> dict[str, object]:
    state = _state(session)
    return dict((getattr(state, "screen", None) if state else None) or SCREEN)


def _viewport(session) -> dict[str, object]:
    state = _state(session)
    return dict((getattr(state, "viewport", None) if state else None) or VIEWPORT)


def _stable_uuid_attr(session, attr: str, hex_value: bool = False) -> str:
    state = _state(session)
    if not state:
        return uuid.uuid4().hex if hex_value else str(uuid.uuid4())
    value = getattr(state, attr, "")
    if not value:
        value = uuid.uuid4().hex if hex_value else str(uuid.uuid4())
        setattr(state, attr, value)
    return value


def _datadog_view_id(session, service: str, page_url: str) -> str:
    state = _state(session)
    if not state:
        return str(uuid.uuid4())
    if not getattr(state, "datadog_view_ids", None):
        state.datadog_view_ids = {}
    # Keep one stable view per service/path in the local protocol session.
    key = f"{service}:{page_url.split('?', 1)[0]}"
    if key not in state.datadog_view_ids:
        state.datadog_view_ids[key] = str(uuid.uuid4())
    return state.datadog_view_ids[key]


def send_xo_logger(session, event_data: dict[str, object]):
    """Send client event log to PayPal XO Logger."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "x-app-name": "checkoutuinodeweb",
        "Origin": "https://www.paypal.com",
        "Referer": getattr(getattr(session, "state", None), "signup_url", "")
        or f"https://www.paypal.com/pay?token={getattr(getattr(session, 'state', None), 'ba_token', '')}&ul=1",
    }
    try:
        session.post(
            "https://www.paypal.com/xoplatform/logger/api/logger/",
            json=event_data,
            headers=headers,
        )
    except Exception as e:
        logger.warning(f"XO Logger failed: {e}")


def send_analytics_ts(session, page_name: str, ba_token: str,
                      ec_token: str = "", user_id: str = "",
                      event: str = "im"):
    """Send PayPal Analytics tracking pixel (t.paypal.com/ts)."""
    ts = int(time.time() * 1000)
    state = _state(session)
    profile = _profile(session)
    screen = _screen(session)
    viewport = _viewport(session)
    pxp_guid = getattr(state, "pxp_guid", "") if state else ""
    if state and not pxp_guid:
        state.pxp_guid = uuid.uuid4().hex
        pxp_guid = state.pxp_guid
    page_start = getattr(state, "page_start_time_ms", 0) if state else 0
    if state and not page_start:
        state.page_start_time_ms = ts - random.randint(2000, 5000)
        page_start = state.page_start_time_ms
    calc = getattr(state, "fpti_calc", "") if state else ""
    if state and not calc:
        state.fpti_calc = uuid.uuid4().hex[:13]
        calc = state.fpti_calc
    params = {
        "v": "1.15.0",
        "t": str(ts),
        # PayPal FPTI expects JavaScript Date#getTimezoneOffset() in minutes.
        # For UTC+8 this is -480; for São Paulo UTC-3 this is +180.
        # Keep this aligned with FraudNet p1's tz/tzName.
        "g": str(profile["timezone_offset_minutes"]),
        "pgrp": "main:billing:hagrid",
        "page": page_name,
        "pgtf": "Nodejs",
        "s": "ci",
        "env": "live",
        "comp": "checkoutuinodeweb",
        "tsrce": "checkoutuinodeweb",
        "cu": "1",
        "ef_policy": "ccpa",
        "c_prefs": "T=1,P=1,F=1,type=explicit_banner",
        "pxpguid": pxp_guid or uuid.uuid4().hex,
        "pgst": str(page_start or (ts - random.randint(2000, 5000))),
        "calc": calc or uuid.uuid4().hex[:13],
        "rsta": profile["locale"],
        "ccpg": profile["country"],
        "cnac": profile["country"],
        "flnm": "Hagrid",
        "e": event,
        "fpti_sdk_name": "pa-js",
        "cd": str(screen["colorDepth"]),
        "sw": str(screen["width"]),
        "sh": str(screen["height"]),
        "bw": str(viewport["width"]),
        "bh": str(viewport["height"]),
        "ce": "1",
    }
    if ec_token:
        params["fltk"] = ec_token
    if user_id:
        params["cust"] = user_id
        params["party_id"] = user_id
        params["acnt"] = "personal"
        params["aver"] = "unverified"
        params["rstr"] = "unrestricted"

    try:
        session.get("https://t.paypal.com/ts", params=params)
    except Exception as e:
        logger.warning(f"Analytics ts failed: {e}")


def send_observability_emit(session, ba_token: str):
    """Send observability emit (trpc endpoint)."""
    state = getattr(session, "state", None)
    referer = getattr(state, "signup_url", "") or f"https://www.paypal.com/pay?token={ba_token}&ul=1"
    try:
        session.post(
            f"https://www.paypal.com/pay/api/trpc/observability.handleClientEmit?token={ba_token}",
            content=b"",
            headers={
                "Content-Type": "application/json",
                "Origin": "https://www.paypal.com",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except Exception as e:
        logger.warning(f"Observability emit failed: {e}")


def send_weasley_log(session, ec_token: str, signup_url: str, event_names: list[str],
                      country: str = "BR", lang: str = "pt",
                      extra_payload: dict[str, object] | None = None):
    """Send checkoutweb/weasley client logger events in browser-like order."""
    if not ec_token or not event_names:
        return

    now = int(time.time() * 1000)
    locale = f"{lang}_{country}"
    events = []
    for i, name in enumerate(event_names):
        payload: dict[str, object] = {
            "clientCountry": country,
            "clientLocale": locale,
            "clientTimestamp": now + i,
            "timestamp": str(now + i),
            "token": ec_token,
        }
        if extra_payload:
            payload.update(extra_payload)
        events.append({"level": "info", "event": name, "payload": payload})

    body = {
        "events": events,
        "meta": {
            "integrationData": {
                "contextId": ec_token,
                "contextType": ec_token,
                "integrationMethod": "FULLPAGE",
                "integrationType": "EC",
            }
        },
        "tracking": [],
        "metrics": [],
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.paypal.com",
        "Referer": signup_url,
        "X-Requested-With": "XMLHttpRequest",
        "X-App-Name": "checkoutuinodeweb_weasley",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    try:
        session.post(
            "https://www.paypal.com/xoplatform/logger/api/logger/",
            json=body,
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Weasley logger failed: {e}")


_DD_MODXO_CONFIG: dict[str, object] = {
    "client_token": "pub09bb4929a2fe5661ad79710dbf90a55a",
    "app_id": "e04156c3-60a0-43e6-93fc-69f3b371849d",
    "service": "modxo",
    "sdk_version": "6.33.0",
    "version": "modularcheckoutnodeweb-0.506.0_2026070118325682",
    "session_replay_sample_rate": 100,
}
_DD_WEASLEY_CONFIG: dict[str, object] = {
    "client_token": "pub415c6a9024efa76be0dc3ee2e2099763",
    "app_id": "223dbba5-b459-4d87-80e7-b063c4436787",
    "service": "weasley(checkoutuinodeweb)",
    "sdk_version": "5.35.1",
    "version": "ebcfab6",
    "release_date": "6/26",
    "session_replay_sample_rate": 0,
}
_DD_HAGRID_CONFIG: dict[str, object] = {
    "client_token": "pub0d65d12a15f063f50a51b21d246ce62c",
    "app_id": "bb542bc3-8372-49a6-8db7-725f60a5cc7a",
    "service": "hagrid",
    "sdk_version": "5.35.1",
    "version": "",
}
_DD_AUTHCHALLENGE_CONFIG: dict[str, object] = {
    "client_token": "pubbc14edcb954efe6e30dbd32fac3e7fd7",
    "app_id": "094d6ec4-6434-4564-a7aa-7283dd0d40f1",
    "service": "authchallengenodeweb",
    "sdk_version": "6.31.0",
    "version": "",
    "session_replay_sample_rate": 5,
}


def _query_value(url: str, name: str) -> str:
    query = urllib.parse.urlsplit(url).query
    for key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        if key == name:
            return value
    return ""


def _datadog_tags(dd_config: dict[str, object], api: str = "") -> str:
    tags = [
        f"sdk_version:{dd_config['sdk_version']}",
        f"service:{dd_config['service']}",
    ]
    if api:
        tags.insert(1, f"api:{api}")
    version = str(dd_config.get("version", ""))
    if version:
        tags.append(f"version:{version}")
    return ",".join(tags)


def _datadog_body_tags(dd_config: dict[str, object]) -> str:
    if str(dd_config["service"]) in {"modxo", "authchallengenodeweb"}:
        return _datadog_tags(dd_config)
    return ""


def _datadog_query_tags(dd_config: dict[str, object], api: str) -> str:
    if str(dd_config["service"]) in {"modxo", "authchallengenodeweb"}:
        return ""
    return _datadog_tags(dd_config, api)


def _datadog_uses_api_param(dd_config: dict[str, object]) -> bool:
    return str(dd_config["service"]) in {"modxo", "authchallengenodeweb"}


def _datadog_referer(session, referrer: str = "") -> str:
    state = _state(session)
    return (
        referrer
        or getattr(state, "datadog_referrer", "")
        or getattr(state, "modxo_pay_page_url", "")
        or "https://www.paypal.com/"
    )


def _datadog_headers(session, referrer: str) -> dict[str, str]:
    profile = _profile(session)
    chrome_major = str(profile.get("chrome_major", 150))
    user_agent = str(profile.get("user_agent", USER_AGENT))
    return {
        "Accept": "*/*",
        "Content-Type": "text/plain;charset=UTF-8",
        "Origin": "https://www.paypal.com",
        "Referer": referrer,
        "User-Agent": user_agent,
        "sec-ch-ua": f'"Not;A=Brand";v="8", "Chromium";v="{chrome_major}", "Google Chrome";v="{chrome_major}"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": str(profile.get("sec_ch_platform", '"Linux"')),
    }


def _datadog_configuration(dd_config: dict[str, object]) -> dict[str, object]:
    replay_rate = dd_config.get("session_replay_sample_rate", 0)
    return {
        "session_sample_rate": 100,
        "session_replay_sample_rate": replay_rate if isinstance(replay_rate, int) else int(str(replay_rate)),
        "profiling_sample_rate": 0,
        "trace_sample_rate": 100,
        "beta_encode_cookie_options": False,
    }


def _datadog_replay_sample_rate(dd_config: dict[str, object]) -> int:
    replay_rate = dd_config.get("session_replay_sample_rate", 0)
    return replay_rate if isinstance(replay_rate, int) else int(str(replay_rate))


def _datadog_sdk_major(dd_config: dict[str, object]) -> int:
    return int(str(dd_config["sdk_version"]).split(".", 1)[0])


def _datadog_sampled_for_replay(dd_config: dict[str, object]) -> bool:
    return _datadog_replay_sample_rate(dd_config) >= 100


def _datadog_start_session_replay_recording_manually(dd_config: dict[str, object]) -> bool:
    return _datadog_sdk_major(dd_config) < 6


def _datadog_device(session) -> dict[str, object]:
    profile = _profile(session)
    locale = str(profile.get("locale", "pt_BR"))
    return {
        "locale": locale,
        "locales": [locale.replace("_", "-")],
        "time_zone": str(profile.get("timezone", "America/Sao_Paulo")),
    }


def _datadog_usr(session) -> dict[str, object]:
    return {"anonymous_id": _stable_uuid_attr(session, "datadog_anonymous_id")}


def _datadog_display(session) -> dict[str, object]:
    viewport = _viewport(session)
    return {"viewport": {"width": viewport["width"], "height": viewport["height"]}}


def _datadog_scroll(session) -> dict[str, object]:
    viewport = _viewport(session)
    height = int(str(viewport["height"]))
    return {
        "max_depth": height,
        "max_depth_scroll_top": 0,
        "max_scroll_height": height,
        "max_scroll_height_time": 0,
    }


def _datadog_connectivity(session) -> dict[str, object]:
    profile = _profile(session)
    return {
        "status": "connected",
        "interfaces": ["unknown"],
        "effective_type": profile.get("connection_effective_type", "unknown"),
    }


def _datadog_feature_flags(service: str) -> dict[str, object]:
    if service == "hagrid":
        return {}
    if service == "authchallengenodeweb":
        return {}
    if service == "modxo":
        return {
            "datadog-browser-sdk-v7-enabled": False,
            "routing-in-modxo-enabled": False,
            "pay-token-path-rewrite-enabled": False,
        }
    return {
        "datadog-browser-sdk-v7-enabled": True,
        "weasley_applePayButtonShown": False,
        "weasley_blockNonDomesticPayers": False,
    }


def _datadog_context(session, page_url: str, dd_config: dict[str, object]) -> dict[str, object]:
    state = _state(session)
    profile = _profile(session)
    context = {
        "token": getattr(state, "ec_token", "") or _query_value(page_url, "token"),
        "ba_token": getattr(state, "ba_token", "") or _query_value(page_url, "ba_token"),
        "country": profile.get("country", "BR"),
        "locale": profile.get("locale", "pt_BR"),
        "source": "paypal-checkout",
    }
    if str(dd_config["service"]) == "weasley(checkoutuinodeweb)":
        context.update(
            {
                "is_basl": False,
                "weasley_init_corr_id": context["token"],
                "weasley_release_hash": str(dd_config.get("version", "")),
                "weasley_release_date": str(dd_config.get("release_date", "")),
            }
        )
    return context


def _datadog_tab_id(session) -> str:
    return _stable_uuid_attr(session, "datadog_tab_id")


def _datadog_loading_type(session, service: str, page_url: str) -> str:
    state = _state(session)
    if not state:
        return "initial_load"
    if not getattr(state, "datadog_seen_view_keys", None):
        state.datadog_seen_view_keys = set()
    key = f"{service}:{page_url.split('?', 1)[0]}"
    if key in state.datadog_seen_view_keys:
        return "route_change"
    state.datadog_seen_view_keys.add(key)
    return "initial_load"


def _datadog_params(dd_config: dict[str, object], api: str, now: int) -> dict[str, str]:
    sdk_version = str(dd_config["sdk_version"])
    params = {
        "ddsource": "browser",
        "dd-api-key": str(dd_config["client_token"]),
        "dd-evp-origin": "browser",
        "dd-evp-origin-version": sdk_version,
        "dd-request-id": str(uuid.uuid4()),
        "batch_time": str(now),
    }
    query_tags = _datadog_query_tags(dd_config, api)
    if query_tags:
        params["ddtags"] = query_tags
    if _datadog_uses_api_param(dd_config):
        params["_dd.api"] = api
    return params


def _datadog_event_base(
    session,
    dd_config: dict[str, object],
    page_url: str,
    now: int,
) -> dict[str, object]:
    service = str(dd_config["service"])
    event: dict[str, object] = {
        "date": now,
        "service": service,
        "source": "browser",
        "application": {"id": str(dd_config["app_id"])},
        "session": {"id": _stable_uuid_attr(session, "datadog_session_id", hex_value=True), "type": "user"},
        "tab": {"id": _datadog_tab_id(session)},
        "display": _datadog_display(session),
        "connectivity": _datadog_connectivity(session),
        "context": _datadog_context(session, page_url, dd_config),
        "privacy": {"replay_level": "mask"},
        "_dd": {
            "format_version": 2,
            "drift": 0,
            "configuration": _datadog_configuration(dd_config),
            "sdk_name": "rum",
            "discarded": False,
        },
    }
    if service in {"modxo", "authchallengenodeweb"}:
        event["device"] = _datadog_device(session)
        event["usr"] = _datadog_usr(session)
    version = str(dd_config.get("version", ""))
    if version:
        event["version"] = version
    feature_flags = _datadog_feature_flags(service)
    if feature_flags:
        event["feature_flags"] = feature_flags
    return event


def _datadog_apply_body_tags(event: dict[str, object], dd_config: dict[str, object]) -> None:
    body_tags = _datadog_body_tags(dd_config)
    if body_tags:
        event["ddtags"] = body_tags


def _datadog_apply_view_state(event: dict[str, object], session, dd_config: dict[str, object]) -> None:
    session_context = event["session"]
    if isinstance(session_context, dict):
        session_context["sampled_for_replay"] = _datadog_sampled_for_replay(dd_config)
        if _datadog_sampled_for_replay(dd_config):
            session_context["has_replay"] = True
    dd_context = event["_dd"]
    if isinstance(dd_context, dict):
        configuration = dd_context["configuration"]
        if isinstance(configuration, dict):
            configuration["start_session_replay_recording_manually"] = (
                _datadog_start_session_replay_recording_manually(dd_config)
            )
        dd_context["document_version"] = 1
        dd_context["page_states"] = [{"start": 0, "state": "active"}]
        if _datadog_sampled_for_replay(dd_config):
            dd_context["replay_stats"] = {
                "records_count": 4,
                "segments_count": 1,
                "segments_total_raw_size": 0,
            }
    display = event["display"]
    if isinstance(display, dict):
        display["scroll"] = _datadog_scroll(session)


def _datadog_apply_has_replay(event: dict[str, object], dd_config: dict[str, object]) -> None:
    if not _datadog_sampled_for_replay(dd_config):
        return
    session_context = event["session"]
    if isinstance(session_context, dict):
        session_context["has_replay"] = True


def _datadog_resource_event(
    session,
    dd_config: dict[str, object],
    page_url: str,
    now: int,
    dd_view_id: str,
    referer: str,
    api: str,
    action_ids: list[str] | None = None,
) -> dict[str, object]:
    event = _datadog_event_base(session, dd_config, page_url, now)
    _datadog_apply_body_tags(event, dd_config)
    event["type"] = "resource"
    event["view"] = {"id": dd_view_id, "url": page_url, "referrer": referer}
    event["action"] = {"id": action_ids or []}
    duration = random.randint(35_000_000, 280_000_000)
    dns_start = random.randint(1_000_000, 8_000_000)
    dns_duration = random.randint(1_000_000, 30_000_000)
    connect_start = dns_start + dns_duration
    connect_duration = random.randint(20_000_000, 80_000_000)
    ssl_start = connect_start + random.randint(1_000_000, 5_000_000)
    ssl_duration = max(0, connect_duration - (ssl_start - connect_start))
    first_byte_start = connect_start + connect_duration
    if first_byte_start >= duration:
        duration = first_byte_start + random.randint(50_000_000, 180_000_000)
    first_byte_duration = random.randint(10_000_000, max(10_000_001, duration // 2))
    download_start = min(duration, first_byte_start + first_byte_duration)
    download_duration = max(0, duration - download_start)
    encoded_size = random.randint(3000, 90000)
    event["resource"] = {
        "id": str(uuid.uuid4()),
        "type": api,
        "method": "GET",
        "url": page_url,
        "status_code": 200,
        "duration": duration,
        "dns": {"start": dns_start, "duration": dns_duration},
        "connect": {"start": connect_start, "duration": connect_duration},
        "ssl": {"start": ssl_start, "duration": ssl_duration},
        "redirect": {"start": 0, "duration": 0},
        "first_byte": {"start": first_byte_start, "duration": first_byte_duration},
        "download": {"start": download_start, "duration": download_duration},
        "size": encoded_size,
        "encoded_body_size": encoded_size,
        "decoded_body_size": encoded_size,
        "transfer_size": encoded_size + random.randint(300, 900),
        "delivery_type": "cache",
        "protocol": "h2",
        "render_blocking_status": "non_blocking",
    }
    return event


def _datadog_long_task_event(
    session,
    dd_config: dict[str, object],
    page_url: str,
    now: int,
    dd_view_id: str,
    referer: str,
    action_ids: list[str] | None = None,
) -> dict[str, object]:
    event = _datadog_event_base(session, dd_config, page_url, now)
    _datadog_apply_body_tags(event, dd_config)
    event["type"] = "long_task"
    event["view"] = {"id": dd_view_id, "url": page_url, "referrer": referer}
    event["action"] = {"id": action_ids or []}
    duration = random.randint(50_000_000, 180_000_000)
    event["long_task"] = {
        "id": str(uuid.uuid4()),
        "duration": duration,
        "entry_type": "long-animation-frame" if _datadog_sdk_major(dd_config) >= 6 else "long-task",
    }
    if _datadog_sdk_major(dd_config) >= 6:
        duration = random.randint(1_000_000_000, 2_000_000_000)
        start_time = random.randint(0, 10_000_000)
        render_start = start_time + max(0, duration - random.randint(15_000_000, 60_000_000))
        style_start = render_start + random.randint(1_000_000, 10_000_000)
        script_duration = max(0, min(duration, render_start - start_time))
        event["long_task"] = {
            "id": str(uuid.uuid4()),
            "duration": duration,
            "entry_type": "long-animation-frame",
            "start_time": start_time,
            "blocking_duration": random.randint(0, 15_000_000),
            "render_start": render_start,
            "style_and_layout_start": style_start,
            "first_ui_event_timestamp": 0,
            "scripts": [
                {
                    "duration": script_duration,
                    "execution_start": start_time,
                    "forced_style_and_layout_duration": 0,
                    "invoker": page_url,
                    "invoker_type": "classic-script",
                    "pause_duration": 0,
                    "source_char_position": 0,
                    "source_function_name": "",
                    "source_url": page_url,
                    "start_time": start_time,
                    "window_attribution": "self",
                }
            ],
        }
    _datadog_apply_has_replay(event, dd_config)
    return event


def _datadog_body(events: dict[str, object] | list[dict[str, object]]) -> str:
    batch = [events] if isinstance(events, dict) else events
    return "".join(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n" for event in batch)


def send_datadog_rum_view(
    session,
    page_url: str,
    ba_token: str,
    dd_config: dict[str, object] = _DD_MODXO_CONFIG,
    referrer: str = "",
    api: str = "fetch",
):
    try:
        _ = ba_token
        now = int(time.time() * 1000)
        service = str(dd_config["service"])
        dd_view_id = _datadog_view_id(session, service, page_url)
        referer = _datadog_referer(session, referrer)
        params = _datadog_params(dd_config, api, now)
        headers = _datadog_headers(session, referer)
        event = _datadog_event_base(session, dd_config, page_url, now)
        _datadog_apply_body_tags(event, dd_config)
        _datadog_apply_view_state(event, session, dd_config)
        event["type"] = "view"
        first_byte = random.randint(3_000_000, 10_000_000)
        first_contentful_paint = random.randint(800_000_000, 1_800_000_000)
        largest_contentful_paint = first_contentful_paint + random.randint(0, 250_000_000)
        performance: dict[str, object] = {"cls": {"score": 0}}
        if _datadog_sdk_major(dd_config) >= 6:
            performance["fcp"] = {"timestamp": first_contentful_paint}
            performance["lcp"] = {
                "timestamp": largest_contentful_paint,
                "target_selector": "body",
                "sub_parts": {
                    "load_delay": 0,
                    "load_time": 0,
                    "render_delay": max(0, largest_contentful_paint - first_byte),
                },
            }
        event["view"] = {
            "id": dd_view_id,
            "url": page_url,
            "referrer": referer,
            "in_foreground": True,
            "is_active": True,
            "loading_type": _datadog_loading_type(session, service, page_url),
            "time_spent": random.randint(3000, 15000),
            "loading_time": random.randint(2_000_000, 120_000_000),
            "first_byte": first_byte,
            "first_contentful_paint": first_contentful_paint,
            "largest_contentful_paint": largest_contentful_paint,
            "largest_contentful_paint_target_selector": "body",
            "interaction_to_next_paint": 0,
            "interaction_to_next_paint_time": 0,
            "interaction_to_next_paint_target_selector": "body",
            "cumulative_layout_shift": 0,
            "long_task": {"count": 0},
            "resource": {"count": random.randint(5, 20)},
            "error": {"count": 0},
            "action": {"count": random.randint(1, 5)},
            "frustration": {"count": 0},
            "performance": performance,
            "dom_complete": random.randint(800, 3000),
            "dom_content_loaded": random.randint(500, 2000),
            "dom_interactive": random.randint(400, 1500),
            "load_event": random.randint(1000, 4000),
        }
        events = [
            _datadog_resource_event(session, dd_config, page_url, now - 120, dd_view_id, referer, api),
            _datadog_long_task_event(session, dd_config, page_url, now - 40, dd_view_id, referer),
            event,
        ]
        session.post(
            "https://browser-intake-us5-datadoghq.com/api/v2/rum",
            params=params,
            content=_datadog_body(events),
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Datadog RUM view failed: {e}")


def send_datadog_rum_action(
    session,
    action_name: str,
    page_url: str,
    dd_config: dict[str, object] = _DD_MODXO_CONFIG,
    referrer: str = "",
    api: str = "fetch",
):
    try:
        now = int(time.time() * 1000)
        service = str(dd_config["service"])
        dd_view_id = _datadog_view_id(session, service, page_url)
        referer = _datadog_referer(session, referrer)
        params = _datadog_params(dd_config, api, now)
        headers = _datadog_headers(session, referer)
        event = _datadog_event_base(session, dd_config, page_url, now)
        _datadog_apply_body_tags(event, dd_config)
        _datadog_apply_has_replay(event, dd_config)
        event["type"] = "action"
        action_id = str(uuid.uuid4())
        event["action"] = {
            "id": action_id,
            "type": "custom",
            "target": {"name": action_name},
            "loading_time": random.randint(100, 2000),
            "resource": {"count": 0},
            "error": {"count": 0},
            "long_task": {"count": 0},
            "frustration": {"count": 0, "type": []},
        }
        dd_context = event["_dd"]
        if isinstance(dd_context, dict):
            dd_context["action"] = {
                "target": {
                    "width": 1,
                    "height": 1,
                    "selector": f'[data-datadog-action-name="{action_name}"]',
                },
                "position": {"x": 0, "y": 0},
            }
        event["view"] = {
            "id": dd_view_id,
            "url": page_url,
            "referrer": referer,
            "in_foreground": True,
        }
        events = [
            _datadog_resource_event(session, dd_config, page_url, now - 80, dd_view_id, referer, api, [action_id]),
            event,
        ]
        session.post(
            "https://browser-intake-us5-datadoghq.com/api/v2/rum",
            params=params,
            content=_datadog_body(events),
            headers=headers,
        )
    except Exception as e:
        logger.debug(f"Datadog RUM action failed: {e}")
