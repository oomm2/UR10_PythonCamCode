#!/usr/bin/env python3
"""CSV recording of live telemetry for the read-only UR monitor.

``RecordingSession`` streams the latest RTDE snapshot to a timestamped CSV file
so a demo run can be replayed and compared later. It deliberately holds no lock
while writing: the RTDE reader hands it a fresh snapshot and returns immediately.
"""
from __future__ import annotations

import csv
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

RECORDING_COLUMNS = (
    "wall_time",
    "robot_timestamp",
    "actual_q1", "actual_q2", "actual_q3", "actual_q4", "actual_q5", "actual_q6",
    "tcp_x", "tcp_y", "tcp_z", "tcp_rx", "tcp_ry", "tcp_rz",
    "tcp_speed_linear", "tcp_speed_angular",
    "joint_current_1", "joint_current_2", "joint_current_3",
    "joint_current_4", "joint_current_5", "joint_current_6",
    "joint_temp_1", "joint_temp_2", "joint_temp_3",
    "joint_temp_4", "joint_temp_5", "joint_temp_6",
    "robot_mode", "safety_mode", "speed_scaling",
    "digital_input_bits", "digital_output_bits",
)


def _number(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if not math.isfinite(float(value)):
        return ""
    return f"{float(value):.6f}"


def recording_row(snapshot: dict[str, Any], wall_time: float) -> list[Any]:
    joints = snapshot.get("actual_q") or [None] * 6
    pose = snapshot.get("actual_TCP_pose") or [None] * 6
    speed = snapshot.get("actual_TCP_speed") or [None] * 6
    current = snapshot.get("actual_joint_current") or [None] * 6
    temperature = snapshot.get("joint_temperatures") or [None] * 6
    linear = math.sqrt(sum(float(value) ** 2 for value in speed[:3] if isinstance(value, (int, float))))
    angular = math.sqrt(sum(float(value) ** 2 for value in speed[3:6] if isinstance(value, (int, float))))
    return [
        f"{wall_time:.6f}",
        _number(snapshot.get("timestamp")),
        *[_number(value) for value in joints[:6]],
        *[_number(value) for value in pose[:6]],
        f"{linear:.6f}", f"{angular:.6f}",
        *[_number(value) for value in current[:6]],
        *[_number(value) for value in temperature[:6]],
        _number(snapshot.get("robot_mode")),
        _number(snapshot.get("safety_mode")),
        _number(snapshot.get("speed_scaling")),
        _number(snapshot.get("actual_digital_input_bits")),
        _number(snapshot.get("actual_digital_output_bits")),
    ]


class RecordingSession:
    """Stream the live state to a CSV file without holding a lock while writing."""

    def __init__(self, directory: Path, max_seconds: float) -> None:
        self._lock = threading.Lock()
        self._directory = directory
        self._max_seconds = max(1.0, float(max_seconds))
        self._handle: Any = None
        self._writer: Any = None
        self._path: Path | None = None
        self._name: str | None = None
        self._started_at: float | None = None
        self._started_monotonic: float | None = None
        self._rows = 0
        self._error: str | None = None
        self._auto_stopped = False

    def start(self, now: float | None = None) -> dict[str, Any]:
        with self._lock:
            if self._handle is not None:
                raise RuntimeError("a recording is already running")
        wall_time = time.time() if now is None else float(now)
        self._directory.mkdir(parents=True, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(wall_time)) + ".csv"
        path = self._directory / name
        handle = path.open("w", encoding="utf-8", newline="")
        writer = csv.writer(handle)
        writer.writerow(RECORDING_COLUMNS)
        with self._lock:
            self._handle = handle
            self._writer = writer
            self._path = path
            self._name = name
            self._started_at = wall_time
            self._started_monotonic = time.monotonic()
            self._rows = 0
            self._error = None
            self._auto_stopped = False
        LOGGER.info("Recording started: %s", path)
        return self.status()

    def write(self, snapshot: dict[str, Any]) -> None:
        if not snapshot.get("connected") or snapshot.get("timestamp") is None:
            return
        row = recording_row(snapshot, time.time())
        with self._lock:
            if self._handle is None:
                return
            if self._started_monotonic is not None:
                elapsed = time.monotonic() - self._started_monotonic
                if elapsed >= self._max_seconds:
                    self._close_locked(auto=True)
                    return
            try:
                self._writer.writerow(row)
                self._rows += 1
            except (OSError, csv.Error) as exc:
                self._error = str(exc)
                self._close_locked(auto=True)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._handle is None:
                return self.status()
            self._close_locked(auto=False)
        LOGGER.info("Recording stopped: %s", self._name)
        return self.status()

    def _close_locked(self, *, auto: bool) -> None:
        if self._handle is None:
            return
        try:
            self._handle.flush()
        except OSError as exc:
            self._error = self._error or str(exc)
        try:
            self._handle.close()
        except OSError as exc:
            self._error = self._error or str(exc)
        self._handle = None
        self._writer = None
        self._started_monotonic = None
        if auto:
            self._auto_stopped = True

    def status(self) -> dict[str, Any]:
        return {
            "recording": self._handle is not None,
            "file": self._name,
            "rows": self._rows,
            "started_at": self._started_at,
            "duration_seconds": (
                max(0.0, time.monotonic() - self._started_monotonic)
                if self._started_monotonic is not None
                else None
            ),
            "error": self._error,
            "auto_stopped": self._auto_stopped,
            "max_seconds": self._max_seconds,
        }

    def download_path(self, name: str) -> Path | None:
        """Resolve a recording file name, rejecting anything that is not a plain name."""
        if not name or name != Path(name).name or Path(name).suffix.lower() != ".csv":
            return None
        if any(character in name for character in "\x00\r\n"):
            return None
        try:
            candidate = (self._directory / name).resolve()
            root = self._directory.resolve()
        except OSError:
            return None
        if candidate.parent != root or not candidate.is_file():
            return None
        return candidate

    def list_recordings(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            entries = sorted(
                (path for path in self._directory.glob("*.csv") if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        results = []
        for path in entries[:limit]:
            try:
                stat = path.stat()
            except OSError:
                continue
            results.append({
                "name": path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        return results

    def read_rows(self, name: str, limit: int = 0) -> list[dict[str, Any]]:
        """Return parsed rows for a stored recording, for baseline comparison.

        ``limit`` of 0 or less returns every row; otherwise the most recent
        ``limit`` rows are returned so a huge capture cannot flood a browser.
        """
        path = self.download_path(name)
        if path is None:
            return []
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for raw in reader:
                    parsed: dict[str, Any] = {}
                    for key, value in raw.items():
                        if key is None:
                            continue
                        try:
                            parsed[key] = float(value) if value not in (None, "") else None
                        except (TypeError, ValueError):
                            parsed[key] = None
                    rows.append(parsed)
                    # Keep only the newest window without buffering the file twice.
                    if limit > 0 and len(rows) > limit:
                        rows.pop(0)
        except (OSError, csv.Error):
            return []
        return rows


def ensure_recorder(
    holder: dict[str, Any],
    lock: threading.RLock,
    root: Path,
    directory_name: str,
    max_seconds: float,
) -> RecordingSession:
    """Create the shared recorder once, or reconfigure it when settings change.

    ``holder`` is a single-key dict (``{"recorder": ...}``) used as a mutable
    slot because the recorder is shared across request threads.
    """
    directory = (root / directory_name).resolve()
    with lock:
        current = holder.get("recorder")
        if current is None or current._directory != directory or current._max_seconds != max_seconds:
            if current is not None:
                current.stop()
            current = RecordingSession(directory, max_seconds)
            holder["recorder"] = current
        return current
