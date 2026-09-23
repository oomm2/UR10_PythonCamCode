#!/usr/bin/env python3
"""Fail CI when tracked source contains common private deployment artifacts."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".venv", "venv", "node_modules", "recordings", "artifacts"}
FORBIDDEN_PATH_PARTS = {".zcode", "logs", "tmp"}
FORBIDDEN_FILENAMES = {"settings.json", "config.json", ".env"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".txt", ".xml", ".html", ".js", ".mjs", ".bat", ".command"}
PRIVATE_IPV4 = re.compile(
    r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|"
    r"192\.168\.(?:\d{1,3}\.)\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3})\b"
)
PERSONAL_PATH = re.compile(r"/(?:Users|home)/[^/\s]+")
REAL_RTSP = re.compile(r"rtsp://(?!<username>:<password>@)[^\s`]+:[^\s`@]+@", re.IGNORECASE)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [ROOT / path for path in result.stdout.decode().split("\0") if path]


def should_scan(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {"README", "LICENSE", "SECURITY"}


def main() -> int:
    problems: list[str] = []
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PATH_PARTS for part in relative.parts):
            problems.append(f"forbidden path: {relative}")
        if path.name in FORBIDDEN_FILENAMES:
            problems.append(f"forbidden local configuration: {relative}")
        if relative == Path("tools/check_public_tree.py"):
            continue
        if any(part in IGNORED_PARTS for part in relative.parts) or not should_scan(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in (
            (PRIVATE_IPV4, "private IPv4 address"),
            (PERSONAL_PATH, "personal absolute path"),
            (REAL_RTSP, "credentialed RTSP URL"),
        ):
            if pattern.search(text):
                problems.append(f"{label}: {relative}")
    if problems:
        print("Public-tree safeguard failed:", file=sys.stderr)
        print("\n".join(f"- {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("Public-tree safeguard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
