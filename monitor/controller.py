#!/usr/bin/env python3
"""Mac Vision controller bookkeeping for the read-only UR monitor.

Two responsibilities, both about the *controller* side rather than the robot:

* ``HeartbeatThrottle`` slows down token guessing per source IP.
* ``ControlClientStore`` remembers the last authenticated heartbeat and turns it
  into the structured status the dashboard shows: frame rate, tracking
  confidence, current gesture and dropped frames, not just a bare string.
"""
from __future__ import annotations

import threading
import time
from typing import Any

# Structured heartbeat fields carried from the Mac Vision controller. Everything
# is optional so an older controller that only sends ``details`` still works.
_STRUCTURED_TEXT_FIELDS = {
    "gesture": 40,
    "tracker": 60,
    "vision_model": 60,
    "mode": 40,
}
_STRUCTURED_NUMBER_FIELDS = {
    "fps": (0.0, 1000.0),
    "confidence": (0.0, 1.0),
    "dropped_frames": (0.0, 1e9),
    "frame_index": (0.0, 1e12),
    "inference_ms": (0.0, 60000.0),
}


class HeartbeatThrottle:
    """Per-source-IP failure counter that slows down token brute forcing.

    A correct token always clears the counter. Repeated failures lock the source
    out for a configurable window, so an offline controller that simply cannot
    reach the monitor is not affected by other clients' failures.
    """

    def __init__(self, max_failures: int, lockout_seconds: float) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}
        self._max_failures = max(1, int(max_failures))
        self._lockout_seconds = max(0.0, float(lockout_seconds))

    def configure(self, max_failures: int, lockout_seconds: float) -> None:
        with self._lock:
            self._max_failures = max(1, int(max_failures))
            self._lockout_seconds = max(0.0, float(lockout_seconds))

    def retry_after(self, source_ip: str) -> float:
        """Seconds the caller must wait, or 0.0 when the source is not locked out."""
        now = time.monotonic()
        with self._lock:
            entry = self._failures.get(source_ip)
            if entry is None:
                return 0.0
            failures, locked_until = entry
            if self._lockout_seconds <= 0 or failures < self._max_failures:
                return 0.0
            remaining = locked_until - now
            if remaining <= 0:
                self._failures.pop(source_ip, None)
                return 0.0
            return remaining

    def record_failure(self, source_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            failures = self._failures.get(source_ip, (0, 0.0))[0] + 1
            self._failures[source_ip] = (failures, now + self._lockout_seconds)
            if len(self._failures) > 1024:
                self._prune_locked(now)

    def clear(self, source_ip: str) -> None:
        with self._lock:
            self._failures.pop(source_ip, None)

    def _prune_locked(self, now: float) -> None:
        for key, (failures, locked_until) in list(self._failures.items()):
            if failures < self._max_failures and locked_until < now:
                self._failures.pop(key, None)


def _text(value: Any, default: str, max_length: int = 100) -> str:
    text = str(value if value is not None else default).strip()
    return text[:max_length] or default


def _bounded_number(value: Any, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number < minimum or number > maximum:  # NaN-safe
        return None
    return number


def structured_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the optional structured Vision telemetry block from a heartbeat.

    Returning ``{}`` for a legacy payload keeps the dashboard happy while an
    updated Mac controller can start sending richer fields immediately.
    """
    result: dict[str, Any] = {}
    for field, max_length in _STRUCTURED_TEXT_FIELDS.items():
        raw = payload.get(field)
        if isinstance(raw, str) and raw.strip():
            result[field] = raw.strip()[:max_length]
    for field, (minimum, maximum) in _STRUCTURED_NUMBER_FIELDS.items():
        number = _bounded_number(payload.get(field), minimum, maximum)
        if number is not None:
            result[field] = round(number, 4)
    controller_time = payload.get("sent_at")
    if isinstance(controller_time, (int, float)) and not isinstance(controller_time, bool):
        result["controller_sent_at"] = float(controller_time)
    if isinstance(payload.get("clock_sync"), bool):
        result["clock_sync"] = payload["clock_sync"]
    return result


class ControlClientStore:
    """Last controller identity reported to the heartbeat endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._record: dict[str, Any] | None = None

    def heartbeat(self, source_ip: str, payload: dict[str, Any]) -> dict[str, Any]:
        record = {
            "ip": source_ip,
            "name": _text(payload.get("name"), "Vision controller"),
            "state": _text(payload.get("state"), "controlling", 40),
            "protocol": _text(payload.get("protocol"), "controller heartbeat", 60),
            "session_id": _text(payload.get("session_id"), "", 100),
            "robot_ip": _text(payload.get("robot_ip"), "", 64),
            "details": _text(payload.get("details"), "", 200),
            "telemetry": structured_telemetry(payload),
            "heartbeats": 0,
            "last_seen": time.time(),
            "_last_seen_monotonic": time.monotonic(),
        }
        with self._lock:
            previous = self._record
            if previous is not None and previous.get("session_id") == record["session_id"]:
                record["heartbeats"] = int(previous.get("heartbeats", 0)) + 1
            else:
                record["heartbeats"] = 1
            self._record = record
        public = dict(record)
        public.pop("_last_seen_monotonic", None)
        return public

    def snapshot(self, timeout_seconds: float) -> dict[str, Any]:
        with self._lock:
            record = dict(self._record) if self._record else None
        if record is None:
            return {
                "reported": False,
                "active": False,
                "ip": None,
                "name": None,
                "state": "unknown",
                "protocol": None,
                "session_id": None,
                "details": None,
                "telemetry": {},
                "heartbeats": 0,
                "last_seen": None,
                "age_seconds": None,
                "timeout_seconds": timeout_seconds,
                "clock_offset_seconds": None,
            }
        age = max(0.0, time.monotonic() - float(record["_last_seen_monotonic"]))
        record.pop("_last_seen_monotonic", None)
        record.update(
            {
                "reported": True,
                "active": age <= timeout_seconds,
                "age_seconds": age,
                "timeout_seconds": timeout_seconds,
                "clock_offset_seconds": record.get("clock_offset_seconds"),
            }
        )
        return record

    def reset(self) -> None:
        with self._lock:
            self._record = None
