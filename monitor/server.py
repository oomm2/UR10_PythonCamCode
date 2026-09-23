#!/usr/bin/env python3
"""Read-only UR RTDE monitor for Windows.

The RTDE connection only configures an output recipe. It never configures an
input recipe or sends URScript. A separate authenticated heartbeat endpoint lets
the Mac Vision controller identify itself to the dashboard without changing the
robot control path.

This module is intentionally the thin HTTP + process layer. All domain logic
lives in sibling modules:

* ``monitor_config``  - config.json / .env loading, validation and caches
* ``telemetry_state`` - live state, trajectory ring buffer, latency statistics
* ``events``          - SSE broker and publisher
* ``recording``       - CSV recording sessions
* ``controller``      - Mac Vision heartbeat bookkeeping
* ``rtde_client``     - the read-only RTDE reader thread
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import parse_qs, unquote

from controller import ControlClientStore, HeartbeatThrottle
from events import EventStreamBroker, StatePublisher
from monitor_config import (
    STATIC_DIR,
    ConfigError,
    allowed_origins,
    get_heartbeat_token,
    load_config,
    route_local_ip,
)
from recording import RecordingSession, ensure_recorder
from rtde_client import RTDEMonitor
from telemetry_state import LatencyTracker, StateStore, TrajectoryBuffer

MAX_HEARTBEAT_PAYLOAD = 4096
HTTP_SOCKET_TIMEOUT_SECONDS = 5.0
# Long-lived SSE connections must not be cut short by the socket timeout.
EVENT_STREAM_SOCKET_TIMEOUT_SECONDS = 300.0
# Cap on how many trajectory points a single /api/trajectory response may carry.
MAX_TRAJECTORY_POINTS = 60000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATE = StateStore()
TRAJECTORY = TrajectoryBuffer()
LATENCY = LatencyTracker()
CONTROL_CLIENT = ControlClientStore()
HEARTBEAT_THROTTLE = HeartbeatThrottle(max_failures=5, lockout_seconds=60.0)
EVENT_STREAM = EventStreamBroker()
_RECORDER_HOLDER: dict[str, Any] = {"recorder": None}
_RECORDER_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Backwards-compatible shims.
#
# ``server`` is the public entry module, so older call sites and tests refer to
# ``server.RECORDER`` / ``server.ROOT`` / ``server._ensure_recorder``. The code
# itself now lives in sibling modules, so these names are thin proxies:
# reading ``RECORDER`` returns the live shared recorder, and assigning it swaps
# that shared slot so tests can isolate themselves.
# ---------------------------------------------------------------------------
def _ensure_recorder(config: dict[str, Any]) -> RecordingSession:
    """Legacy alias for :func:`ensure_recorder_for`, kept for older tests."""
    return ensure_recorder_for(config)


def _get_recorder() -> RecordingSession | None:
    """Return the shared recorder, or ``None`` before the first request."""
    return _RECORDER_HOLDER.get("recorder")


def _set_recorder(recorder: RecordingSession | None) -> None:
    """Replace the shared recorder slot, letting tests isolate themselves."""
    _RECORDER_HOLDER["recorder"] = recorder


def ensure_recorder_for(config: dict[str, Any]) -> RecordingSession:
    # Read ROOT through the module so tests that relocate the project root take
    # effect; the imported name is a snapshot taken at import time.
    import monitor_config as _cfg

    return ensure_recorder(
        _RECORDER_HOLDER,
        _RECORDER_LOCK,
        Path(_cfg.ROOT),
        str(config.get("recording_directory", "recordings")),
        float(config.get("recording_max_seconds", 3600.0)),
    )


def monitor_snapshot() -> dict[str, Any]:
    """Build the payload every client sees, for JSON responses and SSE alike."""
    try:
        config = load_config()
    except ConfigError:
        config = {}
    timeout = float(config.get("controller_heartbeat_timeout", 4.0)) if config else 4.0
    snapshot = STATE.snapshot()
    snapshot["control_client"] = CONTROL_CLIENT.snapshot(timeout)
    snapshot["latency"] = LATENCY.stats()
    snapshot["trajectory"] = TRAJECTORY.status()
    snapshot["subscribers"] = EVENT_STREAM.subscriber_count()
    return snapshot


def _safe_static_path(request_path: str) -> Path | None:
    """Resolve a URL path under STATIC_DIR, rejecting traversal and Windows paths."""
    raw_path = request_path.split("?", 1)[0]
    try:
        decoded = unquote(raw_path, errors="strict")
    except UnicodeDecodeError:
        return None
    if "\x00" in decoded or any(character in decoded for character in "\r\n"):
        return None
    decoded = decoded.replace("\\", "/")
    if decoded in {"", "/"}:
        relative = "index.html"
    else:
        if decoded.startswith("//"):
            return None
        relative = decoded.lstrip("/")
        windows_path = PureWindowsPath(relative)
        if windows_path.drive or windows_path.root:
            return None
    parts = relative.split("/")
    if any(part in {"..", "."} or ":" in part for part in parts):
        return None
    root = STATIC_DIR.resolve()
    candidate = (STATIC_DIR / Path(*parts)).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


class MonitorHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server that stays quiet when a browser drops a connection."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (OSError, TimeoutError)):
            logging.debug("Client connection ended: %s", exc)
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "URMonitor/3.1"
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(HTTP_SOCKET_TIMEOUT_SECONDS)

    @property
    def _source_ip(self) -> str:
        """TCP source address as observed by this host, never a client-supplied value."""
        try:
            return str(self.client_address[0])
        except (IndexError, TypeError):
            return "unknown"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid flooding the terminal with the browser's frequent state polls.
        message = fmt % args
        if '"GET /api/state ' in message or '"GET /api/stream ' in message:
            return
        logging.info("HTTP " + fmt, *args)

    def _cors_headers(self, origin: str | None) -> None:
        self.send_header("Vary", "Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Heartbeat-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, value: Any, status: int = 200, *, origin: str | None = None) -> None:
        data = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, message: str, status: int, origin: str | None = None) -> None:
        # Rejecting before consuming a request body must close keep-alive so
        # leftover bytes cannot be parsed as a second HTTP request.
        self.close_connection = True
        self._send_json({"ok": False, "error": message}, status, origin=origin)

    def _api_context(self) -> tuple[dict[str, Any], str | None] | None:
        try:
            config = load_config()
        except ConfigError as exc:
            self._send_error_json("Monitor configuration unavailable", 500)
            logging.error("HTTP request rejected because configuration is invalid: %s", exc)
            return None
        origin = self.headers.get("Origin")
        if origin and origin not in allowed_origins(config):
            self._send_error_json("Origin is not allowed", 403)
            return None
        return config, origin

    def _controller_snapshot(self, config: dict[str, Any]) -> dict[str, Any]:
        timeout = float(config.get("controller_heartbeat_timeout", 4.0))
        return CONTROL_CLIENT.snapshot(timeout)

    def do_OPTIONS(self) -> None:
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send_error_json("Not found", 404)
            return
        context = self._api_context()
        if context is None:
            return
        _, origin = context
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers(origin)
        self.end_headers()

    def _read_payload(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            length = 0
        else:
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                self._send_error_json("Invalid Content-Length", 400, origin=getattr(self, "_origin", None))
                return None
        if length < 0:
            self._send_error_json("Invalid Content-Length", 400, origin=getattr(self, "_origin", None))
            return None
        if length > MAX_HEARTBEAT_PAYLOAD:
            self._send_error_json("Invalid payload size", 413, origin=getattr(self, "_origin", None))
            return None
        try:
            body = self.rfile.read(length) if length else b"{}"
        except (OSError, TimeoutError):
            self._send_error_json("Request body read timed out", 408, origin=getattr(self, "_origin", None))
            return None
        if len(body) != length:
            self._send_error_json("Incomplete request body", 400, origin=getattr(self, "_origin", None))
            return None
        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_error_json(str(exc), 400, origin=getattr(self, "_origin", None))
            return None
        if not isinstance(payload, dict):
            self._send_error_json("JSON object required", 400, origin=getattr(self, "_origin", None))
            return None
        return payload

    def _authorize_heartbeat(self) -> bool:
        # Reject locked-out sources before touching the configured secret so a
        # guessing client cannot use this endpoint as an oracle.
        retry_after = HEARTBEAT_THROTTLE.retry_after(self._source_ip)
        if retry_after > 0:
            self.send_response(429)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Retry-After", str(math.ceil(retry_after)))
            self.close_connection = True
            self._cors_headers(self._origin)
            body = json.dumps(
                {"ok": False, "error": "Too many failed heartbeat attempts"},
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False

        expected_token = get_heartbeat_token()
        if not expected_token:
            self._send_error_json("Heartbeat authentication is not configured", 503, origin=self._origin)
            return False
        supplied_candidates = []
        header_token = self.headers.get("X-Heartbeat-Token", "")
        if header_token:
            supplied_candidates.append(header_token)
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied_candidates.append(auth[7:].strip())
        expected_bytes = expected_token.encode("ascii")
        for supplied_token in supplied_candidates:
            try:
                supplied_bytes = supplied_token.encode("ascii")
            except UnicodeEncodeError:
                continue
            if hmac.compare_digest(supplied_bytes, expected_bytes):
                HEARTBEAT_THROTTLE.clear(self._source_ip)
                return True
        HEARTBEAT_THROTTLE.record_failure(self._source_ip)
        self._send_error_json("Invalid heartbeat token", 401, origin=self._origin)
        return False

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/api/recording/start", "/api/recording/stop"):
            self._handle_recording_control(path)
            return
        if path == "/api/trajectory/clear":
            self._handle_trajectory_clear()
            return
        if path not in ("/api/control/heartbeat", "/api/controller/heartbeat"):
            self._send_error_json("Not found", 404)
            return
        context = self._api_context()
        if context is None:
            return
        _, origin = context
        self._origin = origin
        # Authenticate before reading the request body; unauthenticated clients
        # cannot make the server consume arbitrary payloads.
        if not self._authorize_heartbeat():
            return
        payload = self._read_payload()
        if payload is None:
            return

        source_ip = self._source_ip
        # Measure arrival against the controller's own clock stamp before any
        # further processing, so the figure reflects the link and not this host.
        latency = LATENCY.observe(payload, received_monotonic=time.monotonic())
        record = CONTROL_CLIENT.heartbeat(source_ip, payload)
        if latency is not None and latency.get("offset_seconds") is None:
            sent_at = payload.get("sent_at")
            if isinstance(sent_at, (int, float)) and not isinstance(sent_at, bool):
                offset = time.time() - float(sent_at)
                if abs(offset) <= 86400.0:
                    LATENCY.note_clock_offset(round(offset, 4))
                    record["clock_offset_seconds"] = round(offset, 4)
        logging.info(
            "Controller heartbeat: %s (%s) from %s, state=%s, latency=%sms",
            record["name"],
            record["protocol"],
            source_ip,
            record["state"],
            None if latency is None else latency.get("last_ms"),
        )
        self._send_json(
            {
                "ok": True,
                "observed_ip": source_ip,
                "last_seen": record["last_seen"],
                "latency": latency,
                "telemetry": record.get("telemetry", {}),
            },
            origin=origin,
        )

    def _handle_trajectory_clear(self) -> None:
        context = self._api_context()
        if context is None:
            return
        _, origin = context
        self._origin = origin
        # Clearing the buffer only discards in-memory history, but it is still a
        # mutating operation, so reuse the same secret as other write endpoints.
        if not self._authorize_heartbeat():
            return
        removed = TRAJECTORY.clear()
        self._send_json({"ok": True, "removed": removed, "trajectory": TRAJECTORY.status()}, origin=origin)

    def _handle_recording_control(self, path: str) -> None:
        context = self._api_context()
        if context is None:
            return
        config, origin = context
        self._origin = origin
        # Reuse the heartbeat secret: starting a recording writes files to the
        # host, so it must not be reachable from an unauthenticated client.
        if not self._authorize_heartbeat():
            return
        payload = self._read_payload()
        if payload is None:
            return
        recorder = ensure_recorder_for(config)
        try:
            status = recorder.start() if path == "/api/recording/start" else recorder.stop()
        except RuntimeError as exc:
            self._send_error_json(str(exc), 409, origin=origin)
            return
        except OSError as exc:
            self._send_error_json(f"Cannot write recording: {exc}", 500, origin=origin)
            return
        self._send_json({"ok": True, "recording": status}, origin=origin)

    def _handle_event_stream(self, config: dict[str, Any], origin: str | None) -> None:
        """Stream state snapshots as Server-Sent Events until the client leaves."""
        frequency = float(config.get("event_stream_frequency", 10.0))
        subscriber = EVENT_STREAM.subscribe()
        # A long-lived stream must not be cut off by the short request timeout.
        self.connection.settimeout(EVENT_STREAM_SOCKET_TIMEOUT_SECONDS)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._cors_headers(origin)
            self.end_headers()
            # Some proxies close an idle stream, so emit a comment when no
            # telemetry arrived within the keep-alive window.
            keepalive = max(0.5, min(15.0, 5.0 / max(0.1, frequency)))
            while True:
                try:
                    message = subscriber.get(timeout=keepalive)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    self.wfile.write(f"data: {message}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError):
            return
        finally:
            EVENT_STREAM.unsubscribe(subscriber)

    def _handle_recording_download(self, config: dict[str, Any], name: str) -> None:
        recorder = ensure_recorder_for(config)
        path = recorder.download_path(name)
        if path is None:
            self.send_error(404)
            return
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle_trajectory(self, _config: dict[str, Any], origin: str | None) -> None:
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        limit = 0
        raw_limit = (query.get("limit") or [""])[0]
        if raw_limit:
            try:
                limit = max(0, min(int(raw_limit), MAX_TRAJECTORY_POINTS))
            except (TypeError, ValueError):
                limit = 0
        self._send_json(
            {
                "ok": True,
                "status": TRAJECTORY.status(),
                "points": TRAJECTORY.points(limit or None),
                "clear_requires_token": True,
            },
            origin=origin,
        )

    def _handle_baseline(self, config: dict[str, Any], origin: str | None) -> None:
        query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
        left = (query.get("left") or [""])[0]
        right = (query.get("right") or [""])[0]
        try:
            limit = max(100, min(int((query.get("limit") or ["4000"])[0]), 20000))
        except (TypeError, ValueError):
            limit = 4000
        recorder = ensure_recorder_for(config)
        if not left or not right:
            self._send_json(
                {"ok": False, "error": "left and right recording names are required"},
                400,
                origin=origin,
            )
            return
        left_rows = recorder.read_rows(left, limit=limit)
        right_rows = recorder.read_rows(right, limit=limit)
        if not left_rows or not right_rows:
            self._send_json(
                {
                    "ok": False,
                    "error": "one or both recordings could not be read",
                    "left_rows": len(left_rows),
                    "right_rows": len(right_rows),
                },
                404,
                origin=origin,
            )
            return
        self._send_json(
            {
                "ok": True,
                "left": {"name": left, "rows": left_rows},
                "right": {"name": right, "rows": right_rows},
                "columns": list(left_rows[0].keys()),
            },
            origin=origin,
        )

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/"):
            context = self._api_context()
            if context is None:
                return
            config, origin = context
            if path == "/api/state":
                self._send_json(monitor_snapshot(), origin=origin)
                return
            if path == "/api/control-client":
                snapshot = self._controller_snapshot(config)
                snapshot["latency"] = LATENCY.stats()
                self._send_json(snapshot, origin=origin)
                return
            if path == "/api/latency":
                self._send_json(
                    {
                        "ok": True,
                        "latency": LATENCY.stats(),
                        "mode": LATENCY.mode,
                        "configured_mode": config.get("latency_mode"),
                    },
                    origin=origin,
                )
                return
            if path == "/api/trajectory":
                self._handle_trajectory(config, origin)
                return
            if path == "/api/diagnostics":
                self._send_json(
                    {
                        "ok": True,
                        "diagnostics": STATE.diagnostics(config),
                        "latency": LATENCY.stats(),
                        "trajectory": TRAJECTORY.status(),
                        "subscribers": EVENT_STREAM.stats(),
                        "controller": self._controller_snapshot(config),
                    },
                    origin=origin,
                )
                return
            if path == "/api/stream":
                self._handle_event_stream(config, origin)
                return
            if path == "/api/recording":
                recorder = ensure_recorder_for(config)
                self._send_json(
                    {"ok": True, "recording": recorder.status(), "files": recorder.list_recordings()},
                    origin=origin,
                )
                return
            if path == "/api/baseline":
                self._handle_baseline(config, origin)
                return
            if path.startswith("/api/recording/download/"):
                self._handle_recording_download(config, unquote(path[len("/api/recording/download/"):]))
                return
            if path == "/api/config":
                recorder = ensure_recorder_for(config)
                self._send_json(
                    {
                        "robot_ip": config.get("robot_ip"),
                        "rtde_port": config.get("rtde_port"),
                        "rtde_frequency": config.get("rtde_frequency"),
                        "http_port": config.get("http_port"),
                        "robot_model": config.get("robot_model"),
                        "latency_mode": config.get("latency_mode"),
                        "monitor_ip": STATE.snapshot().get("monitor_ip"),
                        "cors_allowed_origins": sorted(allowed_origins(config)),
                        "event_stream_frequency": config.get("event_stream_frequency"),
                        "recording_max_seconds": config.get("recording_max_seconds"),
                        "heartbeat_max_failures": config.get("heartbeat_max_failures"),
                        "trajectory_sample_hz": config.get("trajectory_sample_hz"),
                        "trajectory_capacity": config.get("trajectory_capacity"),
                        "recordings": [entry["name"] for entry in recorder.list_recordings(limit=20)],
                    },
                    origin=origin,
                )
                return
            self._send_error_json("Not found", 404, origin=origin)
            return

        file_path = _safe_static_path(self.path)
        if file_path is None:
            self.send_error(403)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".xml": "application/xml; charset=utf-8",
            ".urdf": "application/xml; charset=utf-8",
            ".dae": "model/vnd.collada+xml",
            ".stl": "model/stl",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }
        try:
            data = file_path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", types.get(file_path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    config = load_config()
    # Resolve the credential at startup. Handler requests use this cached value
    # and never reopen .env/config.json for secrets.
    get_heartbeat_token()
    HEARTBEAT_THROTTLE.configure(
        int(config.get("heartbeat_max_failures", 5)),
        float(config.get("heartbeat_lockout_seconds", 60.0)),
    )
    LATENCY.configure(str(config.get("latency_mode", "monotonic")))
    http_host = str(config.get("http_host", "127.0.0.1"))
    http_port = int(config.get("http_port", 8080))
    server = MonitorHTTPServer((http_host, http_port), Handler)
    monitor = RTDEMonitor(config, STATE, TRAJECTORY, _RECORDER_HOLDER.get("recorder"), route_local_ip)
    monitor.start()
    publisher = StatePublisher(STATE, EVENT_STREAM, config, snapshot=monitor_snapshot)
    publisher.start()
    monitor_ip = str(config.get("monitor_advertised_ip") or route_local_ip(str(config["robot_ip"])))
    logging.info("Monitor running locally at http://127.0.0.1:%s", http_port)
    if http_host in ("0.0.0.0", "::") and monitor_ip:
        logging.info("LAN heartbeat endpoint: http://%s:%s/api/control/heartbeat", monitor_ip, http_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop_event.set()
        publisher.stop_event.set()
        server.server_close()
        monitor.join(timeout=3.0)
        publisher.join(timeout=3.0)
        recorder = _RECORDER_HOLDER.get("recorder")
        if recorder is not None:
            recorder.stop()


if __name__ == "__main__":
    main()
