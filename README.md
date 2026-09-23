# UR10 FYP Vision and Monitoring Suite

[English](README.md) ｜ [廣東話](README_yue.md)

**Documentation synchronised: 2026-09-23**

A privacy-conscious Final Year Project suite for Universal Robots UR10 / URSim. It combines two deliberately separate modules:

| Module | Purpose | Robot-control capability |
| --- | --- | --- |
| [Vision Controller](#vision-controller) | Qt dashboard that maps MediaPipe hand gestures to conservative Cartesian velocity commands. | **Yes, opt-in only.** Physical control is fail-closed by default. |
| [Read-only Monitor](#read-only-monitor) | Local RTDE-output telemetry dashboard with a 3D UR10 view. | **No.** It has no RTDE input recipe, URScript, or motion-command path. |

> **Academic prototype — not a certified safety system.** Start with camera-only behaviour, then URSim. Never use either module as a replacement for a site-specific risk assessment, controller safety configuration, protective stops, or a physical emergency stop.

## FYP overview

This project explores hand-gesture interaction for robot control while separating the control and observation paths:

```text
Camera → MediaPipe landmarks → calibration and gesture filter → safety gate → RTDE controller
                                                               ↘
                                                                optional read-only telemetry monitor → local browser dashboard
```

The controller is responsible for commands; the monitor only observes RTDE output telemetry. The monitor may report unusual state but cannot block, stop, or move the robot.

- [FYP overview and scope](docs/fyp-overview.md)
- [廣東話 FYP 概覽](docs/fyp-overview_yue.md)
- [Connection and commissioning guide](CONNECTION_GUIDE.md)
- [Security and privacy policy](SECURITY.md)

## Vision Controller

The supported controller application is `ur10_vision_qt_app.py`. `ur10_vision_app.py` is a compatibility launcher, not the primary implementation.

### Features

- PySide6 dashboard with camera preview and single-hand MediaPipe tracking
- Neutral-pose calibration, confidence filtering, smoothing, and directional gesture zones
- Worker-process RTDE control path to isolate native connection failures from the UI
- URSim demonstration workspace profile and conservative speed defaults
- Physical profile defaults to unset workspace limits, so physical control is locked until locally reviewed values exist
- Safety-policy unit tests

### Camera-only quick start

No robot or network connection is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python ur10_vision_qt_app.py
```

1. Click **開啟相機** and select camera source `0` if necessary.
2. Hold an open palm steady and click **校準中立姿態**.
3. Observe the on-screen direction state while robot control stays disabled.

On macOS, `ur-rtde` may require Boost:

```bash
brew install boost
export CMAKE_PREFIX_PATH="$(brew --prefix boost):${CMAKE_PREFIX_PATH:-}"
python -m pip install --no-cache-dir ur-rtde
```

### Local controller configuration

Copy the public-safe template before changing settings:

```bash
cp settings.example.json settings.json
```

`settings.json` is ignored by Git. Store actual robot addresses, RTSP URLs, credentials, camera settings, calibration, and personal tuning values only in this local file. The checked-in defaults are examples only:

- URSim: `127.0.0.1`
- Physical robot address: blank
- Camera: `0`
- RTSP format: `rtsp://<username>:<password>@<camera-host>:554/stream`

## Read-only Monitor

`monitor/` contains a separate Windows-oriented local telemetry dashboard. It reads RTDE **outputs only** and provides a browser dashboard, trends, optional local recordings, and a 3D UR10 model. It cannot issue URScript, configure RTDE inputs, or send robot motion commands.

### Monitor quick start

```powershell
cd monitor
Copy-Item config.example.json config.json
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Open `http://127.0.0.1:8080`. Keep the HTTP server on loopback unless a trusted local network deployment has been explicitly reviewed. Put the actual controller address and any heartbeat token only in ignored `monitor/config.json` and `monitor/.env`.

See [monitor/README.md](monitor/README.md) for its API, recording, heartbeat, and dashboard details. Recordings, screenshots, exported reports, and telemetry may contain deployment or personal data; review them before sharing.

## Safety and data handling

- Test controller behaviour with camera-only mode and URSim before any hardware test.
- `safety_config.py` leaves every `REAL_WORKSPACE_*` value as `None`; this intentionally locks physical control in the public build.
- Software workspace checks cover TCP behaviour only. They do not replace the controller's safety functions or account for every link, tool, payload, or collision hazard.
- Do not commit actual IP addresses, RTSP URLs, passwords, certificates, workspace measurements, tool/TCP data, logs, camera captures, recordings, or assistant-session artifacts.
- The monitor is observational, not a safety device. Its read endpoints must never be exposed directly to the internet.

## Tests

### Vision controller

```bash
python -m unittest discover -s tests -v
```

### Read-only monitor

```powershell
cd monitor
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test "tests/js/*.test.mjs"
```

The monitor tests and smoke test use local loopback only; they do not open a robot connection. CI runs controller and monitor checks independently.

## Repository layout

```text
ur10_vision_qt_app.py  Supported Qt vision controller
safety_config.py       Controller safety profiles and velocity validation
settings.example.json  Public-safe controller configuration template
monitor/               Read-only RTDE monitor, dashboard, model assets, and tests
docs/                  FYP overview in English and Cantonese
tests/                 Controller safety-policy tests
```

## License and third-party material

The root [MIT License](LICENSE) applies to the root controller project and documentation, except where a more specific notice applies. The integrated monitor's original source code is **not** relicensed by the root MIT license; see [monitor/LICENSE.md](monitor/LICENSE.md). Its bundled Three.js, urdf-loader, and Universal Robots model assets retain their own notices in [monitor/THIRD_PARTY_NOTICES.md](monitor/THIRD_PARTY_NOTICES.md).
