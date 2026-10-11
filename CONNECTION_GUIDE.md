# UR10 and URSim Connection Guide

This guide uses placeholders only. Keep actual controller addresses, RTSP URLs, credentials, calibration values, telemetry, and installation notes in ignored local files; do not commit them.

> **Safety notice:** Connecting is not permission to move a robot. Keep robot control disabled while checking video and network connectivity. Physical deployment requires a site-specific risk assessment, controller safety configuration, verified workspace limits, and an accessible physical emergency stop.

## URSim in Docker on the local machine

Start URSim with its services exposed locally:

```bash
docker run --rm -it \
  -p 5900:5900 -p 6080:6080 \
  -p 29999:29999 -p 30001-30004:30001-30004 \
  universalrobots/ursim-e-series
```

Open `http://localhost:6080/vnc.html` to use PolyScope. In the app, select **URSim**, use `127.0.0.1`, then click **連接機械人**. Motion remains disabled until you explicitly enable it.

The selected target and address are locked for a connection session. Choosing URSim in the app does not prove that the entered address belongs to a simulator; verify the endpoint yourself.

Prepare power, brakes, and the controller operating mode manually in PolyScope before requesting motion. The app does not power on or release the brakes through the Dashboard server.

After connecting, wait for the RTDE control interface and communication watchdog to report ready before enabling control. If setup times out, stop and disconnect, correct the controller state, then reconnect.

## URSim in a virtual machine or another host

Use a local, non-public setting such as `<URSIM_IP>` in your ignored `settings.json`:

```bash
nc -vz <URSIM_IP> 30004
```

For NAT networking, forward host TCP port `30004` to guest TCP port `30004`, then use `127.0.0.1` in the app.

## Physical UR10

1. Connect the development computer and controller to an approved local network.
2. Read the controller address from the teach pendant and store it only in local `settings.json`.
3. Confirm reachability without enabling control:

   ```bash
   ping <ROBOT_IP>
   nc -vz <ROBOT_IP> 30004
   ```

4. Select **實體 UR10**, enter `<ROBOT_IP>`, and connect.
5. Keep motion disabled while checking camera commands. For e-Series, configure Remote Control / Remote mode through PolyScope before any controlled test.
6. Before physical control, measure and review the TCP base-frame X/Y/Z limits for the actual cell, then set the six `REAL_WORKSPACE_*` values locally. The public defaults are `None`, which intentionally lock physical control.

With control disabled, confirm the reviewed physical speed explicitly. Startup resets to 0.02 m/s; 0.50 m/s is only a configurable software ceiling, not a validated safe speed. Physical targets cannot run the automatic axis tests or return motions.

## Remote-control status check

After configuring Remote mode in PolyScope, inspect the status through the Dashboard server:

```bash
printf "is in remote control\n" | nc -w 2 <ROBOT_IP> 29999
```

A `true` reply only indicates remote mode. It does not replace the hardware checks and local authorization required before enabling motion.

## First-test checklist

- Start with camera-only UI behaviour, then URSim.
- Test one Cartesian axis at a time at the lowest practical speed.
- Verify all workspace boundaries in URSim before considering hardware.
- Keep an emergency stop accessible and keep people outside the workspace.
- Treat the software guard as supervisory only; use the controller’s configured safety functions and local risk assessment.

## Troubleshooting

| Message | What to check |
| --- | --- |
| 下限必須小於上限 | Correct the measured profile in `safety_config.py`; values are never silently swapped. |
| 控制介面未就緒 / 建立逾時 | PolyScope power, operating mode and connection; reconnect after correction. |
| TCP 姿態回饋中斷 | Controller timestamp progression, network and worker state. |
| 影像回饋中斷或過期 | Camera/RTSP input, inference time and stream buffering; re-open and recalibrate if needed. |
| STOP 未確認 | Inspect the controller directly. A UI request is not proof of a physical stop. |
| CSV 寫入失敗 | Disk permissions / capacity; logging failure is reported separately. |
