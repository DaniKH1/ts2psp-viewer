"""Regression tests for exporting GLB models as external-file glTF."""

import json
import tempfile
import unittest
from pathlib import Path

from ts2psp.formats.characters import GLB
from ts2psp.exporting.gltf import export_gltf


class GltfExportTests(unittest.TestCase):
    def test_exports_binary_and_embedded_textures_as_external_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.glb"
            target = root / "model.gltf"
            texture = b"\x89PNG\r\n\x1a\ntexture payload"
            glb = GLB()
            glb.add_texture("body/diffuse", texture)
            glb.accessor(b"\x00" * 12, 5126, "VEC3", 1)
            glb.save(source)

            export_gltf(source, target)

            document = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(document["buffers"][0]["uri"], "model.bin")
            self.assertEqual(
                (root / "model.bin").read_bytes(),
                bytes(glb.bin[: document["buffers"][0]["byteLength"]]),
            )
            image = document["images"][0]
            self.assertIn("uri", image)
            self.assertNotIn("bufferView", image)
            self.assertEqual((root / image["uri"]).read_bytes(), texture)

    def test_rejects_non_gltf_target(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, r"\.gltf extension"):
                export_gltf(
                    Path(directory) / "missing.glb", Path(directory) / "model.glb"
                )


if __name__ == "__main__":
    unittest.main()
