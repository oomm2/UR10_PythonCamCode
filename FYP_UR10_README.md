# FYP UR10 Vision Control

This FYP prototype translates calibrated monocular hand gestures into conservative XYZ Cartesian velocity requests for URSim / UR10. It includes camera-only preview, persistent local settings, worker-isolated RTDE control, TCP telemetry, CSV logging, workspace interlocks and URSim axis tests.

[README.md](README.md) is the authoritative setup, control and safety description. [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md) covers controller preparation. [docs/validation.md](docs/validation.md) records the validation scope. The separate read-only RTDE dashboard is documented in [monitor/README.md](monitor/README.md). Keeping one operational guide avoids divergent copies of speed limits and calibration instructions.

## Architecture

```text
Camera / RTSP -> vision process -> latest frame + landmarks -> Qt gesture mapping
                       |                                      |
               capture health                                 v
                       +---------------------------> RTDE worker mailbox
                                                       |
                              mode / freshness / workspace / watchdog checks
                                                       |
                                                       v
                                                   URSim / UR10
```

Qt owns presentation and deliberate mode changes. The vision process owns capture and MediaPipe. The RTDE process independently enforces freshness and the TCP envelope, forwards accepted commands and reports feedback. One command owner is active at a time; tests and return motions cannot be overridden by camera callbacks.

## Demonstration procedure

1. Start camera-only, apply detection settings, calibrate and inspect all six directions.
2. Prepare URSim manually in PolyScope, connect, then wait for the RTDE control interface and watchdog to become ready.
3. Enable control explicitly. Test one mode at a time: gestures, axis tests or supervised wall return.
4. Exercise STOP, missing hand, camera failure, stale feedback and disconnect. Faults must disable control and require deliberate re-enabling.
5. Capture evidence with known test inputs. CSV intent and accepted commands are different from measured TCP movement; do not infer end-to-end latency from an intent row alone.

## Limitations and further research

- Palm-size depth is affected by orientation and perspective. Recognition heuristics require evaluation across users, lighting and cameras.
- The software workspace checks the TCP only; it is not full-arm collision avoidance or certified robot safety.
- Physical bounds and allowed speed require installation-specific measurement and review. Existing invalid bounds remain blocked.
- Physical stopping time, stopping distance, input-to-motion latency and gesture accuracy have not been established by the offline tests.
- The gripper reference material under local `tmp/` is not an implemented gripper controller.
- Future work includes recorded-input replay datasets, end-to-end latency measurements, usability evaluation and a separately reviewed gripper interface.

The project is an academic development and demonstration prototype. It must not be treated as an autonomous controller suitable for use around people.
