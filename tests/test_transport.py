from vision_control.clock import monotonic
import multiprocessing as mp
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from safety_config import workspace_profile, URSIM_TARGET
from vision_control.policy import Mode
from vision_control.transport import CommandMailbox, RTDEWorkerClient, _rtde_worker

class InstantThread:
    def __init__(self,target,**kwargs):self.target=target
    def start(self):self.target()

class PendingThread(InstantThread):
    def start(self):pass

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.context=mp.get_context('spawn')
        self.mailbox=CommandMailbox(self.context)
        self.feedback=self.context.Array('d',19,lock=True)
        self.camera=self.context.Array('d',[1.,100.],lock=True)
        self.events=queue.Queue(maxsize=32)
        self.now=100.
        self.calls=[]

    def run_worker(self,pose_stalls=False,heartbeat=False,hook=None,duration=1.,thread=InstantThread,stop_fails=False):
        def stop(*args):
            self.calls.append(('stop',self.now))
            return not stop_fails
        robot=SimpleNamespace(setWatchdog=lambda frequency:True,
            kickWatchdog=lambda:True,speedStop=stop,
            speedL=lambda vector,*args:self.calls.append(('speed',self.now,tuple(vector))) or True)
        receive=SimpleNamespace(getTimestamp=lambda:100. if pose_stalls else self.now,
            getActualTCPPose=lambda:[0.,0.,.5,0.,0.,0.],getSafetyMode=lambda:1,getRobotStatus=lambda:1)
        def sleep(seconds):
            self.now+=seconds
            if heartbeat:
                self.mailbox.speed([.02,0,0,0,0,0],.25,.1,self.now)
            if hook:hook()
            if self.now >= 100.+duration:self.mailbox.close()
        modules={'rtde_control':SimpleNamespace(RTDEControlInterface=lambda host:robot),
                 'rtde_receive':SimpleNamespace(RTDEReceiveInterface=lambda host:receive)}
        with patch.dict('sys.modules',modules),patch('vision_control.transport.monotonic',lambda:self.now),patch('vision_control.transport.time.sleep',sleep),patch('vision_control.transport.threading.Thread',thread):
            self.mailbox.mode(Mode.AXIS_TEST)
            self.mailbox.speed([.02,0,0,0,0,0],.25,.1,self.now)
            _rtde_worker(self.mailbox,self.feedback,self.camera,self.events,'offline',workspace_profile(URSIM_TARGET))

    def test_watchdog_stops_without_any_ui_poll(self):
        self.run_worker()
        speeds=[c for c in self.calls if c[0]=='speed']
        stops=[c for c in self.calls if c[0]=='stop' and c[1]>100.]
        self.assertTrue(speeds)
        self.assertTrue(stops)
        self.assertLessEqual(stops[0][1]-100.,.38)
        self.assertGreater(self.feedback[5],0)
        self.assertEqual(len(speeds),1)

    def test_pose_timestamp_does_not_refresh_on_cached_receive_data(self):
        self.run_worker(pose_stalls=True,heartbeat=True)
        self.assertEqual(self.feedback[2],100.)
        events=[]
        while not self.events.empty():events.append(self.events.get())
        self.assertTrue(any('TCP' in e[1] for e in events))

    def test_stop_is_seen_even_if_new_speed_overwrites_mailbox(self):
        invoked=[]
        def hook():
            if not invoked:
                self.mailbox.stop()
                self.mailbox.speed([.01,0,0,0,0,0],.25,.1,self.now)
                invoked.append(True)
        self.run_worker(hook=hook,duration=.1)
        self.assertEqual([c[0] for c in self.calls[:4]],['stop','speed','stop','speed'])
        self.assertEqual(self.calls[3][2][0],.01)

    def test_build_timeout_starts_at_actual_start(self):
        self.run_worker(thread=PendingThread,duration=.2)
        self.assertEqual(self.feedback[5],0)

    def test_build_timeout_expires_after_elapsed_deadline(self):
        self.run_worker(thread=PendingThread,duration=17.)
        self.assertGreater(self.feedback[5],0)

    def test_failed_stop_is_reported_and_no_speed_follows(self):
        self.run_worker(stop_fails=True)
        self.assertFalse(any(c[0]=='speed' for c in self.calls))
        errors=[]
        while not self.events.empty():errors.append(self.events.get()[1])
        self.assertTrue(any('STOP' in e for e in errors))

    def test_client_keeps_worker_acquisition_time(self):
        client=RTDEWorkerClient.__new__(RTDEWorkerClient)
        client.feedback=self.feedback
        self.feedback[0]=self.feedback[1]=1
        self.feedback[2]=100.
        client.connected=False
        client.process=SimpleNamespace(is_alive=lambda:True)
        client.events=self.events
        client.notices=[]
        client.fault_generation=0
        with patch('vision_control.transport.monotonic',return_value=1000.):
            client.poll()
        self.assertEqual(client.pose_received_at,100.)

    def test_zero_gesture_heartbeat_updates_frame_age(self):
        self.mailbox.mode(Mode.GESTURE)
        self.mailbox.stop(frame_at=123.)
        self.assertEqual(self.mailbox.snapshot().frame_at,123.)


def spawned_fake_worker(mailbox,feedback,camera,events):
    """Install fake SDKs inside the child; this target cannot open robot sockets."""
    import time
    control=SimpleNamespace(setWatchdog=lambda *a:True,kickWatchdog=lambda:True,
        speedStop=lambda *a:True,speedL=lambda *a:time.sleep(.05) or True)
    receive=SimpleNamespace(getTimestamp=monotonic,
        getActualTCPPose=lambda:[0.,0.,.5,0.,0.,0.],getSafetyMode=lambda:1,getRobotStatus=lambda:1)
    with patch.dict('sys.modules',{'rtde_control':SimpleNamespace(RTDEControlInterface=lambda host:control),
                                  'rtde_receive':SimpleNamespace(RTDEReceiveInterface=lambda host:receive)}):
        _rtde_worker(mailbox,feedback,camera,events,'offline',workspace_profile(URSIM_TARGET))
    events.cancel_join_thread()


class SpawnedTransportTests(unittest.TestCase):
    def test_worker_stops_after_parent_stops_publishing(self):
        import time
        context=mp.get_context('spawn')
        mailbox=CommandMailbox(context)
        feedback=context.Array('d',19,lock=True)
        camera=context.Array('d',2,lock=True)
        events=context.Queue(maxsize=32)
        process=context.Process(target=spawned_fake_worker,args=(mailbox,feedback,camera,events),daemon=True)
        process.start()
        def wait_for(predicate,seconds):
            deadline=monotonic()+seconds
            while monotonic()<deadline and not predicate():time.sleep(.01)
            self.assertTrue(predicate())
        try:
            wait_for(lambda:bool(feedback[1]),10)
            mailbox.mode(Mode.AXIS_TEST)
            mailbox.speed([.02,0,0,0,0,0],.25,.1,0.)
            wait_for(lambda:feedback[13]>0,2)
            # No Qt polling and no more command publication.
            wait_for(lambda:feedback[5]>0,2)
            self.assertEqual(list(feedback[13:19]),[0.]*6)
        finally:
            mailbox.close()
            process.join(timeout=1.)
            if process.is_alive():
                process.terminate();process.join(timeout=1.)
            events.close();events.cancel_join_thread()
        self.assertFalse(process.is_alive())
