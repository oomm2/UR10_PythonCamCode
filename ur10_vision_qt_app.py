"""Qt desktop UI for UR10 hand-vision control.

Camera hand position controls left/right/up/down. Hand size controls
forward/backward after a neutral-size calibration in the centre of the frame.
"""

from __future__ import annotations

from vision_control.clock import monotonic

import math
import multiprocessing as mp_process
import os
import re
import socket
import sys
import time
from typing import Optional

from safety_config import DEFAULT_SPEED, MAX_TRANSLATIONAL_SPEED, REAL_TARGET, RETURN_SPEED, URSIM_TARGET, speed_for_profile, workspace_profile

import cv2
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from types import SimpleNamespace
from vision_control.motion import MotionMixin
from vision_control.ui import DashboardUiMixin
from vision_control.settings import SettingsMixin
from vision_control.logging import LoggingMixin
from vision_control.transport import RTDEWorkerClient
from vision_control.vision import VisionClient, CameraScanner, MANUAL_CAMERA
from vision_control.gestures import zone_command
from vision_control.policy import Mode, fresh, FRAME_TIMEOUT, RETURN_TIMEOUT
from vision_control.i18n import LocalizedMessageBox as QMessageBox

cv2.setNumThreads(1)

# Display-only translation for command names. Internal command strings stay
# in English so CSV rows and zone logic remain stable.
COMMAND_ZH = {
    "STOP": "停止", "LEFT": "左移", "RIGHT": "右移",
    "UP": "上升", "DOWN": "下降", "FWD": "前進", "BACK": "後退",
}
SAFETY_MODE_ZH = {
    1: "正常", 2: "降速", 3: "保護停止", 4: "恢復中",
    5: "安全裝置停止", 6: "系統急停", 7: "機械人急停", 8: "違規", 9: "關節故障",
}


class UR10VisionQtApp(MotionMixin, DashboardUiMixin, SettingsMixin, LoggingMixin, QMainWindow):
    RTDE_PORT = 30004
    ACCELERATION = 0.25
    CONTROL_PERIOD = 0.10
    LOST_HAND_GRACE = 3
    MIN_HAND_SIZE = 0.06
    MAX_HAND_SIZE = 0.85
    MIN_LANDMARK_SPREAD = 0.015
    SIZE_SMOOTHING = 0.12
    COMMAND_CONFIRM_FRAMES = 6
    SAFETY_SPEED_CAP = MAX_TRANSLATIONAL_SPEED

    # Installation values are validated without inventing or sorting bounds.
    POSE_FEEDBACK_TIMEOUT = 0.50
    AXIS_TEST_DISTANCE = 0.03
    SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
    RETURN_SPEED_CAP = RETURN_SPEED
    RETURN_TIMEOUT = RETURN_TIMEOUT
    RETURN_POSITION_TOLERANCE = 0.010
    REAL_CHECKLIST_ITEMS = (
        "實體工作區已清空，機械人活動範圍內沒有人。",
        "實體緊急停止按鈕就在手邊，並已測試有效。",
        "控制器上已設定安全平面／保護性停止。",
        "速度設定已在控制關閉時由操作者明確選擇；0.5 m/s 僅為軟體可設定上限，不代表安全或已驗證。",
    )
    # e-Series only: CB3 (PolyScope 3.x) has no Remote Control mode at all.
    REAL_REMOTE_CHECKLIST_ITEM = "PolyScope 狀態列顯示 Remote（遠端控制已啟用）。"
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UR10 Vision Control")
        self.resize(1440, 900)
        self.setMinimumSize(1000, 720)
        self.theme = 'light'
        self.language = 'yue'
        QApplication.instance().setProperty('ui_language', self.language)

        self.vision = None
        self.scanner = None
        self.camera_state = mp_process.get_context("spawn").Array('d', 2, lock=True)
        self.frame_acquired_at = 0.0
        self.mode = Mode.DISABLED
        self._last_dispatched = None
        self._stop_request_failed = False
        self._last_stop_ack = -1
        self.robot = None
        self.rtde_client: Optional[RTDEWorkerClient] = None
        self.camera_running = False
        self.robot_enabled = False
        self.connection_in_progress = False
        self.session_generation = 0
        self.session_identity: Optional[tuple[str, str, int]] = None
        self.session_profile = None
        self._setting_speed_profile = False
        self.real_speed_selected = False
        self.selected_real_speed = DEFAULT_SPEED
        self._workspace_profile = workspace_profile(URSIM_TARGET)
        self.last_robot_update = 0.0
        self.last_fps_time = monotonic()
        self.fps_frames = 0
        self.current_fps = 0.0
        self.lost_hand_frames = 0
        self.smooth_x: Optional[float] = None
        self.smooth_y: Optional[float] = None
        self.smooth_size: Optional[float] = None
        self.neutral_x: Optional[float] = None
        self.neutral_y: Optional[float] = None
        self.neutral_hand_size: Optional[float] = None
        self.neutral_samples = 0
        self.neutral_x_sum = 0.0
        self.neutral_y_sum = 0.0
        self.neutral_size_sum = 0.0
        self.calibration_active = False
        self.show_zones = False
        self.depth_inverted = False
        self.direction_inverted = False
        self.rtde_cleanup_in_progress = False
        self.rtde_lost = False
        self.workspace_fault = False
        self.workspace_fault_reason = ""
        self.returning_to_origin = False
        self.return_started_at = 0.0
        self.return_timer = QTimer(self)
        self.return_timer.setInterval(100)
        self.return_timer.timeout.connect(self._return_step)
        self.pending_command = "STOP"
        self.pending_command_frames = 0
        self.stable_command = "STOP"

        self._setup_csv_logging()
        self._last_frame_command = "STOP"


        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.update_frame)
        self.test_timer = QTimer(self)
        self.test_timer.timeout.connect(self._test_step)
        self.rtde_timer = QTimer(self)
        self.rtde_timer.timeout.connect(self._poll_rtde)

        self._loading_settings = False
        self._build_ui()
        self._apply_style()
        self._load_settings()
        QApplication.instance().installEventFilter(self)
        self.rtde_timer.start(50)
        self.log(f"CSV 記錄：{self.csv_path}")
        self.log("就緒。請先連接 URSim，再啟動控制。")
        self.log("請開啟相機並按「校準中立姿態」，完成後才啟動手勢控制。")
        self.log(self._workspace_summary())
        self._update_status()

    # --- UI ------------------------------------------------------------

    def eventFilter(self, watched, event):
        # Window shortcuts are blocked by modal dialogs. Keep Escape's STOP
        # active there too, while still allowing the dialog to handle Escape.
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.emergency_stop()
        return super().eventFilter(watched, event)


    # --- Parameters and status ---------------------------------------

    def _value(self, name: str) -> float:
        slider, _, scale = self.tuners[name]
        return slider.value() / scale

    def _repolish(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _update_status(self, command: str = "STOP", hand: str = "-, -",
                       confidence: float = 0.0) -> None:
        connected = bool(self.rtde_client and self.rtde_client.connected)
        robot = f"{self._target_id()} | " + ("已連接" if connected else "未連接")
        self.status_values["Robot"].setText(robot)
        self.status_values["Control"].setText({Mode.DISABLED:"關閉", Mode.READY:"待命", Mode.GESTURE:"手勢", Mode.AXIS_TEST:"單軸測試", Mode.RETURN:"返回原點", Mode.FAULT:"故障鎖定"}[self.mode])
        dispatched = getattr(self.rtde_client, 'dispatched_velocity', None)
        control_command = self._command_name(*dispatched[:3]) if connected and dispatched else 'STOP'
        command_display = '＋'.join(COMMAND_ZH.get(part, part) for part in control_command.split('+'))
        if connected and getattr(self.rtde_client, 'stop_pending', False):
            command_display = '停止待確認'
        self.status_values["Command"].setText(command_display)
        self.status_values["Hand"].setText(hand)
        self.status_values["Confidence"].setText(f"{confidence * 100:.0f}%" if confidence else "--")
        self.status_values["FPS"].setText(f"{self.current_fps:.1f}")
        self.confidence_bar.setText("左右手分類分數僅供參考")
        arrow = {"STOP": "■", "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓", "FWD": "↗", "BACK": "↙"}.get(command, "↔")
        gesture_display = '＋'.join(COMMAND_ZH.get(part, part) for part in command.split('+'))
        self.direction_badge.setText(f"{arrow}  {gesture_display}")
        color = "#223047" if command == "STOP" else "#315f9e"
        self.direction_badge.setStyleSheet(
            f"background: {color}; border-radius: 4px; padding: 5px 10px;"
            "font-weight: 600; font-size: 14px;")
        # Telemetry: TCP pose, safety mode, CSV state
        pose = self.rtde_client.actual_tcp_pose if self.rtde_client else None
        if pose is not None:
            self.tcp_label.setText(f"TCP: X{pose[0]:+.2f}  Y{pose[1]:+.2f}  Z{pose[2]:+.2f} m")
        else:
            self.tcp_label.setText("TCP: --")
        safety = self.rtde_client.safety_status if self.rtde_client else None
        if safety is not None:
            self.safety_label.setText(f"安全模式: {SAFETY_MODE_ZH.get(safety, safety)}")
        else:
            self.safety_label.setText("安全模式: --")
        if getattr(self, "csv_writer", None) is not None:
            self.csv_indicator.setText(f"● CSV 錄製中：{os.path.basename(self.csv_path)}")
        else:
            self.csv_indicator.setText("○ CSV 未啟用")
        # Status banner colour reflects real state regardless of its text.
        if self.mode == Mode.FAULT or self.workspace_fault or self.rtde_lost:
            banner_state = "error"
        elif self.returning_to_origin:
            banner_state = "warn"
        elif self.robot_enabled:
            banner_state = "live"
        else:
            banner_state = "idle"
        self.status_banner.setProperty("state", banner_state)
        self._repolish(self.status_banner)
        if self.workspace_fault:
            workspace = f"工作區故障：{self.workspace_fault_reason}"
            workspace_state = "error"
        elif self.returning_to_origin:
            workspace = "工作區：返回安全原點中"
            workspace_state = "warn"
        elif not self._workspace_is_configured():
            workspace = self._workspace_summary()
            workspace_state = "error"
        elif not connected:
            workspace = "工作區：已設定 | 機械人未連接"
            workspace_state = "warn"
        elif not self._workspace_feedback_is_fresh():
            workspace = "工作區：等待姿態回饋"
            workspace_state = "warn"
        else:
            workspace = "工作區：就緒"
            workspace_state = "ok"
        self.workspace_status.setText(workspace)
        self.workspace_status.setProperty("state", workspace_state)
        self._repolish(self.workspace_status)
        self._refresh_workflow()

    def _target_id(self) -> str:
        data = self.target_combo.currentData() if hasattr(self.target_combo, 'currentData') else None
        return data if data in (URSIM_TARGET, REAL_TARGET) else self.target_combo.currentText()

    def _set_target_id(self, target: str) -> None:
        self.target_combo.blockSignals(True)
        try:
            index = self.target_combo.findData(target) if hasattr(self.target_combo, 'findData') else -1
            if index >= 0:
                self.target_combo.setCurrentIndex(index)
            else:
                self.target_combo.setCurrentText(target)
        finally:
            self.target_combo.blockSignals(False)

    def _target_changed(self, target: str) -> None:
        if self.connection_in_progress or self.rtde_client is not None:
            # The target is locked for the whole connection session. Safety
            # decisions use session_identity, never a relabelled combo box.
            self._set_target_id(self.session_identity[0] if self.session_identity else target)
            return
        if hasattr(self, "_ips"):
            previous = getattr(self, "_last_target", target)
            if previous != target:
                self._ips[previous] = self.robot_ip.text().strip()
            self.robot_ip.setText(self._ips.get(target, self.robot_ip.text()))
            self._last_target = target
        self._apply_target_profile(target)
        self._save_settings()
        self._update_status()

    def _apply_target_profile(self, target: str, saved_speed: object = None) -> None:
        self._workspace_profile = workspace_profile(target)
        self.real_speed_selected = False
        self._setting_speed_profile = True
        try:
            speed = speed_for_profile(target, saved_speed)
            slider, _, scale = self.tuners["Max speed"]
            slider.setValue(int(round(speed * scale)))
            if target == REAL_TARGET:
                self.selected_real_speed = speed
        finally:
            self._setting_speed_profile = False

    def _set_speed_editor_enabled(self, enabled: bool) -> None:
        slider, _, _ = self.tuners["Max speed"]
        slider.setEnabled(enabled)


    def confirm_physical_speed(self):
        if self._target_id() == REAL_TARGET and not self.robot_enabled:
            self.real_speed_selected = True
            self.selected_real_speed = self._value('Max speed')
            self.log(f'已明確選擇實體速度 {self.selected_real_speed:.2f} m/s')

    def _max_speed_changed(self, _value: int) -> None:
        if (getattr(self, "target_combo", None) is not None
                and self._target_id() == REAL_TARGET
                and not self.robot_enabled
                and not self.connection_in_progress
                and not self._setting_speed_profile):
            self.real_speed_selected = True
            self.selected_real_speed = self._value("Max speed")
        self._save_settings()


    # --- CSV logging ----------------------------------------------------


    # --- Settings persistence -------------------------------------------


    # --- Robot ---------------------------------------------------------

    def connect_robot(self) -> None:
        if self.connection_in_progress:
            return
        if self.rtde_client is not None:
            self._disconnect_robot()
            return
        target = self._target_id()
        host = self.robot_ip.text().strip()
        self.connection_in_progress = True
        self.target_combo.setEnabled(False)
        self.robot_ip.setEnabled(False)
        self.session_generation += 1
        identity = (target, host, self.session_generation)
        self.session_identity = identity
        self.session_profile = workspace_profile(target)
        try:
            if hasattr(self, "_ips"):
                self._ips[target] = host
                self._last_target = target
                self._save_settings()
            self.status_banner.setText(f"正在檢查 RTDE {host}:{self.RTDE_PORT}…")
            QApplication.processEvents()
            with socket.create_connection((host, self.RTDE_PORT), timeout=2.0):
                pass
            if self.session_identity != identity:
                return
            self.rtde_client = RTDEWorkerClient(host, target, self.camera_state)
            self.robot = self.rtde_client
            self.rtde_lost = False
            self.connect_button.setText("取消連接")
            self.test_button.setEnabled(False)
            self.status_banner.setText("RTDE 連接中…")
            self.log(f"RTDE 工作程序已啟動：{host} ({target})")
            self.log(self._workspace_summary())
            self.rtde_timer.start(100)
        except Exception as exc:
            self.robot = None
            self.rtde_client = None
            self.session_identity = None
            self.status_banner.setText("RTDE 連接失敗")
            self.log(f"RTDE 錯誤: {exc}")
            QMessageBox.warning(self, "連接失敗", f"無法連接 RTDE {host}:30004。\n\n{exc}")
        finally:
            self.connection_in_progress = False
            if self.rtde_client is None:
                self.target_combo.setEnabled(True)
                self.robot_ip.setEnabled(True)
        self._update_status()


    def _poll_rtde(self):
        self._poll_scanner()
        if self.rtde_client is None:
            self._refresh_workflow()
            return
        event = self.rtde_client.poll()
        for notice in self.rtde_client.notices:
            self.log(notice)
        self.rtde_client.notices.clear()
        if event and event[0] == 'connected':
            self.connect_button.setText("中斷連線")
            self.status_banner.setText("姿態已連接 | 等待控制介面就緒")
            self.log("RTDE 已連接；請在控制器確認電源及操作模式")
        elif event and event[0] in ('error', 'fault'):
            self.rtde_lost = event[0] == 'error'
            self._trip_workspace_fault(event[1])
        if self.robot_enabled and self.mode != Mode.READY:
            if not self._workspace_feedback_is_fresh() or not self._workspace_safety_status_is_acceptable():
                self._trip_workspace_fault("TCP 姿態或安全回饋不可用")
        sim_ready = (self.session_identity is not None and self.session_identity[0] == URSIM_TARGET
                     and self.rtde_client.connected and self.rtde_client.control_ready)
        self.test_button.setEnabled(sim_ready)
        for button in self.axis_buttons.values():
            button.setEnabled(sim_ready)
        dispatched = self.rtde_client.dispatched_velocity
        if dispatched != self._last_dispatched:
            self._csv_row('worker_dispatch', command=self._command_name(*dispatched[:3]),
                          vx=dispatched[0], vy=dispatched[1], vz=dispatched[2],
                          note='RTDE call accepted; not measured velocity')
            self._last_dispatched = dispatched
        if self.rtde_client.stop_ack > self._last_stop_ack:
            self._last_stop_ack = self.rtde_client.stop_ack
            if self.mode == Mode.DISABLED and not self._stop_request_failed and not self.rtde_client.stop_pending:
                self.status_banner.setText('STOP 已由控制器接受 | 控制關閉')
        self._update_status(self._last_frame_command, getattr(self,"_last_hand_display","-, -"), getattr(self,"_last_handedness",0.0))

    def _disconnect_robot(self):
        if self.rtde_cleanup_in_progress:
            return
        self.rtde_cleanup_in_progress = True
        self._set_mode(Mode.DISABLED)
        worker = self.rtde_client
        if worker is not None:
            worker.disconnect()
        self.robot = self.rtde_client = None
        self.session_identity = None
        self.session_profile = None
        self.connect_button.setText("連接機械人")
        self.test_button.setEnabled(False)
        self.target_combo.setEnabled(True)
        self.robot_ip.setEnabled(True)
        for button in self.axis_buttons.values():
            button.setEnabled(False)
        self.status_banner.setText("機械人未連接 | 控制關閉")
        self.log("機械人已斷線")
        self.rtde_lost = False
        self.rtde_cleanup_in_progress = False
        self._update_status()


    def _dashboard_polyscope_version(self, host: str) -> Optional[tuple[int, int, int]]:
        """Query the dashboard server for the controller's PolyScope version.

        PolyScope 3.x means a CB3 controller, which has no Remote Control
        mode at all; 5.x means e-Series. Returns None when the version
        cannot be parsed, in which case callers must assume e-Series.
        """
        try:
            with socket.create_connection((host, 29999), timeout=2.0) as sock:
                sock.settimeout(2.0)
                sock.sendall(b"PolyscopeVersion\n")
                data = b""
                deadline = monotonic() + 2.0
                while monotonic() < deadline and len(data) < 4096:
                    chunk = sock.recv(512)
                    if not chunk:
                        break
                    data += chunk
                    for raw_line in data.decode("utf-8", "ignore").splitlines():
                        line = raw_line.strip()
                        if line.lower().startswith("connected"):
                            continue
                        match = re.search(r"(\d+)\.(\d+)\.(\d+)", line)
                        if match:
                            major, minor, build = (int(part) for part in match.groups())
                            return (major, minor, build)
                return None
        except OSError:
            return None

    def _dashboard_remote_state(self, host: str) -> Optional[bool]:
        """Read a bounded dashboard reply, skipping the initial greeting."""
        try:
            with socket.create_connection((host, 29999), timeout=2.0) as sock:
                sock.settimeout(2.0)
                sock.sendall(b"is in remote control\n")
                data = b""
                deadline = monotonic() + 2.0
                result: Optional[bool] = None
                while monotonic() < deadline and len(data) < 4096:
                    chunk = sock.recv(512)
                    if not chunk:
                        break
                    data += chunk
                    for raw_line in data.decode("utf-8", "ignore").splitlines():
                        line = raw_line.strip().lower()
                        if line == "true":
                            result = True
                        elif line == "false":
                            result = False
                    if result is not None:
                        return result
                return result
        except OSError:
            return None


    # --- Camera / vision ----------------------------------------------

    def toggle_camera(self) -> None:
        if self.camera_running:
            self.stop_camera()
        else:
            self.start_camera()

    def _camera_source_changed(self, _text):
        self.camera_source.setVisible(self.camera_combo.currentData() == MANUAL_CAMERA)

    def _scan_cameras(self):
        if self.scanner is not None:
            return
        if self.camera_running or self.robot_enabled:
            QMessageBox.warning(self, "請先停止", "掃描前請關閉相機及機械人控制。")
            return
        self.scanner = CameraScanner()
        self.camera_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.scan_button.setText("掃描中…")

    def _camera_value(self):
        value = self.camera_combo.currentData()
        source = self.camera_source.text().strip() if value == MANUAL_CAMERA else str(value)
        if not source:
            raise ValueError('請輸入相機索引或 RTSP URL')
        try:
            return int(source)
        except ValueError:
            return source

    def start_camera(self):
        if self.vision is not None:
            return
        if self.scanner is not None:
            QMessageBox.warning(self, '掃描中', '請等待相機掃描完成後再開啟相機。')
            return
        self._set_mode(Mode.DISABLED)
        try:
            self.vision = VisionClient(self._camera_value(), self._value('Detection'),
                                       self._value('Tracking'), self.camera_state)
        except Exception as exc:
            QMessageBox.warning(self, '相機錯誤', str(exc))
            return
        self.camera_running = True
        self.frame_acquired_at = 0.0
        self.smooth_x = self.smooth_y = self.smooth_size = None
        self.neutral_x = self.neutral_y = self.neutral_hand_size = None
        self.calibration_active = False
        self._stabilize_command('STOP')
        self.neutral_samples = 0
        self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
        self.lost_hand_frames = 0
        self.last_fps_time = monotonic()
        self.fps_frames = 0
        self.camera_combo.setEnabled(False)
        self.camera_source.setEnabled(False)
        self.camera_button.setText('關閉相機')
        self.scan_button.setEnabled(False)
        self.calibrate_button.setEnabled(True)
        self.depth_button.setEnabled(True)
        self.frame_timer.start(25)
        self.status_banner.setText('相機啟動中 | 控制關閉')
        self.log('相機 worker 已啟動；影像出現後請按「校準中立姿態」')

    def stop_camera(self):
        self._set_mode(Mode.DISABLED)
        self.camera_running = False
        self.frame_timer.stop()
        if self.vision is not None:
            self.vision.close()
            self.vision = None
        self.camera_combo.setEnabled(True)
        self.camera_source.setEnabled(True)
        self.camera_button.setText('開啟相機')
        self.scan_button.setEnabled(self.scanner is None)
        self.calibrate_button.setEnabled(False)
        self.depth_button.setEnabled(False)
        self.video_label.clear()
        self.video_label.setText('相機未開啟')
        self.status_banner.setText('相機已關閉 | 控制關閉')
        self._update_status()
        self.log('相機已關閉，控制已停用')

    def start_calibration(self) -> None:
        self._set_mode(Mode.DISABLED)
        self._stabilize_command("STOP")
        self.neutral_x = self.neutral_y = self.neutral_hand_size = None
        self.neutral_samples = 0
        self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
        self.calibration_active = True
        self.status_banner.setText("校準中 | 請保持張開手掌不動")
        self.log("手動中立校準開始")
        self._update_status()

    def toggle_zones(self) -> None:
        self.show_zones = not self.show_zones
        label = "顯示" if self.show_zones else "隱藏"
        self.zones_button.setText(f"分區顯示：{label}")
        self.log(f"分區顯示已{label}")

    def toggle_depth_direction(self) -> None:
        self._set_mode(Mode.DISABLED)
        self.depth_inverted = not self.depth_inverted
        label = "反轉" if self.depth_inverted else "正常"
        self.depth_button.setText(f"深度方向：{label}")
        self.log(f"深度方向設為{label}")
        self._save_settings()

    def toggle_direction_invert(self) -> None:
        self._set_mode(Mode.DISABLED)
        self.direction_inverted = not self.direction_inverted
        label = "反轉" if self.direction_inverted else "正常"
        self.invert_button.setText(f"移動方向：{label}")
        self.log(f"移動方向設為{label}")
        self._save_settings()

    def _hand_valid(self, hand) -> bool:
        xs = [p.x for p in hand.landmark]
        ys = [p.y for p in hand.landmark]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if not (self.MIN_HAND_SIZE <= width <= self.MAX_HAND_SIZE and self.MIN_HAND_SIZE <= height <= self.MAX_HAND_SIZE):
            return False
        palm = (0, 5, 9, 13, 17)
        spread = max(max(hand.landmark[i].x for i in palm) - min(hand.landmark[i].x for i in palm),
                     max(hand.landmark[i].y for i in palm) - min(hand.landmark[i].y for i in palm))
        return spread > self.MIN_LANDMARK_SPREAD

    @staticmethod
    def _hand_size(hand) -> float:
        index_mcp, pinky_mcp = hand.landmark[5], hand.landmark[17]
        wrist, middle_mcp = hand.landmark[0], hand.landmark[9]
        palm_width = math.hypot(index_mcp.x - pinky_mcp.x, index_mcp.y - pinky_mcp.y)
        palm_length = math.hypot(middle_mcp.x - wrist.x, middle_mcp.y - wrist.y)
        return (palm_width + palm_length) * 0.5

    @staticmethod
    def _open_palm(hand) -> bool:
        landmarks = hand.landmark
        palm = (0, 5, 9, 13, 17)
        cx = sum(landmarks[i].x for i in palm) / len(palm)
        cy = sum(landmarks[i].y for i in palm) / len(palm)
        def d(index: int) -> float:
            return math.hypot(landmarks[index].x - cx, landmarks[index].y - cy)
        return sum(d(tip) > d(pip) * 1.15 for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))) >= 3

    def _smooth_position(self, x: float, y: float) -> tuple[float, float]:
        if self.smooth_x is None:
            self.smooth_x, self.smooth_y = x, y
        else:
            alpha = self._value("Smoothing")
            if math.hypot(x - self.smooth_x, y - self.smooth_y) > 0.15:
                alpha = min(0.60, alpha * 2.5)
            self.smooth_x += alpha * (x - self.smooth_x)
            self.smooth_y += alpha * (y - self.smooth_y)
        return self.smooth_x, self.smooth_y


    @staticmethod
    def _command_name(vx: float, vy: float, vz: float) -> str:
        parts = []
        if vx < -1e-9: parts.append("LEFT")
        elif vx > 1e-9: parts.append("RIGHT")
        if vz > 1e-9: parts.append("UP")
        elif vz < -1e-9: parts.append("DOWN")
        if vy > 1e-9: parts.append("FWD")
        elif vy < -1e-9: parts.append("BACK")
        return "+".join(parts) if parts else "STOP"

    def _zone_command(self, x, y, size, confidence, speed):
        return zone_command(x, y, size, self.neutral_x, self.neutral_y, self.neutral_hand_size,
                            speed, self._value('Dead zone'), self._value('Size sensitivity'), self.depth_inverted)

    def _stabilize_command(self, command: str) -> str:
        if command == "STOP":
            self.pending_command = "STOP"
            self.pending_command_frames = 0
            self.stable_command = "STOP"
            return "STOP"
        if command == self.pending_command:
            self.pending_command_frames += 1
        else:
            self.pending_command = command
            self.pending_command_frames = 1
            self.stable_command = "STOP"
        if self.pending_command_frames >= self.COMMAND_CONFIRM_FRAMES:
            self.stable_command = command
        return self.stable_command


    def update_frame(self):
        if self.vision is None:
            return
        sample = self.vision.poll()
        now = monotonic()
        if sample is None:
            if self.frame_acquired_at and not fresh(now, self.frame_acquired_at, FRAME_TIMEOUT):
                if self.mode == Mode.GESTURE:
                    self._trip_workspace_fault('影像回饋過期')
            elif not self.frame_acquired_at and now - self.vision.started > 15:
                self.stop_camera()
                self.status_banner.setText('相機啟動逾時 | 控制關閉')
            return
        if sample[0] == 'error':
            self.stop_camera()
            self.status_banner.setText(sample[1])
            self.log(sample[1])
            return
        _, acquired_at, frame, points, confidence, quality_ok = sample
        if not fresh(now, acquired_at, FRAME_TIMEOUT):
            if self.mode == Mode.GESTURE:
                self._trip_workspace_fault('影像處理結果過期')
            return
        self.frame_acquired_at = acquired_at
        self.fps_frames += 1
        if now - self.last_fps_time >= 1:
            self.current_fps = self.fps_frames / (now - self.last_fps_time)
            self.fps_frames, self.last_fps_time = 0, now
        hand = SimpleNamespace(landmark=[SimpleNamespace(x=x, y=y, z=z) for x,y,z in points])
        vx = vy = vz = 0.0
        hand_display = "-, - | --"
        valid = False

        if points:
            if quality_ok and self._hand_valid(hand):
                valid = True
                self.lost_hand_frames = 0
                palm = (0, 5, 9, 13, 17)
                raw_x = sum(hand.landmark[i].x for i in palm) / len(palm)
                raw_y = sum(hand.landmark[i].y for i in palm) / len(palm)
                x, y = self._smooth_position(raw_x, raw_y)
                raw_size = self._hand_size(hand)
                self.smooth_size = raw_size if self.smooth_size is None else self.smooth_size + self.SIZE_SMOOTHING * (raw_size - self.smooth_size)
                size = self.smooth_size
                if self._open_palm(hand):
                    # Average several open-palm frames before defining the
                    # user's centre. This prevents the first noisy landmark
                    # frame from becoming a permanent movement offset.
                    if self.calibration_active and (self.neutral_x is None or self.neutral_y is None):
                        if self.neutral_samples:
                            n = self.neutral_samples
                            if (math.hypot(x - self.neutral_x_sum/n, y - self.neutral_y_sum/n) > .03
                                    or abs(size - self.neutral_size_sum/n) > .10 * self.neutral_size_sum/n):
                                self.neutral_samples = 0
                                self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
                        self.neutral_samples += 1
                        self.neutral_x_sum += x
                        self.neutral_y_sum += y
                        self.neutral_size_sum += size
                        if self.neutral_samples >= 8:
                            self.neutral_x = self.neutral_x_sum / self.neutral_samples
                            self.neutral_y = self.neutral_y_sum / self.neutral_samples
                            self.neutral_hand_size = self.neutral_size_sum / self.neutral_samples
                            self.calibration_active = False
                            self.status_banner.setText("中立校準完成 | 就緒")
                            self.log(f"中立姿態已校準：x={self.neutral_x:.2f}, y={self.neutral_y:.2f}, size={self.neutral_hand_size:.3f}")
                    elif self.neutral_x is not None and self.neutral_y is not None:
                        speed = min(self._value("Max speed"), self.SAFETY_SPEED_CAP)
                        vx, vy, vz, requested = self._zone_command(x, y, size, confidence, speed)
                        command = self._stabilize_command(requested)
                        # Never continue a previous direction while the hand
                        # crosses into a different zone. A new zone needs six
                        # consecutive frames, and the robot stays stopped
                        # during that confirmation window.
                        if command != requested:
                            vx = vy = vz = 0.0
                        elif command == "STOP":
                            vx = vy = vz = 0.0
                        elif self.direction_inverted:
                            vx, vy, vz = -vx, -vy, -vz
                else:
                    self._stabilize_command("STOP")
                    if self.calibration_active:
                        self.neutral_samples = 0
                        self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
                if self.neutral_hand_size:
                    delta = (size - self.neutral_hand_size) / self.neutral_hand_size * 100
                    hand_display = f"{x:.2f}, {y:.2f} | {delta:+.0f}%"
                else:
                    hand_display = f"{x:.2f}, {y:.2f} | 待校準"
            else:
                hand_display = "已偵測 | 請靠近並張開手掌"

        if not valid:
            if self.calibration_active:
                self.neutral_samples = 0
                self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
            self.lost_hand_frames += 1
            if self.lost_hand_frames > self.LOST_HAND_GRACE:
                self.smooth_x = self.smooth_y = self.smooth_size = None

        command = locals().get("command", "STOP")
        if not valid:
            command = self._stabilize_command("STOP")
            vx = vy = vz = 0.0
        command = self._command_name(vx, vy, vz)
        if self.mode == Mode.GESTURE:
            self._send_velocity(vx, vy, vz, source=Mode.GESTURE)
        self._last_hand_display, self._last_handedness = hand_display, confidence
        self._update_status(command, hand_display, confidence)
        self._draw_overlay(frame, command, confidence)
        if self.robot_enabled or command != self._last_frame_command:
            size_delta = None
            if self.neutral_hand_size:
                size_delta = (size - self.neutral_hand_size) / self.neutral_hand_size * 100 if valid else None
            self._csv_row("vision_request", command=command, vx=vx, vy=vy, vz=vz,
                          hand_x=x if valid else None, hand_y=y if valid else None,
                          size_delta_pct=size_delta, confidence=confidence)
            self._last_frame_command = command
            if self.robot_enabled and int(monotonic()) != getattr(self, "_csv_flush_tick", 0):
                self._csv_flush_tick = int(monotonic())
                self._flush_csv()
        self._show_frame(frame)

    def _draw_overlay(self, frame, command: str, confidence: float) -> None:
        h, w = frame.shape[:2]
        if self.show_zones:
            center = self.neutral_x if self.neutral_x is not None else .5
            left_x = int(w * (center - self._value("Dead zone")))
            right_x = int(w * (center + self._value("Dead zone")))
            cv2.line(frame, (left_x, 0), (left_x, h), (255, 180, 0), 2)
            cv2.line(frame, (right_x, 0), (right_x, h), (255, 180, 0), 2)
            if self.neutral_y is not None:
                top_y = int(h * (self.neutral_y - self._value("Dead zone")))
                bottom_y = int(h * (self.neutral_y + self._value("Dead zone")))
                cv2.line(frame, (left_x, top_y), (right_x, top_y), (255, 180, 0), 2)
                cv2.line(frame, (left_x, bottom_y), (right_x, bottom_y), (255, 180, 0), 2)
            cv2.putText(frame, "LEFT", (20, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
            cv2.putText(frame, "UP", (w // 2 - 15, max(25, h // 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
            cv2.putText(frame, "CENTRE: SIZE FWD/BACK", (w // 2 - 105, h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)
            cv2.putText(frame, "DOWN", (w // 2 - 25, min(h - 35, h * 4 // 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
            cv2.putText(frame, "RIGHT", (w - 80, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
        color = (0, 200, 0) if command != "STOP" else (0, 0, 220)
        cv2.putText(frame, f"{command} | {'LIVE' if self.robot_enabled else 'SIMULATION'}", (18, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2)
        cv2.putText(frame, f"Handedness: {confidence * 100:.0f}%  FPS: {self.current_fps:.0f}", (18, 66),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (225, 225, 225), 1)
        size_text = f"Size neutral: {self.neutral_hand_size:.3f}" if self.neutral_hand_size else "Centre hand to calibrate size"
        cv2.putText(frame, size_text, (18, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 220, 220), 1)

    def _show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, channels = rgb.shape
        image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self.video_label.setPixmap(pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def closeEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        self.stop_camera()
        self._disconnect_robot()
        self.rtde_timer.stop()
        if self.scanner is not None:
            self.scanner.close()
        self._save_settings()
        self._flush_csv()
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = self.csv_writer = None
        event.accept()


    def _poll_scanner(self):
        if self.scanner is None:
            return
        found = self.scanner.poll()
        if found is None:
            return
        self.scanner.close()
        self.scanner = None
        for source in found:
            if self.camera_combo.findData(source) < 0:
                self.camera_combo.addItem(source, source)
        self.scan_button.setEnabled(True)
        self.camera_button.setEnabled(True)
        self.scan_button.setText('掃描')
        self.log('找到相機：' + ', '.join(found) if found else '未找到相機或掃描逾時')

    def apply_vision_settings(self):
        running = self.camera_running
        if running:
            self.stop_camera()
        self._save_settings()
        if running:
            self.start_camera()
        self.log('偵測／追蹤設定已套用；開啟相機後須重新校準及啟動控制')


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = UR10VisionQtApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
