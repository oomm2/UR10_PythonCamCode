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
