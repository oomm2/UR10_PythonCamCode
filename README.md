# UR10 Vision Control

[English](README.md) ｜ [廣東話](README_yue.md)

A Python / Qt desktop prototype that uses MediaPipe hand landmarks to produce conservative Cartesian velocity commands for Universal Robots URSim or a UR10 through RTDE.

> **Prototype only — not a certified safety system.** Start with the camera-only demo or URSim. Do not use this project for autonomous operation around people. A real robot requires a site-specific risk assessment, controller safety configuration, verified workspace limits, an accessible physical emergency stop, and qualified supervision.

## What is included

- PySide6 dashboard with camera preview and one-hand MediaPipe tracking
- Neutral-pose calibration, confidence filtering, smoothing, and gesture zones
- Conservative RTDE control path isolated in a worker process
- URSim demonstration workspace profile and speed limiting
- Fail-closed physical-robot profile: physical control remains locked until local workspace limits are reviewed and configured
- Safety-policy unit tests

## Quick start: camera-only demo

This mode does not need a robot or network connection.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python ur10_vision_qt_app.py
```

1. Click **開啟相機** and select camera source `0` if needed.
2. Hold an open palm steady and click **校準中立姿態**.
3. Observe the on-screen direction state. Keep robot control disabled.

On macOS, `ur-rtde` may require Boost:

```bash
brew install boost
export CMAKE_PREFIX_PATH="$(brew --prefix boost):${CMAKE_PREFIX_PATH:-}"
python -m pip install --no-cache-dir ur-rtde
```

## Local configuration

The repository contains only [settings.example.json](settings.example.json). Copy it locally before changing settings:

```bash
cp settings.example.json settings.json
```

`settings.json` is ignored by Git. Put any actual robot address, RTSP URL, credential, camera source, or personal tuning value **only** in that local file. Never commit it.

Public defaults are deliberately generic:

- URSim: `127.0.0.1`
- Physical robot IP: blank
- Camera: `0`
- RTSP example: `rtsp://<username>:<password>@<camera-host>:554/stream`

## Optional URSim demo

The application can connect to a local URSim instance after you configure URSim and explicitly enable control. See [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md). The source includes an URSim-only demonstration workspace; review movement directions and all boundaries in your own simulator before enabling any motion.

## Physical robot safety

The public `safety_config.py` keeps every `REAL_WORKSPACE_*` value as `None`. That intentionally blocks physical motion. Do not publish your calibrated limits, TCP information, controller address, logs, screenshots, or production configuration. Keep local measurements and settings outside version control.

The software workspace guard does not replace Universal Robots Safety Planes, protective stops, controller limits, emergency stops, or a documented risk assessment.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Project layout

```text
ur10_vision_qt_app.py  Qt UI, camera processing, gesture logic, RTDE client
ur10_vision_app.py     Compatibility launcher for the Qt application
safety_config.py       Safety profiles and velocity validation
settings.example.json  Public-safe configuration template
tests/                 Safety-policy unit tests
```

## License

Released under the [MIT License](LICENSE).
