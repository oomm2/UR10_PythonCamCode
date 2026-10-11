import math
import unittest
from dataclasses import replace
from safety_config import workspace_profile, workspace_errors, URSIM_TARGET, REAL_TARGET, WorkspaceProfile
from vision_control.policy import Command, Mode, WorkerGuard, guarded_velocity
from vision_control.gestures import zone_command
from vision_control.vision import HandQuality

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.guard = WorkerGuard(workspace_profile(URSIM_TARGET))
        self.command = Command(1, 0, 1, Mode.GESTURE, 100., 100., (.02,0,0,0,0,0))

    def evaluate(self, command=None, now=100.1, pose=(0.,0.,.5,0,0,0), pose_at=100., safety=1, camera=(1,100.)):
        return self.guard.evaluate(command or self.command, now, pose, pose_at, safety, camera)

    def test_command_stalls_without_ui_stops_and_latches(self):
        self.assertIsNotNone(self.evaluate()[0])
        self.assertIsNone(self.evaluate(now=100.4, pose_at=100.4, camera=(1,100.4))[0])
        renewed = replace(self.command,created_at=100.4,frame_at=100.4)
        self.assertIsNone(self.evaluate(renewed,now=100.4,pose_at=100.4,camera=(1,100.4))[0])
        self.assertIsNotNone(self.evaluate(replace(renewed,arm_sequence=2),now=100.4,pose_at=100.4,camera=(1,100.4))[0])

    def test_camera_read_failure_stops_on_first_failure(self):
        _, reason, _ = self.evaluate(camera=(0,100.))
        self.assertIn('影像',reason)

    def test_stale_inference_rejected_even_if_capture_keeps_running(self):
        _, reason, _ = self.evaluate(replace(self.command,frame_at=99.),camera=(1,100.1))
        self.assertIn('影像',reason)

    def test_stale_pose_not_relabelled_as_fresh(self):
        _, reason, _ = self.evaluate(pose_at=99.)
        self.assertIn('TCP',reason)

    def test_future_timestamps_are_invalid(self):
        self.assertIsNone(self.evaluate(replace(self.command,created_at=101.))[0])

    def test_safety_mode_change_blocks_motion(self):
        self.assertIsNone(self.evaluate(safety=3)[0])

    def test_test_and_return_do_not_require_camera(self):
        for mode in (Mode.AXIS_TEST,Mode.RETURN):
            vector,reason,_=self.evaluate(replace(self.command,mode=mode),camera=(0,0))
            self.assertFalse(reason)
            self.assertIsNotNone(vector)
            if mode == Mode.RETURN:
                self.assertLessEqual(abs(vector[0]),.005)

    def test_real_target_rejects_automatic_modes_even_with_valid_bounds(self):
        self.guard=WorkerGuard(WorkspaceProfile(REAL_TARGET,(-1,1,-1,1,0,1),None,False))
        for index, mode in enumerate((Mode.AXIS_TEST,Mode.RETURN)):
            self.assertIsNone(self.evaluate(replace(self.command,mode=mode,arm_sequence=index+1))[0])

    def test_return_deadline_is_independent_of_ui(self):
        cmd=replace(self.command,mode=Mode.RETURN)
        self.evaluate(cmd)
        _,reason,_=self.evaluate(replace(cmd,created_at=131.),now=131.,pose_at=131.)
        self.assertIn('逾時',reason)

    def test_workspace_errors_are_specific_and_handle_bad_types(self):
        self.assertIn('X 下限',workspace_errors((0,-.5,0,1,0,1))[0])
        for limits in ((None,)*6, ('abc',1,0,1,0,1), (0,math.nan,0,1,0,1), (0,1)):
            self.assertTrue(workspace_errors(limits))

    def test_wall_guard_all_six_sides_and_inward_motion(self):
        limits=(-1,1,-1,1,0,1)
        for axis in range(3):
            for side in (-1,1):
                pose=[0.,0.,.5]
                pose[axis]=limits[2*axis+(side==1)]
                velocity=[0.]*6
                velocity[axis]=side*.1
                vector,error,wall=guarded_velocity(velocity,pose,limits)
                self.assertFalse(error)
                self.assertTrue(wall)
                self.assertEqual(vector[axis],0)
                velocity[axis]*=-1
                vector,error,wall=guarded_velocity(velocity,pose,limits)
                self.assertFalse(wall)
                self.assertNotEqual(vector[axis],0)

    def test_nonfinite_pose_and_angular_commands_fail_closed(self):
        for vector,pose in (([.1,0,0,0,0,0],[math.nan,0,.5]),([0,0,0,1,0,0],[0,0,.5])):
            self.assertIsNone(guarded_velocity(vector,pose,(-1,1,-1,1,0,1))[0])

    def test_neutral_offset_is_actually_neutral(self):
        self.assertEqual(zone_command(.30,.5,.2,.30,.5,.2,.02)[3],'STOP')
        self.assertEqual(zone_command(.15,.5,.2,.30,.5,.2,.02)[3],'LEFT')

    def test_dead_zone_changes_effective_threshold(self):
        self.assertEqual(zone_command(.65,.5,.2,.5,.5,.2,.02,dead_zone=.1)[3],'RIGHT')
        self.assertEqual(zone_command(.65,.5,.2,.5,.5,.2,.02,dead_zone=.2)[3],'STOP')

    def test_hand_quality_requires_continuity_and_finite_landmarks(self):
        hand=[(.3+(i%5)*.03,.3+(i//5)*.03,0.) for i in range(21)]
        quality=HandQuality()
        self.assertFalse(quality.check(hand))
        self.assertFalse(quality.check(hand))
        self.assertTrue(quality.check(hand))
        moved=[(x+.3,y,z) for x,y,z in hand]
        self.assertFalse(quality.check(moved))
        hand[0]=(math.nan,0.,0.)
        self.assertFalse(quality.check(hand))

    def test_near_wall_stops_before_taper_becomes_asymptotic(self):
        vector,reason,wall=guarded_velocity([.02,0,0,0,0,0],[.996,0,.5],(-1,1,-1,1,0,1))
        self.assertTrue(wall)
        self.assertEqual(vector[0],0)

    def test_invalid_acceleration_and_period_are_rejected(self):
        for command in (replace(self.command,acceleration=math.nan),replace(self.command,period=-1.)):
            self.assertIsNone(self.evaluate(command)[0])
