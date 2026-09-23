"""End-to-end smoke test against a live server process (no robot required).

Run from anywhere:
    .venv/Scripts/python.exe tools/smoke_test.py

It starts server.py on a throwaway port with a sandboxed configuration, exercises
every HTTP endpoint, then shuts the process down again. No RTDE connection is
opened and no robot is contacted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8099
TOKEN = "smoke-token-0123456789abcdef"

# The server keeps its mutable paths in monitor_config, so point that module at
# the throwaway directory before load_config() reads anything. ROOT and
# CONFIG_PATH stay in sync because ensure_recorder() reads ROOT at call time.
BOOT = (
    "import sys; sys.argv=['server.py']; "
    "import pathlib, monitor_config; "
    "root = pathlib.Path(r'{root}'); "
    "monitor_config.ROOT = root; "
    "monitor_config.CONFIG_PATH = root / 'config.json'; "
    "monitor_config.ENV_PATH = root / '.env'; "
    "monitor_config.clear_config_cache(); "
    # Disable the RTDE worker's connection branch even when the dependency is installed.
    "import rtde_client; rtde_client.rtde = None; rtde_client.rtde_config = None; "
    "import server; "
    "server.HEARTBEAT_THROTTLE.configure(3, 30.0); "
    "server.main()"
)


def request(method: str, path: str, *, body: bytes | None = None, headers: dict | None = None, timeout: float = 5.0):
    url = f"http://127.0.0.1:{PORT}{path}"
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "config.json").write_text(json.dumps({
            "robot_ip": "127.0.0.1",
            "rtde_port": 30004,
            "rtde_frequency": 25.0,
            "http_host": "127.0.0.1",
            "http_port": PORT,
            "monitor_advertised_ip": "127.0.0.1",
            "controller_heartbeat_timeout": 4.0,
            "cors_allowed_origins": [],
            "heartbeat_max_failures": 3,
            "heartbeat_lockout_seconds": 30.0,
            "event_stream_frequency": 20.0,
            "recording_directory": "recordings",
            "recording_max_seconds": 60.0,
            "robot_model": "UR10",
            "latency_mode": "monotonic",
            "trajectory_capacity": 20000,
            "trajectory_sample_hz": 25.0,
        }), encoding="utf-8")

        env = dict(os.environ)
        env["UR_MONITOR_HEARTBEAT_TOKEN"] = TOKEN
        # Reuse the real server module but keep its writable state in the sandbox.
        boot = BOOT.format(root=root)
        proc = subprocess.Popen(
            [sys.executable, "-c", boot],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        failures: list[str] = []
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    status, _, _ = request("GET", "/api/config", timeout=1.0)
                    if status == 200:
                        break
                except Exception:
                    time.sleep(0.2)
            else:
                print("FAIL: server did not start")
                return 1

            # --- /api/config -------------------------------------------------
            status, _, body = request("GET", "/api/config")
            payload = json.loads(body)
            assert status == 200, status
            assert payload["event_stream_frequency"] == 20.0, payload
            assert payload["heartbeat_max_failures"] == 3, payload
            assert payload["robot_model"] == "UR10", payload
            assert payload["latency_mode"] == "monotonic", payload
            assert payload["trajectory_capacity"] == 20000, payload
            assert TOKEN not in body.decode(), "token leaked in /api/config"
            print("ok   /api/config exposes extended settings without the token")

            # --- flight recorder / latency / diagnostics ----------------------
            status, _, body = request("GET", "/api/trajectory")
            trajectory = json.loads(body)
            assert status == 200, status
            assert trajectory["points"] == [], trajectory
            assert trajectory["status"]["points"] == 0, trajectory
            assert trajectory["status"]["capacity"] == 20000, trajectory
            assert trajectory["clear_requires_token"] is True, trajectory
            print("ok   /api/trajectory starts empty and reports capacity")

            status, _, body = request("GET", "/api/latency")
            latency = json.loads(body)
            assert status == 200, status
            assert latency["latency"]["mode"] == "monotonic", latency
            assert latency["latency"]["last_ms"] is None, latency
            assert latency["latency"]["samples"] == 0, latency
            print("ok   /api/latency reports the configured mode with no samples")

            status, _, body = request("GET", "/api/diagnostics")
            diagnostics = json.loads(body)
            assert status == 200, status
            diag = diagnostics["diagnostics"]
            assert diag["samples_read"] == 0, diagnostics
            # The isolated bootstrap disables RTDE connections. Keep checking
            # the diagnostics schema without relying on a connection attempt.
            assert isinstance(diag["reconnects"], int), diagnostics
            assert diag["connected"] is False, diagnostics
            assert "read-only" in diag["protocol"], diagnostics
            print("ok   /api/diagnostics reports read-only counters")

            status, _, body = request("GET", "/api/baseline")
            assert status == 400, (status, body)
            assert "required" in json.loads(body)["error"], body
            status, _, body = request("GET", "/api/baseline?left=nope.csv&right=nope.csv")
            assert status == 404, (status, body)
            print("ok   /api/baseline validates its arguments and missing files")

            status, _, _ = request("POST", "/api/trajectory/clear", body=b"{}")
            assert status == 401, status
            status, _, body = request("POST", "/api/trajectory/clear", body=b"{}",
                                      headers={"X-Heartbeat-Token": TOKEN})
            assert status == 200, (status, body)
            assert json.loads(body)["removed"] == 0, body
            print("ok   /api/trajectory/clear is authenticated")

            # --- /api/state --------------------------------------------------
            status, _, body = request("GET", "/api/state")
            state = json.loads(body)
            assert status == 200, status
            for key in ("actual_TCP_speed", "actual_joint_current", "joint_temperatures"):
                assert key in state, f"missing {key} in /api/state"
                assert len(state[key]) == 6, f"{key} is not a 6-vector"
            for key in ("latency", "trajectory", "subscribers"):
                assert key in state, f"missing {key} block in /api/state"
            print("ok   /api/state reports the new RTDE vectors and telemetry blocks")

            # --- SSE ---------------------------------------------------------
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/stream")
            with urllib.request.urlopen(req, timeout=8) as stream:
                assert stream.status == 200
                assert "text/event-stream" in stream.headers.get("Content-Type", "")
                first = stream.readline()
                assert first.startswith(b": keep-alive") or first.startswith(b"data: "), first
            print("ok   /api/stream serves text/event-stream")

            # --- heartbeat auth ---------------------------------------------
            status, _, _ = request("POST", "/api/control/heartbeat", body=b"{}")
            assert status == 401, status
            status, _, body = request(
                "POST", "/api/control/heartbeat",
                body=json.dumps({"name": "smoke"}).encode(),
                headers={"X-Heartbeat-Token": TOKEN},
            )
            assert status == 200, (status, body)
            assert json.loads(body)["observed_ip"] == "127.0.0.1"
            print("ok   heartbeat requires and accepts the token")

            # --- lockout -----------------------------------------------------
            for _ in range(3):
                request("POST", "/api/control/heartbeat", body=b"{}",
                        headers={"X-Heartbeat-Token": "wrong"})
            status, headers, body = request("POST", "/api/control/heartbeat", body=b"{}",
                                            headers={"X-Heartbeat-Token": TOKEN})
            assert status == 429, (status, body)
            assert "Retry-After" in headers, headers
            print("ok   repeated bad tokens trigger 429 with Retry-After")

            # Restart the server so the lockout does not hide the recording checks.
            proc.terminate()
            proc.wait(timeout=10)
            proc = subprocess.Popen(
                [sys.executable, "-c", boot],
                cwd=str(ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if request("GET", "/api/config", timeout=1.0)[0] == 200:
                        break
                except Exception:
                    time.sleep(0.2)

            # --- recording ---------------------------------------------------
            auth = {"X-Heartbeat-Token": TOKEN}
            status, _, _ = request("POST", "/api/recording/start", body=b"{}")
            assert status == 401, status
            status, _, body = request("POST", "/api/recording/start", body=b"{}", headers=auth)
            assert status == 200, (status, body)
            started = json.loads(body)["recording"]
            assert started["recording"] is True, started
            name = started["file"]
            status, _, body = request("POST", "/api/recording/start", body=b"{}", headers=auth)
            assert status == 409, (status, body)
            status, _, body = request("POST", "/api/recording/stop", body=b"{}", headers=auth)
            assert status == 200, status
            print("ok   recording start/stop is authenticated and single-flight")

            status, _, body = request("GET", "/api/recording")
            listing = json.loads(body)
            assert status == 200
            assert name in [item["name"] for item in listing["files"]], listing
            status, headers, body = request("GET", f"/api/recording/download/{name}")
            assert status == 200, status
            assert "text/csv" in headers.get("Content-Type", "")
            header = body.decode().splitlines()[0].split(",")
            assert header[0] == "wall_time", header
            assert "actual_q6" in header, header
            assert "tcp_speed_linear" in header, header
            assert len(header) == 33, len(header)
            print(f"ok   recording CSV downloads with {len(header)} columns")

            status, _, _ = request("GET", "/api/recording/download/../server.py")
            assert status in (403, 404), status
            print("ok   recording download rejects traversal")

            # --- static + CORS ----------------------------------------------
            status, headers, _ = request("GET", "/app.js")
            assert status == 200 and "text/javascript" in headers.get("Content-Type", "")
            for asset in ("/theme.js", "/scene.js", "/replay.js"):
                status, _, _ = request("GET", asset)
                assert status == 200, (asset, status)
            status, _, body = request("GET", "/ur10.urdf")
            assert status == 200, status
            assert b"<robot" in body, "URDF did not serve"
            status, _, _ = request("GET", "/meshes/ur10/base.dae")
            assert status == 200, status
            print("ok   static assets, extra modules and the UR10 model serve")

            status, _, _ = request("GET", "/api/state", headers={"Origin": "http://evil.test:8099"})
            assert status == 403, status
            print("ok   disallowed origins are rejected")

        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
        if failures:
            print("\n--- server log ---")
            print(out[-3000:])
            print("\nFAIL:", *failures, sep="\n  ")
            return 1
        print("\nSMOKE TEST PASSED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
