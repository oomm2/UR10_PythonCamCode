from vision_control.clock import monotonic
import math
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safety_config import (
    DEFAULT_SPEED,
    MAX_TRANSLATIONAL_SPEED,
    REAL_TARGET,
    URSIM_TARGET,
    cap_translational_velocity,
    workspace_profile,
    workspace_is_configured,
)
from ur10_vision_qt_app import UR10VisionQtApp
from vision_control.policy import Mode


class _Combo:
    def __init__(self, text):
        self.text = text
    def currentText(self):
        return self.text


class _Slider:
    def __init__(self):
        self.value_seen = None
    def setValue(self, value):
        self.value_seen = value


class _Robot:
    def __init__(self):
        self.sent = []
    def speedL(self, vector, acceleration, period, **kwargs):
        self.sent.append((vector, acceleration, period))
    def speedStop(self, deceleration):
        self.sent.append(("stop", deceleration))


class _Client:
    connected = True
    safety_status = 1
    actual_tcp_pose = (0.0, 0.0, 0.5, 0.0, 0.0, 0.0)
    pose_received_at = monotonic()


class SafetyPolicyTests(unittest.TestCase):
    def test_profiles_keep_sim_bounds_and_fail_closed_real(self):
        self.assertEqual(workspace_profile(URSIM_TARGET).limits,
                         (-1.2, 1.2, -1.2, 1.2, 0.0, 1.3))
        self.assertFalse(workspace_is_configured(workspace_profile(REAL_TARGET).limits))

    def test_cap_is_euclidean_and_rejects_nonfinite(self):
        capped = cap_translational_velocity([0.4, 0.4, 0.0, 0.0, 0.0, 0.0])
        self.assertIsNotNone(capped)
        assert capped is not None
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in capped[:3])), MAX_TRANSLATIONAL_SPEED)
        self.assertIsNone(cap_translational_velocity([math.nan, 0, 0, 0, 0, 0]))
        self.assertIsNone(cap_translational_velocity([math.inf, 0, 0, 0, 0, 0]))

    def test_target_switch_does_not_restore_saved_high_real_speed(self):
        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        slider = _Slider()
        app.tuners = {"Max speed": (slider, None, 100.0)}
        app._apply_target_profile(REAL_TARGET, 0.50)
        self.assertEqual(slider.value_seen, int(DEFAULT_SPEED * 100))
        self.assertFalse(app.real_speed_selected)
        app._apply_target_profile(URSIM_TARGET, 0.50)
        self.assertEqual(slider.value_seen, 50)

    def test_final_qt_dispatch_caps_norm_before_robot(self):
        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        app.returning_to_origin = False
        app.mode = Mode.GESTURE
        app.frame_acquired_at = monotonic()
        app._value = lambda name: 0.5
        app.session_identity = (URSIM_TARGET, "sim", 1)
        app.robot_enabled = True
        app.rtde_client = _Client()
        app.robot = _Robot()
        app.last_robot_update = 0.0
        app.CONTROL_PERIOD = 0.0
        app._workspace_profile = workspace_profile(URSIM_TARGET)
        app._workspace_feedback_is_fresh = lambda: True
        app._workspace_safety_status_is_acceptable = lambda: True
        app._trip_workspace_fault = lambda reason: self.fail(reason)
        app._send_velocity(0.4, 0.4, 0.0)
        vector = app.robot.sent[-1][0]
        self.assertLessEqual(math.sqrt(sum(value * value for value in vector[:3])), MAX_TRANSLATIONAL_SPEED + 1e-9)
        self.assertEqual(vector[3:], [0.0, 0.0, 0.0])

    def test_test_and_return_require_captured_ursim_identity(self):
        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        app.session_identity = (REAL_TARGET, "robot", 2)
        app.rtde_client = _Client()
        app.robot_enabled = True
        app.test_timer = type("Timer", (), {"start": lambda self, *_: None})()
        app.status_banner = type("Label", (), {"setText": lambda self, *_: None})()
        app.log = lambda *_: None
        app.test_steps = 0
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "warning", staticmethod(lambda *args: None)):
            UR10VisionQtApp.test_ursim_motion(app)
            app.returning_to_origin = False
            app._trip_workspace_fault = lambda reason: setattr(app, "fault_reason", reason)
            app._start_return_to_origin()
        self.assertEqual(app.test_steps, 0)
        self.assertIn("實體", app.fault_reason)

    def test_pose_freshness_never_bypassed_while_building(self):
        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        app.POSE_FEEDBACK_TIMEOUT = 0.5
        client = _Client()
        app.rtde_client = client
        client.control_ready = False
        client.pose_received_at = monotonic() - 10.0
        self.assertFalse(UR10VisionQtApp._workspace_feedback_is_fresh(app))
        client.control_ready = True
        self.assertFalse(UR10VisionQtApp._workspace_feedback_is_fresh(app))
        client.pose_received_at = monotonic()
        self.assertTrue(UR10VisionQtApp._workspace_feedback_is_fresh(app))

    def test_zone_command_prefers_clear_vertical_over_depth_drift(self):
        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        app.neutral_x = 0.50
        app.neutral_y = 0.50
        app.neutral_hand_size = 0.20
        app.depth_inverted = False
        app._value = lambda name: .10 if name == "Dead zone" else 1.0

        # far above neutral with a large palm-size drift: vertical must win
        self.assertEqual(
            UR10VisionQtApp._zone_command(app, 0.50, 0.25, 0.30, 0.9, 0.38),
            (0.0, 0.0, 0.38, "UP"))
        # far below neutral: DOWN wins too
        self.assertEqual(
            UR10VisionQtApp._zone_command(app, 0.50, 0.80, 0.30, 0.9, 0.38),
            (0.0, 0.0, -0.38, "DOWN"))
        # moderate offset with size near neutral: UP
        self.assertEqual(
            UR10VisionQtApp._zone_command(app, 0.50, 0.35, 0.205, 0.9, 0.38),
            (0.0, 0.0, 0.38, "UP"))
        # forward push near neutral: depth still wins
        self.assertEqual(
            UR10VisionQtApp._zone_command(app, 0.50, 0.55, 0.30, 0.9, 0.38)[3], "FWD")
        # inside every dead zone: STOP
        self.assertEqual(
            UR10VisionQtApp._zone_command(app, 0.50, 0.52, 0.202, 0.9, 0.38),
            (0.0, 0.0, 0.0, "STOP"))

    def test_dashboard_remote_state_accepts_split_greeting_and_boolean(self):
        class FakeSocket:
            def __init__(self):
                self.chunks = [b"Connected: Universal Robots Dashboard Server\n", b"true\n"]
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def settimeout(self, _timeout):
                pass
            def sendall(self, _data):
                pass
            def recv(self, _size):
                return self.chunks.pop(0) if self.chunks else b""

        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        with patch("ur10_vision_qt_app.socket.create_connection", return_value=FakeSocket()):
            self.assertIs(UR10VisionQtApp._dashboard_remote_state(app, "robot"), True)


        app = UR10VisionQtApp.__new__(UR10VisionQtApp)
        app.connection_in_progress = True
        app.rtde_client = object()
        app.session_identity = (URSIM_TARGET, "sim", 1)
        app.target_combo = type("Combo", (), {
            "blockSignals": lambda self, *_: None,
            "setCurrentText": lambda self, value: setattr(self, "text", value),
        })()
        app._target_changed(REAL_TARGET)
        self.assertEqual(app.target_combo.text, URSIM_TARGET)


if __name__ == "__main__":
    unittest.main()
