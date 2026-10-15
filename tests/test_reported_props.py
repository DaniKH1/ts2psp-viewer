"""Reported prop regressions, checked against a locally imported user ISO."""

import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from ts2psp.formats.animations import open_glb
from ts2psp.formats.prop_assets import PropAssets
from ts2psp.formats.prop_anim import PropAnimator, iter_prop_animations
from ts2psp.formats.prop_skin import SkinnedPropAnimator
from ts2psp.formats.props import convert_prop, rdms_indices
from ts2psp.rendering.animation import posed_meshes
from ts2psp.rendering.props import load_parts

ARCHIVE = (
    Path(os.environ.get("TS2_PSP_DATA_DIR", "data")) / "sims2_psp_props_recursos.zip"
)


class IndexWidthTests(unittest.TestCase):
    def test_u8_indices_can_use_255_and_leave_vertices_unused(self):
        for maximum in (120, 255):
            data = bytes([0, maximum, 1, 2] * 8)
            self.assertEqual(
                rdms_indices(
                    data, 0, len(data), 256, 1, len(data), vertex_at=len(data)
                ),
                tuple(data),
            )


@unittest.skipUnless(
    ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR to imported ISO resources"
)
class ReportedPropTests(unittest.TestCase):
    def setUp(self):
        self.assets = PropAssets(ARCHIVE)

    def data(self, name):
        with zipfile.ZipFile(ARCHIVE) as z:
            return z.read(f"recursos/objects/{name}.res")

    def require_support(self):
        if not self.assets.manifest["textures"]:
            self.skipTest("Reimport the ISO to test the object support bank")

    def test_moneytree_uvs_do_not_jump_sixteen_tiles(self):
        parts = load_parts(self.data("aspirations/moneytree/body/moneytree"))
        leaves = parts[1]
        uv = np.asarray([v[2] for v in leaves["vertices"]])
        self.assertLess(float(np.ptp(uv, axis=0).max()), 2)
        self.assertLess(float(uv[:, 0].min()), 0)

    def test_tracking_rest_assembles_housing_and_lid_but_keeps_clip(self):
        data = self.data("accessories_perks/fx_trackingdevice")
        parts = load_parts(data)
        housing, lid = parts[:2]
        z_body = [v[0][2] for v in housing["vertices"]]
        z_lid = [v[0][2] for v in lid["vertices"]]
        self.assertLess(abs(max(z_body) - max(z_lid)), 0.01)
        animator = PropAnimator(data)
        self.assertEqual(len(animator.rest_overrides), 2)
        animated, _ = animator.pose(parts, 0)
        self.assertGreater(
            abs(animated[0]["vertices"][0][0][2] - housing["vertices"][0][0][2]), 0.06
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "tracking.glb"
            convert_prop(data, "objects/accessories_perks/fx_trackingdevice", target)
            doc, binary = open_glb(target)
            for t in (0, 0.5, 1, 2):
                preview, _ = animator.pose(parts, t)
                exported = posed_meshes(doc, binary, doc["animations"][0], t)
                for part, (_, xyz, _) in zip(preview, exported):
                    np.testing.assert_allclose(
                        xyz, [v[0] for v in part["vertices"]], atol=2e-6
                    )

    def test_polygraph_placeholder_is_replaced_with_real_iso_atlas(self):
        self.require_support()
        path = "objects/career_polygraph/fx_polygraph"
        parts = load_parts(
            self.data("career_polygraph/fx_polygraph"), ruta=path, assets=self.assets
        )
        chair = next(p for p in parts if p["node_name"] == "chairShape")
        self.assertEqual(
            chair["texture"][0],
            "objects/career_polygraph/txt_careerpolygraph-surfaces.tif",
        )

    def test_water_recovers_bitmap_and_identifies_generated_uvs(self):
        self.require_support()
        for name, uv_source in (
            ("bathtubs/fx_o-tub-fill", "generated_planar"),
            ("sinks/fx_o2a-sink-washhands-start", "source"),
            ("toilets/fx_o2a-toilet-lowerseat", "source"),
        ):
            with self.subTest(name=name):
                parts = load_parts(
                    self.data(name), ruta="objects/" + name, assets=self.assets
                )
                self.assertTrue(parts)
                for part in parts:
                    self.assertIsNotNone(part["texture"])
                    self.assertEqual(part["uv_source"], uv_source)
                    self.assertTrue(all(v[2] is not None for v in part["vertices"]))

    def test_cowthulu_four_clips_skin_the_same_in_preview_and_glb(self):
        self.require_support()
        name = "objects/cowthulu_stage/cowthulu_stage"
        data = self.data("cowthulu_stage/cowthulu_stage")
        parts = load_parts(data)
        clips = self.assets.clips(name)
        self.assertEqual(len(clips), 4)
        reference = next(c for c in clips if c["name"].endswith("idle-breathe"))
        rows = list(
            iter_prop_animations(
                ARCHIVE, [{"ruta_interna": name, "archivo_zip": f"recursos/{name}.res"}]
            )
        )
        self.assertEqual(len(rows), 4)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "stage.glb"
            info = convert_prop(data, name, target, assets=self.assets)
            self.assertEqual((info["bones"], info["animations"]), (13, 4))
            doc, binary = open_glb(target)
            moved = 0.0
            for clip, animation in zip(clips, doc["animations"]):
                animator = SkinnedPropAnimator(data, clip, reference)
                for t in (0, 0.5, 1):
                    preview, positions = animator.pose(parts, t)
                    self.assertEqual(len(positions), 13)
                    exported = posed_meshes(doc, binary, animation, t)
                    for part, original, (_, xyz, _) in zip(preview, parts, exported):
                        np.testing.assert_allclose(
                            xyz, [v[0] for v in part["vertices"]], atol=2e-6
                        )
                        moved = max(
                            moved,
                            float(
                                np.abs(xyz - [v[0] for v in original["vertices"]]).max()
                            ),
                        )
            self.assertGreater(moved, 0.01)
