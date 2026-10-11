"""Crash-isolated RTDE connection with a latest-value mailbox and worker watchdog.

No motion queue can grow while Qt is blocked. STOP has a separate generation
that must be handled before any later speed, even if the speed overwrites it.
"""
from .clock import monotonic
import math
import multiprocessing as mp
import queue
import threading
import time
from .policy import Command, Mode, WorkerGuard, BUILD_TIMEOUT
from safety_config import workspace_profile, URSIM_TARGET


class CommandMailbox:
    def __init__(self, context):
        # sequence, stop generation, arm generation, mode, created, frame,
        # six velocity components, acceleration, period, shutdown
        self.data = context.Array('d', 15, lock=True)

    def snapshot(self):
        with self.data.get_lock():
            d = list(self.data[:])
        return Command(int(d[0]), int(d[1]), int(d[2]), Mode(int(d[3])),
                       d[4], d[5], tuple(d[6:12]), d[12] or .25, d[13] or .10)

    def mode(self, mode):
        with self.data.get_lock():
            self.data[0] += 1
            self.data[1] += 1
            self.data[2] += 1
            self.data[3] = int(mode)
            self.data[4] = monotonic()
            self.data[6:12] = [0.0] * 6

    def stop(self, frame_at=None):
        with self.data.get_lock():
            self.data[0] += 1
            self.data[1] += 1
            self.data[4] = monotonic()
            if frame_at is not None:
                self.data[5] = frame_at
            self.data[6:12] = [0.0] * 6

    def speed(self, velocity, acceleration, period, frame_at):
        if len(velocity) != 6:
            raise ValueError('Expected six velocity components')
        with self.data.get_lock():
            self.data[0] += 1
            self.data[4] = monotonic()
            self.data[5] = frame_at
            self.data[6:12] = velocity
            self.data[12] = acceleration
            self.data[13] = period

    def close(self):
        self.stop()
        with self.data.get_lock():
            self.data[14] = 1


def _publish(events, event):
    try:
        events.put_nowait(event)
    except queue.Full:
        pass  # Critical fault state also lives in the shared feedback block.


def _rtde_worker(mailbox, feedback, camera_state, events, host, profile):
    control = None
    stopped = True
    # feedback: connected, ready, pose timestamp, safety, robot status,
    # fault generation, stop ACK generation, pose[6], last dispatched velocity[6]
    def fault(message):
        nonlocal stopped
        stop_error = ''
        if control is not None:
            try:
                if control.speedStop(2.0) is False:
                    raise RuntimeError('控制器拒絕 STOP')
                stopped = True
                with feedback.get_lock():
                    feedback[13:19] = [0.] * 6
            except Exception as exc:
                stop_error = f'; STOP 失敗: {exc}'
        with feedback.get_lock():
            feedback[5] += 1
        _publish(events, ('fault', message + stop_error))
        return bool(stop_error)

    try:
        import rtde_control
        import rtde_receive
        receive = rtde_receive.RTDEReceiveInterface(host)
        with feedback.get_lock():
            feedback[0] = 1
        build_result = queue.Queue(maxsize=1)
        def build():
            try:
                candidate = rtde_control.RTDEControlInterface(host)
                if candidate.setWatchdog(2.0) is False:
                    raise RuntimeError('無法啟用控制器通訊 watchdog')
                build_result.put((candidate, None))
            except Exception as exc:
                build_result.put((None, str(exc)))
        started = monotonic()
        threading.Thread(target=build, daemon=True).start()
        guard = WorkerGuard(profile)
        last_timestamp = None
        pose_at = 0.0
        handled_stop = -1
        last_sequence = -1
        announced_fault_arm = None
        pose = ()
        while True:
            now = monotonic()
            if mailbox.data[14]:
                if control is not None:
                    if control.speedStop(2.0) is False:
                        raise RuntimeError('關閉時 STOP 失敗')
                    with feedback.get_lock():
                        feedback[6] = mailbox.snapshot().stop_sequence
                        feedback[13:19] = [0.] * 6
                break
            if control is None:
                try:
                    control, error = build_result.get_nowait()
                except queue.Empty:
                    error = None
                if error:
                    raise RuntimeError(error)
                if control is not None:
                    with feedback.get_lock():
                        feedback[1] = 1
                    _publish(events, ('notice', '控制介面及通訊 watchdog 已就緒'))
                elif now - started > BUILD_TIMEOUT:
                    raise RuntimeError('控制介面建立逾時；請檢查機械人狀態後重新連線')
            stamp = float(receive.getTimestamp())
            if math.isfinite(stamp) and (last_timestamp is None or stamp > last_timestamp):
                last_timestamp = stamp
                pose_at = monotonic()
                pose = tuple(receive.getActualTCPPose())
            safety = int(receive.getSafetyMode())
            with feedback.get_lock():
                feedback[2] = pose_at
                feedback[3] = safety
                feedback[4] = int(receive.getRobotStatus())
                if len(pose) == 6:
                    feedback[7:13] = pose
            if control is None:
                time.sleep(.02)
                continue
            command = mailbox.snapshot()
            if command.stop_sequence != handled_stop:
                if control.speedStop(2.0) is False:
                    raise RuntimeError('控制器拒絕 STOP')
                stopped = True
                handled_stop = command.stop_sequence
                with feedback.get_lock():
                    feedback[6] = handled_stop
                    feedback[13:19] = [0.] * 6
                # A new speed must never overtake this acknowledged stop.
            with camera_state.get_lock():
                camera = tuple(camera_state[:])
            vector, error, wall = guard.evaluate(command, monotonic(), pose, pose_at, safety, camera)
            if error:
                if announced_fault_arm != command.arm_sequence:
                    announced_fault_arm = command.arm_sequence
                    if fault(error):
                        break  # Stop kicking the controller watchdog on failed STOP.
                if control.kickWatchdog() is False:
                    raise RuntimeError("控制器 watchdog 更新失敗")
                time.sleep(.02)
                continue
            if command.mode not in (Mode.GESTURE, Mode.AXIS_TEST, Mode.RETURN) or not any(vector):
                if not stopped:
                    if control.speedStop(2.0) is False:
                        raise RuntimeError('控制器拒絕 STOP')
                    stopped = True
                with feedback.get_lock():
                    feedback[13:19] = [0.] * 6
            elif command.sequence != last_sequence:
                # Re-read STOP after feedback/policy work and before dispatch.
                if mailbox.snapshot().stop_sequence != handled_stop:
                    continue
                if control.speedL(vector, command.acceleration, min(command.period, .1)) is False:
                    raise RuntimeError('控制器拒絕速度指令')
                stopped = False
                with feedback.get_lock():
                    feedback[13:19] = vector
            last_sequence = command.sequence
            if control.kickWatchdog() is False:
                raise RuntimeError('控制器 watchdog 更新失敗')
            time.sleep(.01)
    except Exception as exc:
        fault(str(exc))
    finally:
        with feedback.get_lock():
            feedback[0] = 0
            feedback[1] = 0
        # Native disconnect can deadlock after RTDE EOF; process exit reclaims it.


class RTDEWorkerClient:
    def __init__(self, host, target=URSIM_TARGET, camera_state=None):
        context = mp.get_context('spawn')
        self.mailbox = CommandMailbox(context)
        self.feedback = context.Array('d', 19, lock=True)
        self.feedback[6] = -1
        self.camera_state = camera_state if camera_state is not None else context.Array('d', 2, lock=True)
        self.events = context.Queue(maxsize=32)
        self.process = context.Process(target=_rtde_worker, args=(
            self.mailbox, self.feedback, self.camera_state, self.events, host, workspace_profile(target)), daemon=True)
        self.connected = False
        self.control_ready = False
        self.actual_tcp_pose = None
        self.pose_received_at = 0.0
        self.safety_status = None
        self.robot_status = None
        self.dispatched_velocity = (0.,) * 6
        self.notices = []
        self.error = ''
        self.fault_generation = 0
        self.stop_ack = -1
        self.process.start()

    def poll(self):
        with self.feedback.get_lock():
            f = list(self.feedback[:])
        previously_connected = self.connected
        self.connected = bool(f[0]) and self.process.is_alive()
        self.control_ready = bool(f[1]) and self.connected
        self.pose_received_at = f[2]  # acquisition time, never dequeue time
        self.actual_tcp_pose = tuple(f[7:13]) if f[2] else None
        self.safety_status, self.robot_status = int(f[3]), int(f[4])
        self.dispatched_velocity = tuple(f[13:19])
        self.stop_ack = int(f[6])
        fault_message = ''
        while True:
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'fault':
                fault_message = message
            else:
                self.notices.append(message)
        if int(f[5]) != self.fault_generation or fault_message:
            self.fault_generation = int(f[5])
            self.error = fault_message or 'RTDE worker 安全故障（STOP 結果請查看控制器）'
            return ('fault', self.error)
        if not self.process.is_alive():
            return ('error', 'RTDE worker 已結束；控制器通訊 watchdog 將停止控制')
        if self.connected and not previously_connected:
            return ('connected', '')
        return None

    @property
    def stop_pending(self):
        return self.stop_ack < self.mailbox.snapshot().stop_sequence

    def set_mode(self, mode):
        self.mailbox.mode(mode)

    def speedL(self, velocity, acceleration, period, frame_at=0.0):
        if not self.connected or not self.process.is_alive():
            raise RuntimeError('RTDE 未連接')
        self.mailbox.speed(velocity, acceleration, period, frame_at)

    def speedStop(self, deceleration=2.0, frame_at=None):
        if not self.process.is_alive():
            raise RuntimeError('RTDE worker 已結束，無法確認 STOP')
        self.mailbox.stop(frame_at)

    def disconnect(self):
        self.mailbox.close()
        self.process.join(timeout=.4)
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=.2)
        self.connected = False
        self.events.close()
        self.events.cancel_join_thread()
