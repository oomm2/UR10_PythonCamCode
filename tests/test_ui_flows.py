from vision_control.clock import monotonic
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QMessageBox
from ur10_vision_qt_app import UR10VisionQtApp
from safety_config import URSIM_TARGET, REAL_TARGET, workspace_profile
from vision_control.policy import Mode
from vision_control.vision import MANUAL_CAMERA

class Robot:
    def __init__(self):
        self.connected=self.control_ready=True
        self.actual_tcp_pose=(0.,0.,.5,0.,0.,0.)
        self.pose_received_at=monotonic()
        self.safety_status=1
        self.robot_status=1
        self.calls=[]
        self.notices=[]
        self.dispatched_velocity=(0.,)*6
        self.stop_ack=0
        self.stop_pending=False
    def set_mode(self,mode):self.calls.append(('mode',mode))
    def speedStop(self,*args,**kwargs):self.calls.append(('stop',))
    def speedL(self,vector,*args,**kwargs):self.calls.append(('speed',vector,kwargs))
    def disconnect(self):self.connected=False
    def poll(self):return None

class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=patch.object(UR10VisionQtApp,'SETTINGS_PATH',self.temp.name+'/settings.json')
        self.path.start()
        def no_logs(window):
            window.csv_path='test-disabled';window.csv_file=window.csv_writer=None
        self.logging=patch.object(UR10VisionQtApp,'_setup_csv_logging',no_logs)
        self.logging.start()
        self.window=UR10VisionQtApp()
        self.window.rtde_timer.stop()
        self.robot=Robot()
        self.window.robot=self.window.rtde_client=self.robot
        self.window.session_identity=(URSIM_TARGET,'offline',1)
        self.window.frame_acquired_at=monotonic()

    def tearDown(self):
        self.window.close()
        self.logging.stop();self.path.stop();self.temp.cleanup()

    def frame(self,points=None,quality=False):
        points=points or []
        sample=('frame',monotonic(),np.zeros((100,100,3),dtype=np.uint8),points,.99,quality)
        self.window.vision=SimpleNamespace(poll=lambda:sample,close=lambda:None,started=monotonic())
        self.window.camera_running=True
        return sample

    def test_checklist_constructs_every_checkbox_and_requires_all(self):
        seen=[]
        def accept(dialog):
            boxes=dialog.findChildren(QCheckBox)
            seen.append(len(boxes))
            for box in boxes:box.setChecked(True)
            return QDialog.Accepted
        with patch.object(QDialog,'exec',accept):
            self.assertTrue(self.window._confirm_real_robot_checklist(False))
            self.assertTrue(self.window._confirm_real_robot_checklist(True))
        self.assertEqual(seen,[5,4])
        with patch.object(QDialog,'exec',lambda *_:QDialog.Accepted):
            self.assertFalse(self.window._confirm_real_robot_checklist(False))

    def test_manual_camera_value_and_field_visibility(self):
        w=self.window
        w.camera_combo.setCurrentIndex(w.camera_combo.findData(MANUAL_CAMERA))
        w.camera_source.setText('rtsp://example.invalid/stream')
        self.assertFalse(w.camera_source.isHidden())
        self.assertEqual(w._camera_value(),'rtsp://example.invalid/stream')
        w.camera_source.setText('2')
        self.assertEqual(w._camera_value(),2)

    def test_camera_source_settings_roundtrip_and_legacy_labels(self):
        w=self.window
        for source in ('2','manual','<Manual entry>','<手動輸入>'):
            with open(w.SETTINGS_PATH,'w') as fh:
                json.dump({'camera_source':source,'camera_manual':'rtsp://example.invalid/video'},fh)
            w._load_settings()
            self.assertEqual(w._camera_value(),2 if source=='2' else 'rtsp://example.invalid/video')
        w._save_settings()
        with open(w.SETTINGS_PATH) as fh:self.assertEqual(json.load(fh)['camera_source'],'manual')

    def test_settings_load_is_read_only_and_rejects_malformed_values(self):
        w=self.window
        content='{"target":"實體 UR10","max_speed":0.5,"detection":"bad","tracking":"NaN"}'
        with open(w.SETTINGS_PATH,'w') as fh:fh.write(content)
        w._load_settings()
        with open(w.SETTINGS_PATH) as fh:self.assertEqual(fh.read(),content)
        self.assertEqual(w._value('Max speed'),.02)
        self.assertFalse(w.real_speed_selected)

    def test_first_camera_failure_disables_control(self):
        w=self.window
        w._set_mode(Mode.GESTURE)
        w.vision=SimpleNamespace(poll=lambda:('error','read failed'),close=lambda:None)
        w.update_frame()
        self.assertEqual(w.mode,Mode.DISABLED)
        self.assertFalse(w.robot_enabled)
        self.assertIn(('stop',),self.robot.calls)

    def test_stale_pose_disables_without_camera_callback(self):
        w=self.window
        w._set_mode(Mode.GESTURE)
        self.robot.pose_received_at=monotonic()-10
        w._poll_rtde()
        self.assertEqual(w.mode,Mode.FAULT)
        self.assertIn(('stop',),self.robot.calls)

    def test_axis_and_return_own_output_while_camera_updates(self):
        w=self.window
        for mode in (Mode.AXIS_TEST,Mode.RETURN):
            w._set_mode(mode)
            self.frame()
            self.robot.calls.clear()
            w.update_frame()
            self.assertEqual(self.robot.calls,[])
            self.assertFalse(w._send_velocity(.02,0,0,source=Mode.GESTURE))
            self.assertEqual(self.robot.calls,[])

    def test_stop_bypasses_speed_throttle(self):
        w=self.window
        w._set_mode(Mode.GESTURE)
        w.last_robot_update=monotonic()
        self.robot.calls.clear()
        w._send_velocity(0,0,0)
        self.assertEqual(self.robot.calls,[('stop',)])

    def test_return_executes_with_speed_cap_and_stops_at_origin(self):
        w=self.window
        w._start_return_to_origin()
        self.assertEqual(w.mode,Mode.RETURN)
        self.robot.calls.clear()
        w._return_step()
        speeds=[c for c in self.robot.calls if c[0]=='speed']
        self.assertTrue(speeds)
        self.assertLessEqual(sum(v*v for v in speeds[-1][1][:3])**.5,.005+1e-12)
        self.robot.actual_tcp_pose=(.4,0.,.4,0.,0.,0.)
        w._return_step()
        self.assertEqual(w.mode,Mode.DISABLED)
        self.assertFalse(w.return_timer.isActive())

    def test_return_timeout_and_real_target_are_blocked(self):
        w=self.window
        w._start_return_to_origin()
        w.return_started_at=monotonic()-31
        w._return_step()
        self.assertEqual(w.mode,Mode.FAULT)
        w.returning_to_origin=False
        w.session_identity=(REAL_TARGET,'offline',2)
        w._start_return_to_origin()
        self.assertEqual(w.mode,Mode.FAULT)
        self.assertFalse(w.return_timer.isActive())

    def test_axis_completion_uses_pose_not_tick_count(self):
        w=self.window
        w._set_mode(Mode.READY)
        w.axis_test(1,0,0)
        self.assertEqual(w.mode,Mode.AXIS_TEST)
        w._test_step()
        self.assertTrue(any(c[0]=='speed' for c in self.robot.calls))
        self.robot.actual_tcp_pose=(*w.test_target,0.,0.,0.)
        w._test_step()
        self.assertEqual(w.mode,Mode.DISABLED)

    def test_direction_change_requires_six_frames_again(self):
        w=self.window
        for _ in range(6):w._stabilize_command('LEFT')
        self.assertEqual(w._stabilize_command('RIGHT'),'STOP')
        self.assertEqual(w._stabilize_command('LEFT'),'STOP')
        for _ in range(5):result=w._stabilize_command('LEFT')
        self.assertEqual(result,'LEFT')

    def test_apply_model_settings_stops_then_restarts(self):
        w=self.window
        self.frame()
        w._set_mode(Mode.GESTURE)
        with patch.object(w,'start_camera') as restart:
            w.apply_vision_settings()
        self.assertEqual(w.mode,Mode.DISABLED)
        restart.assert_called_once()

    def test_stop_failure_is_visible(self):
        w=self.window
        self.robot.speedStop=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('offline'))
        w.emergency_stop()
        self.assertEqual(w.mode,Mode.FAULT)
        self.assertFalse(w.robot_enabled)
        self.assertIn('STOP 未確認',w.status_banner.text())
        self.assertIn('STOP 失敗',w.event_log.toPlainText())

    def test_enable_requires_control_ready(self):
        w=self.window
        self.robot.control_ready=False
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes),patch.object(QMessageBox,'warning') as warning:
            w.toggle_robot_control()
        self.assertFalse(w.robot_enabled)
        warning.assert_called_once()

    def test_all_modes_stop_on_disconnect(self):
        w=self.window
        for mode in (Mode.AXIS_TEST,Mode.RETURN,Mode.GESTURE):
            w.robot=w.rtde_client=self.robot
            self.robot.connected=True
            w._set_mode(mode)
            w._disconnect_robot()
            self.assertEqual(w.mode,Mode.DISABLED)
            self.assertFalse(w.test_timer.isActive())
            self.assertFalse(w.return_timer.isActive())

    def test_escape_stops_even_with_modal_dialog_open(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        w=self.window
        w.show()
        w._set_mode(Mode.AXIS_TEST)
        dialog=QDialog(w)
        dialog.setModal(True)
        dialog.show()
        self.qt.processEvents()
        try:
            QTest.keyClick(dialog,Qt.Key_Escape)
            self.assertEqual(w.mode,Mode.DISABLED)
        finally:
            dialog.close()

    def test_camera_cannot_start_while_scan_owns_devices(self):
        w=self.window
        w.scanner=SimpleNamespace(close=lambda:None)
        with patch('ur10_vision_qt_app.VisionClient') as create,patch.object(QMessageBox,'warning'):
            w.start_camera()
            create.assert_not_called()
        self.assertFalse(w.camera_running)

    def test_workflow_reports_calibration_and_fault_without_starting_motion(self):
        w=self.window
        self.frame()
        w.calibration_active=True
        w.neutral_samples=3
        w._update_status()
        self.assertEqual(w.calibration_progress.value(),3)
        self.assertIn('3/8',w.calibration_label.text())
        self.assertFalse(w.robot_enabled)
        w.neutral_hand_size=.2
        w.calibration_active=False
        w._update_status()
        self.assertEqual(w.calibration_progress.value(),8)
        self.assertIn('準備啟動',w.workflow_title.text())
        self.assertFalse(w.robot_enabled)
        w._set_mode(Mode.GESTURE)
        w._update_status()
        self.assertEqual(w.workflow_title.text(),'手勢控制中')
        w._set_mode(Mode.FAULT)
        w._update_status()
        self.assertEqual(w.workflow_card.property('state'),'error')
        self.assertEqual(w.status_banner.property('state'),'error')

    def test_status_separates_gesture_from_dispatch_and_pending_stop(self):
        w=self.window
        w._set_mode(Mode.GESTURE)
        self.robot.dispatched_velocity=(0.,.005,0.,0.,0.,0.)
        w._update_status('LEFT')
        self.assertEqual(w.status_values['Command'].text(),'前進')
        self.assertIn('左移',w.direction_badge.text())
        self.robot.stop_pending=True
        w._update_status()
        self.assertEqual(w.status_values['Command'].text(),'停止待確認')

    def test_stop_and_preview_remain_accessible_on_every_tab_at_small_size(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        w=self.window
        w.resize(1000,720)
        w.show()
        for index in range(w.side_tabs.count()):
            w.side_tabs.setCurrentIndex(index)
            w._set_mode(Mode.AXIS_TEST)
            self.qt.processEvents()
            self.assertTrue(w.stop_button.isVisible())
            self.assertTrue(w.rect().contains(w.stop_button.geometry()))
            video_bottom=w.video_label.geometry().bottom()
            self.assertLess(video_bottom,w.calibration_label.geometry().top())
            QTest.mouseClick(w.stop_button,Qt.LeftButton)
            self.assertEqual(w.mode,Mode.DISABLED)

    def test_theme_choice_survives_settings_reload_without_changing_control(self):
        w=self.window
        w.theme_button.click()
        self.assertEqual(w.theme,'dark')
        self.assertIn('淺色模式',w.theme_button.text())
        with open(w.SETTINGS_PATH,encoding='utf-8') as fh:
            self.assertEqual(json.load(fh)['theme'],'dark')
        self.assertEqual(w.mode,Mode.DISABLED)
        w.theme='light'
        w._apply_style()
        w._load_settings()
        self.assertEqual(w.theme,'dark')
        self.assertIn('淺色模式',w.theme_button.text())
        w.theme_button.click()
        self.assertEqual(w.theme,'light')
