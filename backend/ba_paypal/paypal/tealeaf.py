"""Tealeaf session replay simulation.

Tealeaf (IBM) captures mouse movements, clicks, keyboard events,
DOM snapshots, and sends them gzip-compressed to /platform/tealeaftarget.

Config from patlcfg.js:
- AppKey: 76938917d7504ff7a962174c021690bd
- Queue: maxEvents=30, timerInterval=20s, maxSize=30KB, encoder=gzip
- Mousemove: sampleRate=200ms, ignoreRadius=3px
- DOM capture: diffEnabled, triggered on click/change/load
"""
import gzip
import json
import time
import random
import uuid
from typing import Any, Optional
from loguru import logger
from config import TEALEAF_APP_KEY, VIEWPORT, SCREEN, BROWSER_PROFILE


def _viewport_for_session(session: Any | None) -> dict[str, int]:
    state = getattr(session, "state", None)
    value = getattr(state, "viewport", None) if state else None
    return value or VIEWPORT


def _screen_for_session(session: Any | None) -> dict[str, int]:
    state = getattr(session, "state", None)
    value = getattr(state, "screen", None) if state else None
    return value or SCREEN


def _profile_for_session(session: Any | None) -> dict[str, Any]:
    state = getattr(session, "state", None)
    value = getattr(state, "browser_profile", None) if state else None
    return value or BROWSER_PROFILE


def _next_tealeaf_serial(session: Any | None) -> int:
    state = getattr(session, "state", None)
    if not state:
        return random.randint(1, 999)
    state.tealeaf_serial_number = int(getattr(state, "tealeaf_serial_number", 0) or 0) + 1
    return state.tealeaf_serial_number


def _tealeaf_envelope(
    session: Any | None,
    page_url: str,
    messages: list[dict[str, Any]],
    *,
    referrer: str = "",
) -> dict[str, Any]:
    """Wrap events in the UIC session envelope used by PayPal Tealeaf."""
    state = getattr(session, "state", None)
    viewport = _viewport_for_session(session)
    screen = _screen_for_session(session)
    profile = _profile_for_session(session)
    page_id = getattr(state, "tealeaf_page_id", "") if state else ""
    tab_id = getattr(state, "tealeaf_tab_id", "") if state else ""
    start_time = int(getattr(state, "tealeaf_start_time_ms", 0) or (int(time.time() * 1000) - random.randint(1000, 5000)))
    serial = _next_tealeaf_serial(session)
    timezone_offset = int(profile.get("timezone_offset_minutes", 0))
    return {
        "messageVersion": "13.0.0",
        "serialNumber": serial,
        "sessions": [
            {
                "id": page_id or f"P.{uuid.uuid4().hex[:24].upper()}",
                "tabId": tab_id or f"Y{random.randint(100, 999)}",
                "startTime": start_time,
                "timezoneOffset": timezone_offset,
                "messages": messages,
                "clientEnvironment": {
                    "webEnvironment": {
                        "libVersion": "6.4.177",
                        "buildNote": "TealeafConnect-6.4.177-PayPal",
                        "domain": "www.paypal.com",
                        "page": page_url,
                        "referrer": referrer or page_url,
                        "mouseMovement": True,
                        "screen": {
                            "devicePixelRatio": profile.get("device_pixel_ratio", 1),
                            "deviceWidth": screen.get("width", 0),
                            "deviceHeight": screen.get("height", 0),
                            "deviceToolbarHeight": 0,
                            "width": viewport.get("width", 0),
                            "height": viewport.get("height", 0),
                            "orientation": 0,
                            "orientationMode": "LANDSCAPE" if viewport.get("width", 0) >= viewport.get("height", 0) else "PORTRAIT",
                        },
                    }
                },
            }
        ],
        "log": {"requests": [{"serialNumber": serial, "reqStart": messages[0].get("offset", 0) if messages else 0}]},
    }


class TealeafSession:
    """Queue and flush browser-like Tealeaf messages for one page."""

    def __init__(self, session: Any, page_url: str):
        self.session: Any = session
        self.page_url: str = page_url
        self.offset_ms: int = 0
        self._queue: list[dict[str, Any]] = []

    def _next_gap(self, minimum_ms: int = 80, maximum_ms: int = 250) -> int:
        return random.randint(minimum_ms, maximum_ms)

    def _queue_event(
        self,
        event_type: int,
        event_key: str,
        event_body: dict[str, Any],
        advance_ms: int | None = None,
    ) -> None:
        message = {
            "type": event_type,
            "offset": self.offset_ms,
            "screenviewOffset": 0,
            event_key: event_body,
        }
        self._queue.append(message)
        self.offset_ms += advance_ms if advance_ms is not None else self._next_gap()

    def _queue_focus(self, field_id: str, advance_ms: int | None = None) -> None:
        self._queue_event(
            15,
            "focus",
            {"target": {"id": field_id, "type": "INPUT"}},
            advance_ms,
        )

    def _queue_blur(self, field_id: str, advance_ms: int | None = None) -> None:
        self._queue_event(
            16,
            "blur",
            {"target": {"id": field_id, "type": "INPUT"}},
            advance_ms,
        )

    def _queue_input(
        self,
        field_id: str,
        value_length: int,
        advance_ms: int | None = None,
    ) -> None:
        self._queue_event(
            17,
            "change",
            {
                "target": {"id": field_id, "type": "INPUT"},
                "currentValue": {"length": value_length, "masked": True},
            },
            advance_ms,
        )

    def _field_value_length(self, field_id: str) -> int:
        lowered = field_id.lower()
        if "email" in lowered:
            return random.randint(16, 28)
        if "password" in lowered:
            return random.randint(10, 18)
        if "phone" in lowered or "mobile" in lowered:
            return random.randint(10, 11)
        if "card" in lowered or "number" in lowered:
            return 16
        if "cvv" in lowered or "csc" in lowered or "security" in lowered:
            return random.randint(3, 4)
        if "zip" in lowered or "postal" in lowered:
            return random.randint(8, 9)
        return random.randint(5, 16)

    def _tealeaf_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Tealeaf-SaaS-AppKey": TEALEAF_APP_KEY,
            "Origin": "https://www.paypal.com",
            "Referer": self.page_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.session.state.tltsid:
            headers["X-Tealeaf-SaaS-TLTSID"] = self.session.state.tltsid
        if self.session.state.tltdid:
            headers["X-Tealeaf-TLTDID"] = self.session.state.tltdid
        return headers

    def _build_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return _tealeaf_envelope(self.session, self.page_url, messages)

    def record_focus(self, field_id: str) -> None:
        self._queue_focus(field_id)

    def record_blur(self, field_id: str) -> None:
        self._queue_blur(field_id)

    def record_input(self, field_id: str, value_length: int) -> None:
        self._queue_input(field_id, value_length)

    def record_click(self, x: int, y: int, target_id: str) -> None:
        self._queue_event(
            4,
            "click",
            {
                "position": {"x": x, "y": y},
                "target": {"id": target_id, "type": "BUTTON"},
            },
        )

    def record_scroll(self, scroll_top: int) -> None:
        viewport = _viewport_for_session(self.session)
        self._queue_event(
            10,
            "scroll",
            {
                "position": {"x": 0, "y": scroll_top},
                "viewport": {
                    "width": viewport["width"],
                    "height": viewport["height"],
                },
            },
        )

    def flush(self) -> Any | None:
        if not self._queue:
            return None

        messages = list(self._queue)
        payload = self._build_payload(messages)
        payload_json = json.dumps(payload).encode("utf-8")
        compressed = gzip.compress(payload_json)

        try:
            logger.info(f"Sending {len(messages)} Tealeaf queued messages...")
            return self.session.post(
                "https://www.paypal.com/platform/tealeaftarget",
                content=compressed,
                headers=self._tealeaf_headers(),
            )
        except Exception as e:
            logger.warning(f"Tealeaf session flush failed: {e}")
            return None
        finally:
            self._queue.clear()

    def send_form_interaction_batch(
        self,
        field_ids: list[str] | tuple[str, ...],
    ) -> Any | None:
        field_ids = list(field_ids)
        for index, field_id in enumerate(field_ids):
            self._queue_focus(field_id, self._next_gap(80, 180))
            self._queue_input(
                field_id,
                self._field_value_length(field_id),
                self._next_gap(120, 350),
            )
            if index < len(field_ids) - 1:
                field_gap = self._next_gap(200, 800)
            else:
                field_gap = self._next_gap(100, 250)
            self._queue_blur(field_id, field_gap)
        return self.flush()

    def send_page_view(self, page_url: str, referrer: str = "") -> Any | None:
        self.page_url = page_url
        now_ms = int(time.time() * 1000)
        load_event_end = now_ms - random.randint(200, 800)
        dom_loaded = load_event_end - random.randint(400, 1200)
        navigation_start = dom_loaded - random.randint(800, 2500)

        self._queue_event(
            2,
            "screenview",
            {
                "type": "LOAD",
                "name": page_url,
                "url": page_url,
                "host": "www.paypal.com",
                "referrer": referrer,
            },
            self._next_gap(20, 80),
        )
        self._queue_event(
            6,
            "performance",
            {
                "timing": {
                    "navigationStart": navigation_start,
                    "domContentLoadedEventEnd": dom_loaded,
                    "loadEventEnd": load_event_end,
                },
            },
            self._next_gap(100, 250),
        )
        return self.flush()


def generate_mouse_path(start_x: int, start_y: int, end_x: int, end_y: int,
                        steps: int = 10, interval_ms: int = 200) -> dict:
    """Generate a realistic mouse movement path between two points."""
    dx = []
    dy = []
    ts = []
    for i in range(steps):
        progress = i / (steps - 1) if steps > 1 else 1
        # add slight randomness for natural movement
        jitter_x = random.randint(-3, 3)
        jitter_y = random.randint(-3, 3)
        x = int(start_x + (end_x - start_x) * progress + jitter_x)
        y = int(start_y + (end_y - start_y) * progress + jitter_y)
        dx.append(x)
        dy.append(y)
        ts.append(i * interval_ms + random.randint(0, 50))
    return {"dx": dx, "dy": dy, "ts": ts}


def build_tealeaf_payload(page_url: str, offset_ms: int = 0,
                          mouse_data: Optional[dict] = None,
                          dom_html: Optional[str] = None,
                          session: Any | None = None) -> dict:
    """Build a Tealeaf message batch."""
    messages = []

    # Performance message (type 6)
    messages.append({
        "type": 6,
        "offset": 0,
        "screenviewOffset": 0,
        "performance": {
            "timing": {
                "navigationStart": int(time.time() * 1000) - 5000,
                "domContentLoadedEventEnd": int(time.time() * 1000) - 3000,
                "loadEventEnd": int(time.time() * 1000) - 2000,
            },
        },
    })

    # Screenview message (type 2)
    messages.append({
        "type": 2,
        "offset": offset_ms,
        "screenviewOffset": 0,
        "screenview": {
            "type": "LOAD",
            "name": page_url,
            "url": page_url,
            "host": "www.paypal.com",
            "referrer": "",
        },
    })

    # DOM capture (type 12) - full or diff
    if dom_html:
        messages.append({
            "type": 12,
            "offset": offset_ms + 500,
            "screenviewOffset": 0,
            "domCapture": {
                "dcid": str(uuid.uuid4()),
                "fullDOM": True,
                "root": dom_html[:50000],  # cap at 50KB
                "mutationCount": 0,
            },
        })

    # Mouse movement (type 11)
    if mouse_data:
        messages.append({
            "type": 11,
            "offset": offset_ms + 1000,
            "screenviewOffset": 0,
            "mouseMove": mouse_data,
        })

    return _tealeaf_envelope(session, page_url, messages)


def send_tealeaf_data(session, page_url: str, dom_html: Optional[str] = None,
                      mouse_data: Optional[dict] = None, offset_ms: int = 0,
                      endpoint_url: Optional[str] = None):
    """Send Tealeaf session data to PayPal."""
    if not mouse_data:
        # generate default mouse movement (simulate user scrolling/moving)
        mouse_data = generate_mouse_path(
            random.randint(100, 500), random.randint(100, 400),
            random.randint(300, 800), random.randint(200, 600),
        )

    payload = build_tealeaf_payload(page_url, offset_ms, mouse_data, dom_html, session=session)
    payload_json = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(payload_json)

    headers = {
        "Content-Type": "application/json",
        "Content-Encoding": "gzip",
        "X-Tealeaf-SaaS-AppKey": TEALEAF_APP_KEY,
        "Origin": "https://www.paypal.com",
        "Referer": page_url,
        "X-Requested-With": "XMLHttpRequest",
    }
    if session.state.tltsid:
        headers["X-Tealeaf-SaaS-TLTSID"] = session.state.tltsid
    if session.state.tltdid:
        headers["X-Tealeaf-TLTDID"] = session.state.tltdid

    try:
        logger.info("Sending Tealeaf session data...")
        session.post(
            endpoint_url or "https://www.paypal.com/platform/tealeaftarget",
            content=compressed,
            headers=headers,
        )
    except Exception as e:
        logger.warning(f"Tealeaf send failed: {e}")
