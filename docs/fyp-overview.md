# FYP overview

[English](fyp-overview.md) ｜ [廣東話](fyp-overview_yue.md)

**Documentation synchronised: 2026-09-23**

## Project aim

This Final Year Project investigates a conservative hand-gesture interface for a Universal Robots UR10 / URSim workflow. The project separates command generation from observation so that a monitoring dashboard cannot influence robot motion.

## Modules and responsibilities

| Module | Responsibility | Boundary |
| --- | --- | --- |
| Vision Controller | Turns an observed hand gesture into a filtered Cartesian velocity request after local safety checks. | May control URSim or a robot only after explicit user action and preconditions are satisfied. |
| Read-only Monitor | Displays RTDE output telemetry, trends, and a local 3D model. | Cannot send URScript, set RTDE inputs, or issue a motion command. |

## Gesture-control workflow

1. The camera captures a frame.
2. MediaPipe extracts one hand's landmarks.
3. The application calibrates a neutral palm position and size, then applies confidence, smoothing, dead-zone, and confirmation rules.
4. A single directional request is formed: `LEFT`, `RIGHT`, `UP`, `DOWN`, `FWD`, `BACK`, or immediate `STOP`.
5. The controller checks session identity, control enablement, fresh RTDE feedback, safety state, workspace bounds, and velocity limits before dispatching an RTDE velocity command.
6. The monitor, if running, independently reads output telemetry for observation only.

## Demonstration levels

| Level | Scope | Expected environment |
| --- | --- | --- |
| Camera-only | Gesture recognition and UI feedback with robot control disabled. | Local camera only. |
| URSim | Verify axes, workspace guards, and fault behaviour in simulation. | Local or separately hosted URSim configured by the operator. |
| Physical robot | Controlled, supervised validation after local commissioning. | Approved workcell, controller safety settings, risk assessment, and emergency stop. |

## Safety limitations

The implementation is a prototype, not a safety-rated controller. The controller's software workspace guard is supervisory and considers TCP information only; it does not prove clearance for all robot links, tooling, payload, or surrounding objects. Physical control is intentionally unavailable in the public configuration because `REAL_WORKSPACE_*` values are unset.

The monitor is not a safety device. It can report telemetry but cannot intervene in robot movement.

## Privacy and reproducibility

Tracked files contain only public-safe examples. Real addresses, RTSP credentials, workspace measurements, tool/TCP details, calibration data, screenshots, recordings, logs, and exports belong in ignored local files and must be reviewed before sharing.

For setup and test procedures, return to the [root README](../README.md).