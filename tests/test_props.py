"""Regression cases for corrupt pixels, wrong materials and misplaced shadows."""

import csv
import io
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

from ts2psp.formats.props import (
    convert_prop,
    parse_rdms,
    parse_prop_bmsh,
    prop_bones,
    prop_joints,
    prop_node_count,
    prop_node_names,
    prop_skeleton,
    sections,
)
from ts2psp.rendering.props import bones_over, load_parts
from ts2psp.formats.prop_scene import node_hierarchy, rigid_placements
from ts2psp.formats.prop_anim import (
    pose,
    prop_animations,
    prop_clip,
    reparte_canales,
    sample as muestra,
)
from ts2psp.formats.surfaces import decode_bc, decode_surface
from ts2psp.formats.animations import open_glb
from ts2psp.core.coordinates import convert_vector3

DATA = Path(
    os.environ.get("TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
)
ARCHIVE = DATA / "sims2_psp_props_recursos.zip"


class TextureBlockTests(unittest.TestCase):
    def test_dxt_tiles_keep_their_row_order(self):
        blocks = []
        colors = []
        for y in range(8):
            for x in range(8):
                value = ((y + 1) << 11) | ((x + 1) << 5)
                blocks.append(struct.pack("<IHH", 0, value, 0))
                colors.append(((y + 1) * 255 // 31, (x + 1) * 255 // 63, 0, 255))
        image = decode_bc(b"".join(blocks), 32, 32, 0x1F)
        for i, color in enumerate(colors):
            np.testing.assert_array_equal(image[i // 8 * 4 + 1, i % 8 * 4 + 1], color)

    def test_tiny_dxt5_preserves_alpha(self):
        indices = sum(1 << (3 * i) for i in range(16))
        raw = (
            struct.pack("<IHH", 0, 31, 0)
            + indices.to_bytes(6, "little")
            + bytes((0, 255))
        )
        image = decode_bc(raw, 2, 2, 0x21)
        self.assertEqual(image.shape, (2, 2, 4))
        np.testing.assert_array_equal(image, np.tile((0, 0, 255, 255), (2, 2, 1)))


@unittest.skipUnless(
    ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR to run game resource checks"
)
class PropResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = zipfile.ZipFile(ARCHIVE)
        cls.rows = list(
            csv.DictReader(
                io.StringIO(
                    cls.archive.read("inventario_props.csv").decode("utf-8-sig")
                )
            )
        )

    @classmethod
    def tearDownClass(cls):
        cls.archive.close()

    def resource(self, suffix):
        row = next(
            row for row in self.rows if row["ruta_interna"].endswith("/" + suffix)
        )
        return self.archive.read(row["archivo_zip"])

    def test_bookcase_follows_both_shader_references(self):
        parts, rejected = parse_rdms(self.resource("fx_bookcase_newlow"))
        self.assertFalse(rejected)
        self.assertEqual(
            {part["texture"][0] for part in parts}, {"surface_0", "surface_1"}
        )

    def test_single_mip_and_four_bit_palettes_load(self):
        for name in ("fx_counterclub", "fx_espiritu_porch_plant_01"):
            parts, rejected = parse_rdms(self.resource(name))
            self.assertFalse(rejected)
            self.assertTrue(parts)
            self.assertTrue(all(part["texture"] for part in parts))

    def test_shadow_sits_at_ground_instead_of_through_the_seat(self):
        parts, _ = parse_rdms(self.resource("fx_chair_diningpub"))
        shadow, chair = parts
        shadow_points = np.array([v[0] for v in shadow["vertices"]])
        chair_points = np.array([v[0] for v in chair["vertices"]])
        self.assertLess(shadow_points[:, 2].max(), chair_points[:, 2].mean() - 0.2)
        self.assertAlmostEqual(shadow_points[:, 2].mean(), 0.01, places=3)
        np.testing.assert_allclose(
            shadow_points[:, :2].mean(axis=0),
            chair_points[:, :2].mean(axis=0),
            atol=0.1,
        )
        u = [v[2][0] for v in chair["vertices"]]
        self.assertGreater(min(u), 0.003)
        self.assertLess(max(u), 1.0)

    def test_skinned_prop_exports_each_texture(self):
        data = self.resource("moneytree")
        parts = parse_prop_bmsh(data)
        expected = {part["texture"][0] for part in parts if part["texture"]}
        self.assertGreaterEqual(len(expected), 3)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "moneytree.glb"
            result = convert_prop(data, "moneytree", output)
            doc, binary = open_glb(output)
            self.assertEqual({image["name"] for image in doc["images"]}, expected)
            self.assertEqual(result["textured_parts"], len(parts))
            self.assertTrue(doc["skins"])
            for image in doc["images"]:
                view = doc["bufferViews"][image["bufferView"]]
                raw = binary[
                    view["byteOffset"] : view["byteOffset"] + view["byteLength"]
                ]
                with Image.open(io.BytesIO(raw)) as png:
                    self.assertGreater(png.width, 0)

    def test_catalogue_has_no_missing_surfaces_or_rejected_meshes(self):
        surfaces = 0
        self.assertEqual(len(self.rows), 767)
        for row in self.rows:
            with self.subTest(prop=row["ruta_interna"]):
                data = self.archive.read(row["archivo_zip"])
                for tag, off, size, _, _ in sections(data):
                    if tag == b"surf":
                        surfaces += 1
                        self.assertIsNotNone(decode_surface(data, off, size))
                parts, rejected = (
                    parse_rdms(data)
                    if row["formato"] == "rdms"
                    else (parse_prop_bmsh(data), [])
                )
                self.assertTrue(parts)
                self.assertFalse(rejected)
                self.assertFalse(any(p.get("texture_warning") for p in parts))
        self.assertEqual(surfaces, 1619)
