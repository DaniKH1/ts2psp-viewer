"""Packed PSP color channels, stream alignment and cached shading switches."""

import struct
import unittest
from unittest.mock import Mock

import numpy as np

from ts2psp.formats.materials import assign_material
from ts2psp.formats.props import decode_color, parse_rdms
from ts2psp.rendering.opengl import Escena
from ts2psp.rendering.props import without_vertex_colors
from ts2psp.ui.maps import MapTab


class VertexColorTests(unittest.TestCase):
    def test_raw_color_toggle_preserves_source_alpha_geometry_and_exported_colors(self):
        import tempfile
        from pathlib import Path
        from ts2psp.formats.animations import open_glb
        from ts2psp.formats.props import write_rigid_glb
        from ts2psp.rendering.animation import accessor

        part = {
            "name": "terrain",
            "source_texture_path": "textures/terrain/ground.tif",
            "vertices": [
                (p, (0, 0, 1), (0, 0), (0, 0, 0, 0.25))
                for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0))
            ],
            "indices": [0, 1, 2],
            "color": (128, 128, 128, 255),
            "alpha_mode": "BLEND",
        }
        scene = Escena.__new__(Escena)
        scene.tipo = "prop"
        scene._cargar([part], {})
        geometry, source = scene.posiciones, scene._colores_vertex.copy()
        scene.fijar_vertex_colors(False)
        np.testing.assert_allclose(scene.colores[0], (128 / 255,) * 3 + (1,))
        scene.fijar_vertex_colors(True)
        np.testing.assert_array_equal(scene.colores[0], (0, 0, 0, 0.25))
        self.assertIs(scene.posiciones, geometry)
        np.testing.assert_array_equal(scene._colores_vertex, source)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "source.glb"
            write_rigid_glb([part], "source", target)
            doc, binary = open_glb(target)
        color_index = doc["meshes"][0]["primitives"][0]["attributes"]["COLOR_0"]
        np.testing.assert_allclose(
            accessor(doc, binary, color_index), [(0, 0, 0, 0.25)] * 3
        )

    def test_packed_colors_match_psp_byte_expansion_and_rgba_order(self):
        cases = [
            (4, struct.pack("<H", 3 | (17 << 5) | (29 << 11)), (24, 69, 239, 255)),
            (
                5,
                struct.pack("<H", 3 | (17 << 5) | (29 << 10) | 0x8000),
                (24, 140, 239, 255),
            ),
            (5, struct.pack("<H", 31), (255, 0, 0, 0)),
            (6, struct.pack("<H", 0x8421), (17, 34, 68, 136)),
            (7, bytes((12, 34, 56, 78)), (12, 34, 56, 78)),
        ]
        for code, raw, expected in cases:
            with self.subTest(code=code, raw=raw):
                np.testing.assert_allclose(
                    np.asarray(decode_color(raw, code)) * 255, expected
                )

    def test_u8_uvs_do_not_shift_aligned_rgba8888_or_float_normals(self):
        data = bytearray(1024)
        data[:4] = b"res\n"
        struct.pack_into("<I", data, 12, 128)
        struct.pack_into("<I", data, 20, 768)
        struct.pack_into("<I", data, 768, 1)
        struct.pack_into("<4s4I", data, 776, b"rdms", 0, 512, 1, 0)
        head = 128
        struct.pack_into("<I", data, head + 8, 3)
        struct.pack_into("<I", data, head + 12, 224 - head - 12)
        struct.pack_into("<I", data, head + 16, 3)
        struct.pack_into("<I", data, head + 24, 256 - head - 24)
        struct.pack_into("<f", data, head + 40, 1)
        struct.pack_into("<f", data, head + 76, 1)
        vtype = 0x12000000 | 1 | (7 << 2) | (3 << 5) | (3 << 7) | (1 << 11)
        struct.pack_into("<I", data, head + 56, vtype)
        struct.pack_into("<I", data, head + 64, 32)
        data[224:227] = bytes((0, 1, 2))
        for i, pos in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
            at = 256 + i * 32
            data[at : at + 8] = bytes((0, 128, 170, 170, 255, 64, 32, 128))
            struct.pack_into("<3f", data, at + 8, 0, 0, 1)
            struct.pack_into("<3f", data, at + 20, *pos)
        parts, rejected = parse_rdms(data, apply_scene=False)
        self.assertFalse(rejected)
        self.assertEqual(len(parts), 1)
        np.testing.assert_allclose(
            parts[0]["vertices"][0][3], np.array((255, 64, 32, 128)) / 255
        )
        self.assertEqual(parts[0]["vertices"][0][1], (0, 0, 1))
        self.assertEqual(parts[0]["alpha_mode"], "BLEND")

    def test_vertex_alpha_and_export_copies_preserve_source_data(self):
        for vertex_mode in ("BLEND", "MASK"):
            part = {
                "vertices": [((0, 0, 0), None, None, (1, 0, 0, 0))],
                "vertex_alpha_mode": vertex_mode,
            }
            assign_material(part, {"base_color": (1, 1, 1, 1)}, [None])
            self.assertEqual(part["alpha_mode"], vertex_mode)
            copy = without_vertex_colors([part])[0]
            self.assertIsNone(copy["vertices"][0][3])
            self.assertIsNone(copy["alpha_mode"])
            self.assertEqual(part["vertices"][0][3], (1, 0, 0, 0))
            assign_material(part, {"base_color": (1, 1, 1, 1)}, [None], modo="OPAQUE")
            self.assertEqual(part["alpha_mode"], "OPAQUE")

    def test_switch_reuses_geometry_textures_and_current_animated_pose(self):
        scene = Escena.__new__(Escena)
        scene.tipo = "prop"
        scene._subir_textura = Mock(return_value=7)
        part = {
            "name": "triangle",
            "vertices": [
                ((0, 0, 0), None, (0, 0), (1, 0, 0, 0.5)),
                ((1, 0, 0), None, (1, 0), (0, 1, 0, 1)),
                ((0, 1, 0), None, (0, 1), (0, 0, 1, 1)),
            ],
            "color": (128, 128, 128, 255),
            "indices": [0, 1, 2],
            "alpha_mode": "BLEND",
            "alpha_mode_without_vertex_colors": "OPAQUE",
        }
        scene._cargar(
            [("texture", part)], {"texture": np.full((1, 1, 4), 255, np.uint8)}
        )
        geometry, uv, draws = scene.posiciones, scene.UV, scene.dibujos
        colored = scene.colores
        for _ in range(10):
            scene.fijar_vertex_colors(False)
            np.testing.assert_allclose(
                scene.colores[0], [128 / 255, 128 / 255, 128 / 255, 1]
            )
            self.assertEqual(scene.alpha_modes, ["OPAQUE"])
            scene.fijar_vertex_colors(True)
            self.assertIs(scene.colores, colored)
            self.assertEqual(scene.alpha_modes, ["BLEND"])
        self.assertIs(scene.posiciones, geometry)
        self.assertIs(scene.UV, uv)
        self.assertIs(scene.dibujos, draws)
        self.assertEqual(scene._subir_textura.call_count, 1)
        scene.fijar_poses(geometry + 1)
        pose = scene.posiciones
        scene.fijar_vertex_colors(False)
        self.assertIs(scene.posiciones, pose)

    def test_map_switch_only_schedules_redraw(self):
        state = Mock()
        state.vertex_colors.get.return_value = False
        MapTab.toggle_vertex_colors(state)
        state.vertex_colors.set.assert_called_once_with(True)
        state.schedule.assert_called_once()
        state.reload.assert_not_called()
