"""Hardware-independent command ownership and freshness/workspace checks."""
from dataclasses import dataclass
from enum import IntEnum
import math
from safety_config import cap_translational_velocity, workspace_errors, URSIM_TARGET, RETURN_SPEED

COMMAND_TIMEOUT = 0.35
FRAME_TIMEOUT = 0.35
POSE_TIMEOUT = 0.5
BUILD_TIMEOUT = 15.0
RETURN_TIMEOUT = 30.0
WALL_STOP_MARGIN = 0.005

class Mode(IntEnum):
    DISABLED = 0
    READY = 1
    GESTURE = 2
    AXIS_TEST = 3
    RETURN = 4
    FAULT = 5

MOVING_MODES = (Mode.GESTURE, Mode.AXIS_TEST, Mode.RETURN)

@dataclass(frozen=True)
class Command:
    sequence: int
    stop_sequence: int
    arm_sequence: int
    mode: Mode
    created_at: float
    frame_at: float
    velocity: tuple
    acceleration: float = 0.25
    period: float = 0.10


def fresh(now, timestamp, timeout):
    return math.isfinite(timestamp) and timestamp > 0 and 0 <= now - timestamp <= timeout


def pose_inside(pose, limits, tolerance=0.002):
    return (not workspace_errors(limits) and len(pose) >= 3
            and all(math.isfinite(v) and lo - tolerance <= v <= hi + tolerance
                    for v, lo, hi in zip(pose[:3], limits[::2], limits[1::2])))


def guarded_velocity(velocity, pose, limits, period=0.10):
    """Reject invalid feedback; taper and bound each step before an outward wall.

    This is a TCP software envelope, not a collision or stopping-distance model.
    """
    errors = workspace_errors(limits)
    if errors:
        return None, '; '.join(errors), False
    if not pose_inside(pose, limits):
        return None, 'TCP 不在有效工作區內', False
    vector = cap_translational_velocity(velocity)
    if vector is None or any(v != 0 for v in vector[3:]):
        return None, '速度向量無效（只接受 XYZ 平移）', False
    wall = False
    for i, (lo, hi) in enumerate(zip(limits[::2], limits[1::2])):
        v = vector[i]
        if not v:
            continue
        distance = pose[i] - lo if v < 0 else hi - pose[i]
        if distance <= WALL_STOP_MARGIN:
            vector[i] = 0.0
            wall = True
        else:
            scale = min(1.0, max(0.0, (distance - 0.002) / 0.10))
            vector[i] = math.copysign(min(abs(v) * scale, (distance - 0.002) / period), v)
    return vector, '', wall


class WorkerGuard:
    """A fault latches for the current arm generation; UI heartbeat cannot clear it."""
    def __init__(self, profile):
        self.profile = profile
        self.fault_arm = None
        self.mode_started_at = 0.0
        self.arm = None

    def evaluate(self, command, now, pose, pose_at, safety, camera_state):
        if command.arm_sequence != self.arm:
            self.arm = command.arm_sequence
            self.mode_started_at = now
        if command.mode not in MOVING_MODES:
            return [0.0] * 6, '', False
        if self.fault_arm == command.arm_sequence:
            return None, '故障已鎖定，須重新啟動控制', False
        error = ''
        if not (math.isfinite(command.acceleration) and 0 < command.acceleration <= .25
                and math.isfinite(command.period) and 0 < command.period <= .10):
            error = '控制週期或加速度無效'
        elif command.mode in (Mode.AXIS_TEST, Mode.RETURN) and self.profile.target != URSIM_TARGET:
            error = '實體目標禁止自動測試／返回'
        elif not fresh(now, command.created_at, COMMAND_TIMEOUT):
            error = '控制指令過期'
        elif not fresh(now, pose_at, POSE_TIMEOUT):
            error = 'TCP 姿態回饋中斷'
        elif safety not in (1, 2):
            error = f'機械人安全模式 {safety}'
        elif command.mode == Mode.GESTURE and (
                not camera_state[0] or not fresh(now, camera_state[1], FRAME_TIMEOUT)
                or not fresh(now, command.frame_at, FRAME_TIMEOUT)):
            error = '影像回饋中斷或過期'
        elif command.mode == Mode.RETURN and now - self.mode_started_at > RETURN_TIMEOUT:
            error = '返回安全原點逾時'
        if error:
            self.fault_arm = command.arm_sequence
            return None, error, False
        velocity = command.velocity
        if command.mode == Mode.RETURN:
            velocity = cap_translational_velocity(velocity, RETURN_SPEED)
            if velocity is None:
                self.fault_arm = command.arm_sequence
                return None, '回程速度無效', False
        vector, error, wall = guarded_velocity(velocity, pose, self.profile.limits, command.period)
        if error or (wall and self.profile.target != URSIM_TARGET):
            self.fault_arm = command.arm_sequence
            return None, error or '實體工作區邊界已觸發', wall
        return vector, '', wall
