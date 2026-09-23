#!/usr/bin/env python3
"""Generate the flattened URDF and mesh bundle for the robot model.

The read-only monitor renders the UR10 digital twin. Flattening the xacro tree
at build time keeps the browser simple: ``urdf-loader`` gets a plain URDF and
never needs a xacro processor.

Usage::

    py tools/build_models.py             # generate the model
    py tools/build_models.py UR10        # same, spelled explicitly

Outputs land under ``static/ur10.urdf`` with the meshes copied to
``static/meshes/ur10/``. Meshes are copied rather than linked so the static
directory stays self-contained and the file server needs no special cases.

Adding another arm back: add its ``urXY`` -> ``URXY`` entry to ``MODELS`` below,
add the name to the allowed set in ``monitor_config._validate_config``, and
re-run this script. This export includes only UR10 inputs; obtain and license-review additional models separately.
"""
from __future__ import annotations

import argparse
import contextlib
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "Universal_Robots_ROS2_Description"
STATIC = ROOT / "static"
CONFIG_DIR = VENDOR / "config"
MESH_DIR = VENDOR / "meshes"

# Vendor directory name -> the model name used in config.json.
#
# Only UR10 is shipped. The generator itself is model-agnostic and the vendored
# description is limited to UR10. Adding another arm also requires its
# upstream inputs and license review, plus monitor_config._validate_config changes.
MODELS = {
    "ur10": "UR10",
}

# The seven visual parts of every UR arm, in chain order.
MESH_PARTS = ["base", "shoulder", "upperarm", "forearm", "wrist1", "wrist2", "wrist3"]

# Verified against the vendored xacro sources; the chain is identical across
# models and only the kinematics values change.
JOINT_CHAIN = [
    # joint name, parent link, child link, mesh key, axis
    ("base_link-base_link_inertia", "base_link", "base_link_inertia", "base", "fixed"),
    ("shoulder_pan_joint", "base_link_inertia", "shoulder_link", "shoulder", "0 0 1"),
    ("shoulder_lift_joint", "shoulder_link", "upper_arm_link", "upperarm", "0 0 1"),
    ("elbow_joint", "upper_arm_link", "forearm_link", "forearm", "0 0 1"),
    ("wrist_1_joint", "forearm_link", "wrist_1_link", "wrist1", "0 0 1"),
    ("wrist_2_joint", "wrist_1_link", "wrist_2_link", "wrist2", "0 0 1"),
    ("wrist_3_joint", "wrist_2_link", "wrist_3_link", "wrist3", "0 0 1"),
]

# Visual mesh offsets, taken from each model's visual_parameters.yaml. The
# wrist offsets differ per model, so they are read at generation time.
VISUAL_ROTATION = {
    "base": "0 0 3.141592653589793",
    "shoulder": "0 0 3.141592653589793",
    "upperarm": "1.570796327 0 -1.570796327",
    "forearm": "1.570796327 0 -1.570796327",
    "wrist1": "1.570796327 0 0",
    "wrist2": "0 0 0",
    "wrist3": "1.570796327 0 0",
}

DEGREES_RE = re.compile(r"!degrees\s+(?P<value>-?[\d.]+)")


def read_kinematics(model_dir: Path) -> dict[str, dict[str, float]]:
    """Read the nested ``kinematics:`` block into ``{part: {axis: value}}``."""
    path = model_dir / "default_kinematics.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"missing kinematics file: {path}")
    kinematics: dict[str, dict[str, float]] = {}
    current: str | None = None
    found_section = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "kinematics:":
            found_section = True
            continue
        if raw.startswith("  ") and not raw.startswith("    ") and stripped.endswith(":"):
            current = stripped[:-1]
            kinematics[current] = {}
            continue
        if current and raw.startswith("    ") and ":" in stripped:
            key, value = stripped.split(":", 1)
            with contextlib.suppress(ValueError):
                kinematics[current][key.strip()] = float(value.strip())
    if not found_section:
        raise ValueError(f"no kinematics section in {path}")
    return kinematics


def read_joint_limits(model_dir: Path) -> dict[str, dict[str, float]]:
    path = model_dir / "joint_limits.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"missing joint limits file: {path}")
    limits: dict[str, dict[str, float]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "joint_limits:":
            continue
        if raw.startswith("  ") and not raw.startswith("    ") and stripped.endswith(":"):
            current = stripped[:-1]
            limits[current] = {}
            continue
        if current and raw.startswith("    ") and ":" in stripped:
            key, value = stripped.split(":", 1)
            key, value = key.strip(), value.strip()
            if key in {"max_position", "min_position", "max_effort", "max_velocity"}:
                degrees = DEGREES_RE.search(value)
                if degrees:
                    limits[current][key] = float(degrees.group("value")) * 3.141592653589793 / 180.0
                else:
                    try:
                        limits[current][key] = float(value)
                    except ValueError:
                        continue
    return limits


def read_visual_offsets(model_dir: Path) -> dict[str, str]:
    """Return per-part visual origin xyz strings."""
    path = model_dir / "visual_parameters.yaml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    offsets: dict[str, str] = {}
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw.startswith("  ") and not raw.startswith("    ") and stripped.endswith(":"):
            current = stripped[:-1]
            continue
        if current and stripped.startswith("visual_offset_xyz:"):
            offsets[current] = stripped.split(":", 1)[1].strip().strip('"')
    return offsets


def build_urdf(model: str) -> str:
    folder = model.lower()
    model_dir = CONFIG_DIR / folder
    kinematics = read_kinematics(model_dir)
    limits = read_joint_limits(model_dir)
    offsets = read_visual_offsets(model_dir)

    lines: list[str] = ['<?xml version="1.0"?>', f'<robot name="{folder}">', '  <link name="base_link"/>']
    for _, _, child, mesh, _ in JOINT_CHAIN[1:]:
        lines.append(f'  <link name="{child}">')
        lines.append("    <visual>")
        xyz = offsets.get(mesh, "0 0 0")
        lines.append(f'      <origin xyz="{xyz}" rpy="{VISUAL_ROTATION[mesh]}"/>')
        lines.append(f'      <geometry><mesh filename="meshes/{folder}/{mesh}.dae"/></geometry>')
        lines.append("    </visual>")
        lines.append("  </link>")
    # The inertia-carrying base link sits between base_link and the shoulder.
    lines.insert(3, '  <link name="base_link_inertia"/>')

    for name, parent, child, _mesh, axis in JOINT_CHAIN:
        kind = "fixed" if axis == "fixed" else "revolute"
        lines.append(f'  <joint name="{name}" type="{kind}">')
        lines.append(f'    <parent link="{parent}"/><child link="{child}"/>')
        if axis == "fixed":
            lines.append('    <origin xyz="0 0 0" rpy="0 0 3.141592653589793"/>')
        else:
            # The kinematics file keys the arm segments by their own names; the
            # wrist joints map one-to-one while the first three use the segment
            # they drive.
            key = {
                "shoulder_pan": "shoulder",
                "shoulder_lift": "upper_arm",
                "elbow": "forearm",
                "wrist_1": "wrist_1",
                "wrist_2": "wrist_2",
                "wrist_3": "wrist_3",
            }[name.removesuffix("_joint")]
            values = kinematics.get(key, {})
            xyz = f"{values.get('x', 0.0)} {values.get('y', 0.0)} {values.get('z', 0.0)}"
            rpy = f"{values.get('roll', 0.0)} {values.get('pitch', 0.0)} {values.get('yaw', 0.0)}"
            lines.append(f'    <origin xyz="{xyz}" rpy="{rpy}"/>')
            lines.append(f'    <axis xyz="{axis}"/>')
            limit = limits.get(name, {})
            lower = limit.get("min_position", -6.283185307)
            upper = limit.get("max_position", 6.283185307)
            effort = limit.get("max_effort", 150.0)
            velocity = limit.get("max_velocity", 3.142)
            lines.append(
                f'    <limit lower="{lower:.9f}" upper="{upper:.9f}" '
                f'effort="{effort:g}" velocity="{velocity:.9g}"/>'
            )
        lines.append("  </joint>")

    lines.append("</robot>")
    return "\n".join(lines) + "\n"


def copy_meshes(model: str) -> tuple[int, list[str]]:
    """Copy the visual meshes for a model, falling back when a part is absent.

    Some models in the vendored description only ship the parts that differ from
    the base arm (UR16e ships two meshes), so any missing part is borrowed from
    the UR10 set. The returned list names those borrowings so the caller can log
    them rather than silently shipping a half-built robot.

    UR10 itself is the fallback set, so it never borrows from anywhere.
    """
    folder = model.lower()
    source = MESH_DIR / folder / "visual"
    target = STATIC / "meshes" / folder
    if not source.is_dir():
        raise FileNotFoundError(f"missing mesh directory: {source}")
    fallback = None if folder == "ur10" else MESH_DIR / "ur10" / "visual"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    borrowed: list[str] = []
    missing: list[str] = []
    for part in MESH_PARTS:
        candidate = source / f"{part}.dae"
        if candidate.is_file():
            shutil.copy2(candidate, target / f"{part}.dae")
        elif fallback is not None and (fallback / f"{part}.dae").is_file():
            shutil.copy2(fallback / f"{part}.dae", target / f"{part}.dae")
            borrowed.append(part)
        else:
            missing.append(part)
            continue
        copied += 1
    if missing:
        raise FileNotFoundError(
            f"{model} is missing meshes and UR10 cannot cover them: {', '.join(missing)}"
        )
    return copied, borrowed


def build(model: str) -> tuple[Path, int, list[str]]:
    urdf = build_urdf(model)
    path = STATIC / f"{model.lower()}.urdf"
    path.write_text(urdf, encoding="utf-8")
    copied, borrowed = copy_meshes(model)
    return path, copied, borrowed


def _describe(path: Path) -> str:
    """Render a path for logging, relative when it sits under ROOT."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def prune_unused() -> list[str]:
    """Delete generated artefacts for models no longer listed in MODELS.

    The URDFs and mesh folders are build output, not sources, so a model that
    was dropped from MODELS should not keep shipping megabytes of meshes in
    ``static/``. Only paths that match a known generated shape are removed.
    """
    keep = {model.lower() for model in MODELS.values()}
    removed: list[str] = []
    for folder in sorted(STATIC.glob("*.urdf")):
        if folder.stem not in keep:
            folder.unlink()
            removed.append(_describe(folder))
    mesh_root = STATIC / "meshes"
    if mesh_root.is_dir():
        for directory in sorted(mesh_root.iterdir()):
            if directory.is_dir() and directory.name not in keep:
                shutil.rmtree(directory)
                removed.append(_describe(directory))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    valid = sorted(MODELS.values())
    # Not using choices= here: Python 3.10's argparse validates the empty default
    # of nargs="*" against the choice list and rejects it with "invalid choice: []".
    parser.add_argument("models", nargs="*",
                        help=f"models to regenerate; defaults to all ({', '.join(valid)})")
    args = parser.parse_args()
    unknown = sorted(set(args.models) - set(valid))
    if unknown:
        parser.error(f"invalid choice: {', '.join(unknown)} (choose from {', '.join(valid)})")
    if not VENDOR.is_dir():
        print(f"error: vendored description not found at {VENDOR}", file=sys.stderr)
        return 1
    selected = args.models or valid
    for model in selected:
        path, copied, borrowed = build(model)
        note = f" (borrowed from UR10: {', '.join(borrowed)})" if borrowed else ""
        print(f"{model:>6}: {_describe(path)} ({copied} meshes){note}")
    for stale in prune_unused():
        print(f"pruned: {stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
