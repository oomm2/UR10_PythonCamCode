"""UR10 vision-control desktop app.

Enhanced with noise-resistant hand detection, hand-size Z-axis control,
and a richer control panel with tunable parameters.
"""

from __future__ import annotations

import time
import math
import platform
import socket
import subprocess
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Optional

import cv2
from PIL import Image, ImageTk

cv2.setNumThreads(1)


class _CanvasText(tk.Button):
    """Flat text control; reliable on Apple's deprecated Tk build."""

    def __init__(self, parent, *, text=None, textvariable=None, bg, fg,
                 font=None, anchor="w", justify="left", **kwargs):
        options = dict(
            text=text or "", bg=bg, fg=fg, activebackground=bg,
            activeforeground=fg, relief="flat", borderwidth=0,
            highlightthickness=0, anchor=anchor, justify=justify,
            padx=0, pady=0, command=lambda: None,
        )
        if textvariable is not None:
            options["textvariable"] = textvariable
        if font is not None:
            options["font"] = font
        options.update(kwargs)
        super().__init__(parent, **options)

try:
    import mediapipe as mp
except ImportError as exc:
    raise SystemExit("MediaPipe is missing. Run: python -m pip install mediapipe") from exc

if not hasattr(mp, "solutions"):
    raise SystemExit(
        "This app requires MediaPipe 0.10.21 (the mp.solutions API). "
        "Run: python -m pip uninstall -y mediapipe && "
        "python -m pip install mediapipe==0.10.21"
    )


class UR10VisionApp:
    AXIS_SIGN_X = 1.0   # left / right
    AXIS_SIGN_Y = 1.0   # forward / backward (hand size)
    AXIS_SIGN_Z = 1.0   # up / down
    ACCELERATION = 0.25
    CONTROL_PERIOD = 0.10
    RTDE_PORT = 30004
    STABLE_FRAMES = 4
    LOST_HAND_GRACE = 3
    MIN_HAND_SIZE_GEO = 0.06
    MAX_HAND_SIZE_GEO = 0.85
    MIN_LANDMARK_SPREAD = 0.015
    SIZE_DEAD_ZONE = 0.015
    SIZE_SMOOTHING = 0.12
    SIZE_CALIB_RATE = 0.04

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("UR10 Vision Control")
        self.root.geometry("1300x880")
        self.root.minsize(1100, 740)
        self.root.configure(background="#0f172a")

        self.bg = "#0f172a"
        self.panel = "#1e293b"
        self.panel_alt = "#334155"
        self.video_bg = "#0b1220"
        self.fg = "#f8fafc"
        self.muted = "#94a3b8"
        self.button_bg = "#334155"
        self.accent = "#22c55e"
        self.warning = "#f59e0b"
        self.danger = "#ef4444"

        self.cap: Optional[cv2.VideoCapture] = None
        self.robot = None
        self.camera_running = False
        self.robot_enabled = False
        self.last_robot_update = 0.0
        self.last_command = "STOP"
        self.stable_command = "STOP"
        self.candidate_command = "STOP"
        self.candidate_frames = 0
        self.smooth_x = None
        self.smooth_y = None
        self.smooth_size = None
        self.neutral_hand_size = None
        self.frame_job = None
        self.last_frame_time = 0.0
        self.camera_failures = 0
        self.test_job = None
        self.video_photo = None
        self.lost_hand_frames = 0
        self.last_detection_confidence = 0.0
        self.fps_count = 0
        self.fps_timer = time.monotonic()
        self.current_fps = 0.0

        # Tunable parameters
        self.smoothing_alpha = tk.DoubleVar(value=0.25)
        self.dead_zone = tk.DoubleVar(value=0.10)
        self.detection_confidence = tk.DoubleVar(value=0.80)
        self.tracking_confidence = tk.DoubleVar(value=0.60)
        self.max_speed = tk.DoubleVar(value=0.05)
        self.size_sensitivity = tk.DoubleVar(value=3.0)

        # Display variables
        self.camera_source = tk.StringVar(value="0")
        self.robot_ip = tk.StringVar(value="127.0.0.1")
        self.robot_target = tk.StringVar(value="URSim")
        self.status = tk.StringVar(value="Ready | Simulation mode")
        self.command = tk.StringVar(value="STOP")
        self.hand_position = tk.StringVar(value="-, -")
        self.hand_size_var = tk.StringVar(value="--")
        self.robot_state = tk.StringVar(value="Not connected")
        self.control_state = tk.StringVar(value="DISABLED")
        self.confidence_var = tk.StringVar(value="--")
        self.fps_var = tk.StringVar(value="--")
        self.confidence_bar_var = tk.StringVar(value="[----------] 0%")
        self.log_var = tk.StringVar(value="Ready.")
        self.target_robot_summary = tk.StringVar(value="URSim | Not connected")
        self.command_control_summary = tk.StringVar(value="STOP | DISABLED")
        self.hand_size_summary = tk.StringVar(value="-, - | --")
        self.conf_fps_summary = tk.StringVar(value="-- | --")

        self.hands = None
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_hands = mp.solutions.hands

        self._build_ui()
        for variable in (self.robot_target, self.robot_state, self.command, self.control_state,
                         self.hand_position, self.hand_size_var, self.confidence_var, self.fps_var):
            variable.trace_add("write", self._sync_status_summaries)
        self._sync_status_summaries()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._log("Application started in simulation mode")
        self._log("Put hand in centre to calibrate neutral size")

    def _init_hand_tracking(self) -> bool:
        if self.hands is not None:
            return True
        try:
            self.status.set("Loading vision model...")
            self.root.update_idletasks()
            self.hands = self.mp_hands.Hands(
                max_num_hands=1,
                min_detection_confidence=self.detection_confidence.get(),
                min_tracking_confidence=self.tracking_confidence.get(),
            )
            self._log(
                f"Vision model loaded (det={self.detection_confidence.get():.2f}, "
                f"track={self.tracking_confidence.get():.2f})"
            )
            return True
        except Exception as exc:
            self.status.set("Vision model failed to load")
            self._log(f"Vision model error: {exc}")
            messagebox.showerror("Vision model error", str(exc))
            return False

    # -- UI --

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        for r in (2, 3, 4):
            self.root.rowconfigure(r, weight=0)

        # Header
        header = tk.Frame(self.root, bg=self.bg)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        self._static_text(header, text="UR10 Vision Control", font=("Helvetica", 21, "bold"),
                          width=24, bg=self.bg, fg=self.fg).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        self._static_text(header, text="6-axis hand control", width=20,
                          font=("Helvetica", 11), bg=self.bg, fg=self.muted).grid(
            row=1, column=0, sticky="w", padx=19, pady=(0, 14))
        self._static_text(header, textvariable=self.status, width=36, anchor="e",
                          bg=self.bg, fg=self.muted).grid(
            row=0, column=1, rowspan=2, sticky="e", padx=18, pady=(14, 14))

        # Body
        body = tk.Frame(self.root, bg=self.bg)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=420)
        body.rowconfigure(0, weight=1)

        # Video
        video_box = tk.Frame(body, bg=self.panel, bd=0, highlightthickness=1,
                             highlightbackground="#334155")
        video_box.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        video_box.rowconfigure(1, weight=1)
        video_box.columnconfigure(0, weight=1)
        vt = tk.Frame(video_box, bg=self.panel)
        vt.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 9))
        vt.columnconfigure(1, weight=1)
        self._static_text(vt, text="VISION PREVIEW", font=("Helvetica", 11, "bold"),
                          bg=self.panel, fg=self.fg).grid(row=0, column=0, sticky="w")
        self.video_label = tk.Button(
            video_box, text="Camera is stopped", relief="flat", borderwidth=0,
            bg=self.video_bg, fg=self.muted, activebackground=self.video_bg,
            activeforeground=self.muted)
        self.video_label.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        # Right controls
        controls = tk.Frame(body, bg=self.bg)
        controls.grid(row=0, column=1, sticky="nsew")
        controls.columnconfigure(0, weight=1)

        # Camera
        cam = tk.Frame(controls, bg=self.panel, bd=0, highlightthickness=1,
                       highlightbackground="#334155", padx=14, pady=13)
        cam.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        cam.columnconfigure(0, weight=1)
        self._section_title(cam, "CAMERA", "Mac camera or RTSP").grid(
            row=0, column=0, sticky="ew", pady=(0, 8))
        self.camera_source_button = tk.Button(
            cam, textvariable=self.camera_source, command=self.edit_camera_source,
            anchor="w", bg=self.panel_alt, fg=self.fg, activebackground="#3b4b63",
            activeforeground=self.fg, relief="flat", padx=10, pady=7)
        self.camera_source_button.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.camera_button = tk.Button(
            cam, text="Start Camera", command=self.toggle_camera,
            bg=self.button_bg, fg=self.fg, activebackground="#59636d",
            activeforeground=self.fg, relief="flat", padx=8, pady=7)
        self.camera_button.grid(row=2, column=0, sticky="ew")

        # Robot
        rbt = tk.Frame(controls, bg=self.panel, bd=0, highlightthickness=1,
                       highlightbackground="#334155", padx=14, pady=13)
        rbt.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        rbt.columnconfigure(0, weight=1)
        self._section_title(rbt, "UR10 CONNECTION", "Simulation first; live motion is locked").grid(
            row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Button(rbt, text="URSim", command=self.use_ursim,
                  bg="#166534", fg="#ffffff", activebackground="#15803d", activeforeground="#ffffff",
                  relief="flat", padx=8, pady=7).grid(row=1, column=0, sticky="ew", pady=(0, 5))
        tk.Button(rbt, text="Real UR10", command=self.use_real_robot,
                  bg="#55505d", fg="#ffffff", activebackground="#746b80", activeforeground="#ffffff",
                  relief="flat", padx=8, pady=7).grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self._static_text(rbt, text="IP ADDRESS", font=("Helvetica", 10, "bold"),
                          bg=self.panel, fg=self.muted).grid(row=3, column=0, sticky="w")
        self.robot_ip_button = tk.Button(
            rbt, textvariable=self.robot_ip, command=self.edit_robot_ip,
            anchor="w", bg=self.panel_alt, fg=self.fg, activebackground="#3b4b63",
            activeforeground=self.fg, relief="flat", padx=10, pady=7)
        self.robot_ip_button.grid(row=4, column=0, sticky="ew", pady=(4, 8))
        self.connect_button = tk.Button(
            rbt, text="Connect Robot", command=self.connect_robot,
            bg=self.button_bg, fg=self.fg, activebackground="#59636d", activeforeground=self.fg,
            relief="flat", padx=8, pady=7)
        self.connect_button.grid(row=5, column=0, sticky="ew", pady=(0, 5))
        self.enable_button = tk.Button(
            rbt, text="Enable Robot Control", command=self.toggle_robot_control,
            bg=self.button_bg, fg=self.fg, activebackground="#59636d", activeforeground=self.fg,
            relief="flat", padx=8, pady=7)
        self.enable_button.grid(row=6, column=0, sticky="ew")
        tk.Button(rbt, text="STOP", command=self.emergency_stop,
                  bg="#b91c1c", fg="#ffffff", activebackground="#dc2626", activeforeground="#ffffff",
                  relief="flat", padx=8, pady=7).grid(row=7, column=0, sticky="ew", pady=(8, 0))
        self.test_button = tk.Button(
            rbt, text="Test URSim Movement (1 sec)", command=self.test_ursim_motion,
            bg="#216e5a", fg="#ffffff", activebackground="#2d9278", activeforeground="#ffffff",
            relief="flat", padx=8, pady=7, state="disabled")
        self.test_button.grid(row=8, column=0, sticky="ew", pady=(8, 0))

        # Live status
        info = tk.Frame(controls, bg=self.panel, bd=0, highlightthickness=1,
                        highlightbackground="#334155", padx=14, pady=13)
        info.grid(row=2, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        self._section_title(info, "LIVE STATUS", "Vision and robot state").grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        status_items = [
            ("Target / Robot", self.target_robot_summary),
            ("Command / Control", self.command_control_summary),
            ("Hand x,y / size", self.hand_size_summary),
            ("Confidence / FPS", self.conf_fps_summary),
        ]
        info.columnconfigure(0, minsize=112)
        info.columnconfigure(1, minsize=210, weight=1)
        for i, (label, variable) in enumerate(status_items):
            self._static_text(info, text=label, width=14, bg=self.panel, fg=self.muted).grid(
                row=i + 1, column=0, sticky="w", pady=3)
            self._static_text(info, textvariable=variable, width=22, bg=self.panel, fg=self.fg,
                              anchor="e").grid(row=i + 1, column=1, sticky="e", pady=3)

        bar_row = len(status_items) + 1
        self._static_text(info, text="Detection:", bg=self.panel, fg=self.muted).grid(
            row=bar_row, column=0, sticky="w", pady=(6, 0))
        self._static_text(info, textvariable=self.confidence_bar_var, bg=self.panel,
                          fg=self.muted, font=("Courier", 10), anchor="w").grid(
            row=bar_row, column=1, sticky="ew", pady=(6, 0))

        badge_row = bar_row + 1
        self._static_text(info, text="Direction:", bg=self.panel, fg=self.muted).grid(
            row=badge_row, column=0, sticky="w", pady=(6, 0))
        # Apple Tk 8.5 used by the Command Line Tools can omit Label text in
        # dark mode; Button styled flat is a reliable text badge on macOS.
        self.dir_badge = tk.Button(info, text="STOP", bg=self.button_bg, fg=self.fg,
                                   activebackground=self.button_bg, activeforeground=self.fg,
                                   relief="flat", borderwidth=0, highlightthickness=0,
                                   font=("Helvetica", 10, "bold"), padx=10, pady=3)
        self.dir_badge.grid(row=badge_row, column=1, sticky="w", pady=(6, 0))

        # Tuning bar
        tuning = tk.Frame(self.root, bg=self.panel, bd=0, highlightthickness=1,
                          highlightbackground="#334155", padx=14, pady=10)
        tuning.grid(row=2, column=0, sticky="ew")
        tuning.columnconfigure(0, weight=1)
        self._section_title(tuning, "TUNING", "").grid(
            row=0, column=0, sticky="ew", pady=(0, 8))
        sliders = tk.Frame(tuning, bg=self.panel)
        sliders.grid(row=1, column=0, sticky="ew")
        for c in range(3):
            sliders.columnconfigure(c, weight=1, uniform="s")
        self._slider_col(sliders, "Detection", self.detection_confidence, 0.50, 1.00, 0.05, 2, 0, 0)
        self._slider_col(sliders, "Tracking", self.tracking_confidence, 0.30, 1.00, 0.05, 2, 1, 0)
        self._slider_col(sliders, "Smoothing", self.smoothing_alpha, 0.05, 0.80, 0.05, 2, 2, 0)
        self._slider_col(sliders, "Dead Zone", self.dead_zone, 0.03, 0.30, 0.01, 2, 0, 1)
        self._slider_col(sliders, "Max Speed", self.max_speed, 0.01, 0.20, 0.01, 2, 1, 1)
        self._slider_col(sliders, "Size Sens", self.size_sensitivity, 1.0, 8.0, 0.5, 1, 2, 1)

        # Event log
        log_frame = tk.Frame(self.root, bg=self.panel, bd=0, highlightthickness=1,
                             highlightbackground="#334155", padx=10, pady=8)
        log_frame.grid(row=3, column=0, sticky="ew")
        log_frame.columnconfigure(0, weight=1)
        self._static_text(log_frame, text="EVENT LOG", font=("Helvetica", 10, "bold"),
                          bg=self.panel, fg=self.fg).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._static_text(log_frame, textvariable=self.log_var, bg=self.video_bg,
                          fg=self.muted, font=("Courier", 10), anchor="nw",
                          justify="left", height=4, wraplength=1060).grid(
            row=1, column=0, sticky="ew")

        # Footer
        footer = tk.Frame(self.root, bg="#0b1220", height=28)
        footer.grid(row=4, column=0, sticky="ew")
        self._static_text(
            footer,
            text="SAFETY: URSim first - closed hand / no hand = STOP - hand in centre = calibrate size",
            font=("Helvetica", 9), bg="#0b1220", fg=self.muted
        ).pack(anchor="w", padx=18, pady=6)

    def _section_title(self, parent, title: str, subtitle: str):
        block = tk.Frame(parent, bg=self.panel)
        block.columnconfigure(0, weight=1)
        self._static_text(block, text=title, font=("Helvetica", 11, "bold"),
                          bg=self.panel, fg=self.fg).grid(row=0, column=0, sticky="w")
        return block

    @staticmethod
    def _static_text(parent, *, text=None, textvariable=None, bg, fg, font=None, **kwargs):
        # Flat buttons render consistently with Apple's deprecated Tk build.
        return _CanvasText(
            parent,
            text=text,
            textvariable=textvariable,
            bg=bg,
            fg=fg,
            font=font,
            anchor=kwargs.pop("anchor", "w"),
            justify=kwargs.pop("justify", "left"),
            **kwargs,
        )

    def _slider_col(self, parent, label, var, lo, hi, step, decimals, col, row=0):
        frame = tk.Frame(parent, bg=self.panel)
        frame.grid(row=row, column=col, sticky="nsew", padx=8, pady=2)
        frame.columnconfigure(1, weight=1)
        self._static_text(frame, text=label, bg=self.panel, fg=self.muted,
                          font=("Helvetica", 9)).grid(row=0, column=0, columnspan=3,
                                                       sticky="w")
        value_var = tk.StringVar(value=f"{var.get():.{decimals}f}")

        def refresh(*args):
            value_var.set(f"{var.get():.{decimals}f}")

        def adjust(direction):
            value = max(lo, min(hi, var.get() + direction * step))
            var.set(round(value, decimals + 2))
            refresh()

        tk.Button(frame, text="-", command=lambda: adjust(-1), width=2,
                  bg=self.button_bg, fg=self.fg, activebackground="#59636d",
                  activeforeground=self.fg, relief="flat", padx=2).grid(row=1, column=0)
        self._static_text(frame, textvariable=value_var, bg=self.panel, fg=self.fg,
                          font=("Helvetica", 10, "bold"), anchor="center").grid(
            row=1, column=1, sticky="ew")
        tk.Button(frame, text="+", command=lambda: adjust(1), width=2,
                  bg=self.button_bg, fg=self.fg, activebackground="#59636d",
                  activeforeground=self.fg, relief="flat", padx=2).grid(row=1, column=2)
        var.trace_add("write", refresh)

    def _update_confidence_bar(self, confidence: float):
        if not hasattr(self, "confidence_bar_var"):
            return
        confidence = max(0.0, min(1.0, confidence))
        filled = int(round(confidence * 10))
        self.confidence_bar_var.set(f"[{'#' * filled}{'-' * (10 - filled)}] {confidence * 100:.0f}%")

    def _update_dir_badge(self, command: str):
        if not hasattr(self, "dir_badge"):
            return
        colors = {
            "STOP": (self.button_bg, self.fg),
            "LEFT": ("#6b21a8", "#ffffff"),
            "RIGHT": ("#166534", "#ffffff"),
            "UP": ("#1e40af", "#ffffff"),
            "DOWN": ("#9a3412", "#ffffff"),
            "FWD": ("#0f766e", "#ffffff"),
            "BACK": ("#78350f", "#ffffff"),
        }
        # Use first direction in compound command for badge colour
        first = command.split("+")[0] if "+" in command else command
        bg, fg = colors.get(first, (self.button_bg, self.fg))
        self.dir_badge.configure(text=command, bg=bg, fg=fg)

    def _log(self, message: str):
        if not hasattr(self, "log_var"):
            return
        timestamp = time.strftime("%H:%M:%S")
        lines = self.log_var.get().splitlines() if self.log_var.get() else []
        lines.append(f"[{timestamp}] {message}")
        self.log_var.set("\n".join(lines[-4:]))

    def _sync_status_summaries(self, *_):
        """Keep the compact status grid current without adding more rows."""
        if hasattr(self, "target_robot_summary"):
            self.target_robot_summary.set(f"{self.robot_target.get()} | {self.robot_state.get()}")
            self.command_control_summary.set(f"{self.command.get()} | {self.control_state.get()}")
            self.hand_size_summary.set(f"{self.hand_position.get()} | {self.hand_size_var.get()}")
            self.conf_fps_summary.set(f"{self.confidence_var.get()} | {self.fps_var.get()}")

    # -- Camera / robot --

    def _camera_value(self):
        value = self.camera_source.get().strip()
        try:
            return int(value)
        except ValueError:
            return value

    def edit_camera_source(self) -> None:
        value = self._edit_value(
            "Camera source", "Enter camera index or RTSP URL:", self.camera_source.get())
        if value is not None and value.strip():
            self.camera_source.set(value.strip())

    def edit_robot_ip(self) -> None:
        value = self._edit_value(
            "RTDE IP address", "Enter URSim, NAS, or real UR10 IP address:", self.robot_ip.get())
        if value is not None and value.strip():
            self.robot_ip.set(value.strip())

    def _edit_value(self, title: str, prompt: str, initial: str) -> Optional[str]:
        if platform.system() == "Darwin":
            def quote(value: str) -> str:
                return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
            script = (
                f"display dialog {quote(prompt)} default answer {quote(initial)} "
                f"with title {quote(title)} buttons {{\"Cancel\", \"OK\"}} "
                "default button \"OK\""
            )
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if result.returncode == 0 and "text returned:" in result.stdout:
                return result.stdout.split("text returned:", 1)[1].strip()
            return None
        return simpledialog.askstring(title, prompt, initialvalue=initial, parent=self.root)

    def use_ursim(self) -> None:
        self.robot_target.set("URSim")
        if not self.robot_ip.get().strip():
            self.robot_ip.set("127.0.0.1")
        self.status.set("URSim selected - set the RTDE IP if using a VM")
        self._log(f"Target: URSim (IP: {self.robot_ip.get()})")
        self._update_test_button()

    def use_real_robot(self) -> None:
        self.robot_target.set("Real UR10")
        if self.robot_ip.get().strip() in ("127.0.0.1", "localhost"):
            self.robot_ip.set("")
        self.status.set("Real robot selected - motion remains disabled")
        self._log(f"Target: Real UR10 (IP: {self.robot_ip.get()})")
        self._update_test_button()

    def _update_test_button(self) -> None:
        if hasattr(self, "test_button"):
            self.test_button.configure(
                state="normal" if self.robot is not None and self.robot_target.get() == "URSim" else "disabled"
            )

    def toggle_camera(self) -> None:
        if self.hands is None:
            if not self._init_hand_tracking():
                return
        if self.camera_running:
            self.stop_camera()
            return
        cap = cv2.VideoCapture(self._camera_value())
        if not cap.isOpened():
            cap.release()
            messagebox.showerror("Camera error", "Could not open the camera or RTSP stream.")
            self._log("Camera error: could not open device")
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        self.camera_running = True
        self.camera_failures = 0
        self.lost_hand_frames = 0
        self.fps_count = 0
        self.fps_timer = time.monotonic()
        self.neutral_hand_size = None
        self.smooth_size = None
        self.camera_button.configure(text="Stop Camera")
        self.status.set("Camera running - simulation mode")
        self._log("Camera started - put hand in centre to calibrate size")
        self._update_frame()

    def stop_camera(self) -> None:
        self.camera_running = False
        if self.frame_job is not None:
            try:
                self.root.after_cancel(self.frame_job)
            except tk.TclError:
                pass
            self.frame_job = None
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self._send_stop()
        self.stable_command = self.candidate_command = "STOP"
        self.candidate_frames = 0
        self.smooth_x = self.smooth_y = None
        self.smooth_size = None
        self.neutral_hand_size = None
        self.lost_hand_frames = 0
        self.last_detection_confidence = 0.0
        self.camera_button.configure(text="Start Camera")
        self.video_label.configure(image="", text="Camera is stopped")
        self.video_photo = None
        self.confidence_var.set("--")
        self.fps_var.set("--")
        self.hand_size_var.set("--")
        self._update_confidence_bar(0.0)
        self._update_dir_badge("STOP")
        self.status.set("Ready - simulation mode")
        self._log("Camera stopped")

    def connect_robot(self) -> None:
        if self.robot is not None:
            self._disconnect_robot()
            return
        rtde_reachable = False
        try:
            import rtde_control
            host = self.robot_ip.get().strip()
            self.status.set(f"Checking RTDE at {host}:{self.RTDE_PORT}...")
            self.root.update_idletasks()
            with socket.create_connection((host, self.RTDE_PORT), timeout=2.0):
                pass
            rtde_reachable = True
            self.robot = rtde_control.RTDEControlInterface(host)
            self.robot_state.set(f"Connected: {host}")
            self.status.set("Robot connected - control disabled")
            self.connect_button.configure(text="Disconnect Robot")
            self._log(f"Robot connected: {host}")
            self._update_test_button()
        except Exception as exc:
            self._disconnect_robot(update_status=False)
            if rtde_reachable:
                detail = (
                    "RTDE port is reachable, but PolyScope rejected external control.\n\n"
                    "In URSim, enable Remote Control, select Remote mode, then power on the robot."
                )
                self.robot_state.set("Remote Control required")
                self._log("RTDE reachable but PolyScope rejected control")
            else:
                detail = (
                    f"Cannot reach RTDE port {self.RTDE_PORT}.\n\n"
                    "Check the IP address, Ethernet connection, and URSim/robot state."
                )
                self.robot_state.set("RTDE unavailable")
                self._log(f"RTDE unreachable at {host}:{self.RTDE_PORT}")
            self._update_test_button()
            messagebox.showerror("UR10 connection error", f"{detail}\n\n{exc}")

    def _disconnect_robot(self, update_status: bool = True) -> None:
        robot = self.robot
        was_enabled = self.robot_enabled
        self.robot_enabled = False
        self.control_state.set("DISABLED")
        self.enable_button.configure(text="Enable Robot Control")
        self.robot = None
        if robot is not None:
            if was_enabled:
                try:
                    robot.speedStop(2.0)
                except Exception:
                    pass
            try:
                robot.disconnect()
            except Exception:
                pass
        self.robot_state.set("Not connected")
        self.connect_button.configure(text="Connect Robot")
        self._update_test_button()
        if update_status:
            self.status.set("Robot disconnected - simulation mode")
            self._log("Robot disconnected")

    def toggle_robot_control(self) -> None:
        if self.robot is None:
            messagebox.showwarning("Robot not connected", "Connect to the UR10 first, or keep simulation mode.")
            return
        if not self.robot_enabled:
            accepted = messagebox.askyesno(
                "Enable robot motion",
                "Only enable after confirming the work area is clear and the robot is in a safe low-speed mode. Continue?",
            )
            if not accepted:
                return
            self.robot_enabled = True
            self.control_state.set("ENABLED")
            self.enable_button.configure(text="Disable Robot Control")
            self.status.set("LIVE ROBOT CONTROL ENABLED")
            self._log("Robot control ENABLED")
        else:
            self.emergency_stop()

    def test_ursim_motion(self) -> None:
        if self.robot_target.get() != "URSim":
            messagebox.showwarning("URSim only", "Select URSim before using this test.")
            return
        if self.robot is None:
            messagebox.showwarning("Robot not connected", "Connect to URSim first.")
            return
        if not self.robot_enabled:
            messagebox.showwarning("Control disabled", "Press Enable Robot Control first.")
            return
        if self.test_job is not None:
            return
        self.status.set("URSim test: moving slowly for 1 second")
        self._log("URSim test movement started")
        self._test_step(10)

    def _test_step(self, remaining: int) -> None:
        if remaining <= 0 or not self.robot_enabled or self.robot is None:
            self._send_stop()
            self.test_job = None
            self.status.set("URSim test complete - robot stopped")
            self._log("URSim test movement complete")
            return
        try:
            self.robot.speedL([0.02, 0.0, 0.0, 0.0, 0.0, 0.0], 0.10, 0.10)
        except Exception as exc:
            self._send_stop()
            self.test_job = None
            self._disconnect_robot(update_status=False)
            self.status.set(f"URSim test failed: {exc}")
            self._log(f"URSim test failed: {exc}")
            return
        self.test_job = self.root.after(100, lambda: self._test_step(remaining - 1))

    def emergency_stop(self) -> None:
        self.robot_enabled = False
        if self.test_job is not None:
            try:
                self.root.after_cancel(self.test_job)
            except tk.TclError:
                pass
            self.test_job = None
        self.control_state.set("DISABLED")
        self.enable_button.configure(text="Enable Robot Control")
        self._send_stop()
        self.status.set("Stopped - control disabled")
        self._update_dir_badge("STOP")
        self._log("Emergency stop activated")

    def _send_stop(self) -> None:
        if self.robot is not None:
            try:
                self.robot.speedStop(2.0)
            except Exception:
                pass

    # -- Detection --

    def _hand_bbox_valid(self, hand_landmarks) -> bool:
        xs = [lm.x for lm in hand_landmarks.landmark]
        ys = [lm.y for lm in hand_landmarks.landmark]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if w < self.MIN_HAND_SIZE_GEO or w > self.MAX_HAND_SIZE_GEO:
            return False
        if h < self.MIN_HAND_SIZE_GEO or h > self.MAX_HAND_SIZE_GEO:
            return False
        return True

    def _landmark_spread_valid(self, hand_landmarks) -> bool:
        palm_ids = (0, 5, 9, 13, 17)
        xs = [hand_landmarks.landmark[i].x for i in palm_ids]
        ys = [hand_landmarks.landmark[i].y for i in palm_ids]
        spread = max(max(xs) - min(xs), max(ys) - min(ys))
        return spread > self.MIN_LANDMARK_SPREAD

    def _hand_size_metric(self, hand_landmarks) -> float:
        """Return normalised hand bounding-box size (0-1 scale)."""
        xs = [lm.x for lm in hand_landmarks.landmark]
        ys = [lm.y for lm in hand_landmarks.landmark]
        return max(max(xs) - min(xs), max(ys) - min(ys))

    def _open_palm_robust(self, hand_landmarks) -> bool:
        landmarks = hand_landmarks.landmark
        palm_ids = (0, 5, 9, 13, 17)
        cx = sum(landmarks[i].x for i in palm_ids) / len(palm_ids)
        cy = sum(landmarks[i].y for i in palm_ids) / len(palm_ids)

        def dist(idx):
            return math.hypot(landmarks[idx].x - cx, landmarks[idx].y - cy)

        extended = 0
        for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
            if dist(tip) > dist(pip) * 1.15:
                extended += 1
        return extended >= 3

    def _adaptive_smooth(self, raw_x: float, raw_y: float):
        if self.smooth_x is None:
            self.smooth_x, self.smooth_y = raw_x, raw_y
        else:
            alpha = self.smoothing_alpha.get()
            movement = math.hypot(raw_x - self.smooth_x, raw_y - self.smooth_y)
            if movement > 0.15:
                alpha = min(0.60, alpha * 2.5)
            self.smooth_x += alpha * (raw_x - self.smooth_x)
            self.smooth_y += alpha * (raw_y - self.smooth_y)
        return self.smooth_x, self.smooth_y

    def _build_command_string(self, vx: float, vy: float, vz: float) -> str:
        parts = []
        if vx < -0.0005:
            parts.append("LEFT")
        elif vx > 0.0005:
            parts.append("RIGHT")
        if vz > 0.0005:
            parts.append("UP")
        elif vz < -0.0005:
            parts.append("DOWN")
        if vy > 0.0005:
            parts.append("FWD")
        elif vy < -0.0005:
            parts.append("BACK")
        return "+".join(parts) if parts else "STOP"

    def _send_robot_command(self, vx: float, vy: float, vz: float) -> None:
        if not self.robot_enabled or self.robot is None:
            return
        now = time.monotonic()
        if now - self.last_robot_update < self.CONTROL_PERIOD:
            return
        if abs(vx) < 0.0005 and abs(vy) < 0.0005 and abs(vz) < 0.0005:
            self._send_stop()
        else:
            try:
                self.robot.speedL([vx, vy, vz, 0.0, 0.0, 0.0], self.ACCELERATION, self.CONTROL_PERIOD)
            except Exception as exc:
                self._disconnect_robot(update_status=False)
                self.status.set(f"RTDE disconnected - control disabled: {exc}")
                self._log(f"RTDE error during control: {exc}")
                self.command.set("STOP")
        self.last_robot_update = now

    # -- Frame update --

    def _update_frame(self) -> None:
        if not self.camera_running or self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.camera_failures += 1
            self.status.set("Camera read failed")
            if self.camera_failures >= 20:
                self.stop_camera()
                self.status.set("Camera stopped after repeated read failures")
                self._log("Camera stopped: 20 consecutive read failures")
            else:
                self.frame_job = self.root.after(100, self._update_frame)
            return
        self.camera_failures = 0

        now = time.monotonic()
        if now - self.last_frame_time < 1.0 / 15.0:
            self.frame_job = self.root.after(20, self._update_frame)
            return
        self.last_frame_time = now

        # FPS
        self.fps_count += 1
        if now - self.fps_timer >= 1.0:
            self.current_fps = self.fps_count / (now - self.fps_timer)
            self.fps_count = 0
            self.fps_timer = now
            self.fps_var.set(f"{self.current_fps:.1f} FPS")

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if self.hands is None:
            self.root.after(50, self._update_frame)
            return
        result = self.hands.process(rgb)

        vx = vy = vz = 0.0
        confidence = 0.0
        hand_valid = False
        x = y = 0.5
        hand_size = 0.0

        if result.multi_hand_landmarks:
            hand = result.multi_hand_landmarks[0]
            if self._hand_bbox_valid(hand) and self._landmark_spread_valid(hand):
                hand_valid = True
                self.lost_hand_frames = 0

                if result.multi_handedness:
                    confidence = result.multi_handedness[0].classification[0].score
                self.last_detection_confidence = confidence
                self.confidence_var.set(f"{confidence * 100:.0f}%")
                self._update_confidence_bar(confidence)

                # Palm centre (XY position)
                palm_ids = (0, 5, 9, 13, 17)
                raw_x = sum(hand.landmark[i].x for i in palm_ids) / len(palm_ids)
                raw_y = sum(hand.landmark[i].y for i in palm_ids) / len(palm_ids)
                x, y = self._adaptive_smooth(raw_x, raw_y)

                # Hand size (Z axis: forward/backward)
                raw_size = self._hand_size_metric(hand)
                if self.smooth_size is None:
                    self.smooth_size = raw_size
                else:
                    self.smooth_size += self.SIZE_SMOOTHING * (raw_size - self.smooth_size)
                hand_size = self.smooth_size
                self.hand_size_var.set(f"{hand_size:.3f}")

                # Auto-calibrate neutral size when hand is near centre
                dz = self.dead_zone.get()
                if abs(x - 0.5) < dz and abs(y - 0.5) < dz:
                    if self.neutral_hand_size is None:
                        self.neutral_hand_size = hand_size
                        self._log(f"Neutral size calibrated: {hand_size:.3f}")
                    else:
                        self.neutral_hand_size += self.SIZE_CALIB_RATE * (hand_size - self.neutral_hand_size)

                if self._open_palm_robust(hand):
                    speed = self.max_speed.get()

                    # X axis: left/right from hand X
                    dx = max(-0.5, min(0.5, x - 0.5))
                    vx = self.AXIS_SIGN_X * dx * 2.0 * speed

                    # Z axis: up/down from hand Y (camera Y inverted)
                    dy_val = max(-0.5, min(0.5, y - 0.5))
                    vz = self.AXIS_SIGN_Z * -dy_val * 2.0 * speed

                    # Y axis: forward/backward from hand size
                    if self.neutral_hand_size is not None:
                        size_diff = hand_size - self.neutral_hand_size
                        if abs(size_diff) > self.SIZE_DEAD_ZONE:
                            vy = self.AXIS_SIGN_Y * size_diff * self.size_sensitivity.get() * speed * 5
                            vy = max(-speed, min(speed, vy))
                # else: closed hand -> vx=vy=vz=0 (STOP)

                self.mp_draw.draw_landmarks(frame, hand, self.mp_hands.HAND_CONNECTIONS)
                self.hand_position.set(f"{x:.2f}, {y:.2f}")

        if not hand_valid:
            self.lost_hand_frames += 1
            if self.lost_hand_frames > self.LOST_HAND_GRACE:
                self.smooth_x = self.smooth_y = None
                self.smooth_size = None
                self.hand_position.set("-, -")
                self.hand_size_var.set("--")
                self.confidence_var.set("--")
                self._update_confidence_bar(0.0)
            else:
                # Grace: hold last velocities
                pass

        command = self._build_command_string(vx, vy, vz)
        self.command.set(command)
        self.last_command = command
        self._update_dir_badge(command)
        self._send_robot_command(vx, vy, vz)

        # Video overlay
        dz = self.dead_zone.get()
        low = 0.5 - dz
        high = 0.5 + dz
        cv2.rectangle(frame, (int(frame.shape[1] * low), int(frame.shape[0] * low)),
                      (int(frame.shape[1] * high), int(frame.shape[0] * high)),
                      (255, 180, 0), 2)

        # Size gauge bar (right side of frame)
        if hand_size > 0:
            bar_h = int(hand_size * frame.shape[0] * 0.6)
            bx = frame.shape[1] - 30
            cv2.rectangle(frame, (bx, frame.shape[0] - 20 - bar_h), (bx + 12, frame.shape[0] - 20),
                          (0, 200, 200), -1)
            cv2.rectangle(frame, (bx, int(frame.shape[0] * 0.2)),
                          (bx + 12, frame.shape[0] - 20), (60, 60, 60), 1)
            if self.neutral_hand_size is not None:
                ny = int(frame.shape[0] - 20 - self.neutral_hand_size * frame.shape[0] * 0.6)
                cv2.line(frame, (bx - 4, ny), (bx + 16, ny), (0, 255, 0), 2)

        color = (0, 200, 0) if command != "STOP" else (0, 0, 220)
        cv2.putText(frame, f"{command} | {'LIVE' if self.robot_enabled else 'SIMULATION'}",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
        if confidence > 0:
            cv2.putText(frame, f"Confidence: {confidence * 100:.0f}%",
                        (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        if self.neutral_hand_size is not None:
            cv2.putText(frame, f"Size: {hand_size:.3f} (neutral: {self.neutral_hand_size:.3f})",
                        (20, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 200), 1)
        else:
            cv2.putText(frame, "Size: calibrating... (put hand in centre)",
                        (20, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 0), 1)
        cv2.putText(frame, f"FPS: {self.current_fps:.0f}  |  OPEN PALM = MOVE | CLOSED = STOP",
                    (20, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (230, 230, 230), 1)

        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        image.thumbnail((760, 560))
        if self.video_photo is None:
            self.video_photo = ImageTk.PhotoImage(image=image)
            self.video_label.configure(image=self.video_photo, text="")
        else:
            self.video_photo.paste(image)
        self.frame_job = self.root.after(20, self._update_frame)

    def close(self) -> None:
        self.emergency_stop()
        self.stop_camera()
        self._disconnect_robot(update_status=False)
        if self.hands is not None:
            self.hands.close()
        self.root.destroy()


def main() -> None:
    from ur10_vision_qt_app import main as qt_main

    return qt_main()


if __name__ == "__main__":
    main()
