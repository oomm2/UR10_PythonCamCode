# UR10——MonitorUsingPython

Read-only UR10 RTDE telemetry monitor for Windows.

Intended GitHub repository slug: `UR10--MonitorUsingPython`.
All `192.0.2.x` addresses below are documentation examples, not live deployment addresses.
The Mac Vision program remains the only controller: this app uses an RTDE **output-only** recipe and never sends URScript, motion commands, or RTDE input registers.

The design stays read-only on purpose — there is no input recipe, no URScript and no motion command anywhere in this repository.

## Architecture

```text
Mac: Vision hand tracking  ──(your control path)──>  URSim / UR controller
Windows: this monitor       ──(RTDE outputs only)──> URSim / UR controller
VirtualBox: Bridged Adapter; URSim IP = 192.0.2.10
```

The monitor can run at the same time as the Mac control client because it only subscribes to telemetry.

## Install (Windows PowerShell)

```powershell
cd path\to\UR10--MonitorUsingPython
Copy-Item config.example.json config.json
# Edit config.json for your robot and monitor before starting the server.
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The RTDE dependency is Universal Robots' official `RTDE_Python_Client_Library`, pinned to a known upstream commit for repeatable installs.

The browser dashboard uses pinned local copies of Three.js and `urdf-loader`; it does not need an internet connection to load the 3D view.

The example binds HTTP to loopback by default. For a trusted LAN heartbeat, explicitly set
`http_host` and `monitor_advertised_ip` for your own network. Do not expose this server to the internet.

### Optional heartbeat authentication

Copy `.env.example` to `.env` and generate a new private token as described there. Set the
same token on the controller. Never commit `.env` or use a test token in a real deployment.
Without a token, authenticated write endpoints remain disabled; read-only telemetry still works.

For development checks (create the local `config.json` from the example first):

```powershell
# Python unit tests (no robot connection is opened)
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Lint and type checks
.\.venv\Scripts\python.exe -m pip install ruff mypy
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy

# Browser-side unit tests (Node 20+; no npm install needed)
node --test "tests/js/*.test.mjs"

# Regenerate the UR10 URDF and its meshes from vendor/
.\.venv\Scripts\python.exe tools\build_models.py

# End-to-end smoke test: boots the server on a throwaway port and exercises
# every endpoint. Opens no RTDE connection and contacts no robot.
.\.venv\Scripts\python.exe tools\smoke_test.py
```

## Run

1. Start URSim in VirtualBox with **Bridged Adapter** and set `robot_ip` in `config.json` to its actual address.
2. Test the RTDE port:

   ```powershell
   Test-NetConnection <ROBOT_IP> -Port 30004
   ```

3. Start the monitor by double-clicking `start_monitor.bat`, or run:

   ```powershell
   .\.venv\Scripts\python.exe server.py
   ```

4. Open `http://127.0.0.1:8080` in Chrome/Edge.

## What it reads

Every field below is **output-only**. No input register, URScript call, or motion command is ever sent.

| Field | Notes |
|---|---|
| `timestamp` | Controller clock, used as the live-telemetry gate |
| `actual_q` | Six actual joint angles |
| `actual_TCP_pose` | TCP position and orientation |
| `actual_TCP_speed` | TCP velocity; linear and angular magnitudes are derived |
| `actual_joint_current` | Per-joint current in amps |
| `joint_temperatures` | Per-joint temperature in °C; the dashboard warns at 60 °C and flags at 70 °C |
| `robot_mode` | Shown with a status-dependent colour |
| `safety_mode` | Shown with a severity-dependent colour |
| `speed_scaling` | Percentage of programmed speed |
| `actual_digital_input_bits` | Reported as a hexadecimal mask |
| `actual_digital_output_bits` | Reported as a hexadecimal mask |

The Digital Twin uses the official Universal Robots UR10 visual DAE meshes and a flattened URDF (`static/ur10.urdf`). The local URDF loader applies the real UR10 link/joint origins, and the model follows the six live joint values without clamping measured poses. Joint bars use nominal ROS planning ranges from `static/joint_limits.json`; J3's ±180° range is a planning/display hint, not a controller safety limit. Values outside the nominal range remain visible and are marked amber.

## Dashboard behaviour

- **Live stream first.** The dashboard subscribes to `GET /api/stream`, a Server-Sent Events feed published at `event_stream_frequency` (default 10 Hz). If `EventSource` is unavailable or the stream drops, it falls back to `GET /api/state` polling automatically and says so in the Events log.
- **Trends.** Joint angles, tool speed and joint current are charted in the browser over a bounded 600-sample window. Nothing is uploaded and the buffer clears on reload.
- **3D shortcuts.** Click the 3D view, then `R` resets the camera and `G` toggles the reference grid.
- **Recording.** The dashboard can stream telemetry straight to a CSV file on the monitor host. See below.
- **Freeze.** `Space` pauses every panel so you can read a moment without the numbers moving. The stream keeps running in the background; the header shows a `FROZEN` pill while it is on.
- **Jump alert.** If any joint moves more than `25°` between two samples the affected joint is flagged red in the Joints card and a banner appears. Because the monitor is read-only it can only _report_ a jump, never block one.
- **Flight recorder.** The last ~20 000 TCP points are kept in a ring buffer on the host and drawn as a 3D trail. `GET /api/trajectory` returns them, `POST /api/trajectory/clear` (token-gated) empties the buffer.
- **Replay.** Press `P` to scrub backwards and forwards through the recorded trail at 0.25×–4×, with a moving marker on the robot model.
- **Joint limit rings.** `L` toggles a ring per joint that turns amber when that joint leaves its nominal planning range.
- **Latency.** The Link Diagnostics card shows send-to-receive latency once the Mac controller heartbeats include a `sent_at` field. See _Latency_ below.
- **Baseline comparison.** A finished recording can be loaded as a baseline; the chart then draws the baseline and live curves on a shared time axis.
- **Theme.** The toggle switches between the dark and light palettes and remembers the choice in `localStorage`.
- **Snapshot / report.** `C` exports the current view. See _Screenshot and report_ below.
- **Help.** `?` opens the keyboard-shortcut card; every shortcut is listed there.

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Freeze / resume every panel |
| `S` | Toggle the 3D trail |
| `1`–`6` | Highlight a single joint |
| `L` | Toggle joint-limit rings |
| `J` | Toggle the jump alert |
| `P` | Play / pause replay |
| `C` | Export a snapshot or report |
| `T` | Toggle the theme |
| `R` | Reset the 3D camera |
| `G` | Toggle the reference grid |
| `?` | Show or hide the shortcut card |

## Latency

The Link Diagnostics card measures how long a heartbeat takes to arrive. Two modes are
available via `latency_mode`:

| Mode | What it measures | Requirement |
|---|---|---|
| `monotonic` (default) | Send-to-receive time using one clock, so it is honest on a single host | The sender puts `sent_at` (seconds, monotonic-derived) in the heartbeat body |
| `clock_sync` | Full Mac → Windows path using wall clocks | The two machines must be NTP-synced; drift is reported alongside the numbers |

Samples outside `0 s – 60 s` are discarded as clock noise and counted in `rejected`.
`GET /api/latency` returns `last_ms`, `mean_ms`, `p95_ms`, `min_ms`, `max_ms`, `samples`,
`rejected` and the current `mode`.

## Diagnostics

`GET /api/diagnostics` reports connection health gathered while the monitor ran: total
samples received, connects, reconnects, the reason for the last disconnect, subscriber
counts and the number of dropped SSE frames. It is read-only statistics — nothing is
written back to the controller.

## Screenshot and report

`C` (or the Snapshot button) exports the 3D view as a PNG. If a recording is in progress
the same action can produce a self-contained HTML report containing the live values, the
recent trend tables and the rendered 3D image. Both files download in the browser; nothing
is uploaded.

## HTTP API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/state` | no | Full telemetry snapshot (state, latency, trajectory status, subscribers) |
| `GET` | `/api/stream` | no | Server-Sent Events telemetry feed |
| `GET` | `/api/config` | no | Non-secret configuration |
| `GET` | `/api/trajectory` | no | Flight-recorder points |
| `POST` | `/api/trajectory/clear` | token | Empty the flight recorder |
| `GET` | `/api/latency` | no | Latency statistics |
| `GET` | `/api/diagnostics` | no | Connection and subscriber counters |
| `GET` | `/api/baseline` | no | Baseline timeline for comparison |
| `POST` | `/api/control/heartbeat` | token | Controller heartbeat (also carries structured telemetry) |
| `GET` | `/api/recording` | no | Recording status, duration and file list |
| `POST` | `/api/recording/start` | token | Start a CSV recording |
| `POST` | `/api/recording/stop` | token | Stop the active recording |
| `GET` | `/api/recording/download/<name>` | no | Download a `.csv` from `recording_directory` |

Token-gated endpoints expect the shared secret in `X-Heartbeat-Token`.

## Recording telemetry to CSV

Recording is off until started, and it writes on the monitor host — never on the robot.

```powershell
# Start (requires the heartbeat token)
curl.exe -X POST http://127.0.0.1:8080/api/recording/start `
  -H "X-Heartbeat-Token: $env:UR_MONITOR_HEARTBEAT_TOKEN"

# Check status, duration and row count
curl.exe http://127.0.0.1:8080/api/recording

# Stop
curl.exe -X POST http://127.0.0.1:8080/api/recording/stop `
  -H "X-Heartbeat-Token: $env:UR_MONITOR_HEARTBEAT_TOKEN"
```

Files land in `recording_directory` (default `recordings/`) and are downloadable from
`GET /api/recording/download/<name>.csv`. Only plain `.csv` file names inside that directory resolve; anything else returns 404.

Recording stops by itself after `recording_max_seconds` (default 3600) and reports `auto_stopped: true`.
The CSV header is fixed and documented in `recording.py` as `RECORDING_COLUMNS`.

> Recording is gated by the same secret as the heartbeat endpoint, because it writes files to the host. It never touches the robot.

## Files

- `server.py` — HTTP handler and process entry point; wires the modules below together
- `monitor_config.py` — `config.json` / `.env` loading, validation and caching
- `telemetry_state.py` — shared state store, flight-recorder trajectory buffer and latency tracker
- `rtde_client.py` — output-only RTDE reader thread and reconnect loop
- `events.py` — Server-Sent Events broker and state publisher
- `recording.py` — CSV recording sessions and the fixed column set
- `controller.py` — Mac controller heartbeat store, throttling and structured telemetry
- `monitor.xml` — output-only RTDE recipe
- `static/index.html` — dashboard shell and styles (dark and light themes)
- `static/app.js` — telemetry dashboard, charts, validation, freeze, replay, shortcuts and export
- `static/scene.js` — local Three.js / URDF digital twin with trail, rings, camera shortcuts and image capture
- `static/telemetry.js` — testable polling, SSE client, state validation, history, chart and trajectory helpers
- `static/replay.js` — timeline scrubber used by the trajectory replay
- `static/theme.js` — theme controller shared by the dashboard
- `static/vendor/` — pinned local Three.js and URDF loader modules with licenses
- `static/ur10.urdf` — flattened UR10 kinematic hierarchy used by the browser
- `static/joint_limits.json` — nominal bar ranges for the six joints
- `static/meshes/ur10/` — official UR10 visual meshes
- `config.example.json` — public configuration template; copy to ignored `config.json` for local settings
- `.env` — local heartbeat secret (ignored; copy `.env.example`)
- `start_monitor.bat` — one-click launcher
- `tests/` — Python unit tests
- `tests/js/` — browser-module unit tests (Node's built-in runner)
- `tools/build_models.py` — regenerates `static/ur10.urdf` and its meshes from `vendor/`
- `tools/smoke_test.py` — end-to-end HTTP smoke test against a throwaway server
- `pyproject.toml` — ruff and mypy configuration

## Robot model

The digital twin renders a **UR10**. `robot_model` in `config.json` accepts only `UR10`,
which matches the arm this project monitors. Run `tools/build_models.py` once to
(re)generate `static/ur10.urdf` and `static/meshes/ur10/` from
`vendor/Universal_Robots_ROS2_Description`.

To support another arm later, add its `urXY` -> `URXY` entry to `MODELS` in
`tools/build_models.py`, add the same name to the allowed set in
`monitor_config._validate_config`, and re-run the script — it also prunes any URDF or
mesh folder left behind for a model you removed. This export contains only the UR10 build inputs. Supporting another model requires
separately obtaining and reviewing its upstream files and applicable licenses.

## Configuration

All values live in `config.json` and are validated on load. A malformed edit keeps the last
known-good configuration in service and logs a warning; the first load must be valid.

| Key | Default | Meaning |
|---|---|---|
| `robot_ip` | `192.0.2.10` | URSim / controller address |
| `rtde_port` | `30004` | RTDE port |
| `rtde_frequency` | `25.0` | Output recipe frequency in Hz |
| `http_host` | `127.0.0.1` | Safe local default; deliberately enable a LAN bind only on a trusted network |
| `http_port` | `8080` | HTTP port |
| `monitor_advertised_ip` | `192.0.2.20` | Address shown to the Mac; empty means "derive from the route" |
| `controller_heartbeat_timeout` | `4.0` | Seconds before a heartbeat is considered stale |
| `cors_allowed_origins` | `[]` | Extra exact browser origins; wildcards are rejected |
| `heartbeat_max_failures` | `5` | Failed token attempts from one IP before lockout |
| `heartbeat_lockout_seconds` | `60.0` | Lockout duration; `0` disables lockout |
| `event_stream_frequency` | `10.0` | SSE publish rate in Hz |
| `recording_directory` | `recordings` | Relative directory for CSV output |
| `recording_max_seconds` | `3600.0` | Hard cap on recording length |
| `robot_model` | `UR10` | Digital-twin model. Only `UR10` is built and accepted |
| `latency_mode` | `monotonic` | `monotonic` (one clock) or `clock_sync` (NTP-synced hosts) |
| `trajectory_capacity` | `20000` | Flight-recorder ring-buffer size in points |
| `trajectory_sample_hz` | `25.0` | Maximum rate at which trail points are stored |

## Network notes

Use **Bridged Adapter** in VirtualBox if the Windows host and Mac need to reach the URSim VM directly. The monitor connects directly to URSim; it is not a proxy for the Mac Vision controller.

If the VM gets a new address, edit `config.json` and change `robot_ip`.

## See the active Mac controller IP

The dashboard has a **Control Client** card. It shows an IP reliably when the Mac Vision controller sends a token-authenticated heartbeat once per second to the monitor. Windows records the HTTP source address itself, so the displayed IP is not a value supplied by the app. A heartbeat identifies the sender process; it is not proof of an active robot motion command.

- Windows monitor LAN address: `192.0.2.20`
- Heartbeat endpoint: `http://192.0.2.20:8080/api/control/heartbeat`
- Integration guide: `CONTROLLER_HEARTBEAT.md`
- Ready-to-run Mac helper: `mac_controller_heartbeat.py`
- Heartbeat secret: `UR_MONITOR_HEARTBEAT_TOKEN` in ignored `.env` or the process environment — do not commit or share it.

RTDE telemetry alone does **not** expose a list of other client IP addresses. If the Mac app does not send a heartbeat, the card deliberately says `Unknown` instead of guessing.

The example listens locally. To let the Mac reach the heartbeat API, explicitly enable a LAN bind. If the Mac cannot connect, allow inbound TCP 8080 for the private network in Windows Firewall.

Repeated wrong tokens from one address return `429` with a `Retry-After` header once
`heartbeat_max_failures` is reached. During an active lockout, even a correct token must wait for expiry. After expiry,
a successfully authenticated request clears the failure counter.

## Minimal UR10 model sources

This export includes only the UR10 configuration and visual mesh inputs required by
`tools/build_models.py`. No upstream Git metadata or unrelated model families are included.
The browser uses `static/` at runtime; retain the minimal vendor inputs for regeneration and tests.
Mesh authoring-tool, contributor and creation/modification metadata have been removed from
both copies without changing geometry, units or coordinate axes. Upstream license texts are retained.

## License and third-party notices

No license grant has been selected for the original project code. Public availability is not an
open-source license. The owner must select a license before claiming an open-source release.
Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Safety and deployment limitations

This is a monitoring aid, not a safety device. It cannot stop or prevent robot motion.
Read endpoints, including telemetry and recording downloads, are not authenticated. CORS is not
an access-control boundary for non-browser clients. Use only on a trusted network and do not
publish live recordings, screenshots or reports without reviewing them for deployment metadata.

See [PUBLIC_RELEASE_CHECK.md](PUBLIC_RELEASE_CHECK.md) for preparation checks and remaining blockers.
