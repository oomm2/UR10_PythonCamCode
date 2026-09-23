#!/usr/bin/env python3
"""In-memory telemetry state for the read-only UR monitor.

This module owns every piece of volatile robot state the dashboard can see:

* ``StateStore``          - the single latest snapshot plus link diagnostics.
* ``TrajectoryBuffer``    - bounded TCP/joint history for the 3D replay trail.
* ``LatencyTracker``      - heartbeat round-trip statistics in either of the two
                            supported measurement modes.

Nothing in here writes to the robot, opens an input recipe or sends URScript.
Every structure is lock-guarded and returns deep copies so callers can never
mutate live state through a shared reference.
"""
from __future__ import annotations

import collections
import copy
import math
import threading
import time
from typing import Any

STATE_DEFAULTS: dict[str, Any] = {
    "connected": False,
    "robot_ip": "",
    "monitor_ip": "",
    "last_update": None,
    "error": "Not connected",
    "timestamp": None,
    "actual_q": [0.0] * 6,
    "actual_TCP_pose": [0.0] * 6,
    "actual_TCP_speed": [0.0] * 6,
    "actual_joint_current": [0.0] * 6,
    "joint_temperatures": [0.0] * 6,
    "robot_mode": None,
    "safety_mode": None,
    "speed_scaling": None,
    "actual_digital_input_bits": None,
    "actual_digital_output_bits": None,
}


class StateStore:
    """Latest RTDE snapshot plus read-only link diagnostics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = copy.deepcopy(STATE_DEFAULTS)
        self._diagnostics: dict[str, Any] = {
            "reconnects": 0,
            "last_disconnect_reason": None,
            "last_disconnect_at": None,
            "last_connect_at": None,
            "connected_since": None,
            "cumulative_uptime_seconds": 0.0,
            "samples_read": 0,
            "protocol": "RTDE output-only (read-only)",
        }

    def update(self, touch: bool = True, **values: Any) -> None:
        with self._lock:
            self._state.update(values)
            if touch:
                self._state["last_update"] = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    # --- Link diagnostics -------------------------------------------------
    def record_connect(self) -> None:
        """Called once the RTDE synchronisation has actually started."""
        with self._lock:
            now = time.time()
            self._diagnostics["last_connect_at"] = now
            self._diagnostics["connected_since"] = now

    def record_disconnect(self, reason: str) -> None:
        """Called whenever an established link drops, before reconnecting."""
        with self._lock:
            now = time.time()
            started = self._diagnostics.get("connected_since")
            if started is not None:
                self._diagnostics["cumulative_uptime_seconds"] += max(0.0, now - started)
            self._diagnostics["connected_since"] = None
            self._diagnostics["reconnects"] += 1
            self._diagnostics["last_disconnect_reason"] = str(reason)[:300]
            self._diagnostics["last_disconnect_at"] = now

    def record_sample(self) -> int:
        with self._lock:
            self._diagnostics["samples_read"] += 1
            return int(self._diagnostics["samples_read"])

    def diagnostics(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or {}
        with self._lock:
            result = copy.deepcopy(self._diagnostics)
            started = result.get("connected_since")
        uptime = float(result.get("cumulative_uptime_seconds") or 0.0)
        result["current_uptime_seconds"] = max(0.0, time.time() - started) if started else 0.0
        result["cumulative_uptime_seconds"] = uptime + result["current_uptime_seconds"]
        result["connected"] = bool(started)
        result["robot_ip"] = config.get("robot_ip")
        result["rtde_port"] = config.get("rtde_port")
        result["rtde_frequency"] = config.get("rtde_frequency")
        result["latency"] = None  # filled in by the handler from the latency tracker
        return result


class TrajectoryBuffer:
    """Bounded ring buffer of TCP poses and joint angles.

    The dashboard draws this as a 3D path and can scrub through it, which is
    what turns a live-only monitor into something you can show after a demo.
    Points are stored as flat lists so the payload stays compact over the wire.
    """

    def __init__(self, capacity: int = 20000, sample_hz: float = 25.0, min_distance: float = 0.001) -> None:
        self._lock = threading.Lock()
        self._points: collections.deque[list[float]] = collections.deque(maxlen=max(10, int(capacity)))
        self._interval = 1.0 / max(0.1, float(sample_hz))
        self._min_distance = max(0.0, float(min_distance))
        self._last_sample_monotonic = 0.0
        self._last_tcp: list[float] | None = None
        self._total_recorded = 0

    def maybe_record(self, snapshot: dict[str, Any]) -> None:
        """Record a point when enough time or motion has passed since the last one.

        The time gate is the primary limiter and is always in force. The distance
        gate only exists to suppress jitter while the robot is parked, so it is
        disabled when ``min_distance`` is zero.
        """
        if not snapshot.get("connected") or snapshot.get("timestamp") is None:
            return
        pose = snapshot.get("actual_TCP_pose")
        joints = snapshot.get("actual_q")
        if not self._is_pose(pose) or not self._is_joints(joints):
            return
        assert isinstance(pose, (list, tuple)) and isinstance(joints, (list, tuple))
        now = time.monotonic()
        if self._last_tcp is not None and now - self._last_sample_monotonic < self._interval:
            return
        tcp = [float(value) for value in pose[:6]]
        if self._min_distance > 0 and self._last_tcp is not None:
            distance = math.dist(tcp[:3], self._last_tcp[:3])
            if distance < self._min_distance:
                # Parked inside the jitter band: move the clock forward so the
                # filter re-checks after another full interval instead of
                # evaluating on every RTDE frame.
                self._last_sample_monotonic = now
                return
        point = [tcp[0], tcp[1], tcp[2], *[float(value) for value in joints[:6]]]
        with self._lock:
            self._points.append(point)
            self._last_sample_monotonic = now
            self._last_tcp = tcp
            self._total_recorded += 1

    def points(self, limit: int | None = None) -> list[list[float]]:
        with self._lock:
            data = list(self._points)
        if limit is not None and limit > 0:
            data = data[-int(limit):]
        return data

    def clear(self) -> int:
        with self._lock:
            removed = len(self._points)
            self._points.clear()
            self._last_tcp = None
            self._last_sample_monotonic = 0.0
        return removed

    def status(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._points)
            capacity = self._points.maxlen
            total = self._total_recorded
        return {
            "points": count,
            "capacity": capacity,
            "total_recorded": total,
            "sample_hz": round(1.0 / self._interval, 3),
            "min_distance": self._min_distance,
        }

    @staticmethod
    def _finite_vector(value: Any) -> bool:
        """True when *value* is a list/tuple of at least six finite numbers."""
        if not isinstance(value, (list, tuple)) or len(value) < 6:
            return False
        return all(
            isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item))
            for item in value[:6]
        )

    @staticmethod
    def _is_pose(value: Any) -> bool:
        return TrajectoryBuffer._finite_vector(value)

    @staticmethod
    def _is_joints(value: Any) -> bool:
        return TrajectoryBuffer._finite_vector(value)


class LatencyTracker:
    """Round-trip statistics for the Mac Vision controller heartbeat.

    Two measurement modes are supported because a demo laptop and the Mac are
    rarely clock-synchronised:

    ``monotonic``  - the controller stamps ``sent_at`` with its own clock and the
                     monitor records the arrival instant with ``time.monotonic``.
                     The reported figure is the one-way network + queueing delay
                     observed on this host and needs no clock agreement.
    ``clock_sync`` - the controller stamps ``sent_at`` as a Unix timestamp from a
                     clock that is assumed to be NTP-synchronised with this host,
                     which lets the monitor report true Mac -> Windows latency.

    Both modes keep a rolling window so the dashboard can show a live figure and
    a worst case rather than a single noisy number.
    """

    def __init__(self, mode: str = "monotonic", window: int = 240) -> None:
        self._lock = threading.Lock()
        self._mode = mode if mode in {"monotonic", "clock_sync"} else "monotonic"
        self._samples: collections.deque[float] = collections.deque(maxlen=max(10, int(window)))
        self._last_ms: float | None = None
        self._last_at: float | None = None
        self._clock_offset_seconds: float | None = None
        self._clock_offset_drift: float | None = None
        self._rejected = 0

    @property
    def mode(self) -> str:
        return self._mode

    def configure(self, mode: str, window: int | None = None) -> None:
        with self._lock:
            self._mode = mode if mode in {"monotonic", "clock_sync"} else "monotonic"
            if window is not None:
                self._samples = collections.deque(self._samples, maxlen=max(10, int(window)))

    def observe(
        self, payload: dict[str, Any], received_monotonic: float | None = None
    ) -> dict[str, Any] | None:
        """Fold one heartbeat into the statistics, returning its measurement."""
        received = time.monotonic() if received_monotonic is None else float(received_monotonic)
        sent_raw = payload.get("sent_at")
        elapsed_ms: float | None = None
        if isinstance(sent_raw, (int, float)) and not isinstance(sent_raw, bool):
            sent = float(sent_raw)
            if self._mode == "clock_sync":
                # sent_at is an absolute Unix timestamp; compare clock to clock.
                latency = time.time() - sent
            else:
                # sent_at is a monotonic stamp from the same machine, so a
                # non-positive delta means the payload carried a stale value.
                latency = received - sent
                if latency < 0:
                    latency = None
            if latency is not None and 0 <= latency <= 60.0:
                elapsed_ms = latency * 1000.0
        transport_raw = payload.get("transport_latency_ms")
        transport_valid = isinstance(transport_raw, (int, float)) and not isinstance(transport_raw, bool)
        if elapsed_ms is None and transport_valid:
            # mypy narrows through the isinstance check above, so float() is safe.
            transport = float(transport_raw)  # type: ignore[arg-type]
            if 0 <= transport <= 60_000.0:
                elapsed_ms = transport

        with self._lock:
            self._last_at = time.time()
            if elapsed_ms is None:
                self._rejected += 1
                return None
            self._samples.append(elapsed_ms)
            self._last_ms = elapsed_ms
            samples = sorted(self._samples)
            stats = {
                "mode": self._mode,
                "last_ms": round(elapsed_ms, 3),
                "mean_ms": round(sum(samples) / len(samples), 3),
                "p95_ms": round(samples[min(len(samples) - 1, int(0.95 * (len(samples) - 1)))], 3),
                "min_ms": round(samples[0], 3),
                "max_ms": round(samples[-1], 3),
                "samples": len(samples),
                "rejected": self._rejected,
                "updated_at": self._last_at,
                "offset_seconds": self._clock_offset_seconds,
                "offset_drift_seconds": self._clock_offset_drift,
            }
        return stats

    def note_clock_offset(self, offset_seconds: float) -> None:
        """Track the running drift between the controller clock and this host."""
        with self._lock:
            previous = self._clock_offset_seconds
            self._clock_offset_seconds = offset_seconds
            self._clock_offset_drift = None if previous is None else offset_seconds - previous

    def stats(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._samples:
                return {
                    "mode": self._mode,
                    "last_ms": None,
                    "mean_ms": None,
                    "p95_ms": None,
                    "min_ms": None,
                    "max_ms": None,
                    "samples": 0,
                    "rejected": self._rejected,
                    "updated_at": self._last_at,
                    "offset_seconds": self._clock_offset_seconds,
                    "offset_drift_seconds": self._clock_offset_drift,
                }
            samples = sorted(self._samples)
            return {
                "mode": self._mode,
                "last_ms": round(float(self._last_ms or 0.0), 3),
                "mean_ms": round(sum(samples) / len(samples), 3),
                "p95_ms": round(samples[min(len(samples) - 1, int(0.95 * (len(samples) - 1)))], 3),
                "min_ms": round(samples[0], 3),
                "max_ms": round(samples[-1], 3),
                "samples": len(samples),
                "rejected": self._rejected,
                "updated_at": self._last_at,
                "offset_seconds": self._clock_offset_seconds,
                "offset_drift_seconds": self._clock_offset_drift,
            }

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._last_ms = None
            self._last_at = None
            self._rejected = 0
