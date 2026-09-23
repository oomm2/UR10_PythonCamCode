from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import controller as controller_mod
import monitor_config
import server
import telemetry_state


class ServerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tempdir.name) / "config.json"
        self.write_config()
        self.original_config_path = monitor_config.CONFIG_PATH
        self.original_env_path = monitor_config.ENV_PATH
        monitor_config.CONFIG_PATH = self.config_path
        monitor_config.ENV_PATH = Path(self.tempdir.name) / ".env"
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()

    def tearDown(self):
        monitor_config.CONFIG_PATH = self.original_config_path
        monitor_config.ENV_PATH = self.original_env_path
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        self.tempdir.cleanup()

    def config(self, **overrides):
        value = {
            "robot_ip": "127.0.0.1",
            "rtde_port": 30004,
            "rtde_frequency": 25.0,
            "http_host": "127.0.0.1",
            "http_port": 8765,
            "monitor_advertised_ip": "127.0.0.1",
            "controller_heartbeat_timeout": 4.0,
            "cors_allowed_origins": ["http://example.test:9443"],
        }
        value.update(overrides)
        return value

    def write_config(self, **overrides):
        self.config_path.write_text(json.dumps(self.config(**overrides)), encoding="utf-8")

    def test_config_cache_reuses_file_and_isolates_snapshots(self):
        first = monitor_config.load_config()
        first["cors_allowed_origins"].append("http://mutated.test:1")
        second = monitor_config.load_config()
        self.assertNotIn("http://mutated.test:1", second["cors_allowed_origins"])

        with patch.object(Path, "open", wraps=Path.open) as opened:
            monitor_config.load_config()
            monitor_config.load_config()
        self.assertEqual(opened.call_count, 0)

        self.write_config(rtde_frequency=10.0)
        os.utime(self.config_path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
        self.assertEqual(monitor_config.load_config()["rtde_frequency"], 10.0)

    def test_config_keeps_last_good_value_after_bad_edit(self):
        self.assertEqual(monitor_config.load_config()["rtde_port"], 30004)
        self.config_path.write_text("{not json", encoding="utf-8")
        os.utime(self.config_path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
        self.assertEqual(monitor_config.load_config()["rtde_port"], 30004)

    def test_invalid_initial_config_fails(self):
        self.config_path.write_text(json.dumps({"robot_ip": "127.0.0.1", "http_port": 70000}), encoding="utf-8")
        monitor_config.load_config.cache_clear()
        with self.assertRaises(monitor_config.ConfigError):
            monitor_config.load_config()

    def test_config_rejects_secret_and_wildcard(self):
        self.write_config(controller_heartbeat_token="secret")
        monitor_config.load_config.cache_clear()
        with self.assertRaises(monitor_config.ConfigError):
            monitor_config.load_config()
        self.write_config(cors_allowed_origins=["*"])
        monitor_config.load_config.cache_clear()
        with self.assertRaises(monitor_config.ConfigError):
            monitor_config.load_config()

    def test_token_environment_precedes_dotenv_and_is_cached(self):
        monitor_config.ENV_PATH.write_text("UR_MONITOR_HEARTBEAT_TOKEN=dotenv-token\n", encoding="utf-8")
        with patch.dict(os.environ, {monitor_config.HEARTBEAT_TOKEN_ENV: "environment-token"}, clear=False):
            self.assertEqual(server.get_heartbeat_token(), "environment-token")
            monitor_config.ENV_PATH.write_text("UR_MONITOR_HEARTBEAT_TOKEN=changed\n", encoding="utf-8")
            self.assertEqual(server.get_heartbeat_token(), "environment-token")
        server.get_heartbeat_token.cache_clear()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server.get_heartbeat_token(), "changed")

    def test_missing_or_invalid_token_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server.get_heartbeat_token())
        server.get_heartbeat_token.cache_clear()
        monitor_config.ENV_PATH.write_text("UR_MONITOR_HEARTBEAT_TOKEN=bad token\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server.get_heartbeat_token())

    def test_state_snapshot_deep_copies_vectors(self):
        state = telemetry_state.StateStore()
        snapshot = state.snapshot()
        snapshot["actual_q"][0] = 99
        self.assertEqual(state.snapshot()["actual_q"][0], 0.0)

    def test_control_age_uses_monotonic_clock(self):
        store = controller_mod.ControlClientStore()
        store.heartbeat("127.0.0.1", {"name": "test"})
        record = store.snapshot(4.0)
        self.assertTrue(record["reported"])
        self.assertTrue(record["active"])
        self.assertGreaterEqual(record["age_seconds"], 0.0)
        self.assertNotIn("_last_seen_monotonic", record)

    def test_static_path_rejects_traversal(self):
        self.assertTrue(server._safe_static_path("/").is_file())
        for value in [
            "/../server.py",
            "/%2e%2e/server.py",
            "/..%5cserver.py",
            "/C:%5cserver.py",
            "//server/share",
            "/%00index.html",
            "/a/./index.html",
        ]:
            with self.subTest(value=value):
                self.assertIsNone(server._safe_static_path(value))
        self.assertTrue(server._safe_static_path("/joint_limits.json").is_file())


class HttpServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.config_path = Path(cls.tempdir.name) / "config.json"
        cls.env_path = Path(cls.tempdir.name) / ".env"
        cls.config_path.write_text(json.dumps({
            "robot_ip": "127.0.0.1",
            "rtde_port": 30004,
            "rtde_frequency": 25.0,
            "http_host": "127.0.0.1",
            "http_port": 8765,
            "monitor_advertised_ip": "127.0.0.1",
            "controller_heartbeat_timeout": 4.0,
            "cors_allowed_origins": ["http://example.test:9443"],
        }), encoding="utf-8")
        cls.original_config_path = monitor_config.CONFIG_PATH
        cls.original_env_path = monitor_config.ENV_PATH
        monitor_config.CONFIG_PATH = cls.config_path
        monitor_config.ENV_PATH = cls.env_path
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        cls.token_patch = patch.dict(os.environ, {monitor_config.HEARTBEAT_TOKEN_ENV: "test-token"}, clear=False)
        cls.token_patch.start()
        server.get_heartbeat_token.cache_clear()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        cls.token_patch.stop()
        monitor_config.CONFIG_PATH = cls.original_config_path
        monitor_config.ENV_PATH = cls.original_env_path
        monitor_config.load_config.cache_clear()
        server.get_heartbeat_token.cache_clear()
        cls.tempdir.cleanup()

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, data

    def setUp(self):
        # The brute-force throttle is a process-wide singleton shared with the
        # other HTTP test cases, so every test starts from a clean slate.
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

    def tearDown(self):
        server.HEARTBEAT_THROTTLE.clear("127.0.0.1")

    def test_get_state_with_and_without_origin(self):
        status, headers, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        payload = json.loads(body)
        self.assertIn("control_client", payload)
        status, headers, _ = self.request("GET", "/api/state", headers={"Origin": "http://localhost:8765"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:8765")
        self.assertEqual(headers["Vary"], "Origin")

    def test_disallowed_origin_is_rejected(self):
        status, headers, body = self.request("GET", "/api/state", headers={"Origin": "http://evil.test:8765"})
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("Origin is not allowed", body.decode())

    def test_heartbeat_auth_alias_and_details(self):
        body = json.dumps({"name": "Mac", "details": "ready"}).encode()
        headers = {"Content-Type": "application/json", "X-Heartbeat-Token": "test-token"}
        status, response_headers, raw = self.request("POST", "/api/control/heartbeat", body=body, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers.get("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(json.loads(raw)["observed_ip"], "127.0.0.1")
        status, _, raw = self.request("GET", "/api/control-client")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["details"], "ready")
        status, _, _ = self.request("POST", "/api/controller/heartbeat", body=body, headers={**headers, "Authorization": "Bearer test-token"})
        self.assertEqual(status, 200)

    def test_heartbeat_missing_server_token_fails_closed(self):
        server.get_heartbeat_token.cache_clear()
        with patch.dict(os.environ, {}, clear=True), patch.object(
            monitor_config, "ENV_PATH", Path(self.tempdir.name) / "missing.env"
        ):
            server.get_heartbeat_token.cache_clear()
            status, _, raw = self.request("POST", "/api/control/heartbeat", body=b"{}")
        self.assertEqual(status, 503)
        self.assertIn("not configured", raw.decode())
        server.get_heartbeat_token.cache_clear()

    def test_heartbeat_wrong_or_missing_token_rejected(self):
        body = b"{}"
        for headers in [{}, {"X-Heartbeat-Token": "wrong"}]:
            with self.subTest(headers=headers):
                status, _, _ = self.request("POST", "/api/control/heartbeat", body=body, headers=headers)
                self.assertEqual(status, 401)

    def test_heartbeat_bad_size_and_json(self):
        common = {"X-Heartbeat-Token": "test-token"}
        status, _, _ = self.request("POST", "/api/control/heartbeat", body=b"x" * 4097, headers=common)
        self.assertEqual(status, 413)
        status, _, _ = self.request("POST", "/api/control/heartbeat", body=b"not json", headers=common)
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/control/heartbeat", body=b"[]", headers=common)
        self.assertEqual(status, 400)

    def test_config_endpoint_does_not_expose_token(self):
        status, _, raw = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn("test-token", raw.decode())
        self.assertNotIn("heartbeat_token", raw.decode())

    def test_options_preflight_and_static_assets(self):
        status, headers, body = self.request("OPTIONS", "/api/control/heartbeat", headers={"Origin": "http://example.test:9443"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://example.test:9443")
        self.assertEqual(body, b"")
        status, headers, body = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn(b"createPoller", body)

    def test_http_path_traversal_rejected(self):
        for path in ["/../server.py", "/%2e%2e/server.py", "/..%5cserver.py", "/C:%5cserver.py"]:
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertIn(status, {403, 404})
                self.assertNotEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
