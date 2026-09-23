"""Pure safety profiles and final velocity sanitising for the Qt controller.

The physical workspace intentionally starts unset. Edit the REAL_WORKSPACE_* values
below only after measuring and reviewing the installation in the robot base frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

URSIM_TARGET = "URSim"
REAL_TARGET = "實體 UR10"
UNKNOWN_TARGET = "未知目標"
DEFAULT_SPEED = 0.02
MAX_TRANSLATIONAL_SPEED = 0.50
RETURN_SPEED = 0.005

# URSim demonstration profile. Keep these values conservative and review them in
# URSim before any test movement.
URSIM_WORKSPACE = (-1.20, 1.20, -1.20, 1.20, 0.00, 1.30)
URSIM_SAFE_ORIGIN = (0.40, 0.00, 0.40)

# Physical installation worksheet: leave every value None until the actual
# base-frame TCP limits have been measured and reviewed.
REAL_WORKSPACE_X_MIN: Optional[float] = None
REAL_WORKSPACE_X_MAX: Optional[float] = None
REAL_WORKSPACE_Y_MIN: Optional[float] = None
REAL_WORKSPACE_Y_MAX: Optional[float] = None
REAL_WORKSPACE_Z_MIN: Optional[float] = None
REAL_WORKSPACE_Z_MAX: Optional[float] = None
REAL_WORKSPACE = (
    REAL_WORKSPACE_X_MIN, REAL_WORKSPACE_X_MAX,
    REAL_WORKSPACE_Y_MIN, REAL_WORKSPACE_Y_MAX,
    REAL_WORKSPACE_Z_MIN, REAL_WORKSPACE_Z_MAX,
)
REAL_SAFE_ORIGIN = None


@dataclass(frozen=True)
class WorkspaceProfile:
    target: str
    limits: Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]
    safe_origin: Optional[Tuple[float, float, float]]
    auto_return: bool


URSIM_PROFILE = WorkspaceProfile(URSIM_TARGET, URSIM_WORKSPACE, URSIM_SAFE_ORIGIN, True)
REAL_PROFILE = WorkspaceProfile(REAL_TARGET, REAL_WORKSPACE, REAL_SAFE_ORIGIN, False)


def workspace_profile(target: str) -> WorkspaceProfile:
    if target == URSIM_TARGET:
        return URSIM_PROFILE
    if target == REAL_TARGET:
        return REAL_PROFILE
    return WorkspaceProfile(UNKNOWN_TARGET, (None, None, None, None, None, None), None, False)


def workspace_is_configured(limits: Sequence[Optional[float]]) -> bool:
    if len(limits) != 6 or any(value is None or not math.isfinite(float(value)) for value in limits):
        return False
    return all(float(lower) < float(upper) for lower, upper in zip(limits[::2], limits[1::2]))


def speed_for_profile(target: str, saved_value: object = None) -> float:
    """Return the startup speed; never trust a persisted physical speed."""
    if target == REAL_TARGET:
        return DEFAULT_SPEED
    try:
        value = float(saved_value)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if not math.isfinite(value):
        return DEFAULT_SPEED
    return max(0.0, min(MAX_TRANSLATIONAL_SPEED, value))


def cap_translational_velocity(velocity: Sequence[object], cap: float = MAX_TRANSLATIONAL_SPEED) -> Optional[list[float]]:
    """Fail closed on malformed/nonfinite vectors and cap XYZ Euclidean norm."""
    try:
        values = [float(value) for value in velocity]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        return None
    if not math.isfinite(cap) or cap < 0.0:
        return None
    norm = math.sqrt(sum(value * value for value in values[:3]))
    if not math.isfinite(norm):
        return None
    if norm > cap and norm > 0.0:
        scale = cap / norm
        values[:3] = [value * scale for value in values[:3]]
    return values


def session_matches(identity: Optional[Tuple[str, str, int]], target: str) -> bool:
    """Use the captured connection identity, rather than a mutable UI label."""
    return identity is not None and identity[0] == target


def motion_session_valid(identity: Optional[Tuple[str, str, int]], connected: bool, enabled: bool) -> bool:
    return identity is not None and connected and enabled


def physical_speed_selection_required(target: str, selected: bool, enabled: bool) -> bool:
    return target == REAL_TARGET and not enabled and not selected
