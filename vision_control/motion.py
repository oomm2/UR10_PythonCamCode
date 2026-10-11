"""Exclusive motion modes, operator interlocks and supervised test/return flows."""
from .clock import monotonic
import math
import time
from typing import Optional
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QMessageBox
from safety_config import URSIM_TARGET, REAL_TARGET, cap_translational_velocity, workspace_is_configured, workspace_errors
from .policy import Mode, fresh, guarded_velocity, pose_inside

class MotionMixin:
    def _confirm_real_robot_checklist(self, is_cb3: bool) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("實體 UR10 安全檢查清單")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "啟動實體機械人移動前，請逐項確認以下安全事項："))
        checkboxes = []
        items = list(self.REAL_CHECKLIST_ITEMS)
        if not is_cb3:
            items.insert(2, self.REAL_REMOTE_CHECKLIST_ITEM)
        for item in items:
            checkbox = QCheckBox(item, dialog)
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
            if not self.rtde_client.control_ready:
                QMessageBox.warning(self, "控制介面未就緒", "請等待控制介面及 watchdog 就緒；失敗時請重新連線。")
                return
            if not self._workspace_is_configured():
                QMessageBox.warning(self, "工作區設定無效", self._workspace_summary())
                return
            if not self._workspace_feedback_is_fresh() or not self._workspace_safety_status_is_acceptable():
                QMessageBox.warning(self, "回饋不可用", "請確認新鮮的 TCP 姿態及正常安全模式。")
                return
            if not self._workspace_contains_pose(self.rtde_client.actual_tcp_pose):
                QMessageBox.warning(self, "TCP 超出範圍", "請先將 TCP 移回有效工作區內。")
                return
            if self.session_identity[0] == REAL_TARGET:
                if not self.real_speed_selected:
                    QMessageBox.warning(
                        self, "需要確認實體速度",
                        "請在控制保持關閉時，明確設定實體機械人的速度；"
                        "已儲存的速度不會自動沿用。")
                    self.log("已封鎖實體控制：尚未明確選擇速度")
                    return
                polyscope = self._dashboard_polyscope_version(self.session_identity[1])
                is_cb3 = polyscope is not None and polyscope[0] < 5
                if polyscope is not None:
                    version_text = f"{polyscope[0]}.{polyscope[1]}.{polyscope[2]}"
                    self.log(
                        f"偵測到 PolyScope {version_text}"
                        f"（{'CB3' if is_cb3 else 'e-Series'}）")
                if is_cb3:
                    self.log("CB3 控制器毋須遠端控制模式，跳過遠端檢查")
                else:
                    remote = self._dashboard_remote_state(self.session_identity[1])
                    if remote is not True:
                        QMessageBox.warning(
                            self, "需要遠端控制模式",
                            "控制器回報的遠端控制狀態不是 true。\n\n"
                            "請先在 PolyScope 進入遠端模式（設定 → 系統 → 遠端控制），"
                            "再重新啟動控制。")
                        self.log("已封鎖啟動：控制器不在遠端控制模式")
                        return
                if not self._confirm_real_robot_checklist(is_cb3=is_cb3):
                    return
            if not self._workspace_is_configured():
                QMessageBox.warning(
                    self,
                    "需要設定工作區邊界",
                    "虛擬工作區 XYZ 邊界尚未設定，實際移動已被鎖定。"
                    "請先在 safety_config.py 設定經審核的基座座標範圍。",
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
            if not self._set_mode(Mode.GESTURE if self.camera_running and self.neutral_hand_size else Mode.READY):
                return
            self._set_speed_editor_enabled(False)
            self.enable_button.setText("停止機械人控制")
            self.status_banner.setText("實際控制已啟動")
            self.log("機械人控制已啟動")
        else:
            self.emergency_stop()
        self._update_status()


    def emergency_stop(self):
        self._set_mode(Mode.DISABLED)
        self.status_banner.setText("STOP 未確認 | 請查看控制器" if self._stop_request_failed else "停止已請求 | 控制關閉")
        self.log("已請求 STOP；送達狀態由 RTDE worker 回報")
        self._update_status()


    def _send_stop(self):
        self._stop_request_failed = False
        if self.robot is not None:
            try:
                self.robot.speedStop(2.0, frame_at=self.frame_acquired_at)
            except Exception as exc:
                self._stop_request_failed = True
                self.rtde_lost = True
                self.status_banner.setText("STOP 未確認 | 請查看控制器")
                self.log(f"STOP 失敗，無法確認停止：{exc}")
                return False
        return True


    def test_ursim_motion(self):
        if (not self.robot_enabled or self.session_identity is None
                or self.session_identity[0] != URSIM_TARGET):
            QMessageBox.warning(self, "控制未啟動", "請先連接 URSim 並啟動控制。")
            return
        self._start_axis_motion((1., 0., 0.), .02, min(.02, self._value('Max speed')))


    def axis_test(self, dx, dy, dz):
        if (not self.robot_enabled or self.session_identity is None
                or self.session_identity[0] != URSIM_TARGET):
            QMessageBox.warning(self, "URSim 測試未啟動", "單軸測試只限已啟動控制的 URSim。")
            return
        self._start_axis_motion((dx, dy, dz), self.AXIS_TEST_DISTANCE, self._value('Max speed'))


    def _test_step(self):
        if self.mode != Mode.AXIS_TEST:
            self.test_timer.stop()
            return
        if (not self._workspace_feedback_is_fresh() or
                monotonic() > self.test_deadline):
            self._trip_workspace_fault("單軸測試逾時或姿態回饋過期")
            return
        pose = self.rtde_client.actual_tcp_pose
        delta = [self.test_target[i] - pose[i] for i in range(3)]
        distance = math.sqrt(sum(v*v for v in delta))
        if distance <= .001:
            self._set_mode(Mode.DISABLED)
            self.status_banner.setText("測試完成 | 控制關閉")
            self.log("單軸測試完成；需手動重新啟動控制")
            return
        speed = min(self.test_speed, distance / (2 * self.CONTROL_PERIOD))
        self._send_velocity(*(v * speed / distance for v in delta), source=Mode.AXIS_TEST)


    def _workspace_limits(self) -> tuple[Optional[float], ...]:
        return self._workspace_profile.limits


    def _workspace_is_configured(self) -> bool:
        return workspace_is_configured(self._workspace_limits())


    def _workspace_summary(self) -> str:
        if not self._workspace_is_configured():
            return "工作區無效；控制鎖定：" + "；".join(workspace_errors(self._workspace_limits()))
        limits = self._workspace_limits()
        return (
            "虛擬工作區（基座座標 m）： "
            f"X={limits[0]:.3f}..{limits[1]:.3f}, "
            f"Y={limits[2]:.3f}..{limits[3]:.3f}, "
            f"Z={limits[4]:.3f}..{limits[5]:.3f}"
        )


    def _workspace_feedback_is_fresh(self):
        return bool(self.rtde_client and self.rtde_client.actual_tcp_pose is not None
                    and fresh(monotonic(), self.rtde_client.pose_received_at, self.POSE_FEEDBACK_TIMEOUT))


    def _workspace_safety_status_is_acceptable(self) -> bool:
        # UR safety modes: 1=NORMAL and 2=REDUCED are the only motion-permitting modes.
        return bool(self.rtde_client and self.rtde_client.safety_status in (1, 2))


    def _trip_workspace_fault(self, reason):
        if self.workspace_fault and self.workspace_fault_reason == reason:
            return
        self.workspace_fault = True
        self.workspace_fault_reason = reason
        self._set_mode(Mode.FAULT)
        self.status_banner.setText(f'控制故障 | {reason}')
        self.log(f'控制故障：{reason}')
        self._update_status()


    def _workspace_contains_pose(self, pose):
        return pose is not None and pose_inside(pose, self._workspace_limits())


    def _apply_workspace_wall(self, vx, vy, vz):
        if not self._workspace_feedback_is_fresh() or not self._workspace_safety_status_is_acceptable():
            self._trip_workspace_fault('TCP 姿態或安全回饋不可用')
            return None
        vector, error, wall = guarded_velocity([vx, vy, vz, 0., 0., 0.],
            self.rtde_client.actual_tcp_pose, self._workspace_limits(), self.CONTROL_PERIOD or .1)
        if error:
            self._trip_workspace_fault(error)
            return None
        if wall:
            self._start_return_to_origin()
            return None
        # Worker applies the final taper once using its own fresh pose.
        return (vx, vy, vz)


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
        if not self._set_mode(Mode.RETURN):
            return
        self.returning_to_origin = True
        self.return_started_at = monotonic()
        self.status_banner.setText("返回安全原點中 | URSim")
        self.log("撞到空氣牆；以低速直線返回安全原點")
        self.return_timer.start()


    def _return_step(self) -> None:
        if not self.returning_to_origin:
            self.return_timer.stop()
            return
        if (monotonic() - self.return_started_at > self.RETURN_TIMEOUT
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
            self._set_mode(Mode.DISABLED)
            self.status_banner.setText("已返回原點 | 控制關閉")
            self.log("已返回安全原點；控制維持關閉")
            self._update_status()
            return
        scale = min(self.RETURN_SPEED_CAP / distance, 1.0)
        self._send_velocity(delta[0] * scale, delta[1] * scale, delta[2] * scale, source=Mode.RETURN)


    def _send_velocity(self, vx, vy, vz, source=Mode.GESTURE):
        if (self.mode != source or self.session_identity is None or not self.robot_enabled
                or self.rtde_client is None or not self.rtde_client.connected):
            return False
        guarded = self._apply_workspace_wall(vx, vy, vz)
        if guarded is None or self.mode != source:
            return False
        vector = cap_translational_velocity([*guarded, 0., 0., 0.],
            self.RETURN_SPEED_CAP if source == Mode.RETURN else self._value('Max speed'))
        if vector is None:
            self._trip_workspace_fault('速度向量無效')
            return False
        now = monotonic()
        # STOP bypasses the speed-rate limiter.
        if not any(vector):
            return self._send_stop()
        if now - self.last_robot_update < self.CONTROL_PERIOD:
            return False
        try:
            self.robot.speedL(vector, self.ACCELERATION, self.CONTROL_PERIOD,
                              frame_at=self.frame_acquired_at if source == Mode.GESTURE else 0.)
        except Exception as exc:
            self._trip_workspace_fault(f'RTDE 控制錯誤: {exc}')
            return False
        self.last_robot_update = now
        return True


    def _set_mode(self, mode):
        self.test_timer.stop()
        self.return_timer.stop()
        self.returning_to_origin = mode == Mode.RETURN
        self.mode = mode
        self.robot_enabled = mode not in (Mode.DISABLED, Mode.FAULT)
        self._set_speed_editor_enabled(not self.robot_enabled)
        self.enable_button.setText('停止機械人控制' if self.robot_enabled else '啟動機械人控制')
        self.last_robot_update = 0.0
        self._stabilize_command('STOP')
        if self.rtde_client is not None:
            self.rtde_client.set_mode(mode)
        if not self._send_stop():
            self.mode = Mode.FAULT
            self.robot_enabled = False
            self.returning_to_origin = False
            self._set_speed_editor_enabled(True)
            self.enable_button.setText('啟動機械人控制')
            if self.rtde_client is not None:
                self.rtde_client.set_mode(Mode.FAULT)
            return False
        return True


    def _start_axis_motion(self, direction, distance, speed):
        if not self._workspace_feedback_is_fresh():
            self._trip_workspace_fault('單軸測試需要新鮮姿態')
            return
        pose = self.rtde_client.actual_tcp_pose
        self.test_target = tuple(pose[i] + direction[i] * distance for i in range(3))
        if not self._workspace_contains_pose(self.test_target):
            self._trip_workspace_fault('單軸測試目標超出工作區')
            return
        if not self._set_mode(Mode.AXIS_TEST):
            return
        self.test_speed = max(.001, min(speed, self.SAFETY_SPEED_CAP))
        self.test_deadline = monotonic() + max(10., 3 * distance / self.test_speed)
        self.test_timer.start(100)
        self.status_banner.setText('單軸測試中 | 手勢輸出暫停')
        self.log(f'單軸測試目標距離 {distance:.3f} m')
