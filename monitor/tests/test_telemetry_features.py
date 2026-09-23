"""Tests for the trajectory buffer, latency tracker, diagnostics and structured
heartbeat additions introduced in the 3.1 refactor."""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor_config
import server
from controller import ControlClientStore, structured_telemetry
from telemetry_state import LatencyTracker, StateStore, TrajectoryBuffer


def pose(x: float = 0.0, y: float = 0.0, z: float = 0.5) -> dict:
    return {
        "connected": True,
        "timestamp": 1.0,
        "actual_TCP_pose": [x, y, z, 0.0, 0.0, 0.0],
        "actual_q": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
    }


class TrajectoryBufferTests(unittest.TestCase):
    def test_records_points_and_reports_status(self):
        buffer = TrajectoryBuffer(capacity=100, sample_hz=1e9, min_distance=0.0)
        buffer.maybe_record(pose(0.0))
        buffer.maybe_record(pose(0.1))
        points = buffer.points()
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0][:3], [0.0, 0.0, 0.5])
        # Nine values per point: x, y, z plus six joint angles.
        self.assertEqual(len(points[1]), 9)
        status = buffer.status()
        self.assertEqual(status["points"], 2)
        self.assertEqual(status["capacity"], 100)

    def test_ring_buffer_drops_oldest_points(self):
        buffer = TrajectoryBuffer(capacity=10, sample_hz=1e9, min_distance=0.0)
        for index in range(50):
            buffer.maybe_record(pose(index * 0.01))
        self.assertEqual(len(buffer.points()), 10)
        self.assertEqual(buffer.points()[-1][0], 0.49)

    def test_limit_returns_newest_window(self):
        buffer = TrajectoryBuffer(capacity=100, sample_hz=1e9, min_distance=0.0)
        for index in range(20):
            buffer.maybe_record(pose(index * 0.01))
        window = buffer.points(5)
        self.assertEqual(len(window), 5)
        self.assertEqual(window[-1][0], 0.19)

    def test_disconnected_snapshots_are_ignored(self):
        buffer = TrajectoryBuffer(sample_hz=1e9, min_distance=0.0)
        buffer.maybe_record({"connected": False, "timestamp": 1.0})
        buffer.maybe_record({"connected": True, "timestamp": None})
        self.assertEqual(buffer.points(), [])

    def test_rate_limit_skips_rapid_samples(self):
        # A very low sample rate clamps to a long interval, so a second call in
        # the same instant is rejected by the time gate.
        buffer = TrajectoryBuffer(capacity=100, sample_hz=0.1, min_distance=0.0)
        buffer.maybe_record(pose(0.0))
        buffer.maybe_record(pose(0.5))
        self.assertEqual(len(buffer.points()), 1)

    def test_min_distance_filter_drops_tiny_moves(self):
        buffer = TrajectoryBuffer(capacity=100, sample_hz=1e9, min_distance=0.05)
        buffer.maybe_record(pose(0.0))
        buffer.maybe_record(pose(0.001))
        self.assertEqual(len(buffer.points()), 1)

    def test_clear_returns_removed_count(self):
        buffer = TrajectoryBuffer(capacity=100, sample_hz=1e9, min_distance=0.0)
        buffer.maybe_record(pose(0.0))
        buffer.maybe_record(pose(0.1))
        self.assertEqual(buffer.clear(), 2)
        self.assertEqual(buffer.points(), [])

    def test_malformed_vectors_are_rejected(self):
        buffer = TrajectoryBuffer(sample_hz=1e9, min_distance=0.0)
        buffer.maybe_record({"connected": True, "timestamp": 1.0, "actual_TCP_pose": ["x"] * 6,
                             "actual_q": [0] * 6})
        buffer.maybe_record({"connected": True, "timestamp": 1.0, "actual_TCP_pose": [0] * 3,
                             "actual_q": [0] * 6})
        self.assertEqual(buffer.points(), [])


class LatencyTrackerTests(unittest.TestCase):
    def test_monotonic_mode_measures_one_way_delay(self):
        tracker = LatencyTracker(mode="monotonic")
        stats = tracker.observe({"sent_at": 100.0}, received_monotonic=100.05)
        self.assertAlmostEqual(stats["last_ms"], 50.0, places=3)
        self.assertEqual(stats["samples"], 1)

    def test_monotonic_mode_rejects_negative_delta(self):
        tracker = LatencyTracker(mode="monotonic")
        self.assertIsNone(tracker.observe({"sent_at": 200.0}, received_monotonic=100.0))
        self.assertEqual(tracker.stats()["rejected"], 1)

    def test_clock_sync_mode_uses_wall_clock(self):
        tracker = LatencyTracker(mode="clock_sync")
        with patch("telemetry_state.time.time", return_value=1000.0):
            stats = tracker.observe({"sent_at": 999.98})
        self.assertAlmostEqual(stats["last_ms"], 20.0, places=3)
        self.assertEqual(stats["mode"], "clock_sync")

    def test_absurd_latency_is_rejected(self):
        tracker = LatencyTracker(mode="monotonic")
        self.assertIsNone(tracker.observe({"sent_at": 0.0}, received_monotonic=100.0))
        self.assertIsNone(tracker.observe({"sent_at": -500.0}, received_monotonic=0.0))

    def test_transport_latency_fallback_is_used(self):
        tracker = LatencyTracker(mode="monotonic")
        stats = tracker.observe({"transport_latency_ms": 12.5})
        self.assertAlmostEqual(stats["last_ms"], 12.5, places=3)

    def test_statistics_cover_mean_p95_min_max(self):
        tracker = LatencyTracker(mode="monotonic", window=50)
        for index in range(1, 21):
            tracker.observe({"transport_latency_ms": float(index)})
        stats = tracker.stats()
        self.assertEqual(stats["samples"], 20)
        self.assertAlmostEqual(stats["mean_ms"], 10.5, places=3)
        self.assertAlmostEqual(stats["min_ms"], 1.0, places=3)
        self.assertAlmostEqual(stats["max_ms"], 20.0, places=3)
        self.assertGreaterEqual(stats["p95_ms"], 19.0)

    def test_configure_switches_mode(self):
        tracker = LatencyTracker(mode="monotonic")
        tracker.configure("clock_sync")
        self.assertEqual(tracker.mode, "clock_sync")
        tracker.configure("nonsense")
        self.assertEqual(tracker.mode, "monotonic")

    def test_clock_offset_drift_is_tracked(self):
        tracker = LatencyTracker()
        tracker.note_clock_offset(0.5)
        tracker.note_clock_offset(0.75)
        self.assertAlmostEqual(tracker.stats()["offset_drift_seconds"], 0.25, places=6)

    def test_reset_clears_history(self):
        tracker = LatencyTracker()
        tracker.observe({"transport_latency_ms": 5.0})
        tracker.reset()
        self.assertEqual(tracker.stats()["samples"], 0)


class StructuredTelemetryTests(unittest.TestCase):
    def test_extracts_known_fields(self):
        telemetry = structured_telemetry({
            "fps": 29.5, "confidence": 0.87, "gesture": "pinch",
            "dropped_frames": 3, "frame_index": 1200, "inference_ms": 18.2,
            "sent_at": 1234.5, "clock_sync": True,
        })
        self.assertEqual(telemetry["gesture"], "pinch")
        self.assertAlmostEqual(telemetry["confidence"], 0.87, places=4)
        self.assertEqual(telemetry["dropped_frames"], 3.0)
        self.assertTrue(telemetry["clock_sync"])

    def test_legacy_payload_yields_empty_block(self):
        self.assertEqual(structured_telemetry({"details": "old style"}), {})

    def test_out_of_range_numbers_are_dropped(self):
        telemetry = structured_telemetry({"confidence": 5.0, "fps": -1.0})
        self.assertNotIn("confidence", telemetry)
        self.assertNotIn("fps", telemetry)

    def test_non_numeric_values_are_ignored(self):
        telemetry = structured_telemetry({"fps": "fast", "gesture": 42})
        self.assertEqual(telemetry, {})


class ControlClientTelemetryTests(unittest.TestCase):
    def test_heartbeat_counts_repeat_sessions(self):
        store = ControlClientStore()
        store.heartbeat("127.0.0.1", {"session_id": "abc", "fps": 30.0})
        record = store.heartbeat("127.0.0.1", {"session_id": "abc", "fps": 30.0})
        self.assertEqual(record["heartbeats"], 2)
        self.assertEqual(record["telemetry"]["fps"], 30.0)

    def test_new_session_resets_counter(self):
        store = ControlClientStore()
        store.heartbeat("127.0.0.1", {"session_id": "abc"})
        record = store.heartbeat("127.0.0.1", {"session_id": "def"})
        self.assertEqual(record["heartbeats"], 1)

    def test_snapshot_includes_empty_telemetry_when_unreported(self):
        snapshot = ControlClientStore().snapshot(4.0)
        self.assertFalse(snapshot["reported"])
        self.assertEqual(snapshot["telemetry"], {})


class StoreDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_count_reconnects_and_uptime(self):
        state = StateStore()
        state.record_connect()
        state.record_disconnect("cable unplugged")
        state.record_disconnect("timeout")
        diagnostics = state.diagnostics()
        self.assertEqual(diagnostics["reconnects"], 2)
        self.assertEqual(diagnostics["last_disconnect_reason"], "timeout")
        self.assertGreaterEqual(diagnostics["cumulative_uptime_seconds"], 0.0)
        self.assertFalse(diagnostics["connected"])

    def test_sample_counter_increments(self):
        state = StateStore()
        self.assertEqual(state.record_sample(), 1)
        self.assertEqual(state.record_sample(), 2)
        self.assertEqual(state.diagnostics()["samples_read"], 2)

    def test_connect_resets_current_uptime_window(self):
        state = StateStore()
        state.record_connect()
        self.assertTrue(state.diagnostics()["connected"])
        state.record_disconnect("drop")
        self.assertFalse(state.diagnostics()["connected"])


class MultiClientBroadcastTests(unittest.TestCase):
    def test_every_subscriber_receives_the_same_payload(self):
        from events import EventStreamBroker

        broker = EventStreamBroker(max_queue=4)
        first = broker.subscribe()
        second = broker.subscribe()
        third = broker.subscribe()
        broker.publish({"connected": True, "value": 7})
        for subscriber in (first, second, third):
            self.assertIn('"value":7', subscriber.get_nowait())
        self.assertEqual(broker.subscriber_count(), 3)

    def test_slow_subscriber_does_not_block_fast_one(self):
        from events import EventStreamBroker

        broker = EventStreamBroker(max_queue=2)
        slow = broker.subscribe()
        fast = broker.subscribe()
        for index in range(10):
            broker.publish({"index": index})
        # Neither queue ever grows past max_queue, so no publisher is blocked.
        self.assertLessEqual(slow.qsize(), 2)
        self.assertLessEqual(fast.qsize(), 2)
        # A bounded queue keeps the newest samples: 8 then 9 for maxsize=2.
        self.assertEqual(json.loads(fast.get_nowait())["index"], 8)
        self.assertEqual(json.loads(fast.get_nowait())["index"], 9)
        self.assertGreater(broker.stats()["dropped_samples"], 0)

    def test_unsubscribed_clients_stop_receiving(self):
        from events import EventStreamBroker

        broker = EventStreamBroker()
        subscriber = broker.subscribe()
        broker.unsubscribe(subscriber)
        broker.publish({"probe": True})
        self.assertTrue(subscriber.empty())
        self.assertEqual(broker.subscriber_count(), 0)


class NewEndpointHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        config_path = Path(cls.tempdir.name) / "config.json"
        config_path.write_text(json.dumps({
            "robot_ip": "127.0.0.1",
            "http_host": "127.0.0.1",
            "http_port": 8767,
            "monitor_advertised_ip": "127.0.0.1",
            "cors_allowed_origins": [],
            "recording_directory": "recordings",
            "recording_max_seconds": 60.0,
            "trajectory_capacity": 200,
            "trajectory_sample_hz": 50.0,
        }), encoding="utf-8")
        cls.original_config_path = monitor_config.CONFIG_PATH
        cls.original_env_path = monitor_config.ENV_PATH
        cls.original_root = monitor_config.ROOT
        cls.original_recorder = server._get_recorder()
        monitor_config.CONFIG_PATH = config_path
        monitor_config.ENV_PATH = Path(cls.tempdir.name) / ".env"
        monitor_config.ROOT = Path(cls.tempdir.name)
        server._set_recorder(None)
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        cls.token_patch = patch.dict(
            os.environ, {monitor_config.HEARTBEAT_TOKEN_ENV: "unit-token"}, clear=False
        )
        cls.token_patch.start()
        server.get_heartbeat_token.cache_clear()
        cls.httpd = server.MonitorHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    def setUp(self):
        # The brute-force throttle is a process-wide singleton shared with the
        # other HTTP test cases, so every test starts from a clean slate.
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

    def tearDown(self):
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

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
        cls.tempdir.cleanup()

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def test_diagnostics_endpoint_shape(self):
        status, _, body = self.request("GET", "/api/diagnostics")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        for key in ("diagnostics", "latency", "trajectory", "subscribers", "controller"):
            self.assertIn(key, payload)
        self.assertIn("reconnects", payload["diagnostics"])

    def test_trajectory_endpoint_is_read_only(self):
        status, _, body = self.request("GET", "/api/trajectory")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["points"], [])
        self.assertTrue(payload["clear_requires_token"])

    def test_trajectory_clear_requires_token(self):
        status, _, _ = self.request("POST", "/api/trajectory/clear", body=b"{}")
        self.assertEqual(status, 401)
        # Clearing takes no parameters, so the body is not parsed at all.
        status, _, body = self.request(
            "POST", "/api/trajectory/clear", body=b"{}", headers={"X-Heartbeat-Token": "unit-token"}
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertIn("removed", payload)

    def test_latency_endpoint_reports_mode(self):
        status, _, body = self.request("GET", "/api/latency")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "monotonic")
        self.assertIn("latency", payload)

    def test_structured_heartbeat_is_echoed_back(self):
        body = json.dumps({
            "name": "Mac", "session_id": "s1", "fps": 30.0,
            "confidence": 0.9, "gesture": "grab", "dropped_frames": 1,
            "sent_at": 100.0,
        }).encode()
        status, _, response = self.request(
            "POST", "/api/control/heartbeat", body=body, headers={"X-Heartbeat-Token": "unit-token"}
        )
        payload = json.loads(response)
        self.assertEqual(status, 200)
        self.assertEqual(payload["telemetry"]["gesture"], "grab")
        self.assertAlmostEqual(payload["telemetry"]["fps"], 30.0, places=4)

    def test_state_includes_latency_and_trajectory_blocks(self):
        _, _, body = self.request("GET", "/api/state")
        payload = json.loads(body)
        self.assertIn("latency", payload)
        self.assertIn("trajectory", payload)
        self.assertIn("control_client", payload)

    def test_baseline_requires_two_names(self):
        status, _, body = self.request("GET", "/api/baseline")
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["ok"])

    def test_baseline_rejects_unknown_recording(self):
        status, _, _ = self.request("GET", "/api/baseline?left=a.csv&right=b.csv")
        self.assertEqual(status, 404)

    def test_config_exposes_robot_model_and_latency_mode(self):
        _, _, body = self.request("GET", "/api/config")
        payload = json.loads(body)
        self.assertEqual(payload["robot_model"], "UR10")
        self.assertEqual(payload["latency_mode"], "monotonic")


class ConfigValidationForNewFieldsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tempdir.name) / "config.json"
        self.original_config_path = monitor_config.CONFIG_PATH
        monitor_config.CONFIG_PATH = self.config_path
        monitor_config.load_config.cache_clear()

    def tearDown(self):
        monitor_config.CONFIG_PATH = self.original_config_path
        monitor_config.load_config.cache_clear()
        self.tempdir.cleanup()

    def write(self, **overrides):
        value = {"robot_ip": "127.0.0.1"}
        value.update(overrides)
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        monitor_config.load_config.cache_clear()
        return monitor_config.load_config()

    def test_new_defaults(self):
        config = self.write()
        self.assertEqual(config["robot_model"], "UR10")
        self.assertEqual(config["latency_mode"], "monotonic")
        self.assertEqual(config["trajectory_capacity"], 20000)
        self.assertEqual(config["trajectory_sample_hz"], 25.0)

    def test_invalid_robot_model_rejected(self):
        with self.assertRaises(monitor_config.ConfigError):
            self.write(robot_model="UR99")

    def test_invalid_latency_mode_rejected(self):
        with self.assertRaises(monitor_config.ConfigError):
            self.write(latency_mode="guesswork")

    def test_trajectory_capacity_must_be_positive(self):
        with self.assertRaises(monitor_config.ConfigError):
            self.write(trajectory_capacity=0)

    def test_trajectory_sample_rate_must_be_positive(self):
        with self.assertRaises(monitor_config.ConfigError):
            self.write(trajectory_sample_hz=0.0)


if __name__ == "__main__":
    unittest.main()
