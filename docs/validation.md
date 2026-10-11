# Validation scope

The automated suite uses fake RTDE interfaces, synthetic frames, temporary settings and an offscreen Qt window. It does not connect to a robot, open a camera or exercise physical motion.

Coverage includes STOP priority, command/pose/frame expiry without Qt polling, fault latching and explicit rearm, cached controller timestamps, setup timeout timing, failed STOP reporting, all six workspace walls, invalid vectors and bounds, real-target automatic-mode rejection, return speed/deadline/completion, mode ownership, calibration-relative zones, temporal landmark checks, camera-source migration, settings persistence, checklist construction, model-setting application, pose-based axis-test completion, Escape STOP with a modal dialog open and camera startup exclusion during device scanning. UI coverage also checks calibration/fault progress without automatic enabling, gesture versus dispatched-command labels, pending STOP labels and accessible STOP on every tab at 1000 × 720.

Run:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -B -m unittest discover -s tests -v
```

## Manual URSim acceptance checks (not performed by offline tests)

- Confirm camera preview stays responsive during scanning / blocked RTSP input; scanner must time out and shutdown must remain bounded.
- Calibrate off-centre; verify neutral STOP and all six directions. Change dead zone, then apply Detection/Tracking and confirm new calibration is required.
- Verify ready, gesture, axis-test, return and fault displays. During test/return, move or hide the hand and confirm camera callbacks do not take ownership.
- Verify explicit STOP and Esc. Inspect actual TCP response separately from command acceptance.
- Disconnect the camera, stall inference and interrupt feedback; confirm worker faults even while Qt is busy.
- Test all six walls, inward motion, return cap, return completion and return timeout. Verify the whole return path before use.
- Confirm tests finish disabled, old directions do not replay, and faults need deliberate re-enable.
- Repeat with the installed controller / ur-rtde versions and retain observations. No physical stopping-distance or collision-safety claim follows from passing unit tests.

The physical workspace requires measured installation data. The public physical workspace stays unset; invalid values are rejected with axis-specific diagnostics.

## Local verification — 2026-10-12

- **57 tests passed** on the existing macOS / Apple Silicon Python 3.9.6 environment.
- The transport suite includes an actual spawned worker using fake SDKs: it accepts a command, then stops and latches a fault after the parent stops publishing. No robot sockets are opened.
- The vision suite spawns capture/inference against a generated black-frame AVI, verifies output, then verifies end-of-stream failure. No physical camera is opened.
- Offscreen Qt screenshots of light and dark layouts were inspected at 1280 × 820 and 1000 × 720 across operation, test, settings and log tabs. Panel content scrolls independently; the preview, status and STOP remain visible. Calibration is beside the preview rather than beside the camera-open button. The saved [light preview](ui-preview.png) and [dark preview](ui-preview-dark.png) show the disconnected, control-disabled workspace. Theme switching and saved preference reload are covered by the Qt tests.
- Python syntax parsing, `git diff --check` and the macOS launcher syntax check passed.
- All processes use a shared-epoch monotonic clock. The spawned test caught and now covers the per-process clock offset in this older macOS Python runtime.
- The existing MediaPipe installation carries an x86_64 wheel metadata tag on this ARM runtime, so `pip check` reports a platform-tag warning. Its installed ARM loader and synthetic inference work; the environment was not rewritten. Use a clean Python 3.11 environment for installation validation on a target machine.
- Remote CI, Windows launcher execution, real-camera/RTSP latency, URSim motion and physical hardware were **not** exercised during this change. They remain deployment acceptance checks, not automated-test claims.
