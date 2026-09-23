#!/usr/bin/env python3
"""Identify the Mac Vision controller to the Windows UR10 monitor.

This helper only sends an authenticated HTTP heartbeat to the monitor. It never
sends URScript, motion commands, or RTDE input data to the robot.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TOKEN_ENV = "UR_MONITOR_HEARTBEAT_TOKEN"
DEFAULT_MONITOR = "http://192.0.2.20:8080"
DEFAULT_ROBOT_IP = "192.0.2.10"
MAX_ERROR_BODY = 512


class HeartbeatError(RuntimeError):
    """An expected heartbeat request or response error."""


def validate_token(value: str | None) -> str:
    """Validate a token before putting it into an HTTP header."""
    token = str(value or "")
    if not token:
        raise ValueError(
            f"missing heartbeat token; set {TOKEN_ENV} or pass --token"
        )
    try:
        token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("heartbeat token must contain ASCII characters only") from exc
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in token):
        raise ValueError("heartbeat token must not contain whitespace or control characters")
    if len(token) > 256:
        raise ValueError("heartbeat token is too long (maximum 256 characters)")
    return token


def positive_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number greater than zero") from exc
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("interval must be a finite number greater than zero")
    return interval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Identify a Mac Vision controller in the UR10 Monitor"
    )
    parser.add_argument("--monitor", default=DEFAULT_MONITOR, help="monitor base URL")
    parser.add_argument(
        "--token",
        default=None,
        help=f"heartbeat token; defaults to {TOKEN_ENV} (avoid putting secrets in shell history)",
    )
    parser.add_argument("--name", default="Mac Vision controller")
    parser.add_argument("--state", default="controlling")
    parser.add_argument("--protocol", default="Vision / RTDE")
    parser.add_argument("--details", default="", help="optional controller status details")
    parser.add_argument("--robot-ip", default=DEFAULT_ROBOT_IP)
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--interval",
        type=positive_interval,
        default=1.0,
        help="seconds between heartbeats (default: 1.0)",
    )
    return parser


def token_from_args(args: argparse.Namespace) -> str:
    """Resolve an explicit token first, then the process environment."""
    return validate_token(args.token if args.token is not None else os.environ.get(TOKEN_ENV))


def _response_body(response: Any) -> str:
    raw = response.read(MAX_ERROR_BODY + 1)
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    if len(text) > MAX_ERROR_BODY:
        text = text[:MAX_ERROR_BODY] + "…"
    return text


def _describe_http_error(error: urllib.error.HTTPError) -> str:
    body = _response_body(error)
    detail = ""
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict) and payload.get("error"):
                detail = f": {payload['error']}"
            else:
                detail = f": {body}"
        except json.JSONDecodeError:
            detail = f": {body}"
    return f"monitor rejected heartbeat with HTTP {error.code}{detail}"


def send_heartbeat(args: argparse.Namespace) -> dict[str, Any]:
    """Send one heartbeat and return the monitor's JSON response."""
    token = token_from_args(args)
    payload = {
        "name": args.name,
        "state": args.state,
        "protocol": args.protocol,
        "session_id": args.session_id,
        "robot_ip": args.robot_ip,
        "details": args.details,
    }
    request = urllib.request.Request(
        args.monitor.rstrip("/") + "/api/control/heartbeat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Heartbeat-Token": token,
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as exc:
        try:
            message = _describe_http_error(exc)
        except (TimeoutError, OSError):
            message = f"monitor rejected heartbeat with HTTP {exc.code}; error body unavailable"
        finally:
            exc.close()
        raise HeartbeatError(message.replace(token, "[redacted]")) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HeartbeatError(f"could not reach monitor: {exc}") from exc

    try:
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        body = _response_body(response)
    except (TimeoutError, OSError) as exc:
        raise HeartbeatError(f"could not read monitor response: {exc}") from exc
    finally:
        response.close()
    if not 200 <= int(status) < 300:
        raise HeartbeatError(f"monitor returned unexpected HTTP status {status}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HeartbeatError("monitor returned invalid JSON") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise HeartbeatError("monitor returned an invalid heartbeat response")
    if not result.get("observed_ip"):
        raise HeartbeatError("monitor response did not include observed_ip")
    return result


def run(args: argparse.Namespace) -> int:
    """Send heartbeats until interrupted."""
    try:
        token_from_args(args)
    except ValueError as exc:
        print(f"heartbeat configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        while True:
            try:
                result = send_heartbeat(args)
                print(
                    "heartbeat accepted; Windows observed controller IP: "
                    f"{result['observed_ip']}"
                )
            except HeartbeatError as exc:
                print(f"heartbeat failed: {exc}", file=sys.stderr)
            time.sleep(max(0.25, args.interval))
    except KeyboardInterrupt:
        print("heartbeat stopped", file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
