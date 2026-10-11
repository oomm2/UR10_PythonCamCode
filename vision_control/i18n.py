"""Display-only English/Cantonese translations for the controller UI.

Robot target IDs, command names, settings keys and CSV data stay unchanged.
"""

from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
    QLabel, QLineEdit, QMessageBox as QtMessageBox, QPushButton, QTabWidget)

LANGUAGES = ("yue", "en")

ENGLISH = {
    # Window chrome and controls.
    "UR10  /  視覺控制": "UR10 / Vision Control",
    "手勢操作工作台": "Gesture control workspace",
    "深色模式": "Dark mode", "淺色模式": "Light mode",
    "切換深色／淺色介面": "Switch between dark and light themes",
    "就緒 | 控制關閉": "Ready | Control off",
    "操作": "Operate", "測試": "Test", "設定": "Settings", "日誌": "Log",
    "啟動機械人控制": "Enable robot control",
    "停止機械人控制": "Disable robot control",
    "張開手掌移動  ·  握拳／沒有手＝停止": "Open palm to move · Closed or missing hand = stop",
    "停止控制   ·   Esc": "Stop control   ·   Esc",
    "即時影像": "Live camera", "■  停止": "■  Stop",
    "影像辨識到的手勢方向；實際控制指令請看頂部狀態。":
        "Gesture direction detected in the camera; see the top bar for the control command.",
    "尚未開啟相機\n\n在右側選擇來源，開啟即時預覽":
        "Camera is off\n\nChoose a source on the right to open the live preview",
    "相機未開啟": "Camera is off",
    "中立姿態 · 未校準": "Neutral pose · Not calibrated",
    "中立姿態 · 已校準": "Neutral pose · Calibrated",
    "中立姿態 · 校準中 ": "Neutral pose · Calibrating ",
    "校準姿態": "Calibrate pose",
    "張開手掌保持穩定，校準中立姿態。": "Hold an open palm steady to calibrate neutral pose.",
    "手掌位置 / 大小": "Palm position / size",
    "影像 FPS": "Camera FPS", "左右手分類": "Handedness",
    "左右手分類：--": "Handedness: --",
    "MediaPipe 左右手分類分數，並非手掌偵測品質或安全評分。":
        "MediaPipe handedness score, not hand-detection quality or a safety score.",
    "左右手分類分數僅供參考": "Handedness score is for reference only",
    "相機來源": "Camera source", "<手動輸入>": "<Manual entry>",
    "0 或 RTSP URL": "0 or RTSP URL", "掃描": "Scan", "掃描中…": "Scanning…",
    "開啟相機": "Open camera", "關閉相機": "Close camera",
    "機械人連線": "Robot connection", "實體 UR10": "Physical UR10",
    "UR 控制器 IP": "UR controller IP", "連接機械人": "Connect robot",
    "取消連接": "Cancel connection", "中斷連線": "Disconnect",
    "單軸測試": "Axis test", "短距離測試（2 cm）": "Short test (2 cm)",
    "只限 URSim：目標距離 ": "URSim only: target distance ",
    " cm，以 TCP 回饋判斷完成。測試完成後控制關閉。":
        " cm. TCP feedback determines completion. Control turns off after the test.",
    "連線目標": "Target", "控制模式": "Control mode",
    "設定速度 · m/s": "Set speed · m/s", "控制指令": "Control command",
    "操作者設定的速度上限，並非實測 TCP 速度。":
        "Operator-set speed limit, not measured TCP speed.",
    "控制 worker 最近回報的已接受指令，並非機械人實測運動。":
        "Latest command accepted by the control worker, not measured robot motion.",
    "安全模式: ": "Safety mode: ", "安全模式: --": "Safety mode: --",
    "○ CSV 未啟用": "○ CSV off", "● CSV 錄製中：": "● CSV recording: ",
    "工作區：未設定 | 控制鎖定": "Workspace: unset | Control locked",
    "調校": "Tuning", "速度上限 · m/s": "Speed limit · m/s",
    "中立死區": "Neutral dead zone", "位置平滑度": "Position smoothing",
    "手掌大小敏感度": "Palm-size sensitivity",
    "偵測門檻": "Detection threshold", "追蹤門檻": "Tracking threshold",
    "套用偵測／追蹤設定": "Apply detection / tracking settings",
    "確認目前實體速度": "Confirm physical speed",
    "套用偵測／追蹤設定會停止控制、重啟相機，並需要重新校準。":
        "Applying detection / tracking settings stops control, restarts the camera, and requires recalibration.",
    "分區顯示：隱藏": "Zones: hidden", "分區顯示：顯示": "Zones: visible",
    "分區顯示：": "Zones: ",
    "深度方向：正常": "Depth direction: normal", "深度方向：反轉": "Depth direction: reversed",
    "深度方向：": "Depth direction: ",
    "移動方向：正常": "Move direction: normal", "移動方向：反轉": "Move direction: reversed",
    "移動方向：": "Move direction: ",
    "事件日誌": "Event log",
    "連線、模式切換、STOP 及錯誤會記錄於此。":
        "Connections, mode changes, STOP requests, and errors appear here.",
    # Workflow and status.
    "控制已鎖定：請檢查狀態及日誌，處理原因後重新啟動。":
        "Control locked: check status and log, resolve the cause, then enable again.",
    "URSim 自動移動中；完成後控制關閉，可隨時按 STOP。":
        "URSim is moving automatically; control turns off afterward. STOP is always available.",
    "URSim 測試已待命；手勢操作須先開相機、校準，再重新啟動控制。":
        "URSim test ready. For gestures, open the camera, calibrate, then enable control again.",
    "下一步：連接機械人，等待控制介面就緒。":
        "Next: connect the robot and wait for the control interface.",
    "下一步：開啟相機，等待即時影像。": "Next: open the camera and wait for live video.",
    "下一步：張開手掌保持穩定，按預覽下方「校準姿態」。":
        "Next: hold an open palm steady and choose Calibrate pose below the preview.",
    "下一步：確認工作區及速度，按「啟動機械人控制」。":
        "Next: confirm workspace and speed, then choose Enable robot control.",
    "手勢控制已啟動；握拳或移開手掌會停止。":
        "Gesture control is active; close or remove your hand to stop.",
    "等待控制介面及 watchdog 就緒；請留意連線狀態。":
        "Waiting for the control interface and watchdog; check connection status.",
    "校準中：請保持張開手掌不動，累積 8 個有效樣本。":
        "Calibrating: keep an open palm steady for 8 valid samples.",
    "工作區設定無效，啟動保持鎖定；詳情見下方狀態。":
        "Workspace settings are invalid; enabling stays locked. See status below.",
    "等待相機畫面": "Waiting for camera", "校準中立姿態": "Calibrate neutral pose",
    "準備啟動": "Ready to enable", "手勢控制中": "Gesture control active",
    "正在校準姿態": "Calibrating pose", "單軸測試中": "Axis test running",
    "返回原點中": "Returning to origin", "控制已鎖定": "Control locked",
    "下一步 · ": "Next · ",
    "已連接": "Connected", "未連接": "Disconnected",
    "關閉": "Off", "待命": "Ready", "手勢": "Gesture", "返回原點": "Return to origin",
    "故障鎖定": "Fault locked", "停止待確認": "STOP pending",
    "工作區故障：": "Workspace fault: ",
    "工作區：返回安全原點中": "Workspace: returning to safe origin",
    "工作區：已設定 | 機械人未連接": "Workspace: set | Robot disconnected",
    "工作區：等待姿態回饋": "Workspace: waiting for pose feedback",
    "工作區：就緒": "Workspace: ready",
    "正常": "Normal", "降速": "Reduced", "保護停止": "Protective stop",
    "恢復中": "Recovery", "安全裝置停止": "Safeguard stop",
    "系統急停": "System emergency stop", "機械人急停": "Robot emergency stop",
    "違規": "Violation", "關節故障": "Joint fault",
    "停止": "Stop", "左移": "Left", "右移": "Right", "上升": "Up",
    "下降": "Down", "前進": "Forward", "後退": "Back",
    "待校準": "Not calibrated", "已偵測 | 請靠近並張開手掌": "Detected | Move closer and open your palm",
    # Dialogs and safety checklist.
    "實體 UR10 安全檢查清單": "Physical UR10 safety checklist",
    "啟動實體機械人移動前，請逐項確認以下安全事項：":
        "Before moving the physical robot, confirm every item:",
    "實體工作區已清空，機械人活動範圍內沒有人。":
        "The physical workspace is clear and no one is within robot reach.",
    "實體緊急停止按鈕就在手邊，並已測試有效。":
        "The physical emergency stop is accessible and has been tested.",
    "控制器上已設定安全平面／保護性停止。":
        "Safety planes and protective stops are configured on the controller.",
    "速度設定已在控制關閉時由操作者明確選擇；0.5 m/s 僅為軟體可設定上限，不代表安全或已驗證。":
        "The operator selected speed with control off; 0.5 m/s is only a software limit, not a validated safe speed.",
    "PolyScope 狀態列顯示 Remote（遠端控制已啟用）。":
        "PolyScope shows Remote control enabled.",
    "全部確認": "Confirm all", "取消": "Cancel",
    "機械人未連接": "Robot disconnected", "請先連接 URSim 或 UR10。": "Connect URSim or UR10 first.",
    "啟動機械人移動": "Enable robot motion",
    "請先確認機械人工作區已清空，才繼續。確定啟動？":
        "Confirm the robot workspace is clear before continuing. Enable motion?",
    "連線目標已變更": "Connection target changed",
    "連線期間禁止變更控制目標。": "The target cannot change during a connection session.",
    "控制介面未就緒": "Control interface not ready",
    "請等待控制介面及 watchdog 就緒；失敗時請重新連線。":
        "Wait for the control interface and watchdog; reconnect if setup fails.",
    "工作區設定無效": "Invalid workspace settings",
    "回饋不可用": "Feedback unavailable",
    "請確認新鮮的 TCP 姿態及正常安全模式。":
        "Confirm fresh TCP pose feedback and normal safety mode.",
    "TCP 超出範圍": "TCP out of bounds",
    "請先將 TCP 移回有效工作區內。": "Move the TCP back inside the valid workspace first.",
    "需要確認實體速度": "Physical speed confirmation required",
    "請在控制保持關閉時，明確設定實體機械人的速度；已儲存的速度不會自動沿用。":
        "With control off, explicitly select the physical robot speed. Saved speeds are not armed automatically.",
    "需要遠端控制模式": "Remote control mode required",
    "控制器回報的遠端控制狀態不是 true。\n\n請先在 PolyScope 進入遠端模式（設定 → 系統 → 遠端控制），再重新啟動控制。":
        "The controller does not report Remote mode.\n\nEnable Remote mode in PolyScope (Settings → System → Remote Control), then enable control again.",
    "需要設定工作區邊界": "Workspace bounds required",
    "虛擬工作區 XYZ 邊界尚未設定，實際移動已被鎖定。請先在 safety_config.py 設定經審核的基座座標範圍。":
        "XYZ workspace bounds are unset, so motion is locked. Set reviewed base-frame bounds in safety_config.py first.",
    "TCP 姿態不可用": "TCP pose unavailable",
    "在收到新的 RTDE TCP 姿態回饋之前，實際移動會維持鎖定。":
        "Motion stays locked until fresh RTDE TCP pose feedback arrives.",
    "工作區故障未解除": "Workspace fault unresolved",
    "TCP 目前在工作區之外。請先將 TCP 移回設定範圍內，再重新啟動控制。":
        "The TCP is outside the workspace. Move it inside the configured bounds, then enable control again.",
    "控制未啟動": "Control is off", "請先連接 URSim 並啟動控制。":
        "Connect URSim and enable control first.",
    "URSim 測試未啟動": "URSim test unavailable",
    "單軸測試只限已啟動控制的 URSim。": "Axis tests require enabled URSim control.",
    "連接失敗": "Connection failed", "無法連接 RTDE ": "Cannot connect to RTDE ",
    "請先停止": "Stop first", "掃描前請關閉相機及機械人控制。":
        "Close the camera and turn off robot control before scanning.",
    "掃描中": "Scanning", "請等待相機掃描完成後再開啟相機。":
        "Wait for the camera scan to finish before opening a camera.",
    "相機錯誤": "Camera error",
    # Dynamic banners, device errors and diagnostics.
    "正在檢查 RTDE ": "Checking RTDE ", "RTDE 連接中…": "Connecting to RTDE…",
    "RTDE 連接失敗": "RTDE connection failed",
    "姿態已連接 | 等待控制介面就緒": "Pose connected | Waiting for control interface",
    "STOP 已由控制器接受 | 控制關閉": "STOP accepted by controller | Control off",
    "機械人未連接 | 控制關閉": "Robot disconnected | Control off",
    "相機啟動中 | 控制關閉": "Starting camera | Control off",
    "相機已關閉 | 控制關閉": "Camera closed | Control off",
    "校準中 | 請保持張開手掌不動": "Calibrating | Hold an open palm steady",
    "相機啟動逾時 | 控制關閉": "Camera startup timed out | Control off",
    "中立校準完成 | 就緒": "Neutral calibration complete | Ready",
    "STOP 未確認 | 請查看控制器": "STOP unconfirmed | Check controller",
    "停止已請求 | 控制關閉": "STOP requested | Control off",
    "工作區未設定 | 控制鎖定": "Workspace unset | Control locked",
    "姿態回饋中斷 | 控制鎖定": "Pose feedback lost | Control locked",
    "實際控制已啟動": "Robot control enabled",
    "測試完成 | 控制關閉": "Test complete | Control off",
    "單軸測試中 | 手勢輸出暫停": "Axis test running | Gesture output paused",
    "返回安全原點中 | URSim": "Returning to safe origin | URSim",
    "已返回原點 | 控制關閉": "Returned to origin | Control off",
    "控制故障 | ": "Control fault | ",
    "無法開啟相機或 RTSP 串流": "Cannot open camera or RTSP stream",
    "相機讀取失敗；控制已停止，請重新開啟相機":
        "Camera read failed; control stopped. Reopen the camera.",
    "視覺 worker 已結束": "Vision worker exited",
    "控制器拒絕 STOP": "Controller rejected STOP",
    "無法啟用控制器通訊 watchdog": "Cannot enable controller communication watchdog",
    "關閉時 STOP 失敗": "STOP failed during shutdown",
    "控制介面及通訊 watchdog 已就緒": "Control interface and communication watchdog ready",
    "控制介面建立逾時；請檢查機械人狀態後重新連線":
        "Control interface setup timed out; check robot status and reconnect",
    "控制器 watchdog 更新失敗": "Controller watchdog update failed",
    "控制器拒絕速度指令": "Controller rejected speed command",
    "RTDE worker 安全故障（STOP 結果請查看控制器）":
        "RTDE worker safety fault (check STOP result on controller)",
    "RTDE worker 已結束；控制器通訊 watchdog 將停止控制":
        "RTDE worker exited; communication watchdog will stop control",
    "RTDE 未連接": "RTDE disconnected",
    "RTDE worker 已結束，無法確認 STOP": "RTDE worker exited; cannot confirm STOP",
    "TCP 不在有效工作區內": "TCP outside valid workspace",
    "速度向量無效（只接受 XYZ 平移）": "Invalid velocity vector (XYZ translation only)",
    "故障已鎖定，須重新啟動控制": "Fault latched; enable control again",
    "控制週期或加速度無效": "Invalid control period or acceleration",
    "實體目標禁止自動測試／返回": "Automatic test / return disabled for physical target",
    "控制指令過期": "Control command expired",
    "TCP 姿態回饋中斷": "TCP pose feedback lost",
    "機械人安全模式 ": "Robot safety mode ",
    "影像回饋中斷或過期": "Camera feedback lost or expired",
    "返回安全原點逾時": "Return to safe origin timed out",
    "回程速度無效": "Invalid return speed",
    "實體工作區邊界已觸發": "Physical workspace boundary reached",
    "未知目標": "Unknown target",
    "工作區需要六個 XYZ 上下限": "Workspace requires six XYZ bounds",
    " 上下限未設定": " bounds are unset",
    " 上下限必須為有限數值": " bounds must be finite numbers",
    " 下限 ": " lower bound ", " 必須小於上限 ": " must be less than upper bound ",
    " 上下限必須為數值": " bounds must be numeric",
    # Log messages and free-form status fragments.
    "CSV 記錄：": "CSV log: ",
    "就緒。請先連接 URSim，再啟動控制。": "Ready. Connect URSim before enabling control.",
    "請開啟相機並按「校準中立姿態」，完成後才啟動手勢控制。":
        "Open the camera and calibrate neutral pose before enabling gesture control.",
    "設定未載入，使用預設值：": "Settings could not be loaded; using defaults: ",
    "設定根節點必須為 object": "Settings root must be an object",
    "設定儲存失敗：": "Could not save settings: ",
    "本機設定已載入": "Local settings loaded",
    "RTDE 工作程序已啟動：": "RTDE worker started: ",
    "RTDE 錯誤: ": "RTDE error: ",
    "RTDE 已連接；請在控制器確認電源及操作模式":
        "RTDE connected; confirm power and operating mode on the controller",
    "機械人已斷線": "Robot disconnected",
    "相機 worker 已啟動；影像出現後請按「校準中立姿態」":
        "Camera worker started; calibrate neutral pose when video appears",
    "相機已關閉，控制已停用": "Camera closed; control disabled",
    "手動中立校準開始": "Manual neutral calibration started",
    "分區顯示已": "Zone display: ",
    "深度方向設為": "Depth direction set to ",
    "移動方向設為": "Move direction set to ",
    "影像回饋過期": "Camera feedback expired",
    "影像處理結果過期": "Vision result expired",
    "中立姿態已校準：x=": "Neutral pose calibrated: x=",
    "找到相機：": "Cameras found: ",
    "未找到相機或掃描逾時": "No camera found or scan timed out",
    "偵測／追蹤設定已套用；開啟相機後須重新校準及啟動控制":
        "Detection / tracking settings applied; recalibrate and enable control after reopening the camera",
    "實體 UR10 檢查清單未完全確認；控制保持關閉":
        "Physical UR10 checklist incomplete; control remains off",
    "已封鎖實體控制：尚未明確選擇速度":
        "Physical control blocked: speed not explicitly selected",
    "偵測到 PolyScope ": "Detected PolyScope ",
    "CB3 控制器毋須遠端控制模式，跳過遠端檢查":
        "CB3 controller does not need Remote mode; skipping that check",
    "已封鎖啟動：控制器不在遠端控制模式":
        "Enable blocked: controller is not in Remote mode",
    "已封鎖實際控制：虛擬工作區未設定":
        "Control blocked: workspace bounds are unset",
    "已封鎖實際控制：TCP 姿態回饋不可用":
        "Control blocked: TCP pose feedback unavailable",
    "TCP 已回到工作區內；已手動解除工作區故障":
        "TCP returned inside workspace; workspace fault manually cleared",
    "機械人控制已啟動": "Robot control enabled",
    "已請求 STOP；送達狀態由 RTDE worker 回報":
        "STOP requested; the RTDE worker reports delivery status",
    "STOP 失敗，無法確認停止：": "STOP failed; cannot confirm stop: ",
    "單軸測試逾時或姿態回饋過期": "Axis test timed out or pose feedback expired",
    "單軸測試完成；需手動重新啟動控制":
        "Axis test complete; enable control manually to resume",
    "工作區無效；控制鎖定：": "Invalid workspace; control locked: ",
    "虛擬工作區（基座座標 m）： X=": "Virtual workspace (base frame, m): X=",
    "控制故障：": "Control fault: ",
    "TCP 姿態或安全回饋不可用": "TCP pose or safety feedback unavailable",
    "撞牆；實體機械人停用自動返回":
        "Workspace wall reached; automatic return disabled for physical robot",
    "撞牆；安全原點不在工作區內":
        "Workspace wall reached; safe origin is outside workspace",
    "撞牆；姿態或安全回饋不可用":
        "Workspace wall reached; pose or safety feedback unavailable",
    "撞到空氣牆；以低速直線返回安全原點":
        "Workspace wall reached; returning to safe origin at low speed",
    "返回安全原點被中止": "Return to safe origin aborted",
    "返回安全原點時離開工作區": "Left workspace while returning to safe origin",
    "已返回安全原點；控制維持關閉": "Returned to safe origin; control remains off",
    "速度向量無效": "Invalid velocity vector",
    "RTDE 控制錯誤: ": "RTDE control error: ",
    "; STOP 失敗: ": "; STOP failed: ",
    "請輸入相機索引或 RTSP URL": "Enter a camera index or RTSP URL",
    "單軸測試需要新鮮姿態": "Axis test requires fresh pose feedback",
    "單軸測試目標超出工作區": "Axis test target outside workspace",
    "單軸測試目標距離 ": "Axis test target distance ",
    "已明確選擇實體速度 ": "Physical speed explicitly selected: ",
    "CSV 寫入失敗：": "CSV write failed: ",
    "CSV 儲存失敗：": "CSV flush failed: ",
    "隱藏": "hidden", "顯示": "visible", "反轉": "reversed",
}

ORDERED_ENGLISH = tuple(sorted(ENGLISH.items(), key=lambda item: len(item[0]), reverse=True))


def language() -> str:
    app = QApplication.instance()
    return app.property("ui_language") if app and app.property("ui_language") in LANGUAGES else "yue"


def localize(text: str) -> str:
    if language() != "en" or not text:
        return text
    for source, translated in ORDERED_ENGLISH:
        text = text.replace(source, translated)
    return text


class _TooltipMixin:
    def setToolTip(self, text):
        self._source_tooltip = text
        super().setToolTip(localize(text))

    def _retranslate_tooltip(self):
        if hasattr(self, "_source_tooltip"):
            super().setToolTip(localize(self._source_tooltip))


class LocalizedLabel(_TooltipMixin, QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setText(text)

    def setText(self, text):
        self._source_text = str(text)
        super().setText(localize(self._source_text))

    def retranslate(self):
        super().setText(localize(self._source_text))
        self._retranslate_tooltip()


class LocalizedButton(_TooltipMixin, QPushButton):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setText(text)

    def setText(self, text):
        self._source_text = str(text)
        super().setText(localize(self._source_text))

    def retranslate(self):
        super().setText(localize(self._source_text))
        self._retranslate_tooltip()


class LocalizedCheckBox(_TooltipMixin, QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setText(text)

    def setText(self, text):
        self._source_text = str(text)
        super().setText(localize(self._source_text))

    def retranslate(self):
        super().setText(localize(self._source_text))
        self._retranslate_tooltip()


class LocalizedFrame(_TooltipMixin, QFrame):
    def retranslate(self):
        self._retranslate_tooltip()


class LocalizedLineEdit(_TooltipMixin, QLineEdit):
    def setPlaceholderText(self, text):
        self._source_placeholder = text
        super().setPlaceholderText(localize(text))

    def retranslate(self):
        if hasattr(self, "_source_placeholder"):
            super().setPlaceholderText(localize(self._source_placeholder))
        self._retranslate_tooltip()


class LocalizedComboBox(_TooltipMixin, QComboBox):
    def addItem(self, text, userData=None):
        super().addItem(localize(text), userData)
        sources = getattr(self, "_source_items", None)
        if sources is None:
            self._source_items = []
        self._source_items.append(text)

    def retranslate(self):
        self.blockSignals(True)
        try:
            for index, text in enumerate(getattr(self, "_source_items", ())):
                self.setItemText(index, localize(text))
        finally:
            self.blockSignals(False)
        self._retranslate_tooltip()


class LocalizedTabWidget(_TooltipMixin, QTabWidget):
    def addTab(self, widget, label):
        index = super().addTab(widget, localize(label))
        labels = getattr(self, "_source_tabs", None)
        if labels is None:
            self._source_tabs = []
        self._source_tabs.append(label)
        return index

    def retranslate(self):
        for index, label in enumerate(getattr(self, "_source_tabs", ())):
            self.setTabText(index, localize(label))
        self._retranslate_tooltip()


class LocalizedMessageBox:
    Yes = QtMessageBox.Yes
    No = QtMessageBox.No

    @staticmethod
    def warning(parent, title, text, *args):
        return QtMessageBox.warning(parent, localize(title), localize(text), *args)

    @staticmethod
    def question(parent, title, text, *args):
        return QtMessageBox.question(parent, localize(title), localize(text), *args)
