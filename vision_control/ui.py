"""DashboardUiMixin extracted from the desktop controller."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
from pathlib import Path
from .vision import MANUAL_CAMERA
from .clock import monotonic
from .policy import Mode, fresh, FRAME_TIMEOUT
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QPlainTextEdit, QGridLayout, QSizePolicy, QSlider, QProgressBar)
from .i18n import (LocalizedLabel as QLabel, LocalizedFrame as QFrame,
    LocalizedTabWidget as QTabWidget, LocalizedComboBox as QComboBox,
    LocalizedLineEdit as QLineEdit, LocalizedButton as QPushButton)

class DashboardUiMixin:
    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName('workspace')
        self.setCentralWidget(shell)
        root = QVBoxLayout(shell)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel("UR10  /  視覺控制")
        title.setObjectName("title")
        subtitle = QLabel("手勢操作工作台")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        self.language_button = QPushButton("English")
        self.language_button.setObjectName("languageButton")
        self.language_button.clicked.connect(self.toggle_language)
        header.addWidget(self.language_button)
        self.theme_button = QPushButton("深色模式")
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setToolTip("切換深色／淺色介面")
        self.theme_button.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_button)
        self.status_banner = QLabel("就緒 | 控制關閉")
        self.status_banner.setObjectName("statusBanner")
        self.status_banner.setWordWrap(True)
        self.status_banner.setAlignment(Qt.AlignCenter)
        self.status_banner.setMaximumWidth(470)
        header.addWidget(self.status_banner)
        root.addLayout(header)
        root.addWidget(self._build_status_card())

        main = QHBoxLayout()
        main.setSpacing(16)
        main.addWidget(self._build_video_card(), 2)
        panel = QFrame()
        panel.setObjectName("inspector")
        panel.setMinimumWidth(350)
        panel.setMaximumWidth(390)
        side = QVBoxLayout(panel)
        side.setContentsMargins(18, 14, 18, 18)
        side.setSpacing(12)
        self.workflow_card = self._build_workflow_card()
        self.side_tabs = QTabWidget()
        self.side_tabs.setObjectName("sideTabs")
        operations = QWidget()
        operations_layout = QVBoxLayout(operations)
        operations_layout.setContentsMargins(0, 0, 0, 0)
        operations_layout.setSpacing(10)
        operations_layout.addWidget(self._build_robot_card())
        operations_layout.addWidget(self._build_camera_card())
        self.side_tabs.addTab(self._wrap_in_tab(operations), "操作")
        self.side_tabs.addTab(self._wrap_in_tab(self._build_axis_card()), "測試")
        self.side_tabs.addTab(self._wrap_in_tab(self._build_tuning_card()), "設定")
        self.side_tabs.addTab(self._build_log_card(), "日誌")
        self.side_tabs.currentChanged.connect(lambda index: self.workflow_card.setVisible(index == 0))
        side.addWidget(self.side_tabs, 1)
        side.addWidget(self.workflow_card)
        self.enable_button = QPushButton("啟動機械人控制")
        self.enable_button.setMinimumHeight(46)
        self.enable_button.setObjectName("enableButton")
        self.enable_button.clicked.connect(self.toggle_robot_control)
        side.addWidget(self.enable_button)
        main.addWidget(panel, 1)
        root.addLayout(main, 1)
        root.addWidget(self._build_telemetry_card())

        stop_row = QHBoxLayout()
        stop_row.setSpacing(16)
        footer = QLabel("張開手掌移動  ·  握拳／沒有手＝停止")
        footer.setObjectName("footer")
        footer.setWordWrap(True)
        stop_row.addWidget(footer, 1)
        self.stop_button = QPushButton("停止控制   ·   Esc")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setFixedSize(260, 46)
        self.stop_button.clicked.connect(self.emergency_stop)
        stop_row.addWidget(self.stop_button)
        root.addLayout(stop_row)
        self.tuners['Max speed'][0].valueChanged.connect(lambda _value: self._refresh_workflow())
        self.stop_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.stop_shortcut.activated.connect(self.emergency_stop)

    def _wrap_in_tab(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 12, 4, 4)
        layout.addWidget(widget)
        layout.addStretch()
        scroll.setWidget(container)
        return scroll

    def _build_video_card(self) -> QFrame:
        card = self._card()
        card.setObjectName("videoCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(self._section_label("即時影像"))
        heading.addStretch()
        self.direction_badge = QLabel("■  停止")
        self.direction_badge.setObjectName("directionBadge")
        self.direction_badge.setAlignment(Qt.AlignCenter)
        self.direction_badge.setToolTip("影像辨識到的手勢方向；實際控制指令請看頂部狀態。")
        heading.addWidget(self.direction_badge)
        layout.addLayout(heading)
        self.video_label = QLabel("尚未開啟相機\n\n在右側選擇來源，開啟即時預覽")
        self.video_label.setObjectName("video")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(480, 160)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.video_label, 1)
        calibration_row = QHBoxLayout()
        self.calibration_label = QLabel("中立姿態 · 未校準")
        self.calibration_label.setObjectName("hint")
        calibration_row.addWidget(self.calibration_label, 1)
        self.calibration_progress = QProgressBar()
        self.calibration_progress.setRange(0, 8)
        self.calibration_progress.setValue(0)
        self.calibration_progress.setTextVisible(False)
        self.calibration_progress.setFixedSize(96, 4)
        calibration_row.addWidget(self.calibration_progress)
        self.calibrate_button = QPushButton("校準姿態")
        self.calibrate_button.setObjectName("calibrateButton")
        self.calibrate_button.setMinimumHeight(32)
        self.calibrate_button.setEnabled(False)
        self.calibrate_button.setToolTip("張開手掌保持穩定，校準中立姿態。")
        self.calibrate_button.clicked.connect(self.start_calibration)
        calibration_row.addWidget(self.calibrate_button)
        layout.addLayout(calibration_row)
        details = QGridLayout()
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(4)
        for col, (key, title) in enumerate((("Hand", "手掌位置 / 大小"),
                                          ("FPS", "影像 FPS"),
                                          ("Confidence", "左右手分類"))):
            label = QLabel(title)
            label.setObjectName("statusKey")
            value = QLabel("--")
            value.setObjectName("videoDetail")
            value.setWordWrap(True)
            details.addWidget(label, 0, col)
            details.addWidget(value, 1, col)
            self.status_values[key] = value
        details.setColumnStretch(0, 2)
        details.setColumnStretch(1, 1)
        details.setColumnStretch(2, 1)
        layout.addLayout(details)
        self.confidence_bar = QLabel("左右手分類：--")
        self.confidence_bar.setObjectName("hint")
        self.confidence_bar.setToolTip("MediaPipe 左右手分類分數，並非手掌偵測品質或安全評分。")
        layout.addWidget(self.confidence_bar)
        return card

    def _build_workflow_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("workflowCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        self.workflow_title = QLabel("連接機械人")
        self.workflow_title.setObjectName("workflowTitle")
        layout.addWidget(self.workflow_title)
        return card

    def _refresh_workflow(self) -> None:
        connected = bool(self.rtde_client and self.rtde_client.connected)
        ready = connected and self.rtde_client.control_ready
        live = self.camera_running and fresh(monotonic(), self.frame_acquired_at, FRAME_TIMEOUT)
        calibrated = self.camera_running and self.neutral_hand_size is not None
        done = (ready, live, calibrated, self.mode == Mode.GESTURE)
        if self.mode == Mode.FAULT:
            current = 3
            hint = "控制已鎖定：請檢查狀態及日誌，處理原因後重新啟動。"
        elif self.mode in (Mode.AXIS_TEST, Mode.RETURN):
            current = None
            hint = "URSim 自動移動中；完成後控制關閉，可隨時按 STOP。"
        elif self.mode == Mode.READY:
            current = next((i for i, value in enumerate(done[:3]) if not value), 3)
            hint = "URSim 測試已待命；手勢操作須先開相機、校準，再重新啟動控制。"
        else:
            current = next((i for i, value in enumerate(done) if not value), None)
            hint = (
                "下一步：連接機械人，等待控制介面就緒。",
                "下一步：開啟相機，等待即時影像。",
                "下一步：張開手掌保持穩定，按預覽下方「校準姿態」。",
                "下一步：確認工作區及速度，按「啟動機械人控制」。",
            )[current] if current is not None else "手勢控制已啟動；握拳或移開手掌會停止。"
        if self.mode != Mode.FAULT:
            if connected and not ready:
                hint = "等待控制介面及 watchdog 就緒；請留意連線狀態。"
            if self.calibration_active:
                hint = "校準中：請保持張開手掌不動，累積 8 個有效樣本。"
            if current == 3 and not self._workspace_is_configured():
                hint = "工作區設定無效，啟動保持鎖定；詳情見下方狀態。"
        title = ("連接機械人", "等待相機畫面", "校準中立姿態", "準備啟動")[current] if current is not None else "手勢控制中"
        state = "idle"
        if self.calibration_active:
            title = "正在校準姿態"
        if self.mode == Mode.GESTURE:
            state = "live"
        elif self.mode == Mode.AXIS_TEST:
            title, state = "單軸測試中", "warn"
        elif self.mode == Mode.RETURN:
            title, state = "返回原點中", "warn"
        elif self.mode == Mode.FAULT:
            title, state = "控制已鎖定", "error"
        self.workflow_title.setText('下一步 · ' + title if self.mode == Mode.DISABLED else title)
        self.workflow_title.setToolTip(hint)
        if self.workflow_card.property("state") != state:
            self.workflow_card.setProperty("state", state)
            self._repolish(self.workflow_card)
        samples = 8 if calibrated else min(8, self.neutral_samples) if self.calibration_active else 0
        self.calibration_progress.setValue(samples)
        self.calibration_label.setText("中立姿態 · 已校準" if calibrated else
            f"中立姿態 · 校準中 {samples}/8" if self.calibration_active else "中立姿態 · 未校準")
        self.speed_value.setText(f"{self._value('Max speed'):.2f}")
        if self.enable_button.property('active') != self.robot_enabled:
            self.enable_button.setProperty('active', self.robot_enabled)
            self._repolish(self.enable_button)
        self.confirm_speed_button.setVisible(self._target_id() == "實體 UR10")

    def _build_log_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 14, 8, 8)
        layout.addWidget(self._section_label("事件日誌"))
        hint = QLabel("連線、模式切換、STOP 及錯誤會記錄於此。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.event_log = QPlainTextEdit()
        self.event_log.setObjectName("eventLog")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(200)
        layout.addWidget(self.event_log, 1)
        return card

    def _card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card


    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label


    def _build_camera_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("deviceGroup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("相機來源"))
        self.camera_combo = QComboBox()
        self.camera_combo.addItem("0", "0")
        self.camera_combo.addItem("<手動輸入>", MANUAL_CAMERA)
        self.camera_combo.currentTextChanged.connect(self._camera_source_changed)
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(self.camera_combo, 1)
        self.scan_button = QPushButton("掃描")
        self.scan_button.setMinimumHeight(28)
        self.scan_button.clicked.connect(self._scan_cameras)
        source_row.addWidget(self.scan_button)
        layout.addLayout(source_row)
        self.camera_source = QLineEdit("0")
        self.camera_source.setPlaceholderText("0 或 RTSP URL")
        self.camera_source.setVisible(False)
        layout.addWidget(self.camera_source)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.camera_button = QPushButton("開啟相機")
        self.camera_button.setMinimumHeight(28)
        self.camera_button.clicked.connect(self.toggle_camera)
        actions.addWidget(self.camera_button, 1)
        layout.addLayout(actions)
        return card


    def _build_robot_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("deviceGroup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("機械人連線"))

        self.target_combo = QComboBox()
        self.target_combo.addItem("URSim", "URSim")
        self.target_combo.addItem("實體 UR10", "實體 UR10")
        self.target_combo.currentIndexChanged.connect(lambda _index: self._target_changed(self._target_id()))
        fields = QHBoxLayout()
        fields.setSpacing(8)
        fields.addWidget(self.target_combo, 1)

        self.robot_ip = QLineEdit("127.0.0.1")
        self.robot_ip.setPlaceholderText("UR 控制器 IP")
        fields.addWidget(self.robot_ip, 1)
        layout.addLayout(fields)

        self.connect_button = QPushButton("連接機械人")
        self.connect_button.setMinimumHeight(30)
        self.connect_button.clicked.connect(self.connect_robot)
        layout.addWidget(self.connect_button)

        return card


    def _build_axis_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(self._section_label("單軸測試"))
        hint = QLabel(f"只限 URSim：目標距離 {self.AXIS_TEST_DISTANCE * 100:.0f} cm，以 TCP 回饋判斷完成。測試完成後控制關閉。")
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
        self.test_button = QPushButton("短距離測試（2 cm）")
        self.test_button.setMinimumHeight(28)
        self.test_button.clicked.connect(self.test_ursim_motion)
        self.test_button.setEnabled(False)
        layout.addWidget(self.test_button)
        layout.addStretch()
        return card


    def _build_status_card(self) -> QWidget:
        card = QWidget()
        row = QHBoxLayout(card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        card.setObjectName("summaryStrip")
        self.status_values = {}
        for key, title in (("Robot", "連線目標"), ("Control", "控制模式"),
                           ("Speed", "設定速度 · m/s"), ("Command", "控制指令")):
            tile = QFrame()
            tile.setObjectName("tile")
            layout = QVBoxLayout(tile)
            layout.setContentsMargins(18, 12, 18, 12)
            layout.setSpacing(4)
            label = QLabel(title)
            label.setObjectName("statusKey")
            value = QLabel("--")
            value.setObjectName("statusValue")
            layout.addWidget(label)
            layout.addWidget(value)
            row.addWidget(tile, 1)
            if key == "Speed":
                self.speed_value = value
                tile.setToolTip("操作者設定的速度上限，並非實測 TCP 速度。")
            else:
                self.status_values[key] = value
        self.status_values["Command"].setToolTip("控制 worker 最近回報的已接受指令，並非機械人實測運動。")
        return card

    def _build_telemetry_card(self) -> QFrame:
        card = QFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.tcp_label = QLabel("TCP: --")
        self.tcp_label.setObjectName("telemetry")
        row.addWidget(self.tcp_label, 2)
        self.safety_label = QLabel("安全模式: --")
        self.safety_label.setObjectName("telemetry")
        row.addWidget(self.safety_label, 1)
        self.csv_indicator = QLabel("○ CSV 未啟用")
        self.csv_indicator.setObjectName("telemetry")
        row.addWidget(self.csv_indicator, 2)
        layout.addLayout(row)
        self.workspace_status = QLabel("工作區：未設定 | 控制鎖定")
        self.workspace_status.setObjectName("workspaceStatus")
        self.workspace_status.setProperty("state", "warn")
        self.workspace_status.setWordWrap(True)
        layout.addWidget(self.workspace_status)
        return card

    def _build_tuning_card(self) -> QFrame:
        card = QFrame()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._section_label("調校"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)
        self.tuners = {}
        entries = (
            ("Max speed", "速度上限 · m/s", 1, 50, 2, 2, 100.0),
            ("Dead zone", "中立死區", 3, 30, 10, 1, 100.0),
            ("Smoothing", "位置平滑度", 5, 80, 25, 1, 100.0),
            ("Size sensitivity", "手掌大小敏感度", 10, 80, 30, 1, 10.0),
            ("Detection", "偵測門檻", 50, 95, 80, 1, 100.0),
            ("Tracking", "追蹤門檻", 30, 95, 60, 1, 100.0),
        )
        for index, entry in enumerate(entries):
            box = QVBoxLayout()
            self._add_tuner(box, *entry)
            grid.addLayout(box, index // 2, index % 2)
        self.tuners["Max speed"][0].valueChanged.connect(self._max_speed_changed)
        layout.addLayout(grid)
        self.apply_vision_button = QPushButton('套用偵測／追蹤設定')
        self.apply_vision_button.clicked.connect(self.apply_vision_settings)
        layout.addWidget(self.apply_vision_button)
        self.confirm_speed_button = QPushButton('確認目前實體速度')
        self.confirm_speed_button.clicked.connect(self.confirm_physical_speed)
        layout.addWidget(self.confirm_speed_button)
        hint = QLabel("套用偵測／追蹤設定會停止控制、重啟相機，並需要重新校準。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.zones_button = QPushButton("分區顯示：隱藏")
        self.zones_button.setMinimumHeight(28)
        self.zones_button.clicked.connect(self.toggle_zones)
        layout.addWidget(self.zones_button)
        self.depth_button = QPushButton("深度方向：正常")
        self.depth_button.setMinimumHeight(28)
        self.depth_button.setEnabled(False)
        self.depth_button.clicked.connect(self.toggle_depth_direction)
        layout.addWidget(self.depth_button)
        self.invert_button = QPushButton("移動方向：正常")
        self.invert_button.setMinimumHeight(28)
        self.invert_button.clicked.connect(self.toggle_direction_invert)
        layout.addWidget(self.invert_button)

        return card


    def _add_tuner(self, row: QVBoxLayout, name: str, label_zh: str, minimum: int, maximum: int,
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
        row.addLayout(box)
        self.tuners[name] = (slider, value_label, scale)


    def _apply_style(self) -> None:
        light_style = """
            QMainWindow { background: #f3f5f8; }
            QWidget { background: transparent; color: #203047;
                font-family: 'PingFang TC', 'Microsoft JhengHei', Arial; font-size: 13px; }
            QWidget#workspace { background: #f3f5f8; }
            QLabel#title { font-size: 23px; font-weight: 700; color: #17263b; }
            QLabel#subtitle, QLabel#footer { font-size: 12px; color: #778396; }
            QLabel#section { font-size: 13px; font-weight: 600; color: #35465d; }
            QLabel#hint { color: #778396; font-size: 12px; }
            QWidget#summaryStrip { background: #ffffff; border: 1px solid #e2e7ee; border-radius: 8px; }
            QFrame#tile { border: 0; border-right: 1px solid #e9edf2; }
            QLabel#statusKey { color: #8090a3; font-size: 11px; }
            QLabel#statusValue { font-size: 19px; font-weight: 600; color: #1d2c42; }
            QLabel#statusBanner { background: #e9eef5; color: #677891; border: 0;
                border-radius: 5px; padding: 8px 12px; font-size: 12px; }
            QLabel#statusBanner[state="live"] { background: #e3f4eb; color: #238256; }
            QLabel#statusBanner[state="warn"] { background: #fff0d4; color: #9c6c10; }
            QLabel#statusBanner[state="error"] { background: #ffeaee; color: #b33750; }
            QFrame#videoCard { background: #101720; border: 0; border-radius: 8px; }
            QFrame#videoCard QLabel { color: #9ba9bd; }
            QFrame#videoCard QLabel#section { color: #d9e1ed; font-size: 12px; }
            QFrame#videoCard QLabel#video { color: #6e7e94; font-size: 16px; border: 0; }
            QFrame#videoCard QLabel#directionBadge { color: #edf2f9; }
            QLabel#videoDetail { font-family: Menlo, 'PingFang TC'; font-size: 12px; }
            QLabel#directionBadge { border-radius: 4px; padding: 5px 10px; font-size: 14px; font-weight: 600; }
            QPushButton#calibrateButton { background: #1c293b; border-color: #34445c; color: #cfdef2;
                padding: 5px 12px; font-weight: 500; }
            QPushButton#calibrateButton:hover { background: #293d57; }
            QPushButton#calibrateButton:disabled { background: #172131; border-color: #273347; color: #62748d; }
            QFrame#inspector { background: #ffffff; border: 1px solid #e2e7ee; border-radius: 8px; }
            QFrame#deviceGroup { border: 0; border-bottom: 1px solid #e9edf3; }
            QFrame#card { border: 0; }
            QFrame#workflowCard { background: transparent; border: 0; }
            QFrame#workflowCard[state="live"] { background: #eef8f3; }
            QFrame#workflowCard[state="error"] { background: #fff0f2; }
            QLabel#workflowTitle { color: #405673; font-size: 12px; font-weight: 600; }
            QLabel#workflowHint { color: #8290a3; font-size: 11px; }
            QLineEdit, QComboBox { background: #f8fafc; border: 1px solid #dce3ed;
                border-radius: 5px; padding: 8px 10px; min-height: 18px; color: #30445f; }
            QLineEdit:focus, QComboBox:focus { border-color: #6d9eef; background: white; }
            QLineEdit:disabled, QComboBox:disabled { background: #f2f4f7; color: #9da9ba; border-color: #e6eaf0; }
            QComboBox::drop-down { border: 0; width: 24px; }
            QComboBox::down-arrow { image: url("@CHEVRON@"); width: 12px; height: 12px; }
            QComboBox QAbstractItemView { background: #ffffff; color: #30445f;
                selection-background-color: #e8f0ff; selection-color: #2461c2; border: 1px solid #dce3ed; }
            QPushButton { background: #ffffff; color: #405673; border: 1px solid #d6deea;
                border-radius: 5px; padding: 8px 12px; font-weight: 500; }
            QPushButton:hover { background: #f1f5fc; border-color: #9bb7df; }
            QPushButton:pressed { background: #e5edf9; }
            QPushButton:disabled { color: #a8b4c5; background: #f6f8fb; border-color: #e8edf4; }
            QPushButton#enableButton { background: #326ddd; color: white; border-color: #326ddd;
                font-size: 14px; font-weight: 600; padding: 12px; }
            QPushButton#enableButton:hover { background: #245eca; }
            QPushButton#enableButton[active="true"] { background: #fff2f4; color: #b53750; border-color: #e8b1bd; }
            QPushButton#themeButton { background: #e9eef5; border-color: #e9eef5; color: #405673;
                font-size: 12px; padding: 7px 12px; }
            QPushButton#themeButton:hover { background: #dce7f6; }
            QPushButton#stopButton { background: #c33b4d; color: white; border-color: #c33b4d;
                font-size: 15px; font-weight: 600; }
            QPushButton#stopButton:hover { background: #ad3041; }
            QPushButton#axisButton { font-size: 16px; font-weight: 600; padding: 12px; }
            QTabWidget#sideTabs::pane { border: 0; }
            QTabBar::tab { background: transparent; color: #8593a6; padding: 10px 13px;
                border-bottom: 2px solid #edf0f5; font-weight: 500; }
            QTabBar::tab:selected { color: #2866d0; border-bottom-color: #326ddd; }
            QTabBar::tab:hover { color: #2866d0; }
            QLabel#telemetry { color: #778396; font-size: 11px; font-family: Menlo, 'PingFang TC'; }
            QLabel#workspaceStatus { color: #778396; font-size: 11px; padding: 0; }
            QLabel#workspaceStatus[state="ok"] { color: #238256; }
            QLabel#workspaceStatus[state="warn"] { color: #8c7a58; }
            QLabel#workspaceStatus[state="error"] { color: #b33750; }
            QLabel#tunerLabel { color: #778396; font-size: 11px; }
            QLabel#tunerValue { color: #344a68; font-size: 16px; font-weight: 600; }
            QSlider::groove:horizontal { background: #e2e8f2; height: 4px; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #749de8; border-radius: 2px; }
            QSlider::handle:horizontal { background: #326ddd; width: 12px; margin: -5px 0; border-radius: 6px; }
            QSlider::handle:horizontal:disabled { background: #b0bdd1; }
            QProgressBar { background: #26344a; border: 0; border-radius: 2px; }
            QProgressBar::chunk { background: #7baaf7; border-radius: 2px; }
            QPlainTextEdit#eventLog { background: #f8fafc; color: #667891; border: 1px solid #e2e7ee;
                border-radius: 5px; font-family: Menlo, 'PingFang TC'; font-size: 11px; padding: 8px; }
            QScrollBar:vertical { background: transparent; width: 5px; margin: 0; }
            QScrollBar::handle:vertical { background: #ccd5e3; border-radius: 2px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QToolTip { background: #30445f; color: white; border: 0; padding: 6px; }
        """.replace('@CHEVRON@', (Path(__file__).parent / 'assets' / 'chevron-down.svg').as_posix())
        dark_style = """
            QMainWindow, QWidget#workspace { background: #10151c; }
            QWidget { color: #e3eaf4; }
            QLabel#title { color: #f1f5fb; }
            QLabel#subtitle, QLabel#footer, QLabel#hint { color: #a7b5c8; }
            QLabel#section { color: #c4d1e3; }
            QWidget#summaryStrip, QFrame#inspector { background: #1b2430;
                border-color: #334154; }
            QFrame#tile { border-right-color: #334154; }
            QLabel#statusKey { color: #a7b5c8; }
            QLabel#statusValue { color: #f1f5fb; }
            QLabel#statusBanner { background: #273449; color: #d0def0; }
            QLabel#statusBanner[state="live"] { background: #173d2c; color: #91e7b6; }
            QLabel#statusBanner[state="warn"] { background: #483920; color: #f3d080; }
            QLabel#statusBanner[state="error"] { background: #472631; color: #ffb4c3; }
            QFrame#deviceGroup { border-bottom-color: #334154; }
            QFrame#workflowCard[state="live"] { background: #173d2c; }
            QFrame#workflowCard[state="error"] { background: #472631; }
            QLabel#workflowTitle { color: #b7cae3; }
            QLineEdit, QComboBox { background: #202c3c; border-color: #40526a;
                color: #eef4fc; }
            QLineEdit:focus, QComboBox:focus { background: #26364b; border-color: #82b1ff; }
            QLineEdit:disabled, QComboBox:disabled { background: #1c2633;
                border-color: #303e50; color: #8e9fb6; }
            QComboBox QAbstractItemView { background: #202c3c; color: #eef4fc;
                border-color: #40526a; selection-background-color: #345887;
                selection-color: white; }
            QPushButton { background: #27364a; color: #e6effb; border-color: #455b76; }
            QPushButton:hover { background: #344a64; border-color: #7192bd; }
            QPushButton:pressed { background: #223246; }
            QPushButton:disabled { background: #202b39; color: #9aaec8; border-color: #35455a; }
            QPushButton#enableButton { background: #3979e9; border-color: #3979e9; color: white; }
            QPushButton#enableButton:hover { background: #4c88f1; }
            QPushButton#enableButton[active="true"] { background: #4a2630;
                color: #ffc1cc; border-color: #98515f; }
            QPushButton#themeButton { background: #27364a; border-color: #455b76;
                color: #d6e3f4; }
            QPushButton#themeButton:hover { background: #344a64; }
            QPushButton#stopButton { background: #be4251; color: white; border-color: #be4251; }
            QPushButton#stopButton:hover { background: #d34a5b; }
            QTabBar::tab { color: #a6b6ca; border-bottom-color: #334154; }
            QTabBar::tab:selected { color: #98c2ff; border-bottom-color: #78a9f7; }
            QTabBar::tab:hover { color: #c4dfff; }
            QLabel#telemetry, QLabel#tunerLabel { color: #a7b5c8; }
            QLabel#workspaceStatus { color: #a7b5c8; }
            QLabel#workspaceStatus[state="ok"] { color: #91e7b6; }
            QLabel#workspaceStatus[state="warn"] { color: #f3d080; }
            QLabel#workspaceStatus[state="error"] { color: #ffb4c3; }
            QLabel#tunerValue { color: #e6effb; }
            QSlider::groove:horizontal { background: #40516a; }
            QSlider::sub-page:horizontal { background: #75a8f9; }
            QSlider::handle:horizontal { background: #a7cbff; }
            QSlider::handle:horizontal:disabled { background: #6c809b; }
            QPlainTextEdit#eventLog { background: #17212e; color: #c0cee1;
                border-color: #334154; }
            QScrollBar::handle:vertical { background: #596d86; }
            QToolTip { background: #334a67; color: white; }
        """
        self.setStyleSheet(light_style + (dark_style if self.theme == 'dark' else ''))

    def toggle_theme(self) -> None:
        self.theme = 'light' if self.theme == 'dark' else 'dark'
        self.theme_button.setText('淺色模式' if self.theme == 'dark' else '深色模式')
        self._apply_style()
        self._save_settings()

    def _retranslate_ui(self) -> None:
        QApplication.instance().setProperty('ui_language', self.language)
        for widget in self.findChildren(QWidget):
            retranslate = getattr(widget, 'retranslate', None)
            if retranslate is not None:
                retranslate()
        self.language_button.setText('廣東話' if self.language == 'en' else 'English')
        if hasattr(self, '_render_event_log'):
            self._render_event_log()
        self._update_status()

    def toggle_language(self) -> None:
        self.language = 'en' if self.language == 'yue' else 'yue'
        self._retranslate_ui()
        self._save_settings()
