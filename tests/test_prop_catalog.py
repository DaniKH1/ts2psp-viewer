"""Regressions found by reviewing the complete local prop catalogue."""

import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from ts2psp.formats.props import sections, parse_rdms, convert_prop
from ts2psp.formats.prop_scene import (
    node_hierarchy,
    node_name,
    transform_values,
    transform_matrix,
    _transform,
)
from ts2psp.formats.materials import assign_material
from ts2psp.formats.animations import open_glb
from ts2psp.rendering.props import load_parts
from ts2psp.rendering.opengl import Escena
from ts2psp.exporting.glb import GLB

DATA = Path(
    os.environ.get("TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
)
ARCHIVE = DATA / "sims2_psp_props_recursos.zip"


class MaterialAndJointTests(unittest.TestCase):
    def test_export_shares_the_bitmap_but_keeps_distinct_samplers(self):
        glb = GLB()
        clamp = glb.add_texture("shared", b"png", False)
        repeat = glb.add_texture("shared", b"png", True)
        self.assertNotEqual(clamp, repeat)
        self.assertEqual(len(glb.doc["images"]), 1)
        for index, wrap in ((clamp, 33071), (repeat, 10497)):
            texture = glb.doc["textures"][index]
            self.assertEqual(glb.doc["samplers"][texture["sampler"]]["wrapS"], wrap)

    def test_one_bitmap_can_have_separate_repeat_and_clamp_states(self):
        vertices = [
            ((0, 0, 0), None, (0, 0), None),
            ((1, 0, 0), None, (1, 0), None),
            ((0, 1, 0), None, (0, 1), None),
        ]
        pieces = [
            {"vertices": vertices, "indices": [0, 1, 2], "repeat_texture": repeat}
            for repeat in (False, True, False)
        ]
        scene = Escena.__new__(Escena)
        scene.tipo = "prop"
        uploaded = []
        scene._subir_textura = lambda texture, flip, repeat: uploaded.append(
            repeat
        ) or len(uploaded)
        scene._cargar(
            [("shared", part) for part in pieces],
            {"shared": np.full((1, 1, 4), 255, np.uint8)},
        )
        self.assertEqual(uploaded, [False, True])
        self.assertEqual([draw[1] for draw in scene.dibujos], [1, 2, 1])

    def test_textured_material_keeps_its_tint_and_partial_opacity(self):
        part = {}
        assign_material(
            part,
            {"texture": ("glass", b"png"), "base_color": (0.2, 0.4, 0.6, 0.25)},
            [(0, 0)],
        )
        self.assertEqual(part["color"], (51, 102, 153, 64))
        self.assertEqual(part["alpha_mode"], "BLEND")
        self.assertEqual(part["material_kind"], "texture")

    def test_joint_orientation_and_parent_scale_compensation(self):
        data = bytearray(180)
        struct.pack_into("<i", data, 0, 128)
        data[128:134] = b"joint\0"
        struct.pack_into("<I", data, 84, 1)
        struct.pack_into("<4f", data, 88, 0, 0, 0, 1)
        struct.pack_into("<4f", data, 104, 0, 0, np.sqrt(0.5), np.sqrt(0.5))
        values = (1, 1, 2, 3, 0, 0, 0, 1, 1, 1)
        matrix = transform_matrix(
            data, (0, 10, -1, None), values, parent_scale=(2, 3, 4)
        )
        np.testing.assert_allclose(
            matrix[:3, :3], [[0, -0.5, 0], [1 / 3, 0, 0], [0, 0, 0.25]], atol=1e-7
        )
        np.testing.assert_allclose(matrix[:3, 3], (1, 2, 3))


@unittest.skipUnless(ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR for catalogue checks")
class CatalogueResourceTests(unittest.TestCase):
    def resource(self, name):
        with zipfile.ZipFile(ARCHIVE) as archive:
            return archive.read(f"recursos/objects/{name}.res")

    def test_colonial_bath_handles_keep_the_compensated_rest_transform(self):
        data = self.resource("bathtubs/fx_bathtubcolonial_01")
        nodes = node_hierarchy(data, sections(data))[0]
        handles = [
            node
            for node in nodes
            if node_name(data, node) in ("center_handle", "left_handle", "right_handle")
        ]
        self.assertEqual(len(handles), 3)
        for node in handles:
            np.testing.assert_allclose(
                node[3], _transform(transform_values(data, node)), atol=1e-6
            )
        points = np.array([v[0] for part in load_parts(data) for v in part["vertices"]])
        self.assertGreater(points[:, 2].min(), -0.02)
        self.assertLess(points[:, 2].max(), 0.8)

    def test_authoring_meshes_are_preserved_but_hidden_by_default(self):
        data = self.resource("cribs/fx_crib_cheap")
        raw = parse_rdms(data)[0]
        self.assertTrue(any(part["helper"] for part in raw))
        self.assertTrue(all(not part["helper"] for part in load_parts(data)))
        self.assertEqual(len(load_parts(data, include_helpers=True)), len(raw))

    def test_picking_and_footprint_geometry_do_not_cover_the_shower(self):
        data = self.resource("showers/fx_showercommunal")
        self.assertEqual(len(load_parts(data)), 1)
        self.assertEqual(len(load_parts(data, include_helpers=True)), 3)
        self.assertIsNotNone(load_parts(data)[0]["texture"])

    def test_textured_footprint_shadow_is_preserved(self):
        data = self.resource("chairs/fx_chairlivingclub")
        self.assertEqual(len(load_parts(data)), len(parse_rdms(data)[0]))

    def test_helper_only_effect_is_inspectable(self):
        data = self.resource("showers/fx_o-shower-opendoor")
        self.assertEqual(load_parts(data), [])
        self.assertTrue(load_parts(data, include_helpers=True))

    def test_export_keeps_textured_shader_tint(self):
        data = self.resource("chairs/fx_chair_dining_value")
        parts = load_parts(data)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chair.glb"
            convert_prop(data, "chair", path)
            doc, _ = open_glb(path)
        for part, material in zip(parts, doc["materials"]):
            np.testing.assert_allclose(
                material["pbrMetallicRoughness"]["baseColorFactor"], part["base_color"]
            )
