"""Tests for the hardening, event-stream and recording additions."""
from __future__ import annotations

import csv
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import controller as controller_mod
import events as events_mod
import monitor_config
import recording as recording_mod
import server
import telemetry_state


def _ensure_recorder_for_test(config):
    """Drive the real recorder factory but read/write the module ROOT."""
    return server.ensure_recorder_for(config)



class ConfigExtensionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tempdir.name) / "config.json"
        self.original_config_path = monitor_config.CONFIG_PATH
        self.original_env_path = monitor_config.ENV_PATH
        monitor_config.CONFIG_PATH = self.config_path
        monitor_config.ENV_PATH = Path(self.tempdir.name) / ".env"
        monitor_config.load_config.cache_clear()

    def tearDown(self):
        monitor_config.CONFIG_PATH = self.original_config_path
        monitor_config.ENV_PATH = self.original_env_path
        monitor_config.load_config.cache_clear()
        self.tempdir.cleanup()

    def write(self, **overrides):
        value = {
            "robot_ip": "127.0.0.1",
            "rtde_port": 30004,
            "rtde_frequency": 25.0,
            "http_host": "127.0.0.1",
            "http_port": 8765,
            "monitor_advertised_ip": "127.0.0.1",
            "controller_heartbeat_timeout": 4.0,
            "cors_allowed_origins": [],
        }
        value.update(overrides)
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        monitor_config.load_config.cache_clear()
        return monitor_config.load_config()

    def test_new_defaults_are_applied(self):
        config = self.write()
        self.assertEqual(config["heartbeat_max_failures"], 5)
        self.assertEqual(config["heartbeat_lockout_seconds"], 60.0)
        self.assertEqual(config["event_stream_frequency"], 10.0)
        self.assertEqual(config["recording_directory"], "recordings")
        self.assertEqual(config["recording_max_seconds"], 3600.0)

    def test_recording_directory_rejects_traversal_and_absolute_paths(self):
        for value in ["../escape", "/etc", "C:/Windows", "a/../b", "", "   ", "a:b"]:
            with self.subTest(value=value), self.assertRaises(monitor_config.ConfigError):
                self.write(recording_directory=value)
        self.assertEqual(self.write(recording_directory="logs\\runs")["recording_directory"], "logs/runs")

    def test_numeric_bounds_are_enforced(self):
        for overrides in [
            {"heartbeat_max_failures": 0},
            {"heartbeat_max_failures": 101},
            {"heartbeat_max_failures": True},
            {"heartbeat_max_failures": "5"},
            {"heartbeat_lockout_seconds": -1},
            {"event_stream_frequency": 0},
            {"recording_max_seconds": 0},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(monitor_config.ConfigError):
                self.write(**overrides)


class HeartbeatThrottleTests(unittest.TestCase):
    def test_lockout_after_repeated_failures(self):
        throttle = controller_mod.HeartbeatThrottle(max_failures=3, lockout_seconds=60.0)
        self.assertEqual(throttle.retry_after("192.0.2.5"), 0.0)
        for _ in range(2):
            throttle.record_failure("192.0.2.5")
        self.assertEqual(throttle.retry_after("192.0.2.5"), 0.0)
        throttle.record_failure("192.0.2.5")
        self.assertGreater(throttle.retry_after("192.0.2.5"), 0.0)
        # A different source is unaffected by another client's failures.
        self.assertEqual(throttle.retry_after("192.0.2.6"), 0.0)

    def test_success_clears_failures(self):
        throttle = controller_mod.HeartbeatThrottle(max_failures=2, lockout_seconds=60.0)
        throttle.record_failure("192.0.2.5")
        throttle.clear("192.0.2.5")
        throttle.record_failure("192.0.2.5")
        self.assertEqual(throttle.retry_after("192.0.2.5"), 0.0)

    def test_expired_lockout_releases_source(self):
        throttle = controller_mod.HeartbeatThrottle(max_failures=1, lockout_seconds=0.05)
        throttle.record_failure("192.0.2.5")
        self.assertGreater(throttle.retry_after("192.0.2.5"), 0.0)
        time.sleep(0.08)
        self.assertEqual(throttle.retry_after("192.0.2.5"), 0.0)

    def test_lockout_disabled_when_window_is_zero(self):
        throttle = controller_mod.HeartbeatThrottle(max_failures=1, lockout_seconds=0.0)
        for _ in range(5):
            throttle.record_failure("192.0.2.5")
        self.assertEqual(throttle.retry_after("192.0.2.5"), 0.0)


class EventStreamBrokerTests(unittest.TestCase):
    def test_publish_reaches_every_subscriber(self):
        broker = events_mod.EventStreamBroker(max_queue=4)
        first = broker.subscribe()
        second = broker.subscribe()
        self.assertEqual(broker.subscriber_count(), 2)
        broker.publish({"connected": True})
        self.assertEqual(json.loads(first.get_nowait()), {"connected": True})
        self.assertEqual(json.loads(second.get_nowait()), {"connected": True})

    def test_slow_subscriber_drops_oldest_instead_of_blocking(self):
        broker = events_mod.EventStreamBroker(max_queue=2)
        subscriber = broker.subscribe()
        for index in range(5):
            broker.publish({"n": index})
        self.assertEqual(subscriber.qsize(), 2)
        self.assertEqual(json.loads(subscriber.get_nowait()), {"n": 3})

    def test_unsubscribe_stops_delivery(self):
        broker = events_mod.EventStreamBroker()
        subscriber = broker.subscribe()
        broker.unsubscribe(subscriber)
        self.assertEqual(broker.subscriber_count(), 0)
        broker.publish({"connected": True})
        self.assertTrue(subscriber.empty())

    def test_unserialisable_payload_is_ignored(self):
        broker = events_mod.EventStreamBroker()
        subscriber = broker.subscribe()
        broker.publish({"bad": float("nan")})
        self.assertTrue(subscriber.empty())


class RecordingSessionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name) / "recordings"

    def tearDown(self):
        self.tempdir.cleanup()

    def sample(self, **overrides):
        value = {
            "connected": True,
            "timestamp": 1234.5,
            "actual_q": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
            "actual_TCP_pose": [0.1, 0.2, 0.3, 0.0, 0.0, 1.57],
            "actual_TCP_speed": [0.03, 0.04, 0.0, 0.0, 0.0, 0.0],
            "actual_joint_current": [1.0] * 6,
            "joint_temperatures": [40.0] * 6,
            "robot_mode": 7,
            "safety_mode": 1,
            "speed_scaling": 1.0,
            "actual_digital_input_bits": 3,
            "actual_digital_output_bits": 1,
        }
        value.update(overrides)
        return value

    def test_start_writes_header_and_rows(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        status = recorder.start(now=1_700_000_000.0)
        self.assertTrue(status["recording"])
        self.assertEqual(status["rows"], 0)
        recorder.write(self.sample())
        recorder.write(self.sample())
        final = recorder.stop()
        self.assertFalse(final["recording"])
        self.assertEqual(final["rows"], 2)
        with (self.directory / final["file"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], list(recording_mod.RECORDING_COLUMNS))
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[1]), len(recording_mod.RECORDING_COLUMNS))

    def test_linear_and_angular_speed_are_magnitudes(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        recorder.write(self.sample())
        final = recorder.stop()
        with (self.directory / final["file"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        row = rows[1]
        linear = row[header.index("tcp_speed_linear")]
        angular = row[header.index("tcp_speed_angular")]
        self.assertAlmostEqual(float(linear), 0.05, places=6)
        self.assertAlmostEqual(float(angular), 0.0, places=6)

    def test_disconnected_snapshots_are_skipped(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        recorder.write(self.sample(connected=False))
        recorder.write(self.sample(timestamp=None))
        self.assertEqual(recorder.stop()["rows"], 0)

    def test_double_start_is_rejected(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        with self.assertRaises(RuntimeError):
            recorder.start()
        recorder.stop()

    def test_stop_without_start_is_idempotent(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        self.assertFalse(recorder.stop()["recording"])

    def test_max_seconds_auto_stops(self):
        recorder = recording_mod.RecordingSession(self.directory, 1.0)
        recorder.start(now=1_700_000_000.0)
        recorder._started_monotonic = time.monotonic() - 5.0
        recorder.write(self.sample())
        status = recorder.status()
        self.assertFalse(status["recording"])
        self.assertTrue(status["auto_stopped"])

    def test_writes_every_selected_output_column(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        recorder.write(self.sample())
        final = recorder.stop()
        with (self.directory / final["file"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        row = rows[1]
        header = rows[0]
        for name in ["actual_q3", "tcp_rz", "joint_temp_6", "digital_input_bits"]:
            self.assertTrue(row[header.index(name)].strip())

    def test_missing_values_become_empty_fields(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        recorder.write(self.sample(speed_scaling=None, actual_digital_input_bits=None))
        final = recorder.stop()
        with (self.directory / final["file"]).open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        self.assertEqual(rows[1][header.index("speed_scaling")], "")
        self.assertEqual(rows[1][header.index("digital_input_bits")], "")

    def test_download_path_rejects_traversal(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        recorder.start(now=1_700_000_000.0)
        recorder.write(self.sample())
        name = recorder.stop()["file"]
        self.assertIsNotNone(recorder.download_path(name))
        for value in ["../server.py", "..\\server.py", "a/b.csv", "/etc/passwd", "x.txt", "", "sub/../" + name]:
            with self.subTest(value=value):
                self.assertIsNone(recorder.download_path(value))

    def test_list_recordings_is_newest_first(self):
        recorder = recording_mod.RecordingSession(self.directory, 3600.0)
        for index in range(2):
            recorder.start(now=1_700_000_000.0 + index * 10)
            recorder.write(self.sample())
            recorder.stop()
        listed = recorder.list_recordings()
        self.assertEqual(len(listed), 2)
        self.assertGreaterEqual(listed[0]["modified"], listed[1]["modified"])


class EnsureRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_root = monitor_config.ROOT
        self.original_recorder = server._get_recorder()
        monitor_config.ROOT = Path(self.tempdir.name)
        server._set_recorder(None)

    def tearDown(self):
        if server._get_recorder() is not None:
            server._get_recorder().stop()
        monitor_config.ROOT = self.original_root
        server._set_recorder(self.original_recorder)
        self.tempdir.cleanup()

    def test_recorder_is_shared_between_calls(self):
        config = {"recording_directory": "recordings", "recording_max_seconds": 60.0}
        first = _ensure_recorder_for_test(config)
        self.assertIs(first, _ensure_recorder_for_test(config))
        self.assertEqual(first._directory, (Path(self.tempdir.name) / "recordings").resolve())

    def test_recorder_is_rebuilt_when_settings_change(self):
        first = _ensure_recorder_for_test({"recording_directory": "a", "recording_max_seconds": 60.0})
        second = _ensure_recorder_for_test({"recording_directory": "b", "recording_max_seconds": 60.0})
        self.assertIsNot(first, second)
        self.assertEqual(second._directory, (Path(self.tempdir.name) / "b").resolve())


class StatePublisherTests(unittest.TestCase):
    def test_publisher_skips_work_without_subscribers(self):
        state = telemetry_state.StateStore()
        broker = events_mod.EventStreamBroker()
        publisher = events_mod.StatePublisher(state, broker, {"event_stream_frequency": 20.0})
        self.assertAlmostEqual(publisher.interval, 0.05, places=6)
        publisher.start()
        time.sleep(0.15)
        self.assertEqual(broker.subscriber_count(), 0)
        publisher.stop_event.set()
        publisher.join(timeout=2.0)
        self.assertFalse(publisher.is_alive())

    def test_publisher_delivers_to_subscriber(self):
        state = telemetry_state.StateStore()
        broker = events_mod.EventStreamBroker()
        publisher = events_mod.StatePublisher(state, broker, {"event_stream_frequency": 20.0})
        subscriber = broker.subscribe()
        publisher.start()
        try:
            message = subscriber.get(timeout=2.0)
        finally:
            publisher.stop_event.set()
            publisher.join(timeout=2.0)
        self.assertIn("connected", json.loads(message))


class ThrottledHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import http.client

        cls.http = http.client
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.config_path = Path(cls.tempdir.name) / "config.json"
        cls.config_path.write_text(json.dumps({
            "robot_ip": "127.0.0.1",
            "rtde_port": 30004,
            "rtde_frequency": 25.0,
            "http_host": "127.0.0.1",
            "http_port": 8766,
            "monitor_advertised_ip": "127.0.0.1",
            "controller_heartbeat_timeout": 4.0,
            "cors_allowed_origins": [],
            "heartbeat_max_failures": 2,
            "heartbeat_lockout_seconds": 2.0,
            "recording_directory": "recordings",
            "recording_max_seconds": 60.0,
        }), encoding="utf-8")
        cls.original_config_path = monitor_config.CONFIG_PATH
        cls.original_env_path = monitor_config.ENV_PATH
        cls.original_root = monitor_config.ROOT
        cls.original_recorder = server._get_recorder()
        monitor_config.CONFIG_PATH = cls.config_path
        monitor_config.ENV_PATH = Path(cls.tempdir.name) / ".env"
        monitor_config.ROOT = Path(cls.tempdir.name)
        server._set_recorder(None)
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        cls.token_patch = patch.dict(os.environ, {monitor_config.HEARTBEAT_TOKEN_ENV: "unit-token"}, clear=False)
        cls.token_patch.start()
        server.get_heartbeat_token.cache_clear()
        server.HEARTBEAT_THROTTLE.configure(2, 2.0)
        cls.httpd = server.MonitorHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        if server._get_recorder() is not None:
            server._get_recorder().stop()
        cls.token_patch.stop()
        monitor_config.CONFIG_PATH = cls.original_config_path
        monitor_config.ENV_PATH = cls.original_env_path
        monitor_config.ROOT = cls.original_root
        server._set_recorder(cls.original_recorder)
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")
        cls.tempdir.cleanup()

    def request(self, method, path, *, body=None, headers=None):
        connection = self.http.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def setUp(self):
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

    def test_repeated_failures_trigger_lockout(self):
        try:
            for _ in range(2):
                status, _, _ = self.request("POST", "/api/control/heartbeat", body=b"{}",
                                            headers={"X-Heartbeat-Token": "wrong"})
                self.assertEqual(status, 401)
            status, headers, body = self.request("POST", "/api/control/heartbeat", body=b"{}",
                                                 headers={"X-Heartbeat-Token": "unit-token"})
            self.assertEqual(status, 429)
            self.assertIn("Retry-After", headers)
            self.assertIn("Too many", body.decode())
        finally:
            server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

    def test_correct_token_clears_failure_counter(self):
        server.HEARTBEAT_THROTTLE.record_failure("127.0.0.1")
        status, _, _ = self.request("POST", "/api/control/heartbeat", body=b"{}",
                                    headers={"X-Heartbeat-Token": "unit-token"})
        self.assertEqual(status, 200)
        self.assertEqual(server.HEARTBEAT_THROTTLE.retry_after("127.0.0.1"), 0.0)

    def test_header_token_wins_over_bearer_and_both_are_accepted(self):
        body = json.dumps({"name": "Mac"}).encode()
        status, _, _ = self.request("POST", "/api/control/heartbeat", body=body, headers={
            "X-Heartbeat-Token": "unit-token", "Authorization": "Bearer wrong-token",
        })
        self.assertEqual(status, 200)
        status, _, _ = self.request("POST", "/api/controller/heartbeat", body=body,
                                    headers={"Authorization": "Bearer unit-token"})
        self.assertEqual(status, 200)

    def test_recording_start_stop_requires_auth(self):
        status, _, _ = self.request("POST", "/api/recording/start", body=b"{}")
        self.assertEqual(status, 401)
        status, _, body = self.request("POST", "/api/recording/start", body=b"{}",
                                       headers={"X-Heartbeat-Token": "unit-token"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["recording"]["recording"])
        status, _, body = self.request("POST", "/api/recording/stop", body=b"{}",
                                       headers={"X-Heartbeat-Token": "unit-token"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["recording"]["recording"])

    def test_double_start_returns_conflict(self):
        headers = {"X-Heartbeat-Token": "unit-token"}
        self.request("POST", "/api/recording/start", body=b"{}", headers=headers)
        try:
            status, _, _ = self.request("POST", "/api/recording/start", body=b"{}", headers=headers)
            self.assertEqual(status, 409)
        finally:
            self.request("POST", "/api/recording/stop", body=b"{}", headers=headers)

    def test_recording_status_and_download(self):
        headers = {"X-Heartbeat-Token": "unit-token"}
        self.request("POST", "/api/recording/start", body=b"{}", headers=headers)
        server._get_recorder().write({
            "connected": True, "timestamp": 1.0,
            "actual_q": [0.0] * 6, "actual_TCP_pose": [0.0] * 6,
            "actual_TCP_speed": [0.0] * 6, "actual_joint_current": [0.0] * 6,
            "joint_temperatures": [0.0] * 6, "robot_mode": 7, "safety_mode": 1,
            "speed_scaling": 1.0, "actual_digital_input_bits": 0, "actual_digital_output_bits": 0,
        })
        name = server._get_recorder().status()["file"]
        self.request("POST", "/api/recording/stop", body=b"{}", headers=headers)
        status, _, body = self.request("GET", "/api/recording")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["recording"]["rows"], 1)
        self.assertIn(name, [item["name"] for item in payload["files"]])
        status, headers_out, body = self.request("GET", f"/api/recording/download/{name}")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers_out["Content-Type"])
        self.assertIn(b"actual_q1", body)
        status, _, _ = self.request("GET", "/api/recording/download/../server.py")
        self.assertIn(status, {403, 404})

    def test_event_stream_delivers_snapshot(self):
        holder = {}

        def reader():
            connection = self.http.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
            connection.request("GET", "/api/stream")
            response = connection.getresponse()
            holder["status"] = response.status
            holder["type"] = response.getheader("Content-Type")
            chunk = b""
            while b"data: " not in chunk:
                piece = response.readline()
                if not piece:
                    break
                chunk += piece
                if len(chunk) > 8192:
                    break
            holder["body"] = chunk
            connection.close()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = time.monotonic() + 3.0
        while server.EVENT_STREAM.subscriber_count() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        server.EVENT_STREAM.publish({"connected": True, "probe": True})
        thread.join(timeout=5)
        self.assertEqual(holder.get("status"), 200)
        self.assertIn("text/event-stream", holder.get("type", ""))
        self.assertIn(b"probe", holder.get("body", b""))

    def test_config_endpoint_reports_extended_settings(self):
        status, _, body = self.request("GET", "/api/config")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["event_stream_frequency"], 10.0)
        self.assertEqual(payload["heartbeat_max_failures"], 2)
        self.assertNotIn("unit-token", body.decode())


if __name__ == "__main__":
    unittest.main()
