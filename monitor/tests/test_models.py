"""Tests for the robot model pipeline (tools/build_models.py).

Only UR10 ships. The generator stays model-agnostic, so these tests cover the
shipped artefact plus the generation and pruning logic that would run if another
arm were added back to ``BUILDER.MODELS``.
"""
from __future__ import annotations

import importlib
import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    path = ROOT / "tools" / "build_models.py"
    spec = importlib.util.spec_from_file_location("build_models", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILDER = load_builder()
JOINT_NAMES = [
    "shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint",
    "wrist_1_joint", "wrist_2_joint", "wrist_3_joint",
]
URDF = ROOT / "static" / "ur10.urdf"


class GeneratedUrdfTests(unittest.TestCase):
    def test_configured_model_has_a_urdf(self):
        self.assertTrue(URDF.is_file(), f"{URDF} is missing")

    def test_urdf_parses_and_has_the_full_joint_chain(self):
        tree = ET.parse(URDF)
        root = tree.getroot()
        self.assertEqual(root.tag, "robot")
        names = [joint.get("name") for joint in root.findall("joint")]
        for joint in JOINT_NAMES:
            self.assertIn(joint, names)

    def test_revolute_joints_declare_limits(self):
        tree = ET.parse(URDF)
        for joint in tree.getroot().findall("joint"):
            if joint.get("type") != "revolute":
                continue
            limit = joint.find("limit")
            self.assertIsNotNone(limit, f"{joint.get('name')} has no limit")
            lower = float(limit.get("lower"))
            upper = float(limit.get("upper"))
            self.assertLess(lower, upper)

    def test_mesh_references_resolve_to_real_files(self):
        static = ROOT / "static"
        tree = ET.parse(URDF)
        meshes = [mesh.get("filename") for mesh in tree.getroot().iter("mesh")]
        # Six visual links carry a mesh; base_link_inertia is geometry-free in
        # the flattened output, matching the shipped ur10.urdf.
        self.assertEqual(len(meshes), 6, "ur10 should reference six meshes")
        for filename in meshes:
            with self.subTest(mesh=filename):
                self.assertTrue((static / filename).is_file(), f"{filename} is missing")

    def test_kinematics_match_the_real_arm(self):
        # The generator must read UR10's own kinematics rather than emitting a
        # shared skeleton; -0.612 m is the UR10 upper-arm offset in the vendor
        # description.
        tree = ET.parse(URDF)
        joint = next(j for j in tree.getroot().findall("joint") if j.get("name") == "elbow_joint")
        forearm_x = float(joint.find("origin").get("xyz").split()[0])
        self.assertAlmostEqual(forearm_x, -0.612, places=4)

    def test_kinematics_reader_rejects_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            BUILDER.read_kinematics(ROOT / "does-not-exist")


class CopyMeshTests(unittest.TestCase):
    def test_ur10_is_the_fallback_and_borrows_nothing(self):
        copied, borrowed = BUILDER.copy_meshes("UR10")
        self.assertEqual(copied, 7)
        self.assertEqual(borrowed, [])

    def test_a_model_missing_meshes_raises_instead_of_shipping_half_a_robot(self):
        # A made-up folder has no meshes and no UR10 fallback for a base part, so
        # copy_meshes must refuse rather than silently emit a partial model.
        with self.assertRaises(FileNotFoundError):
            BUILDER.copy_meshes("UR3e-nope")


class PruneUnusedTests(unittest.TestCase):
    def test_prune_removes_artifacts_for_models_outside_models(self):
        original_static = BUILDER.STATIC
        original_models = BUILDER.MODELS
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp)
            (static / "ur10.urdf").write_text("<robot/>", encoding="utf-8")
            (static / "ur5e.urdf").write_text("<robot/>", encoding="utf-8")
            (static / "meshes" / "ur10").mkdir(parents=True)
            (static / "meshes" / "ur5e").mkdir(parents=True)
            BUILDER.STATIC = static
            BUILDER.MODELS = {"ur10": "UR10"}
            try:
                removed = BUILDER.prune_unused()
            finally:
                BUILDER.STATIC = original_static
                BUILDER.MODELS = original_models

            self.assertFalse((static / "ur5e.urdf").exists())
            self.assertFalse((static / "meshes" / "ur5e").exists())
            self.assertTrue((static / "ur10.urdf").exists())
            self.assertTrue((static / "meshes" / "ur10").exists())
            self.assertEqual(len(removed), 2)

    def test_prune_is_idempotent(self):
        original_static = BUILDER.STATIC
        with tempfile.TemporaryDirectory() as temp:
            static = Path(temp)
            (static / "ur10.urdf").write_text("<robot/>", encoding="utf-8")
            BUILDER.STATIC = static
            try:
                self.assertEqual(BUILDER.prune_unused(), [])
                self.assertEqual(BUILDER.prune_unused(), [])
            finally:
                BUILDER.STATIC = original_static


class ConfigModelChoiceTests(unittest.TestCase):
    def test_only_ur10_is_a_valid_config_value(self):
        self.assertEqual(set(BUILDER.MODELS.values()), {"UR10"})

        monitor_config = importlib.import_module("monitor_config")
        config = dict(monitor_config.load_config() or {})
        config["robot_model"] = "UR10"
        self.assertEqual(monitor_config._validate_config(config)["robot_model"], "UR10")

    def test_an_unbuilt_model_is_rejected(self):
        import monitor_config

        config = dict(monitor_config.load_config() or {})
        config["robot_model"] = "UR5e"
        with self.assertRaises(monitor_config.ConfigError):
            monitor_config._validate_config(config)


if __name__ == "__main__":
    unittest.main()
