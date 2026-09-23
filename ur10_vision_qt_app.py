"""Qt desktop UI for UR10 hand-vision control.

Camera hand position controls left/right/up/down. Hand size controls
forward/backward after a neutral-size calibration in the centre of the frame.
"""

from __future__ import annotations

import csv
import json
import math
import multiprocessing as mp_process
import os
import socket
import sys
import time
from typing import Optional

from safety_config import (
    DEFAULT_SPEED,
    MAX_TRANSLATIONAL_SPEED,
    REAL_TARGET,
    RETURN_SPEED,
    URSIM_TARGET,
    cap_translational_velocity,
    speed_for_profile,
    workspace_profile,
    workspace_is_configured,
)

import cv2
import mediapipe as mp
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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


def _rtde_worker(pipe, host: str) -> None:
    """Own all ur-rtde native state in a crash-isolated child process."""
    control = None
    receive = None
    try:
        import rtde_control
        import rtde_receive

        # Establish feedback first. RTDEControlInterface starts a native
        # reconnect thread, so defer it until the first motion command.
        receive = rtde_receive.RTDEReceiveInterface(host)
        pipe.send(("connected", ""))
        control = None

        def ensure_control():
            nonlocal control
            if control is None:
                control = rtde_control.RTDEControlInterface(host)
            return control
        next_feedback_at = 0.0
        while True:
            now = time.monotonic()
            if now >= next_feedback_at:
                pipe.send((
                    "pose",
                    list(receive.getActualTCPPose()),
                    int(receive.getSafetyMode()),
                    int(receive.getRobotStatus()),
                ))
                next_feedback_at = now + 0.10
            if not pipe.poll(0.02):
                continue
            message = pipe.recv()
            action = message[0]
            if action == "speed":
                safe_velocity = cap_translational_velocity(message[1])
                if safe_velocity is None:
                    pipe.send(("error", "拒絕非有限或超過 0.5 m/s 向量"))
                    break
                ensure_control().speedL(safe_velocity, message[2], message[3])
            elif action == "stop":
                if control is not None:
                    control.speedStop(message[1])
            elif action == "close":
                break
    except Exception as exc:
        try:
            pipe.send(("error", str(exc)))
        except Exception:
            pass
    finally:
        # Do not call ur-rtde disconnect here after an EOF; process teardown
        # safely reclaims the native socket without touching its reconnecter.
        try:
            pipe.close()
        except Exception:
            pass


class RTDEWorkerClient:
    def __init__(self, host: str) -> None:
        context = mp_process.get_context("spawn")
        self.parent_pipe, child_pipe = context.Pipe()
        self.process = context.Process(target=_rtde_worker, args=(child_pipe, host), daemon=True)
        self.process.start()
        child_pipe.close()
        self.connected = False
        self.error = ""
        self.actual_tcp_pose: Optional[tuple[float, float, float, float, float, float]] = None
        self.pose_received_at = 0.0
        self.safety_status: Optional[int] = None
        self.robot_status: Optional[int] = None

    def poll(self) -> Optional[tuple[str, str]]:
        event_to_return = None
        try:
            while self.parent_pipe.poll():
                event = self.parent_pipe.recv()
                if event[0] == "connected":
                    self.connected = True
                    event_to_return = event
                elif event[0] == "pose":
                    self.actual_tcp_pose = tuple(float(value) for value in event[1])
                    self.pose_received_at = time.monotonic()
                    self.safety_status = int(event[2])
                    self.robot_status = int(event[3])
                elif event[0] == "error":
                    self.connected = False
                    self.error = event[1]
                    event_to_return = event
        except (EOFError, OSError) as exc:
            self.connected = False
            self.error = str(exc)
            return ("error", self.error)
        if event_to_return is not None:
            return event_to_return
        if not self.process.is_alive():
            self.error = "RTDE worker exited unexpectedly"
            self.connected = False
            return ("error", self.error)
        return None

    def speedL(self, velocity, acceleration, period) -> None:
        if self.connected and self.process.is_alive():
            self.parent_pipe.send(("speed", velocity, acceleration, period))

    def speedStop(self, deceleration) -> None:
        if self.connected and self.process.is_alive():
            self.parent_pipe.send(("stop", deceleration))

    def disconnect(self) -> None:
        # Terminate the isolated process instead of invoking native teardown
        # on the Qt/UI process after a possible reconnect crash.
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=0.5)
        self.connected = False
        try:
            self.parent_pipe.close()
        except Exception:
            pass


class UR10VisionQtApp(QMainWindow):
    RTDE_PORT = 30004
    ACCELERATION = 0.25
    CONTROL_PERIOD = 0.10
    LOST_HAND_GRACE = 3
    MIN_HAND_SIZE = 0.06
    MAX_HAND_SIZE = 0.85
    MIN_LANDMARK_SPREAD = 0.015
    # Depth is judged relative to the calibrated palm span, not a fixed
    # pixel-like value. A 6% change rejects jitter but accepts a real move.
    DEPTH_RATIO_DEAD_ZONE = 0.06
    SIZE_SMOOTHING = 0.12
    SIZE_CALIB_RATE = 0.04
    LEFT_ZONE_END = 0.38
    RIGHT_ZONE_START = 0.62
    VERTICAL_ZONE_DEAD = 0.10
    CONFIDENCE_GATE = 0.75
    COMMAND_CONFIRM_FRAMES = 6
    SAFETY_SPEED_CAP = MAX_TRANSLATIONAL_SPEED

    # Workspace values come from safety_config profiles. URSim is the reviewed
    # demonstration range; physical hardware is unset until measured.
    POSE_FEEDBACK_TIMEOUT = 0.50
    WALL_TOLERANCE = 0.002
    WALL_SLOW_ZONE = 0.10
    WALL_MIN_SCALE = 0.25
    AXIS_TEST_DISTANCE = 0.03
    SAFE_ORIGIN = (0.40, 0.00, 0.40)
    SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
    RETURN_SPEED_CAP = RETURN_SPEED
    RETURN_POSITION_TOLERANCE = 0.010
    REAL_CHECKLIST_ITEMS = (
        "實體工作區已清空，機械人活動範圍內沒有人。",
        "實體緊急停止按鈕就在手邊，並已測試有效。",
        "PolyScope 狀態列顯示 Remote（遠端控制已啟用）。",
        "控制器上已設定安全平面／保護性停止。",
        "速度設定已在控制關閉時由操作者明確選擇；0.5 m/s 僅為軟體可設定上限，不代表安全或已驗證。",
    )
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UR10 Vision Control")
        self.resize(1320, 860)
        self.setMinimumSize(1080, 700)

        self.cap: Optional[cv2.VideoCapture] = None
        self.robot = None
        self.rtde_client: Optional[RTDEWorkerClient] = None
        self.hands = None
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
        self.last_frame_time = 0.0
        self.last_fps_time = time.monotonic()
        self.fps_frames = 0
        self.current_fps = 0.0
        self.camera_failures = 0
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
        self.test_steps = 0
        self.test_vector = (0.02, 0.0, 0.0)

        self._setup_csv_logging()
        self._last_frame_command = "STOP"
        self._last_frame_sent = (0.0, 0.0, 0.0)

        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands

        self.frame_timer = QTimer(self)
        self.frame_timer.timeout.connect(self.update_frame)
        self.test_timer = QTimer(self)
        self.test_timer.timeout.connect(self._test_step)
        self.rtde_timer = QTimer(self)
        self.rtde_timer.timeout.connect(self._poll_rtde)

        self._build_ui()
        self._apply_style()
        self._load_settings()
        self.log(f"CSV 記錄：{self.csv_path}")
        self.log("就緒。請先連接 URSim，再啟動控制。")
        self.log("請張開手掌放於畫面中央以校準手掌大小。")
        self._update_status()

    # --- UI ------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("UR10 視覺控制系統")
        title.setObjectName("title")
        subtitle = QLabel("安全分區：左＝左移 ｜ 中央＝手掌大小控制前後 ｜ 右＝右移")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.status_banner = QLabel("就緒 | 模擬模式")
        self.status_banner.setObjectName("statusBanner")
        self.status_banner.setAlignment(Qt.AlignCenter)
        self.status_banner.setMinimumWidth(290)
        header.addWidget(self.status_banner)
        root.addLayout(header)

        main = QHBoxLayout()
        main.setSpacing(14)

        video_card = self._card()
        video_layout = QVBoxLayout(video_card)
        video_layout.setContentsMargins(12, 12, 12, 12)
        video_layout.setSpacing(8)
        video_layout.addWidget(self._section_label("視覺預覽"))
        self.video_label = QLabel("相機未開啟")
        self.video_label.setObjectName("video")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(620, 470)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_layout.addWidget(self.video_label, 1)
        main.addWidget(video_card, 1)

        side = QVBoxLayout()
        side.setSpacing(10)
        self.side_tabs = QTabWidget()
        self.side_tabs.setObjectName("sideTabs")
        self.side_tabs.addTab(self._wrap_in_tab(self._build_camera_card()), "攝影機")
        self.side_tabs.addTab(self._wrap_in_tab(self._build_robot_card()), "連接")
        self.side_tabs.addTab(self._wrap_in_tab(self._build_axis_card()), "單軸測試")
        self.side_tabs.setMinimumWidth(330)
        self.side_tabs.setMaximumWidth(390)
        side.addWidget(self.side_tabs, 1)
        main.addLayout(side, 0)
        root.addLayout(main, 1)

        # Keep status full-width so the connection controls never get
        # vertically compressed on smaller laptop screens.
        root.addWidget(self._build_status_card())

        self._build_advanced_section(root)

        footer = QLabel("安全提示：握拳／沒有手＝停止。請先在 URSim 完成測試，才考慮實體機械人。")
        footer.setObjectName("footer")
        root.addWidget(footer)

    def _wrap_in_tab(self, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.addWidget(widget)
        layout.addStretch()
        return container

    def _build_advanced_section(self, root: QVBoxLayout) -> None:
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("進階控制（調校／事件日誌）  ▸")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.advanced_toggle.clicked.connect(self._toggle_advanced)
        root.addWidget(self.advanced_toggle)

        self.advanced_content = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_content)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(8)
        advanced_layout.addWidget(self._build_tuning_card())

        log_card = self._card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(12, 8, 12, 8)
        log_layout.addWidget(self._section_label("事件日誌"))
        self.event_log = QPlainTextEdit()
        self.event_log.setObjectName("eventLog")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(50)
        self.event_log.setFixedHeight(82)
        log_layout.addWidget(self.event_log)
        advanced_layout.addWidget(log_card)
        self.advanced_content.setVisible(False)
        root.addWidget(self.advanced_content)

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_content.setVisible(checked)
        self.advanced_toggle.setText("進階控制（調校／事件日誌）  ▾" if checked else "進階控制（調校／事件日誌）  ▸")

    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _build_camera_card(self) -> QFrame:
        card = self._card()
        # Four controls need real vertical room; without this Qt compresses
        # the newly added calibration buttons into blank-looking strips.
        card.setMinimumHeight(226)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("攝影機"))
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(["0", "<手動輸入>"])
        self.camera_combo.currentTextChanged.connect(self._camera_source_changed)
        layout.addWidget(self.camera_combo)
        self.camera_source = QLineEdit("0")
        self.camera_source.setPlaceholderText("0 或 RTSP URL")
        self.camera_source.setVisible(False)
        layout.addWidget(self.camera_source)
        scan_row = QHBoxLayout()
        scan_row.setSpacing(6)
        self.scan_button = QPushButton("掃描相機")
        self.scan_button.setMinimumHeight(26)
        self.scan_button.clicked.connect(self._scan_cameras)
        scan_row.addWidget(self.scan_button)
        scan_row.addStretch()
        layout.addLayout(scan_row)
        self.camera_button = QPushButton("開啟相機")
        self.camera_button.setMinimumHeight(28)
        self.camera_button.setObjectName("primaryButton")
        self.camera_button.clicked.connect(self.toggle_camera)
        layout.addWidget(self.camera_button)
        self.calibrate_button = QPushButton("校準中立姿態")
        self.calibrate_button.setMinimumHeight(28)
        self.calibrate_button.setEnabled(False)
        self.calibrate_button.clicked.connect(self.start_calibration)
        layout.addWidget(self.calibrate_button)
        self.zones_button = QPushButton("分區顯示：隱藏")
        self.zones_button.setMinimumHeight(28)
        self.zones_button.clicked.connect(self.toggle_zones)
        layout.addWidget(self.zones_button)
        self.depth_button = QPushButton("深度方向：正常")
        self.depth_button.setMinimumHeight(28)
        self.depth_button.setEnabled(False)
        self.depth_button.clicked.connect(self.toggle_depth_direction)
        layout.addWidget(self.depth_button)
        return card

    def _build_robot_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("UR10 連接"))

        self.target_combo = QComboBox()
        self.target_combo.addItems(["URSim", "實體 UR10"])
        self.target_combo.currentTextChanged.connect(self._target_changed)
        layout.addWidget(self.target_combo)

        self.robot_ip = QLineEdit("127.0.0.1")
        self.robot_ip.setPlaceholderText("UR 控制器 IP")
        layout.addWidget(self.robot_ip)

        self.connect_button = QPushButton("連接機械人")
        self.connect_button.setMinimumHeight(30)
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self.connect_robot)
        layout.addWidget(self.connect_button)

        self.enable_button = QPushButton("啟動機械人控制")
        self.enable_button.setMinimumHeight(30)
        self.enable_button.setObjectName("enableButton")
        self.enable_button.clicked.connect(self.toggle_robot_control)
        layout.addWidget(self.enable_button)

        self.stop_button = QPushButton("急停")
        self.stop_button.setMinimumHeight(30)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.emergency_stop)
        layout.addWidget(self.stop_button)

        self.invert_button = QPushButton("移動方向：正常")
        self.invert_button.setMinimumHeight(28)
        self.invert_button.clicked.connect(self.toggle_direction_invert)
        layout.addWidget(self.invert_button)

        self.test_button = QPushButton("測試移動（1 秒）")
        self.test_button.setMinimumHeight(28)
        self.test_button.clicked.connect(self.test_ursim_motion)
        self.test_button.setEnabled(False)
        layout.addWidget(self.test_button)
        return card

    def _build_axis_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(self._section_label("單軸測試"))
        hint = QLabel(f"每按一下移動 {self.AXIS_TEST_DISTANCE * 100:.0f} cm，以可設定速度上限 {self.SAFETY_SPEED_CAP:.2f} m/s 執行。需先連接並啟動控制。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        axis_grid = QGridLayout()
        axis_grid.setSpacing(8)
        self.axis_buttons: dict[str, QPushButton] = {}
        for index, (label, vector) in enumerate((
                ("+X", (1, 0, 0)), ("+Y", (0, 1, 0)), ("+Z", (0, 0, 1)),
                ("−X", (-1, 0, 0)), ("−Y", (0, -1, 0)), ("−Z", (0, 0, -1)))):
            button = QPushButton(label)
            button.setMinimumHeight(40)
            button.setObjectName("axisButton")
            button.setEnabled(False)
            button.clicked.connect(
                lambda _=False, vec=vector: self.axis_test(*vec))
            row, col = divmod(index, 3)
            axis_grid.addWidget(button, row, col)
            self.axis_buttons[label] = button
        layout.addLayout(axis_grid)
        layout.addStretch()
        return card

    def _build_status_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("即時狀態"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.status_values: dict[str, QLabel] = {}
        tile_titles = {
            "Robot": "機械人", "Control": "控制", "Command": "指令",
            "Hand": "手掌", "Confidence": "信心", "FPS": "帧率",
        }
        for index, key in enumerate(("Robot", "Control", "Command", "Hand", "Confidence", "FPS")):
            row, col = divmod(index, 3)
            tile = QFrame()
            tile.setObjectName("tile")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(10, 6, 10, 6)
            tile_layout.setSpacing(2)
            title = QLabel(tile_titles[key])
            title.setObjectName("statusKey")
            value = QLabel("--")
            value.setObjectName("statusValue")
            value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            tile_layout.addWidget(title)
            tile_layout.addWidget(value)
            grid.addWidget(tile, row, col)
            self.status_values[key] = value
        layout.addLayout(grid)
        telemetry_row = QHBoxLayout()
        telemetry_row.setSpacing(10)
        self.tcp_label = QLabel("TCP: --")
        self.tcp_label.setObjectName("telemetry")
        telemetry_row.addWidget(self.tcp_label, 2)
        self.safety_label = QLabel("安全模式: --")
        self.safety_label.setObjectName("telemetry")
        telemetry_row.addWidget(self.safety_label, 1)
        self.csv_indicator = QLabel("○ CSV 未啟用")
        self.csv_indicator.setObjectName("telemetry")
        telemetry_row.addWidget(self.csv_indicator, 2)
        layout.addLayout(telemetry_row)
        bottom = QHBoxLayout()
        self.confidence_bar = QLabel("[----------] 0%")
        self.confidence_bar.setObjectName("meter")
        bottom.addWidget(self.confidence_bar, 1)
        self.direction_badge = QLabel("停止")
        self.direction_badge.setObjectName("directionBadge")
        self.direction_badge.setAlignment(Qt.AlignCenter)
        self.direction_badge.setMinimumWidth(140)
        self.direction_badge.setMinimumHeight(36)
        bottom.addWidget(self.direction_badge)
        layout.addLayout(bottom)
        self.workspace_status = QLabel("工作區：未設定 | 實際控制鎖定")
        self.workspace_status.setObjectName("workspaceStatus")
        self.workspace_status.setProperty("state", "warn")
        self.workspace_status.setWordWrap(True)
        layout.addWidget(self.workspace_status)
        return card

    def _build_tuning_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("調校"))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.tuners: dict[str, tuple[QSlider, QLabel, float]] = {}
        self._add_tuner(row, "Detection", "偵測信心", 50, 95, 80, 1, 100.0)
        self._add_tuner(row, "Tracking", "追蹤信心", 30, 95, 60, 1, 100.0)
        self._add_tuner(row, "Smoothing", "平滑度", 5, 80, 25, 1, 100.0)
        self._add_tuner(row, "Dead zone", "死區", 3, 30, 10, 1, 100.0)
        self._add_tuner(row, "Max speed", "最高速度（可設定上限）", 1, 50, 2, 2, 100.0)
        self._add_tuner(row, "Size sensitivity", "手掌敏感度", 10, 80, 30, 1, 10.0)
        self.tuners["Max speed"][0].valueChanged.connect(self._max_speed_changed)
        layout.addLayout(row)
        return card

    def _add_tuner(self, row: QHBoxLayout, name: str, label_zh: str, minimum: int, maximum: int,
                   value: int, step: int, scale: float) -> None:
        box = QVBoxLayout()
        box.setSpacing(3)
        title = QLabel(label_zh)
        title.setObjectName("tunerLabel")
        value_label = QLabel()
        value_label.setObjectName("tunerValue")
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(minimum)
        slider.setMaximum(maximum)
        slider.setSingleStep(step)
        slider.setValue(value)
        slider.valueChanged.connect(lambda v, label=value_label, s=scale: label.setText(f"{v / s:.2f}"))
        value_label.setText(f"{value / scale:.2f}")
        box.addWidget(title)
        box.addWidget(value_label)
        box.addWidget(slider)
        row.addLayout(box, 1)
        self.tuners[name] = (slider, value_label, scale)

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0b1220; color: #f8fafc;
                font-family: 'PingFang TC', 'Heiti TC', Arial; }
            QFrame#card { background: #16203a; border: 1px solid #24314f;
                border-radius: 12px; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #f8fafc; }
            QLabel#subtitle, QLabel#footer { font-size: 12px; color: #7c8db0; }
            QLabel#hint { font-size: 12px; color: #94a3b8; }
            QLabel#section { font-size: 12px; font-weight: 700; color: #8ab4f8;
                letter-spacing: 1px; }
            QLabel#statusBanner { background: #1e2a45; color: #e2e8f0;
                border: 1px solid #2c3c63; border-radius: 8px; padding: 10px 14px;
                font-weight: 600; font-size: 14px; }
            QLabel#statusBanner[state="live"] { background: #123c2b; color: #6ee7a0;
                border: 1px solid #1d5c43; }
            QLabel#statusBanner[state="warn"] { background: #3f2f0d; color: #fbbf24;
                border: 1px solid #6b5216; }
            QLabel#statusBanner[state="error"] { background: #44141a; color: #fca5a5;
                border: 1px solid #7a2029; }
            QLabel#video { background: #060b18; color: #64748b;
                border: 1px solid #24314f; border-radius: 8px; font-size: 16px; }
            QLineEdit, QComboBox { background: #0e1730; border: 1px solid #33436b;
                border-radius: 7px; padding: 8px; color: #f8fafc; min-height: 18px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #38bdf8; }
            QComboBox QAbstractItemView { background: #16203a;
                selection-background-color: #2563eb; color: #f8fafc; }
            QPushButton { background: #24314f; color: #f8fafc; border: 0;
                border-radius: 7px; padding: 9px; font-weight: 600; }
            QPushButton:hover { background: #2f4166; }
            QPushButton:pressed { background: #1b2740; }
            QToolButton { background: #1e2a45; color: #f8fafc;
                border: 1px solid #33436b; border-radius: 7px; padding: 9px;
                font-weight: 600; text-align: left; }
            QToolButton:hover { background: #24314f; }
            QPushButton:disabled { color: #55627e; background: #1a2338; }
            QPushButton#primaryButton { background: #2563eb; color: white; }
            QPushButton#primaryButton:hover { background: #3b76f5; }
            QPushButton#primaryButton:pressed { background: #1d4fc4; }
            QPushButton#enableButton { background: #16803c; color: white; }
            QPushButton#enableButton:hover { background: #1b9c49; }
            QPushButton#stopButton { background: #b91c1c; color: white; font-size: 15px; }
            QPushButton#stopButton:hover { background: #dc2626; }
            QPushButton#axisButton { font-size: 15px; font-weight: 700;
                background: #1e2a45; border: 1px solid #33436b; }
            QPushButton#axisButton:hover { background: #2f4166;
                border: 1px solid #38bdf8; }
            QTabWidget#sideTabs::pane { border: 1px solid #24314f;
                border-radius: 10px; background: #101a33; top: -1px; }
            QTabBar::tab { background: #131e38; color: #7c8db0; padding: 8px 14px;
                border-top-left-radius: 7px; border-top-right-radius: 7px;
                font-weight: 600; }
            QTabBar::tab:selected { background: #16203a; color: #8ab4f8; }
            QFrame#tile { background: #101a33; border: 1px solid #24314f;
                border-radius: 8px; }
            QLabel#statusKey { color: #7c8db0; font-size: 11px; }
            QLabel#statusValue { color: #f8fafc; font-weight: 700; font-size: 14px;
                font-family: Menlo, 'PingFang TC'; }
            QLabel#telemetry { color: #94a3b8; font-size: 12px;
                font-family: Menlo, 'PingFang TC'; }
            QLabel#meter { background: #0e1730; color: #34d399; border-radius: 6px;
                padding: 6px 9px; font-family: Menlo; font-size: 13px; }
            QLabel#directionBadge { background: #334155; border-radius: 8px;
                padding: 6px 14px; font-weight: 700; font-size: 17px; }
            QLabel#workspaceStatus[state="ok"] { background: #0e2b1f; color: #34d399;
                border-radius: 7px; padding: 8px; font-weight: 700; }
            QLabel#workspaceStatus[state="warn"] { background: #0e1730; color: #fbbf24;
                border-radius: 7px; padding: 8px; font-weight: 700; }
            QLabel#workspaceStatus[state="error"] { background: #3d1216; color: #f87171;
                border-radius: 7px; padding: 8px; font-weight: 700; }
            QLabel#tunerLabel { color: #7c8db0; font-size: 11px; }
            QLabel#tunerValue { color: #f8fafc; font-size: 12px; font-weight: 700; }
            QSlider::groove:horizontal { background: #24314f; height: 6px;
                border-radius: 3px; }
            QSlider::handle:horizontal { background: #38bdf8; width: 15px;
                margin: -5px 0; border-radius: 7px; }
            QPlainTextEdit#eventLog { background: #0e1730; border: 0;
                border-radius: 7px; color: #9fb3d8; font-family: Menlo, 'PingFang TC';
                font-size: 11px; padding: 6px; }
        """)

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
        robot = f"{self.target_combo.currentText()} | " + ("已連接" if connected else "未連接")
        self.status_values["Robot"].setText(robot)
        self.status_values["Control"].setText("已啟動" if self.robot_enabled else "關閉")
        self.status_values["Command"].setText(COMMAND_ZH.get(command, command))
        self.status_values["Hand"].setText(hand)
        self.status_values["Confidence"].setText(f"{confidence * 100:.0f}%" if confidence else "--")
        self.status_values["FPS"].setText(f"{self.current_fps:.1f}")
        filled = int(round(max(0.0, min(1.0, confidence)) * 10))
        self.confidence_bar.setText(f"[{'#' * filled}{'-' * (10 - filled)}] {confidence * 100:.0f}%")
        self.direction_badge.setText(COMMAND_ZH.get(command, command))
        color = {
            "STOP": "#334155", "LEFT": "#6b21a8", "RIGHT": "#166534",
            "UP": "#1d4ed8", "DOWN": "#9a3412", "FWD": "#0f766e", "BACK": "#78350f",
        }.get(command.split("+")[0], "#334155")
        self.direction_badge.setStyleSheet(
            f"background: {color}; border-radius: 8px; padding: 6px 14px;"
            "font-weight: 700; font-size: 17px;")
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
        if getattr(self, "csv_file", None) is not None:
            self.csv_indicator.setText(f"● CSV 錄製中：{os.path.basename(self.csv_path)}")
        else:
            self.csv_indicator.setText("○ CSV 未啟用")
        # Status banner colour reflects real state regardless of its text.
        if self.workspace_fault or self.rtde_lost:
            banner_state = "error"
        elif self.robot_enabled:
            banner_state = "live"
        elif self.returning_to_origin:
            banner_state = "warn"
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
            workspace = "工作區：未設定 | 實際控制鎖定"
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

    def _target_changed(self, target: str) -> None:
        if self.connection_in_progress or self.rtde_client is not None:
            # The target is locked for the whole connection session. Safety
            # decisions use session_identity, never a relabelled combo box.
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentText(self.session_identity[0] if self.session_identity else target)
            self.target_combo.blockSignals(False)
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

    def _restore_physical_speed_selection(self) -> None:
        self._setting_speed_profile = True
        try:
            slider, _, scale = self.tuners["Max speed"]
            slider.setValue(int(round(self.selected_real_speed * scale)))
        finally:
            self._setting_speed_profile = False

    def _max_speed_changed(self, _value: int) -> None:
        if (getattr(self, "target_combo", None) is not None
                and self.target_combo.currentText() == REAL_TARGET
                and not self.robot_enabled
                and not self.connection_in_progress
                and not self._setting_speed_profile):
            self.real_speed_selected = True
            self.selected_real_speed = self._value("Max speed")
        self._save_settings()

    def log(self, message: str) -> None:
        self.event_log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")
        self._csv_row("event", note=message)

    # --- CSV logging ----------------------------------------------------

    def _setup_csv_logging(self) -> None:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            self.csv_path = os.path.join(
                log_dir, f"session_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow([
                "timestamp", "event", "command", "vx", "vy", "vz",
                "hand_x", "hand_y", "size_delta_pct", "confidence", "fps",
                "tcp_x", "tcp_y", "tcp_z", "safety_mode", "robot_enabled", "note",
            ])
            self.csv_file.flush()
        except OSError as exc:
            self.csv_path = ""
            self.csv_file = None
            self.csv_writer = None

    def _csv_row(self, event: str, command: str = "STOP",
                 vx: float = 0.0, vy: float = 0.0, vz: float = 0.0,
                 hand_x: Optional[float] = None, hand_y: Optional[float] = None,
                 size_delta_pct: Optional[float] = None,
                 confidence: float = 0.0, note: str = "") -> None:
        if self.csv_writer is None:
            return
        pose = self.rtde_client.actual_tcp_pose if self.rtde_client else None
        safety = self.rtde_client.safety_status if self.rtde_client else None
        try:
            self.csv_writer.writerow([
                f"{time.time():.3f}", event, command,
                f"{vx:+.4f}", f"{vy:+.4f}", f"{vz:+.4f}",
                "" if hand_x is None else f"{hand_x:.4f}",
                "" if hand_y is None else f"{hand_y:.4f}",
                "" if size_delta_pct is None else f"{size_delta_pct:+.1f}",
                f"{confidence:.3f}", f"{self.current_fps:.1f}",
                "" if pose is None else f"{pose[0]:+.4f}",
                "" if pose is None else f"{pose[1]:+.4f}",
                "" if pose is None else f"{pose[2]:+.4f}",
                "" if safety is None else int(safety),
                int(self.robot_enabled), note,
            ])
        except Exception:
            pass

    def _flush_csv(self) -> None:
        if self.csv_file is not None:
            try:
                self.csv_file.flush()
                os.fsync(self.csv_file.fileno())
            except Exception:
                pass

    # --- Settings persistence -------------------------------------------

    def _settings_data(self) -> dict:
        return {
            "target": self.target_combo.currentText(),
            "robot_ip_ursim": self._ips.get("URSim", "127.0.0.1"),
            "robot_ip_real": self._ips.get("實體 UR10", ""),
            "direction_inverted": self.direction_inverted,
            "depth_inverted": self.depth_inverted,
            "camera_source": self.camera_combo.currentText(),
            "camera_manual": self.camera_source.text(),
            "max_speed": self._value("Max speed"),
            "detection": self._value("Detection"),
            "tracking": self._value("Tracking"),
            "smoothing": self._value("Smoothing"),
            "dead_zone": self._value("Dead zone"),
            "size_sensitivity": self._value("Size sensitivity"),
        }

    def _load_settings(self) -> None:
        self._ips = {"URSim": "127.0.0.1", "實體 UR10": ""}
        try:
            with open(self.SETTINGS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        self._ips["URSim"] = data.get("robot_ip_ursim", self._ips["URSim"])
        self._ips["實體 UR10"] = data.get("robot_ip_real", self._ips["實體 UR10"])
        target = data.get("target", URSIM_TARGET)
        self.target_combo.setCurrentText(target if target in (URSIM_TARGET, REAL_TARGET) else URSIM_TARGET)
        self._apply_target_profile(self.target_combo.currentText(), data.get("max_speed"))
        self.robot_ip.setText(self._ips.get(self.target_combo.currentText(), "127.0.0.1"))
        self.direction_inverted = bool(data.get("direction_inverted", False))
        self.invert_button.setText(
            f"Robot directions: {'inverted' if self.direction_inverted else 'normal'}")
        self.depth_inverted = bool(data.get("depth_inverted", False))
        self.depth_button.setText(
            f"Depth direction: {'inverted' if self.depth_inverted else 'normal'}")
        camera = data.get("camera_source")
        if camera:
            index = self.camera_combo.findText(camera)
            if index >= 0:
                self.camera_combo.setCurrentIndex(index)
            else:
                self.camera_combo.setCurrentText("<Manual entry>")
                self.camera_source.setText(camera)
        manual = data.get("camera_manual")
        if manual:
            self.camera_source.setText(manual)
        for key, name in (("detection", "Detection"), ("tracking", "Tracking"),
                          ("smoothing", "Smoothing"), ("dead_zone", "Dead zone"),
                          ("size_sensitivity", "Size sensitivity")):
            value = data.get(key)
            if value is not None:
                slider, value_label, scale = self.tuners[name]
                slider.setValue(int(round(float(value) * scale)))
        self.log(f"已從 {os.path.basename(self.SETTINGS_PATH)} 載入設定")

    def _save_settings(self) -> None:
        if not hasattr(self, "_ips"):
            return
        self._ips[self.target_combo.currentText()] = self.robot_ip.text().strip()
        try:
            with open(self.SETTINGS_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._settings_data(), fh, indent=2)
        except OSError as exc:
            self.log(f"設定儲存失敗: {exc}")

    # --- Robot ---------------------------------------------------------

    def connect_robot(self) -> None:
        if self.connection_in_progress:
            return
        if self.rtde_client is not None:
            self._disconnect_robot()
            return
        target = self.target_combo.currentText()
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
            self.rtde_client = RTDEWorkerClient(host)
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

    def _poll_rtde(self) -> None:
        if self.rtde_client is None:
            self.rtde_timer.stop()
            return
        event = self.rtde_client.poll()
        if event is None:
            return
        if event[0] == "connected":
            if self.session_identity is None:
                self._disconnect_robot()
                return
            self.connect_button.setText("中斷連線")
            self.test_button.setEnabled(self.session_identity[0] == URSIM_TARGET)
            for button in self.axis_buttons.values():
                button.setEnabled(True)
            self.status_banner.setText("機械人已連接 | 控制關閉")
            self.log("RTDE 已連接")
            self._update_status()
        else:
            self.rtde_timer.stop()
            self.robot_enabled = False
            self._set_speed_editor_enabled(True)
            self.rtde_lost = True
            self.enable_button.setText("啟動機械人控制")
            self.status_banner.setText("RTDE 失敗 | 控制關閉")
            self.log(f"RTDE 工作程序錯誤: {event[1]}")
            self._update_status()

    def _disconnect_robot(self) -> None:
        if self.rtde_cleanup_in_progress:
            return
        self.rtde_cleanup_in_progress = True
        robot = self.robot
        self.robot = None
        worker = self.rtde_client
        self.rtde_client = None
        self.session_identity = None
        self.robot_enabled = False
        self._set_speed_editor_enabled(True)
        self.test_timer.stop()
        self.rtde_timer.stop()
        if worker is not None:
            worker.disconnect()
        self.connect_button.setText("連接機械人")
        self.enable_button.setText("啟動機械人控制")
        self.test_button.setEnabled(False)
        self.target_combo.setEnabled(True)
        self.robot_ip.setEnabled(True)
        for button in self.axis_buttons.values():
            button.setEnabled(False)
        self.status_banner.setText("機械人未連接 | 模擬模式")
        self.log("機械人已斷線")
        self._update_status()
        self.rtde_lost = False
        self.rtde_cleanup_in_progress = False

    def _confirm_real_robot_checklist(self) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("實體 UR10 安全檢查清單")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "啟動實體機械人移動前，請逐項確認以下安全事項："))
        checkboxes = []
        for item in self.REAL_CHECKLIST_ITEMS:
            checkbox = QCheckBox(item)
            checkboxes.append(checkbox)
            layout.addWidget(checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("全部確認")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        confirmed = dialog.exec() == QDialog.Accepted and all(
            box.isChecked() for box in checkboxes)
        if not confirmed:
            self.log("實體 UR10 檢查清單未完全確認；控制保持關閉")
        return confirmed

    def _dashboard_remote_state(self, host: str) -> Optional[bool]:
        """Read a bounded dashboard reply, skipping the initial greeting."""
        try:
            with socket.create_connection((host, 29999), timeout=2.0) as sock:
                sock.settimeout(2.0)
                sock.sendall(b"is in remote control\n")
                data = b""
                deadline = time.monotonic() + 2.0
                result: Optional[bool] = None
                while time.monotonic() < deadline and len(data) < 4096:
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

    def toggle_robot_control(self) -> None:
        if self.rtde_client is None or not self.rtde_client.connected:
            QMessageBox.warning(self, "機械人未連接", "請先連接 URSim 或 UR10。")
            return
        if not self.robot_enabled:
            answer = QMessageBox.question(
                self, "啟動機械人移動",
                "請先確認機械人工作區已清空，才繼續。確定啟動？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            if self.session_identity is None or self.session_identity[0] != self.target_combo.currentText():
                QMessageBox.warning(self, "連線目標已變更", "連線期間禁止變更控制目標。")
                return
            if self.session_identity[0] == REAL_TARGET:
                if not self.real_speed_selected:
                    QMessageBox.warning(
                        self, "需要確認實體速度",
                        "請在控制保持關閉時，明確設定實體機械人的速度；"
                        "已儲存的速度不會自動沿用。")
                    self.log("已封鎖實體控制：尚未明確選擇速度")
                    return
                remote = self._dashboard_remote_state(self.session_identity[1])
                if remote is not True:
                    QMessageBox.warning(
                        self, "需要遠端控制模式",
                        "控制器回報的遠端控制狀態不是 true。\n\n"
                        "請先在 PolyScope 進入遠端模式（設定 → 系統 → 遠端控制），"
                        "再重新啟動控制。")
                    self.log("已封鎖啟動：控制器不在遠端控制模式")
                    return
                if not self._confirm_real_robot_checklist():
                    return
            if not self._workspace_is_configured():
                QMessageBox.warning(
                    self,
                    "需要設定工作區邊界",
                    "虛擬工作區 XYZ 邊界尚未設定，實際移動已被鎖定。"
                    "請先在 ur10_vision_qt_app.py 設定經審核的基座座標範圍。",
                )
                self.status_banner.setText("工作區未設定 | 控制鎖定")
                self.log("已封鎖實際控制：虛擬工作區未設定")
                return
            if not self._workspace_feedback_is_fresh():
                QMessageBox.warning(
                    self,
                    "TCP 姿態不可用",
                    "在收到新的 RTDE TCP 姿態回饋之前，實際移動會維持鎖定。",
                )
                self.status_banner.setText("姿態回饋中斷 | 控制鎖定")
                self.log("已封鎖實際控制：TCP 姿態回饋不可用")
                return
            if self.workspace_fault:
                pose = self.rtde_client.actual_tcp_pose
                if pose is None or not self._workspace_contains_pose(pose):
                    QMessageBox.warning(
                        self,
                        "工作區故障未解除",
                        "TCP 目前在工作區之外。請先將 TCP 移回設定範圍內，再重新啟動控制。",
                    )
                    return
                self.workspace_fault = False
                self.workspace_fault_reason = ""
                self.log("TCP 已回到工作區內；已手動解除工作區故障")
            self.robot_enabled = True
            self._set_speed_editor_enabled(False)
            self.enable_button.setText("停止機械人控制")
            self.status_banner.setText("實際控制已啟動")
            self.log("機械人控制已啟動")
        else:
            self.emergency_stop()
        self._update_status()

    def emergency_stop(self) -> None:
        self.robot_enabled = False
        self._set_speed_editor_enabled(True)
        self.test_timer.stop()
        self.return_timer.stop()
        self.returning_to_origin = False
        self._send_stop()
        self.enable_button.setText("啟動機械人控制")
        self.status_banner.setText("已停止 | 控制關閉")
        self.log("已送出停止指令")
        self._update_status()

    def _send_stop(self) -> None:
        if self.robot is not None:
            try:
                self.robot.speedStop(2.0)
            except Exception:
                pass

    def test_ursim_motion(self) -> None:
        if (self.session_identity is None
                or self.session_identity[0] != URSIM_TARGET
                or self.rtde_client is None
                or not self.rtde_client.connected
                or not self.robot_enabled):
            QMessageBox.warning(self, "控制未啟動", "請先連接 URSim 並啟動控制。")
            return
        self.test_vector = (0.02, 0.0, 0.0)
        self.test_steps = 10
        self.test_timer.start(100)
        self.status_banner.setText("URSim 測試移動中")
        self.log("URSim 測試開始")

    def axis_test(self, dx: float, dy: float, dz: float) -> None:
        if (self.session_identity is None
                or self.rtde_client is None
                or not self.rtde_client.connected
                or not self.robot_enabled):
            QMessageBox.warning(
                self, "Control disabled",
                "Connect the robot and enable control before axis tests.")
            return
        if self.session_identity[0] != URSIM_TARGET:
            QMessageBox.warning(self, "實體機械人不可測試", "實體機械人不會自動測試或返回。")
            return
        speed = min(self._value("Max speed"), self.SAFETY_SPEED_CAP)
        self.test_vector = tuple(axis * speed for axis in (dx, dy, dz))
        self.test_steps = int(math.ceil(self.AXIS_TEST_DISTANCE / (speed * self.CONTROL_PERIOD)))
        self.test_timer.start(int(self.CONTROL_PERIOD * 1000))
        self.status_banner.setText("單軸測試移動中")
        self.log(f"單軸測試 {dx:+d}X {dy:+d}Y {dz:+d}Z："
                 f"{self.AXIS_TEST_DISTANCE * 100:.0f} cm @ {speed:.3f} m/s")

    def _test_step(self) -> None:
        if self.test_steps <= 0 or not self.robot_enabled or self.robot is None:
            self.test_timer.stop()
            self._send_stop()
            self.status_banner.setText("測試完成 | 已停止")
            return
        try:
            if not self._send_velocity(*self.test_vector):
                self.test_timer.stop()
                return
            self.test_steps -= 1
        except Exception as exc:
            self.test_timer.stop()
            self.log(f"測試移動失敗: {exc}")
            # ur-rtde may have a native reconnect worker after EOF. Disable
            # motion first and leave cleanup to an explicit Disconnect/Close.
            self.robot_enabled = False
            self.enable_button.setText("啟動機械人控制")
            self.status_banner.setText("RTDE 中斷 | 控制關閉")

    # --- Camera / vision ----------------------------------------------

    def toggle_camera(self) -> None:
        if self.camera_running:
            self.stop_camera()
        else:
            self.start_camera()

    def _camera_source_changed(self, text: str) -> None:
        self.camera_source.setVisible(text == "<Manual entry>")

    def _scan_cameras(self) -> None:
        self.scan_button.setEnabled(False)
        self.scan_button.setText("掃描中…")
        QApplication.processEvents()
        found = []
        for index in range(10):
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    found.append(str(index))
            cap.release()
        current_items = [self.camera_combo.itemText(i) for i in range(self.camera_combo.count())]
        for item in found:
            if item not in current_items:
                self.camera_combo.addItem(item)
        if found:
            self.camera_combo.setCurrentText(found[0])
            self.log(f"找到相機: {', '.join(found)}")
        else:
            self.log("找不到相機")
        self.scan_button.setEnabled(True)
        self.scan_button.setText("掃描相機")

    def _camera_value(self):
        text = self.camera_combo.currentText().strip()
        if text == "<Manual entry>" or not text:
            source = self.camera_source.text().strip()
        else:
            source = text
        try:
            return int(source)
        except ValueError:
            return source

    def start_camera(self) -> None:
        if self.hands is None:
            try:
                self.hands = self.mp_hands.Hands(
                    max_num_hands=1,
                    min_detection_confidence=self._value("Detection"),
                    min_tracking_confidence=self._value("Tracking"),
                )
            except Exception as exc:
                QMessageBox.critical(self, "視覺模型錯誤", str(exc))
                return
        cap = cv2.VideoCapture(self._camera_value())
        if not cap.isOpened():
            cap.release()
            QMessageBox.warning(self, "相機錯誤", "無法開啟相機或 RTSP 串流。")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        self.camera_running = True
        self.smooth_x = self.smooth_y = self.smooth_size = self.neutral_hand_size = None
        self.neutral_x = self.neutral_y = None
        self.calibration_active = False
        self.neutral_samples = 0
        self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
        self.lost_hand_frames = 0
        self.last_fps_time = time.monotonic()
        self.fps_frames = 0
        self.camera_button.setText("關閉相機")
        self.calibrate_button.setEnabled(True)
        self.depth_button.setEnabled(True)
        self.frame_timer.start(25)
        self.status_banner.setText("相機運行中 | 模擬模式")
        self.log("相機已開啟；張開手掌置中即可校準")

    def stop_camera(self) -> None:
        self.camera_running = False
        self.frame_timer.stop()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self._send_stop()
        self.camera_button.setText("開啟相機")
        self.calibrate_button.setEnabled(False)
        self.depth_button.setEnabled(False)
        self.video_label.clear()
        self.video_label.setText("相機未開啟")
        self.status_banner.setText("就緒 | 模擬模式")
        self._update_status()
        self.log("相機已關閉")

    def start_calibration(self) -> None:
        self.neutral_x = self.neutral_y = self.neutral_hand_size = None
        self.neutral_samples = 0
        self.neutral_x_sum = self.neutral_y_sum = self.neutral_size_sum = 0.0
        self.calibration_active = True
        self.status_banner.setText("校準中 | 請保持張開手掌不動")
        self.log("手動中立校準開始")

    def toggle_zones(self) -> None:
        self.show_zones = not self.show_zones
        label = "顯示" if self.show_zones else "隱藏"
        self.zones_button.setText(f"分區顯示：{label}")
        self.log(f"分區顯示已{label}")

    def toggle_depth_direction(self) -> None:
        self.depth_inverted = not self.depth_inverted
        label = "反轉" if self.depth_inverted else "正常"
        self.depth_button.setText(f"深度方向：{label}")
        self.log(f"深度方向設為{label}")
        self._save_settings()

    def toggle_direction_invert(self) -> None:
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
    def _axis_from_center(value: float, dead_zone: float, center: float = 0.5) -> float:
        """Return zero inside the centre zone, then scale the remainder."""
        offset = value - center
        magnitude = abs(offset)
        if magnitude <= dead_zone + 1e-6:
            return 0.0
        usable = max(0.001, min(center, 1.0 - center) - dead_zone)
        return math.copysign(min(0.5, (magnitude - dead_zone) / usable * 0.5), offset)

    @staticmethod
    def _command_name(vx: float, vy: float, vz: float) -> str:
        parts = []
        if vx < -0.0005: parts.append("LEFT")
        elif vx > 0.0005: parts.append("RIGHT")
        if vz > 0.0005: parts.append("UP")
        elif vz < -0.0005: parts.append("DOWN")
        if vy > 0.0005: parts.append("FWD")
        elif vy < -0.0005: parts.append("BACK")
        return "+".join(parts) if parts else "STOP"

    def _zone_command(self, x: float, y: float, size: float, confidence: float,
                      speed: float) -> tuple[float, float, float, str]:
        """Conservative mutually-exclusive zones: left, centre-depth, right."""
        if confidence < self.CONFIDENCE_GATE or self.neutral_x is None or self.neutral_y is None:
            return 0.0, 0.0, 0.0, "STOP"

        # X is intentionally zone-based, not continuous: this prevents a
        # slightly noisy landmark from commanding left/right unexpectedly.
        if x < self.LEFT_ZONE_END:
            vx = -speed
            return vx, 0.0, 0.0, "LEFT"
        if x > self.RIGHT_ZONE_START:
            vx = speed
            return vx, 0.0, 0.0, "RIGHT"

        # Centre column: depth (FWD/BACK) is checked first because moving
        # the hand toward the camera naturally causes the palm centre to
        # drift downward in the image. If UP/DOWN were checked first, a
        # forward push would be misread as DOWN before depth is evaluated.
        relative_depth = (size - self.neutral_hand_size) / self.neutral_hand_size
        if abs(relative_depth) > self.DEPTH_RATIO_DEAD_ZONE:
            vy = max(-speed, min(speed, relative_depth * self._value("Size sensitivity") * speed))
            if self.depth_inverted:
                vy = -vy
            return 0.0, vy, 0.0, "FWD" if vy > 0 else "BACK"

        # Palm size unchanged: only then check vertical UP/DOWN.
        vertical_offset = y - self.neutral_y
        if vertical_offset < -self.VERTICAL_ZONE_DEAD:
            return 0.0, 0.0, speed, "UP"
        if vertical_offset > self.VERTICAL_ZONE_DEAD:
            return 0.0, 0.0, -speed, "DOWN"

        return 0.0, 0.0, 0.0, "STOP"

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
        if self.pending_command_frames >= self.COMMAND_CONFIRM_FRAMES:
            self.stable_command = command
        return self.stable_command

    def _workspace_limits(self) -> tuple[Optional[float], ...]:
        return self._workspace_profile.limits

    def _workspace_is_configured(self) -> bool:
        return workspace_is_configured(self._workspace_limits())

    def _workspace_summary(self) -> str:
        if not self._workspace_is_configured():
            return "虛擬工作區未設定；實際移動維持封鎖"
        limits = self._workspace_limits()
        return (
            "虛擬工作區（基座座標 m）： "
            f"X={limits[0]:.3f}..{limits[1]:.3f}, "
            f"Y={limits[2]:.3f}..{limits[3]:.3f}, "
            f"Z={limits[4]:.3f}..{limits[5]:.3f}"
        )

    def _workspace_feedback_is_fresh(self) -> bool:
        return bool(
            self.rtde_client
            and self.rtde_client.actual_tcp_pose is not None
            and time.monotonic() - self.rtde_client.pose_received_at <= self.POSE_FEEDBACK_TIMEOUT
        )

    def _workspace_safety_status_is_acceptable(self) -> bool:
        # UR safety modes: 1=NORMAL and 2=REDUCED are the only motion-permitting modes.
        return bool(self.rtde_client and self.rtde_client.safety_status in (1, 2))

    def _trip_workspace_fault(self, reason: str) -> None:
        if self.workspace_fault:
            return
        self.workspace_fault = True
        self.workspace_fault_reason = reason
        self.robot_enabled = False
        self._set_speed_editor_enabled(True)
        self.test_timer.stop()
        self._send_stop()
        self.enable_button.setText("啟動機械人控制")
        self.status_banner.setText(f"工作區故障 | {reason}")
        self.log(f"工作區故障：{reason}")
        self._update_status()

    def _workspace_contains_pose(self, pose: tuple[float, ...]) -> bool:
        if not self._workspace_is_configured():
            return False
        limits = self._workspace_limits()
        workspace_pairs = ((limits[0], limits[1]), (limits[2], limits[3]),
                           (limits[4], limits[5]))
        return all(
            minimum is not None and maximum is not None
            and minimum - self.WALL_TOLERANCE <= coordinate <= maximum + self.WALL_TOLERANCE
            for coordinate, (minimum, maximum) in zip(pose[:3], workspace_pairs)
        )

    def _apply_workspace_wall(self, vx: float, vy: float, vz: float) -> Optional[tuple[float, float, float]]:
        if not self._workspace_is_configured():
            self._trip_workspace_fault("工作區未設定")
            return None
        if not self._workspace_feedback_is_fresh():
            self._trip_workspace_fault("TCP 姿態回饋中斷")
            return None
        if not self._workspace_safety_status_is_acceptable():
            status = self.rtde_client.safety_status if self.rtde_client else None
            self._trip_workspace_fault(f"機械人安全模式 {SAFETY_MODE_ZH.get(status, status)}")
            return None
        pose = self.rtde_client.actual_tcp_pose
        assert pose is not None
        coordinates = pose[:3]
        limits = self._workspace_limits()
        workspace_pairs = ((limits[0], limits[1]), (limits[2], limits[3]),
                           (limits[4], limits[5]))
        velocities = [vx, vy, vz]
        names = ("X", "Y", "Z")
        for index, (coordinate, (minimum, maximum)) in enumerate(zip(coordinates, workspace_pairs)):
            assert minimum is not None and maximum is not None
            if coordinate < minimum - self.WALL_TOLERANCE or coordinate > maximum + self.WALL_TOLERANCE:
                self._trip_workspace_fault(
                    f"TCP {names[index]}={coordinate:.3f} m 超出 {minimum:.3f}..{maximum:.3f} m"
                )
                return None
            if coordinate <= minimum + self.WALL_TOLERANCE and velocities[index] < 0:
                velocities[index] = 0.0
                self._start_return_to_origin()
            elif coordinate >= maximum - self.WALL_TOLERANCE and velocities[index] > 0:
                velocities[index] = 0.0
                self._start_return_to_origin()
            else:
                # Linear slowdown inside the band before the wall: full speed
                # at WALL_SLOW_ZONE distance, WALL_MIN_SCALE at the wall.
                distance_to_wall = (coordinate - minimum if velocities[index] < 0
                                    else maximum - coordinate)
                if 0.0 < distance_to_wall < self.WALL_SLOW_ZONE:
                    scale = max(self.WALL_MIN_SCALE,
                                distance_to_wall / self.WALL_SLOW_ZONE)
                    velocities[index] *= scale
        return tuple(velocities)

    def _start_return_to_origin(self) -> None:
        if self.returning_to_origin:
            return
        if self.session_identity is None or self.session_identity[0] != URSIM_TARGET:
            self._trip_workspace_fault("撞牆；實體機械人停用自動返回")
            return
        safe_origin = self._workspace_profile.safe_origin
        if safe_origin is None or not self._workspace_is_configured() or not self._workspace_contains_pose(safe_origin):
            self._trip_workspace_fault("撞牆；安全原點不在工作區內")
            return
        if not self._workspace_feedback_is_fresh() or not self._workspace_safety_status_is_acceptable():
            self._trip_workspace_fault("撞牆；姿態或安全回饋不可用")
            return
        self._send_stop()
        self.returning_to_origin = True
        self.return_started_at = time.monotonic()
        self.status_banner.setText("返回安全原點中 | URSim")
        self.log("撞到空氣牆；以低速直線返回安全原點")
        self.return_timer.start()

    def _return_step(self) -> None:
        if not self.returning_to_origin:
            self.return_timer.stop()
            return
        if (time.monotonic() - self.return_started_at > self.RETURN_TIMEOUT
                or self.rtde_client is None
                or not self.rtde_client.connected
                or self.session_identity is None
                or self.session_identity[0] != URSIM_TARGET
                or not self._workspace_feedback_is_fresh()
                or not self._workspace_safety_status_is_acceptable()):
            self.return_timer.stop()
            self.returning_to_origin = False
            self._trip_workspace_fault("返回安全原點被中止")
            return
        pose = self.rtde_client.actual_tcp_pose
        safe_origin = self._workspace_profile.safe_origin
        if pose is None or safe_origin is None or not self._workspace_contains_pose(pose):
            self.return_timer.stop()
            self.returning_to_origin = False
            self._trip_workspace_fault("返回安全原點時離開工作區")
            return
        delta = [safe_origin[i] - pose[i] for i in range(3)]
        distance = math.sqrt(sum(value * value for value in delta))
        if distance <= self.RETURN_POSITION_TOLERANCE:
            self.return_timer.stop()
            self.returning_to_origin = False
            self._send_stop()
            self.robot_enabled = False
            self.status_banner.setText("已返回原點 | 控制關閉")
            self.log("已返回安全原點；控制維持關閉")
            self._update_status()
            return
        scale = min(self.RETURN_SPEED_CAP / distance, 1.0)
        self._send_velocity(delta[0] * scale, delta[1] * scale, delta[2] * scale)

    def _send_velocity(self, vx: float, vy: float, vz: float) -> bool:
        if (self.session_identity is None
                or not self.robot_enabled
                or self.rtde_client is None
                or not self.rtde_client.connected):
            return False
        if self.returning_to_origin and self.session_identity[0] != URSIM_TARGET:
            return False
        guarded_velocity = self._apply_workspace_wall(vx, vy, vz)
        if guarded_velocity is None:
            return False
        safe_vector = cap_translational_velocity([*guarded_velocity, 0.0, 0.0, 0.0])
        if safe_vector is None:
            self._trip_workspace_fault("速度向量無效或超過 0.5 m/s")
            return False
        vx, vy, vz = safe_vector[:3]
        now = time.monotonic()
        if now - self.last_robot_update < self.CONTROL_PERIOD:
            return True
        try:
            if max(abs(vx), abs(vy), abs(vz)) < 0.0005:
                self.robot.speedStop(2.0)
            else:
                self.robot.speedL([vx, vy, vz, 0.0, 0.0, 0.0], self.ACCELERATION, self.CONTROL_PERIOD)
        except Exception as exc:
            self.log(f"RTDE 控制錯誤: {exc}")
            self.robot_enabled = False
            self.rtde_lost = True
            self.enable_button.setText("啟動機械人控制")
            self.status_banner.setText("RTDE 中斷 | 控制關閉")
            return False
        self.last_robot_update = now
        return True

    def update_frame(self) -> None:
        if self.cap is None or self.hands is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.camera_failures += 1
            if self.camera_failures >= 20:
                self.stop_camera()
                self.status_banner.setText("相機連續讀取失敗，已關閉")
            return
        self.camera_failures = 0
        now = time.monotonic()
        if now - self.last_frame_time < 1 / 15:
            return
        self.last_frame_time = now
        self.fps_frames += 1
        if now - self.last_fps_time >= 1:
            self.current_fps = self.fps_frames / (now - self.last_fps_time)
            self.fps_frames, self.last_fps_time = 0, now

        frame = cv2.flip(frame, 1)
        result = self.hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        vx = vy = vz = 0.0
        confidence = 0.0
        hand_display = "-, - | --"
        valid = False

        if result.multi_hand_landmarks:
            hand = result.multi_hand_landmarks[0]
            if result.multi_handedness:
                confidence = result.multi_handedness[0].classification[0].score
            self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)
            if self._hand_valid(hand):
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
                if self.neutral_hand_size:
                    delta = (size - self.neutral_hand_size) / self.neutral_hand_size * 100
                    hand_display = f"{x:.2f}, {y:.2f} | {delta:+.0f}%"
                else:
                    hand_display = f"{x:.2f}, {y:.2f} | 待校準"
            else:
                hand_display = "已偵測 | 請靠近並張開手掌"

        if not valid:
            self.lost_hand_frames += 1
            if self.lost_hand_frames > self.LOST_HAND_GRACE:
                self.smooth_x = self.smooth_y = self.smooth_size = None

        command = locals().get("command", "STOP")
        if not valid or confidence < self.CONFIDENCE_GATE:
            command = self._stabilize_command("STOP")
            vx = vy = vz = 0.0
        self._send_velocity(vx, vy, vz)
        self._update_status(command, hand_display, confidence)
        self._draw_overlay(frame, command, confidence)
        if self.robot_enabled or command != self._last_frame_command:
            size_delta = None
            if self.neutral_hand_size:
                size_delta = (size - self.neutral_hand_size) / self.neutral_hand_size * 100 if valid else None
            self._csv_row("frame", command=command, vx=vx, vy=vy, vz=vz,
                          hand_x=x if valid else None, hand_y=y if valid else None,
                          size_delta_pct=size_delta, confidence=confidence)
            self._last_frame_command = command
            if self.robot_enabled and int(time.monotonic()) != getattr(self, "_csv_flush_tick", 0):
                self._csv_flush_tick = int(time.monotonic())
                self._flush_csv()
        self._show_frame(frame)

    def _draw_overlay(self, frame, command: str, confidence: float) -> None:
        h, w = frame.shape[:2]
        if self.show_zones:
            left_x = int(w * self.LEFT_ZONE_END)
            right_x = int(w * self.RIGHT_ZONE_START)
            cv2.line(frame, (left_x, 0), (left_x, h), (255, 180, 0), 2)
            cv2.line(frame, (right_x, 0), (right_x, h), (255, 180, 0), 2)
            if self.neutral_y is not None:
                top_y = int(h * (self.neutral_y - self.VERTICAL_ZONE_DEAD))
                bottom_y = int(h * (self.neutral_y + self.VERTICAL_ZONE_DEAD))
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
        cv2.putText(frame, f"Confidence: {confidence * 100:.0f}%  FPS: {self.current_fps:.0f}", (18, 66),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (225, 225, 225), 1)
        size_text = f"Size neutral: {self.neutral_hand_size:.3f}" if self.neutral_hand_size else "Centre hand to calibrate size"
        cv2.putText(frame, size_text, (18, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 220, 220), 1)

    def _show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, channels = rgb.shape
        image = QImage(rgb.data, w, h, channels * w, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self.video_label.setPixmap(pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def closeEvent(self, event) -> None:
        self.frame_timer.stop()
        self.test_timer.stop()
        self._send_stop()
        self._disconnect_robot()
        if self.cap is not None:
            self.cap.release()
        if self.hands is not None:
            self.hands.close()
        self._save_settings()
        self._flush_csv()
        if self.csv_file is not None:
            try:
                self.csv_file.close()
            except Exception:
                pass
            self.csv_file = None
            self.csv_writer = None
        event.accept()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = UR10VisionQtApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
