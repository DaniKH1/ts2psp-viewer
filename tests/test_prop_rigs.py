"""Pivot, curve linkage, bind-pose and exported-motion regressions."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from ts2psp.formats.props import prop_joints, parse_rdms, convert_prop, sections
from ts2psp.formats.prop_scene import _transform, node_hierarchy, transform_values
from ts2psp.formats.prop_anim import PropAnimator, prop_clip, curve_bindings, sample
from ts2psp.formats.animations import open_glb
from ts2psp.rendering.animation import accessor, posed_meshes, quat_matrix
from ts2psp.rendering.props import load_parts

DATA = Path(
    os.environ.get("TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
)
ARCHIVE = DATA / "sims2_psp_props_recursos.zip"


class CurveAndPivotTests(unittest.TestCase):
    def test_rotation_and_scale_leave_the_common_pivot_in_place(self):
        pivot = (1, 2, 3)
        values = (1, 4, 5, 6, 0.2, 0.3, 0.4, 2, 3, 4)
        matrix = _transform(values, pivot, pivot)
        np.testing.assert_allclose(
            (matrix @ (*pivot, 1))[:3], np.array(pivot) + values[1:4]
        )

    def test_hermite_tangents_and_held_keys_are_respected(self):
        curve = {
            "times": (0, 1),
            "values": (0, 1),
            "in_tangents": (0, 0),
            "out_tangents": (0, 0),
        }
        self.assertAlmostEqual(sample(curve, 0.25), 0.15625)
        curve["out_tangents"] = (float("inf"), 0)
        self.assertEqual(sample(curve, 0.75), 0)
        self.assertEqual(sample(curve, 1), 1)

    def test_short_cyclic_and_relative_curves_repeat(self):
        curve = {"times": (0, 1), "values": (0, 2), "flags": 512}
        self.assertAlmostEqual(sample(curve, 1.25), 0.5)
        curve["flags"] = 768
        self.assertAlmostEqual(sample(curve, 1.25), 2.5)


@unittest.skipUnless(ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR for prop rig checks")
class PropRigTests(unittest.TestCase):
    def resource(self, name):
        with zipfile.ZipFile(ARCHIVE) as archive:
            return archive.read(f"recursos/objects/{name}.res")

    def satellite(self):
        return self.resource("roberta/fx_rightarm_satellite")

    def test_satellite_curves_target_the_arm_z_and_dish_x(self):
        data = self.satellite()
        clip = prop_clip(data)
        self.assertAlmostEqual(clip["duration"], 10)
        self.assertEqual(
            [
                (x["channel"], x["node"], x["component"], x["factor"])
                for x in curve_bindings(data, clip)
            ],
            [(0, 5, 6, 1.0), (1, 8, 4, 1.0)],
        )
        animator = PropAnimator(data, clip)
        self.assertEqual(set(animator.by_node), {5, 8})
        np.testing.assert_allclose(animator.local_matrix(0, 5), animator.nodes[0][3])

    def test_satellite_dish_and_bones_use_their_actual_pivots(self):
        data = self.satellite()
        parts, rejected = parse_rdms(data)
        self.assertFalse(rejected)
        dish = next(part for part in parts if part["node"] == 9)
        points = np.array([vertex[0] for vertex in dish["vertices"]])
        self.assertGreater(points[:, 2].min(), 0.60)
        self.assertLess(points[:, 2].max(), 1.0)
        bones, anchors = prop_joints(data)
        self.assertEqual(len(bones), 6)
        self.assertEqual({part["node"] for part in parts}, set(anchors))
        by_name = {bone["name"]: bone for bone in bones}
        self.assertAlmostEqual(by_name["dish"]["position"][2], 0.75203254, places=6)
        self.assertAlmostEqual(by_name["arm"]["position"][2], 0.48076486, places=6)

    def test_rest_and_last_frame_preserve_the_satellite_geometry(self):
        data = self.satellite()
        parts = load_parts(data)
        animator = PropAnimator(data)
        bones, _ = prop_joints(data)
        for time in (0, 10):
            posed, positions = animator.pose(parts, time, bones)
            for original, current in zip(parts, posed):
                np.testing.assert_allclose(
                    [v[0] for v in current["vertices"]],
                    [v[0] for v in original["vertices"]],
                    atol=1e-8,
                )
            for bone in bones:
                np.testing.assert_allclose(
                    positions[bone["index"]], bone["position"], atol=1e-8
                )

    def test_exported_satellite_skin_and_animation_match_preview(self):
        data = self.satellite()
        parts = load_parts(data)
        bones, _ = prop_joints(data)
        animator = PropAnimator(data)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "satellite.glb"
            result = convert_prop(data, "satellite", target)
            doc, binary = open_glb(target)
        self.assertEqual(result["animations"], 1)
        self.assertEqual(result["bones"], 6)
        skin = doc["skins"][0]
        parents = {
            child: i
            for i, node in enumerate(doc["nodes"])
            for child in node.get("children", [])
        }

        def world(index):
            node = doc["nodes"][index]
            matrix = np.eye(4)
            matrix[:3, :3] = quat_matrix(node["rotation"]) * node["scale"]
            matrix[:3, 3] = node["translation"]
            return world(parents[index]) @ matrix if index in parents else matrix

        from ts2psp.core.coordinates import convert_vector3

        for bone, joint in zip(bones, skin["joints"]):
            np.testing.assert_allclose(
                world(joint)[:3, 3], convert_vector3(bone["position"]), atol=1e-6
            )
        for mesh in doc["meshes"]:
            attrs = mesh["primitives"][0]["attributes"]
            joints = accessor(doc, binary, attrs["JOINTS_0"])
            self.assertLess(joints.max(), len(skin["joints"]))
            np.testing.assert_array_equal(joints[:, 1:], 0)
            np.testing.assert_allclose(
                accessor(doc, binary, attrs["WEIGHTS_0"])[:, 0], 1
            )
        for time in (0, 1, 2, 5, 10):
            preview, _ = animator.pose(parts, time, bones)
            exported = posed_meshes(doc, binary, doc["animations"][0], time)
            for part, (_, positions, _) in zip(preview, exported):
                np.testing.assert_allclose(
                    positions, [v[0] for v in part["vertices"]], atol=2e-6
                )

    def test_timestopper_keeps_multiple_components_on_the_same_node(self):
        data = self.resource("accessories_perks/fx_timestopper")
        bindings = curve_bindings(data, prop_clip(data))
        self.assertEqual(
            {item["component"] for item in bindings if item["node"] == 11}, {5, 7, 8, 9}
        )
        self.assertEqual(len(bindings), 13)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "timestopper.glb"
            convert_prop(data, "timestopper", output)
            doc, binary = open_glb(output)
            times = accessor(
                doc, binary, doc["animations"][0]["samplers"][0]["input"]
            ).ravel()
            time = float(times[len(times) // 2])
            expected, _ = PropAnimator(data).pose(load_parts(data), time)
            actual = posed_meshes(doc, binary, doc["animations"][0], time)
            for part, (_, positions, _) in zip(expected, actual):
                np.testing.assert_allclose(
                    positions, [v[0] for v in part["vertices"]], atol=2e-6
                )

    def test_animated_translation_keeps_centimetre_to_metre_factor(self):
        data = self.resource(
            "accessories_perks/fx_o2a-cs-cowthulu-sc38-remove-teleporter_teleporter"
        )
        bindings = curve_bindings(data, prop_clip(data))
        self.assertTrue(bindings)
        self.assertTrue(
            all(
                abs(item["factor"] - 0.01) < 1e-8
                for item in bindings
                if item["component"] in (1, 2, 3)
            )
        )

    def test_zero_scale_effect_can_be_animated_and_exported(self):
        data = self.resource("vehiclecompact/fx_o2a-cs-intro-drive-swerve")
        bones, _ = prop_joints(data)
        for bone in bones:
            self.assertTrue(np.isfinite(np.linalg.inv(bone["world"])).all())
        animator = PropAnimator(data)
        parts = load_parts(data)
        for time in (0, animator.clip["duration"] / 2):
            posed, _ = animator.pose(parts, time, bones)
            self.assertTrue(
                all(
                    np.isfinite([v[0] for v in part["vertices"]]).all()
                    for part in posed
                )
            )
        with tempfile.TemporaryDirectory() as folder:
            convert_prop(data, "car", Path(folder) / "car.glb")

    def test_named_bmsh_bones_are_available_to_the_prop_viewer(self):
        bones, _ = prop_joints(self.resource("beds/body/bedsinglecheap"))
        self.assertGreater(len(bones), 1)
        self.assertTrue(all(bone["name"] for bone in bones))
