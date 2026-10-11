"""LoggingMixin extracted from the desktop controller."""
import csv
import os
import time
from typing import Optional
from .i18n import localize

class LoggingMixin:
    def log(self, message: str) -> None:
        if not hasattr(self, '_log_entries'):
            self._log_entries = []
        entry = (time.strftime('%H:%M:%S'), message)
        self._log_entries.append(entry)
        self._log_entries = self._log_entries[-200:]
        self.event_log.appendPlainText(f"[{entry[0]}] {localize(message)}")
        self._csv_row("event", note=message)

    def _render_event_log(self) -> None:
        self.event_log.clear()
        for timestamp, message in getattr(self, '_log_entries', ()):
            self.event_log.appendPlainText(f"[{timestamp}] {localize(message)}")


    def _setup_csv_logging(self) -> None:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            self.csv_path = os.path.join(
                log_dir, f"session_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1000000000:09d}.csv")
            self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow([
                "timestamp", "event", "command", "vx", "vy", "vz",
                "hand_x", "hand_y", "size_delta_pct", "handedness_score", "fps",
                "tcp_x", "tcp_y", "tcp_z", "safety_mode", "robot_enabled", "note",
            ])
            self.csv_file.flush()
        except OSError as exc:
            self.csv_path = ""
            self.csv_file = None
            self.csv_writer = None


    def _csv_row(self, event: str, command: str = "STOP",
                 vx: float = 0.0, vy: float = 0.0, vz: float = 0.0,
                 hand_x: Optional[float] = None, hand_y: Optional[float] = None,
                 size_delta_pct: Optional[float] = None,
                 confidence: float = 0.0, note: str = "") -> None:
        if self.csv_writer is None:
            return
        pose = self.rtde_client.actual_tcp_pose if self.rtde_client else None
        safety = self.rtde_client.safety_status if self.rtde_client else None
        try:
            self.csv_writer.writerow([
                f"{time.time():.3f}", event, command,
                f"{vx:+.4f}", f"{vy:+.4f}", f"{vz:+.4f}",
                "" if hand_x is None else f"{hand_x:.4f}",
                "" if hand_y is None else f"{hand_y:.4f}",
                "" if size_delta_pct is None else f"{size_delta_pct:+.1f}",
                f"{confidence:.3f}", f"{self.current_fps:.1f}",
                "" if pose is None else f"{pose[0]:+.4f}",
                "" if pose is None else f"{pose[1]:+.4f}",
                "" if pose is None else f"{pose[2]:+.4f}",
                "" if safety is None else int(safety),
                int(self.robot_enabled), note,
            ])
        except (OSError, ValueError) as exc:
            self.csv_writer = None
            self.log(f"CSV 寫入失敗：{exc}")


    def _flush_csv(self) -> None:
        if self.csv_file is not None:
            try:
                self.csv_file.flush()
                os.fsync(self.csv_file.fileno())
            except (OSError, ValueError) as exc:
                self.csv_writer = None
                self.log(f"CSV 儲存失敗：{exc}")
