#!/usr/bin/env python3
"""Read-only RTDE reader for the UR monitor.

The connection only ever configures an *output* recipe. It never registers an
input recipe, never sends URScript and never issues a motion command, which is
the whole point of this project: the dashboard watches the robot without being
able to influence it.

The reader reconnects automatically, publishing link diagnostics (reconnect
count, last drop reason, uptime) as it goes so the dashboard can show more than
a bare connected/disconnected flag.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from monitor_config import RECIPE_PATH
from telemetry_state import StateStore, TrajectoryBuffer

LOGGER = logging.getLogger(__name__)

try:
    import rtde.rtde as rtde
    import rtde.rtde_config as rtde_config
except ImportError:  # pragma: no cover - exercised by the missing-package branch
    rtde = None
    rtde_config = None

RECONNECT_DELAY_SECONDS = 2.0


class RTDEMonitor(threading.Thread):
    """Read-only RTDE output client with automatic reconnect."""

    def __init__(
        self,
        config: dict[str, Any],
        state: StateStore,
        trajectory: TrajectoryBuffer | None = None,
        recorder: Any | None = None,
        route_local_ip: Any | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.config = config
        self.state = state
        self.trajectory = trajectory
        self.recorder = recorder
        self.route_local_ip = route_local_ip
        self.stop_event = threading.Event()
        self.name = "rtde-monitor"

    def run(self) -> None:
        host = str(self.config["robot_ip"])
        port = int(self.config.get("rtde_port", 30004))
        frequency = float(self.config.get("rtde_frequency", 25.0))
        monitor_ip = str(self.config.get("monitor_advertised_ip") or "")
        if not monitor_ip and self.route_local_ip is not None:
            monitor_ip = str(self.route_local_ip(host))
        self.state.update(touch=False, robot_ip=host, monitor_ip=monitor_ip)
        if rtde is None or rtde_config is None:
            self.state.update(
                touch=False,
                error="RTDE package missing. Run: py -m pip install -r requirements.txt",
            )
            LOGGER.error("RTDE package missing")
            return

        while not self.stop_event.is_set():
            connection = None
            try:
                LOGGER.info("Connecting read-only RTDE client to %s:%s", host, port)
                cfg = rtde_config.ConfigFile(str(Path(RECIPE_PATH)))
                output_names, output_types = cfg.get_recipe("monitor")
                connection = rtde.RTDE(host, port)
                connection.connect()
                connection.get_controller_version()
                if not connection.send_output_setup(output_names, output_types, frequency):
                    raise ConnectionError("RTDE output recipe was rejected")
                if not connection.send_start():
                    raise ConnectionError("RTDE synchronization did not start")

                self.state.update(touch=False, connected=True, error="")
                self.state.record_connect()
                LOGGER.info("RTDE connected; outputs: %s", output_names)
                while not self.stop_event.is_set():
                    data = connection.receive()
                    if data is None:
                        raise ConnectionError("RTDE connection closed")
                    values = {name: getattr(data, name) for name in output_names}
                    self.state.update(**values)
                    self.state.record_sample()
                    snapshot = self.state.snapshot()
                    if self.trajectory is not None:
                        self.trajectory.maybe_record(snapshot)
                    recorder = self.recorder
                    if recorder is not None:
                        recorder.write(snapshot)
            except Exception as exc:
                LOGGER.warning("RTDE disconnected: %s", exc)
                self.state.record_disconnect(str(exc))
                self.state.update(touch=False, connected=False, error=str(exc))
                self.stop_event.wait(RECONNECT_DELAY_SECONDS)
            finally:
                if connection is not None:
                    with contextlib.suppress(Exception):
                        connection.disconnect()
