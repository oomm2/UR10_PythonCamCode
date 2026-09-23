#!/usr/bin/env python3
"""Configuration and secret loading for the read-only UR RTDE monitor.

Everything here is pure I/O against ``config.json`` and ``.env``; it holds no
robot state and never talks to the robot. Splitting it out keeps ``server.py``
focused on HTTP handling and the RTDE reader.

Two caches live here and both invalidate on file signature changes:

* ``load_config`` memoises the validated configuration dict.
* ``get_heartbeat_token`` resolves the shared secret exactly once per process so
  request handlers never reopen ``.env`` while serving traffic.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import socket
import threading
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"
RECIPE_PATH = ROOT / "monitor.xml"
STATIC_DIR = ROOT / "static"
VENDOR_DIR = ROOT / "static" / "vendor"
HEARTBEAT_TOKEN_ENV = "UR_MONITOR_HEARTBEAT_TOKEN"


class ConfigError(ValueError):
    """The monitor configuration is missing or invalid."""


_CONFIG_LOCK = threading.RLock()
_CONFIG_SIGNATURE: tuple[int, int] | None = None
_CONFIG_PATH_CACHE: Path | None = None
_CONFIG_VALUE: dict[str, Any] | None = None
_CONFIG_ERROR_SIGNATURE: tuple[int, int] | None = None
_CONFIG_ERROR_MESSAGE: str | None = None

_TOKEN_LOCK = threading.Lock()
_TOKEN_READY = False
_HEARTBEAT_TOKEN: str | None = None


def _finite_number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        suffix = f" >= {minimum}" if minimum is not None else ""
        raise ConfigError(f"{name} must be finite{suffix}")
    return result


def _port(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"{name} must be an integer from 1 to 65535")
    return value


def _validate_origin(origin: Any) -> str:
    if not isinstance(origin, str) or not origin or origin != origin.strip():
        raise ConfigError("cors_allowed_origins entries must be non-empty strings")
    if "*" in origin or "\x00" in origin:
        raise ConfigError("cors_allowed_origins does not allow wildcards or NUL bytes")
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"invalid CORS origin: {origin!r}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or parsed.netloc != parsed.netloc.strip()
        or origin.endswith("/")
    ):
        raise ConfigError(f"invalid exact CORS origin: {origin!r}")
    return origin


def _validate_host(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigError(f"{name} must be a non-empty host string")
    if any(character in value for character in "\x00/\\ \t\r\n"):
        raise ConfigError(f"{name} contains invalid characters")
    return value


def _validate_relative_directory(value: Any) -> str:
    """Validate a directory name that must stay relative to the project root."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigError("recording_directory must be a non-empty string")
    if any(character in value for character in "\x00\r\n"):
        raise ConfigError("recording_directory contains invalid characters")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or PureWindowsPath(normalized).drive:
        raise ConfigError("recording_directory must be a relative path inside the project")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {"..", "."} or ":" in part for part in parts):
        raise ConfigError("recording_directory must not contain traversal segments")
    return "/".join(parts)


def _validate_choice(value: Any, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        options = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {options}")
    return value


def _validate_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError("config.json must contain a JSON object")
    config = dict(raw)
    config["robot_ip"] = _validate_host(config.get("robot_ip"), "robot_ip")
    config["rtde_port"] = _port(config.get("rtde_port", 30004), "rtde_port")
    config["rtde_frequency"] = _finite_number(
        config.get("rtde_frequency", 25.0), "rtde_frequency", minimum=0.001
    )
    config["http_host"] = _validate_host(config.get("http_host", "127.0.0.1"), "http_host")
    config["http_port"] = _port(config.get("http_port", 8080), "http_port")
    advertised = config.get("monitor_advertised_ip", "")
    config["monitor_advertised_ip"] = "" if advertised in (None, "") else _validate_host(
        advertised, "monitor_advertised_ip"
    )
    config["controller_heartbeat_timeout"] = _finite_number(
        config.get("controller_heartbeat_timeout", 4.0),
        "controller_heartbeat_timeout",
        minimum=0.001,
    )
    origins = config.get("cors_allowed_origins", [])
    if not isinstance(origins, list):
        raise ConfigError("cors_allowed_origins must be a JSON array")
    config["cors_allowed_origins"] = [_validate_origin(origin) for origin in origins]
    max_failures = config.get("heartbeat_max_failures", 5)
    if isinstance(max_failures, bool) or not isinstance(max_failures, int) or not 1 <= max_failures <= 100:
        raise ConfigError("heartbeat_max_failures must be an integer from 1 to 100")
    config["heartbeat_max_failures"] = max_failures
    config["heartbeat_lockout_seconds"] = _finite_number(
        config.get("heartbeat_lockout_seconds", 60.0),
        "heartbeat_lockout_seconds",
        minimum=0.0,
    )
    config["event_stream_frequency"] = _finite_number(
        config.get("event_stream_frequency", 10.0),
        "event_stream_frequency",
        minimum=0.1,
    )
    config["recording_directory"] = _validate_relative_directory(
        config.get("recording_directory", "recordings")
    )
    config["recording_max_seconds"] = _finite_number(
        config.get("recording_max_seconds", 3600.0),
        "recording_max_seconds",
        minimum=1.0,
    )
    # --- New in 3.1: trajectory buffer, diagnostics and latency instrumentation.
    config["trajectory_capacity"] = _finite_number(
        config.get("trajectory_capacity", 20000),
        "trajectory_capacity",
        minimum=10,
    )
    config["trajectory_sample_hz"] = _finite_number(
        config.get("trajectory_sample_hz", 25.0),
        "trajectory_sample_hz",
        minimum=0.1,
    )
    config["latency_mode"] = _validate_choice(
        config.get("latency_mode", "monotonic"),
        "latency_mode",
        {"monotonic", "clock_sync"},
    )
    config["robot_model"] = _validate_choice(
        config.get("robot_model", "UR10"),
        "robot_model",
        # Only UR10 is built and shipped; the browser loads static/ur10.urdf.
        # Add a name here and to tools/build_models.MODELS to support another arm.
        {"UR10"},
    )
    if "controller_heartbeat_token" in config:
        raise ConfigError(
            "controller_heartbeat_token is not allowed in config.json; use "
            f"{HEARTBEAT_TOKEN_ENV} or the local ignored .env file"
        )
    return config


def _config_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc
    return stat.st_mtime_ns, stat.st_size


def clear_config_cache() -> None:
    """Clear cached configuration; useful after changing CONFIG_PATH in tests."""
    global _CONFIG_SIGNATURE, _CONFIG_PATH_CACHE, _CONFIG_VALUE
    global _CONFIG_ERROR_SIGNATURE, _CONFIG_ERROR_MESSAGE
    with _CONFIG_LOCK:
        _CONFIG_SIGNATURE = None
        _CONFIG_PATH_CACHE = None
        _CONFIG_VALUE = None
        _CONFIG_ERROR_SIGNATURE = None
        _CONFIG_ERROR_MESSAGE = None


def load_config() -> dict[str, Any]:
    """Load and validate config.json, reopening it only after mtime/size changes.

    A previously valid configuration remains in service if a later edit is
    malformed. The first malformed configuration raises ConfigError so startup
    fails clearly instead of silently using defaults.
    """
    global _CONFIG_SIGNATURE, _CONFIG_PATH_CACHE, _CONFIG_VALUE
    global _CONFIG_ERROR_SIGNATURE, _CONFIG_ERROR_MESSAGE
    path = Path(CONFIG_PATH)
    with _CONFIG_LOCK:
        try:
            signature = _config_signature(path)
        except ConfigError as exc:
            if _CONFIG_VALUE is not None and _CONFIG_PATH_CACHE == path:
                if _CONFIG_ERROR_SIGNATURE != (-1, -1):
                    logging.warning("Keeping last known-good monitor config: %s", exc)
                    _CONFIG_ERROR_SIGNATURE = (-1, -1)
                    _CONFIG_ERROR_MESSAGE = str(exc)
                return copy.deepcopy(_CONFIG_VALUE)
            raise

        if _CONFIG_PATH_CACHE == path:
            if _CONFIG_VALUE is not None and _CONFIG_SIGNATURE == signature:
                return copy.deepcopy(_CONFIG_VALUE)
            if _CONFIG_ERROR_SIGNATURE == signature:
                if _CONFIG_VALUE is not None:
                    return copy.deepcopy(_CONFIG_VALUE)
                raise ConfigError(_CONFIG_ERROR_MESSAGE or "invalid monitor configuration")

        try:
            with path.open("r", encoding="utf-8-sig") as file:
                parsed = json.load(file)
            validated = _validate_config(parsed)
        except (OSError, json.JSONDecodeError, ConfigError) as exc:
            message = f"invalid monitor configuration in {path}: {exc}"
            _CONFIG_ERROR_SIGNATURE = signature
            _CONFIG_ERROR_MESSAGE = message
            if _CONFIG_VALUE is not None and _CONFIG_PATH_CACHE == path:
                logging.warning("Keeping last known-good monitor config: %s", message)
                return copy.deepcopy(_CONFIG_VALUE)
            raise ConfigError(message) from exc

        _CONFIG_PATH_CACHE = path
        _CONFIG_SIGNATURE = signature
        _CONFIG_VALUE = validated
        _CONFIG_ERROR_SIGNATURE = None
        _CONFIG_ERROR_MESSAGE = None
        return copy.deepcopy(validated)


# Handy for stdlib tests and callers that already use functools-style cache APIs.
load_config.cache_clear = clear_config_cache  # type: ignore[attr-defined]


def _dotenv_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logging.warning("Cannot read local environment file %s: %s", path, exc)
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        candidate_key, value = stripped.split("=", 1)
        if candidate_key.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def _valid_token(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return None
    if len(value) > 256 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        return None
    return value


def clear_heartbeat_token_cache() -> None:
    global _TOKEN_READY, _HEARTBEAT_TOKEN
    with _TOKEN_LOCK:
        _TOKEN_READY = False
        _HEARTBEAT_TOKEN = None


def get_heartbeat_token() -> str | None:
    """Resolve the secret once, preferring the process environment over .env."""
    global _TOKEN_READY, _HEARTBEAT_TOKEN
    with _TOKEN_LOCK:
        if _TOKEN_READY:
            return _HEARTBEAT_TOKEN
        raw = os.environ.get(HEARTBEAT_TOKEN_ENV)
        if raw is None:
            raw = _dotenv_value(Path(ENV_PATH), HEARTBEAT_TOKEN_ENV)
        _HEARTBEAT_TOKEN = _valid_token(raw)
        if raw not in (None, "") and _HEARTBEAT_TOKEN is None:
            logging.warning("Heartbeat token is invalid; heartbeat writes are disabled")
        if _HEARTBEAT_TOKEN is None:
            logging.warning("No heartbeat token configured; heartbeat writes are disabled")
        _TOKEN_READY = True
        return _HEARTBEAT_TOKEN


get_heartbeat_token.cache_clear = clear_heartbeat_token_cache  # type: ignore[attr-defined]


def route_local_ip(remote_ip: str) -> str:
    """Return the Windows address selected for the route to the robot."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_ip, 9))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        sock.close()


def allowed_origins(config: dict[str, Any]) -> set[str]:
    port = int(config["http_port"])
    origins = {
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    }
    advertised = str(config.get("monitor_advertised_ip") or "")
    if advertised:
        host = f"[{advertised}]" if ":" in advertised and not advertised.startswith("[") else advertised
        origins.add(f"http://{host}:{port}")
    http_host = str(config.get("http_host") or "")
    if http_host not in {"0.0.0.0", "::", ""}:
        host = f"[{http_host}]" if ":" in http_host and not http_host.startswith("[") else http_host
        origins.add(f"http://{host}:{port}")
    origins.update(config.get("cors_allowed_origins", []))
    return origins
