"""Two diffuse layers must not turn terrain blend weights into transparency."""

import io
import csv
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from ts2psp.exporting.texture_blend import bake_texture_blend
from ts2psp.formats.animations import open_glb
from ts2psp.formats.materials import assign_material, prop_materials
from ts2psp.formats.props import parse_rdms, write_rigid_glb
from ts2psp.rendering.animation import accessor
from ts2psp.rendering.props import without_vertex_colors


def texture(name, colors):
    output = io.BytesIO()
    Image.fromarray(np.asarray([colors], dtype=np.uint8)).save(output, format="PNG")
    return name, output.getvalue()


def layered_part():
    first = texture("terrain/grass.tif", [(255, 0, 0, 255)])
    second = texture("terrain/dirt.tif", [(0, 0, 255, 255), (255, 255, 0, 255)])
    part = {
        "name": "piece",
        "vertices": [
            (p, (0, 0, 1), (0, 0), (0.2, 0.3, 0.4, 0.25))
            for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))
        ],
        "indices": [0, 1, 2],
        "vertex_alpha_mode": "BLEND",
        "secondary_uvs": [(0.75, 0.5)] * 3,
    }
    assign_material(
        part,
        {
            "texture": first,
            "texture_blend": True,
            "texture_layers": [
                {"texture": first, "source": first[0]},
                {"texture": second, "source": second[0]},
            ],
            "base_color": (1, 1, 1, 1),
        },
        [(0, 0)] * 3,
    )
    return part


class LayerTests(unittest.TestCase):
    def test_untextured_shader_reads_the_container_header(self):
        for header in (96, 128, 160):
            with self.subTest(header=header):
                data = bytearray(512)
                struct.pack_into("<I", data, 12, header)
                struct.pack_into("<3f", data, header + 8, 0.25, 0.5, 0.75)
                struct.pack_into("<f", data, header + 0x40, 1)
                material = prop_materials(bytes(data), [(b"gshd", 0, 96, 1, 0)])[header]
                self.assertEqual(material["base_color"], (0.25, 0.5, 0.75, 1))

    def test_mask_is_retained_when_colors_are_disabled_without_cutting_holes(self):
        part = layered_part()
        self.assertEqual(part["alpha_mode"], "OPAQUE")
        plain = without_vertex_colors([part])[0]
        self.assertEqual(plain["blend_weights"], [0.25] * 3)
        self.assertIsNone(plain["vertices"][0][3])
        self.assertEqual(part["vertices"][0][3], (0.2, 0.3, 0.4, 0.25))
        part["secondary_uvs"][0] = (3, 0)
        assign_material(
            part, {"texture": part["texture"], "base_color": (1, 1, 1, 1)}, [(0, 0)] * 3
        )
        self.assertTrue(part["repeat_texture"])

    def test_bake_uses_both_uv_sets_and_keeps_geometry_and_original_mask(self):
        part = layered_part()
        baked = bake_texture_blend(part)
        np.testing.assert_array_equal(
            [v[0] for v in baked["vertices"]], [v[0] for v in part["vertices"]]
        )
        self.assertEqual(baked["source_blend_weights"], [0.25] * 3)
        self.assertTrue(all(v[3][3] == 1 for v in baked["vertices"]))
        image = Image.open(io.BytesIO(baked["texture"][1])).convert("RGBA")
        np.testing.assert_allclose(image.getpixel((24, 24)), (255, 191, 0, 255), atol=1)
        plain = bake_texture_blend(without_vertex_colors([part])[0])
        self.assertEqual(plain["texture"], baked["texture"])

    def test_glb_contains_portable_bake_and_recoverable_source_attributes(self):
        part = layered_part()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "layers.glb"
            write_rigid_glb([part], "map", target)
            doc, binary = open_glb(target)
        mesh = doc["meshes"][0]
        attributes = mesh["primitives"][0]["attributes"]
        np.testing.assert_allclose(
            accessor(doc, binary, attributes["_PSP_BLEND"]), [[0.25]] * 3
        )
        np.testing.assert_allclose(
            accessor(doc, binary, attributes["_PSP_UV1"]), [(0.75, 0.5)] * 3
        )
        colors = accessor(doc, binary, attributes["COLOR_0"])
        np.testing.assert_allclose(colors, [(0.2, 0.3, 0.4, 1)] * 3)
        self.assertEqual(
            mesh["extras"]["textureBlend"]["sourceTextures"],
            ["terrain/grass.tif", "terrain/dirt.tif"],
        )
        self.assertEqual(len(doc["images"]), 3)
        self.assertEqual(part["vertices"][0][3][3], 0.25)


ARCHIVE = (
    Path(os.environ.get("TS2_PSP_DATA_DIR", "data")) / "sims2_psp_mapas_recursos.zip"
)


@unittest.skipUnless(
    ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR to imported ISO resources"
)
class GraveyardTests(unittest.TestCase):
    def test_graveyard_grass_has_two_uv_streams_and_a_non_opacity_mask(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            rows = csv.DictReader(
                io.StringIO(archive.read("inventario_mapas.csv").decode("utf-8-sig"))
            )
            row = next(
                r for r in rows if r["ruta_interna"] == "levels/deadtree/graveyard"
            )
            data = archive.read(row["archivo_zip"])
        parts, rejected = parse_rdms(data, ruta="levels/deadtree/graveyard")
        self.assertFalse(rejected)
        part = next(
            p
            for p in parts
            if p["source_texture_path"] == "textures/terrain/txt_graveyardgrass.tif"
        )
        self.assertTrue(part["texture_blend"])
        self.assertEqual(
            part["texture_layers"][1]["source"],
            "textures/terrain/txt_terrain_dirt_01.tif",
        )
        self.assertEqual(part["alpha_mode"], "OPAQUE")
        self.assertEqual(len(part["secondary_uvs"]), len(part["vertices"]))
        self.assertGreater(
            np.max(
                np.abs(
                    np.array(part["secondary_uvs"])
                    - np.array([v[2] for v in part["vertices"]])
                )
            ),
            0.1,
        )
        self.assertIn(0, part["blend_weights"])
        self.assertIn(1, part["blend_weights"])
        plain, _ = parse_rdms(
            data, ruta="levels/deadtree/graveyard", vertex_colors=False
        )
        plain = next(p for p in plain if p["name"] == part["name"])
        self.assertEqual(plain["blend_weights"], part["blend_weights"])
        self.assertTrue(all(v[3] is None for v in plain["vertices"]))


if __name__ == "__main__":
    unittest.main()
