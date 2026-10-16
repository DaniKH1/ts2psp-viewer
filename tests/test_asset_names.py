"""Texture names survive SURF decoding, layer labels and model exports."""

import csv
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath
from unittest.mock import Mock

from PIL import Image

from ts2psp.core.names import layer_label, part_label, texture_paths
from ts2psp.exporting.gltf import export_gltf
from ts2psp.formats.animations import open_glb
from ts2psp.formats.materials import surface_textures
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.surfaces import read_surface
from ts2psp.ui.maps import MapTab


def surface_bundle(header, colors):
    data = bytearray(512 * len(colors))
    struct.pack_into("<I", data, 12, header)
    records = []
    for i, color in enumerate(colors):
        off, head = i * 512, i * 512 + header
        source = b"C:/project/data/textures/txt_shared.tif\0"
        struct.pack_into("<iIHH", data, head + 4, 0x3C, 0x01000018, 1, 1)
        struct.pack_into("<I", data, head + 0x14, 0x1C)
        struct.pack_into("<H", data, head + 0x30, color)
        data[head + 0x40 : head + 0x40 + len(source)] = source
        records.append((b"surf", off, 384, 1, 0))
    return bytes(data), records


class NamingTests(unittest.TestCase):
    def test_map_surface_pixels_can_end_at_the_declared_section_boundary(self):
        # Indexed counter bitmaps occupy the full section in 160-byte bundles.
        header, size = 160, 0x4A0
        data = bytearray(header + size)
        struct.pack_into("<I", data, 12, header)
        source = b"textures/design_mode/counters/counter01.tif\0"
        data[48 : 48 + len(source)] = source
        struct.pack_into("<iIHH", data, header + 4, 48 - header - 4, 0x0100011D, 16, 8)
        struct.pack_into("<II", data, header + 0x10, 8, 0x40C)
        struct.pack_into("<II", data, header + 0x18, 8, 0x00050100)
        data[header + 0x20 : header + 0x24] = bytes((12, 34, 56, 255))
        png, name = read_surface(bytes(data), 0, size)
        self.assertEqual(name, source[:-1].decode())
        self.assertEqual(
            Image.open(io.BytesIO(png)).getpixel((15, 7)), (12, 34, 56, 255)
        )
        self.assertEqual(read_surface(bytes(data), 0, size - 1), (None, ""))

    def test_names_come_from_the_decoded_header_in_all_resource_layouts(self):
        for header in (96, 128, 160):
            with self.subTest(header=header):
                data, _ = surface_bundle(header, [0xFFFF])
                png, name = read_surface(data, 0, 384)
                self.assertTrue(png.startswith(b"\x89PNG"))
                self.assertEqual(name, "textures/txt_shared.tif")

    def test_distinct_bitmaps_with_the_same_source_are_not_overwritten(self):
        data, records = surface_bundle(160, [0xFFFF, 0xF00F, 0xFFFF, 0xF00F])
        textures = [texture for _, texture, _ in surface_textures(data, records)]
        self.assertEqual(textures[0], textures[2])
        self.assertEqual(textures[1], textures[3])
        self.assertNotEqual(textures[0][0], textures[1][0])
        self.assertNotEqual(textures[0][1], textures[1][1])
        self.assertIn("txt_shared", textures[1][0])

    def test_export_paths_keep_internal_names_and_cannot_collide_or_escape(self):
        names = [
            "textures/chair.tif",
            "textures/chair.png",
            "Textures/CHAIR.tif",
            "../../C:/bad/../chair.tif",
        ]
        paths = texture_paths(names)
        self.assertEqual(paths[names[0]], "textures/chair.png")
        self.assertEqual(len({path.casefold() for path in paths.values()}), 4)
        for path in paths.values():
            self.assertFalse(PurePosixPath(path).is_absolute())
            self.assertNotIn("..", PurePosixPath(path).parts)
            self.assertNotIn(":", path)


ARCHIVE = (
    Path(os.environ.get("TS2_PSP_DATA_DIR", "data")) / "sims2_psp_mapas_recursos.zip"
)


@unittest.skipUnless(
    ARCHIVE.is_file(), "Set TS2_PSP_DATA_DIR to imported ISO resources"
)
class MapNamingTests(unittest.TestCase):
    def test_espiritu_counters_resolve_their_authored_texture(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            rows = csv.DictReader(
                io.StringIO(archive.read("inventario_mapas.csv").decode("utf-8-sig"))
            )
            row = next(
                r for r in rows if r["ruta_interna"] == "levels/paradise_place/espiritu"
            )
            parts, rejected = parse_rdms(
                archive.read(row["archivo_zip"]), ruta=row["ruta_interna"]
            )
        self.assertFalse(rejected)
        counters = [
            p
            for p in parts
            if p.get("resource_path", "").startswith("objects/counters/")
        ]
        self.assertEqual(len(counters), 51)
        for part in counters:
            self.assertEqual(
                part["texture"][0], "textures/design_mode/counters/counter01.tif"
            )
            self.assertNotIn("texture_warning", part)

    def test_layers_glb_gltf_and_png_keep_the_same_texture_identity(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            rows = csv.DictReader(
                io.StringIO(archive.read("inventario_mapas.csv").decode("utf-8-sig"))
            )
            row = next(r for r in rows if r["ruta_interna"] == "levels/deadtree/hub")
            data = archive.read(row["archivo_zip"])
        parts, _ = parse_rdms(data)
        textured = [p for p in parts if p.get("texture")][:12]
        self.assertTrue(textured)
        tab = object.__new__(MapTab)
        tab.parts, tab.ocultas = textured, {textured[0]["name"]}
        tab.layer_filter = Mock()
        tab.layer_filter.get.return_value = ""
        labels = tab._filas_de_capas()
        self.assertTrue(labels[0].startswith("[ ]"))
        originals = {
            texture[0]: texture[1]
            for _, texture, _ in surface_textures(data, sections(data))
            if texture
        }
        for part, label in zip(textured, labels):
            self.assertEqual(label[4:], layer_label(part))
            self.assertEqual(part["texture"][1], originals[part["texture"][0]])
            self.assertNotIn("surface_", part["texture"][0])
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "map.glb", Path(directory) / "map.gltf"
            write_rigid_glb(textured, row["ruta_interna"], source)
            doc, _ = open_glb(source)
            for mesh, part in zip(doc["meshes"], textured):
                self.assertIn(part_label(part), mesh["name"])
                self.assertEqual(mesh["extras"]["sourcePart"], part["name"])
                self.assertEqual(mesh["extras"]["sourceTexture"], part["texture"][0])
            self.assertEqual(len(set(m["name"] for m in doc["meshes"])), len(textured))
            export_gltf(source, target)
            exported = json.loads(target.read_text(encoding="utf-8"))
            for image in exported["images"]:
                self.assertEqual(
                    (target.parent / image["uri"]).read_bytes(),
                    originals[image["name"]],
                )
                self.assertEqual(
                    Path(image["uri"]).stem, PurePosixPath(image["name"]).stem
                )


if __name__ == "__main__":
    unittest.main()
