"""Focused regression tests for the PSP Z-up to glTF Y-up conversion."""

import math
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from ts2psp.formats.animations import attach_animations, normalize, open_glb, qinv, qmul
from ts2psp.formats.characters import GLB
from ts2psp.core.coordinates import convert_quaternion, convert_vector3
from ts2psp.rendering.animation import posiciones_visorde_z_up, quat_matrix


def accessor_values(doc, binary, accessor_index, components):
    accessor = doc["accessors"][accessor_index]
    view = doc["bufferViews"][accessor["bufferView"]]
    offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    count = accessor["count"] * components
    return struct.unpack_from("<" + "f" * count, binary, offset)


def gltf_matrix(values):
    return np.asarray(values, dtype=np.float64).reshape(4, 4).T


class CoordinateConversionTests(unittest.TestCase):
    def test_animation_preview_maps_gltf_y_up_to_viewer_z_up(self):
        points = np.asarray(((1.0, 2.0, 3.0), (0.0, 1.0, 0.0)))
        np.testing.assert_allclose(
            posiciones_visorde_z_up(points), ((-1.0, 3.0, 2.0), (0.0, 0.0, 1.0))
        )

    def test_animation_bone_rotation_survives_viewer_axis_conversion(self):
        point = np.asarray(((0.7, -1.2, 2.3),))
        source_rotation = normalize((0.23, -0.31, 0.4, 0.84))
        gltf_point = np.asarray([convert_vector3(point[0])])
        gltf_rotation = convert_quaternion(source_rotation)
        gltf_pose = gltf_point @ quat_matrix(gltf_rotation).T
        viewer_pose = posiciones_visorde_z_up(gltf_pose)
        source_pose = point @ quat_matrix(source_rotation).T

        np.testing.assert_allclose(viewer_pose, source_pose, atol=1e-7)

    def test_mesh_skin_morphs_and_animations_use_y_up_basis(self):
        glb = GLB()
        world = np.eye(4)
        angle = 0.6
        world[:3, :3] = (
            (1.0, 0.0, 0.0),
            (0.0, math.cos(angle), math.sin(angle)),
            (0.0, -math.sin(angle), math.cos(angle)),
        )
        world[3, :3] = (1.0, 2.0, 3.0)
        bone = {
            "name": "joint",
            "parent": -1,
            "world": world,
            "ibm": np.linalg.inv(world),
        }
        skin = glb.add_skin([bone])
        part = {
            "name": "body",
            "palette": [0],
            "vertices": [((1000, 2000, 3000), (0, 0, 127), (0, 0), [128])],
            "indices": [0, 0, 0],
            "morph_positions": [[(100, 200, 300)]],
            "morph_names": ["smile"],
        }
        glb.add_mesh("character", [part], skin, 1)

        primitive = glb.doc["meshes"][0]["primitives"][0]
        positions = accessor_values(
            glb.doc, glb.bin, primitive["attributes"]["POSITION"], 3
        )
        normals = accessor_values(
            glb.doc, glb.bin, primitive["attributes"]["NORMAL"], 3
        )
        morph = accessor_values(
            glb.doc, glb.bin, primitive["targets"][0]["POSITION"], 3
        )
        self.assertEqual(positions, (-1.0, 3.0, 2.0))
        self.assertEqual(normals, (0.0, 1.0, 0.0))
        np.testing.assert_allclose(morph, (-0.1, 0.3, 0.2), atol=1e-7)
        self.assertEqual(glb.doc["meshes"][0]["extras"]["targetNames"], ["smile"])
        self.assertAlmostEqual(np.linalg.norm(positions), math.sqrt(14.0))
        self.assertEqual(sorted(abs(v) for v in positions), [1.0, 2.0, 3.0])

        inverse_bind_accessor = glb.doc["skins"][0]["inverseBindMatrices"]
        inverse_bind = gltf_matrix(
            accessor_values(glb.doc, glb.bin, inverse_bind_accessor, 16)
        )
        joint = gltf_matrix(glb.doc["nodes"][0]["matrix"])
        np.testing.assert_allclose(joint @ inverse_bind, np.eye(4), atol=1e-6)
        np.testing.assert_allclose(joint[:3, 3], (-1.0, 3.0, 2.0), atol=1e-6)

        rotation = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))
        expected_rotation = (0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5))
        np.testing.assert_allclose(
            convert_quaternion(rotation), expected_rotation, atol=1e-7
        )

        translation_track = {
            "name": "joint",
            "kind": "translation",
            "base": (0.1, 0.2, 0.3),
            "times": (),
            "varying": 0,
            "animated_components": (),
            "curves": (),
        }
        rotation_track = {
            "name": "joint",
            "kind": "rotation",
            "base": rotation,
            "times": (),
            "varying": 0,
            "animated_components": (),
            "curves": (),
        }
        reference_translation = {
            "name": "joint",
            "kind": "translation",
            "base": (0.4, 0.5, 0.6),
            "times": (),
            "varying": 0,
            "animated_components": (),
            "curves": (),
        }
        reference_rotation = {
            "name": "joint",
            "kind": "rotation",
            "base": (0.0, 0.5, 0.0, math.sqrt(0.75)),
            "times": (),
            "varying": 0,
            "animated_components": (),
            "curves": (),
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.glb"
            output = Path(directory) / "animated.glb"
            glb.save(source)
            attach_animations(
                source,
                [
                    {
                        "name": "test",
                        "duration": 1.0,
                        "tracks": [translation_track, rotation_track],
                    }
                ],
                {"tracks": [reference_translation, reference_rotation]},
                output,
                fps=1,
            )
            doc, binary = open_glb(output)

        channels = {
            channel["target"]["path"]: doc["accessors"][
                doc["animations"][0]["samplers"][channel["sampler"]]["output"]
            ]
            for channel in doc["animations"][0]["channels"]
        }
        translation = accessor_values(
            doc,
            binary,
            doc["animations"][0]["samplers"][
                next(
                    c["sampler"]
                    for c in doc["animations"][0]["channels"]
                    if c["target"]["path"] == "translation"
                )
            ]["output"],
            3,
        )
        animated_rotation = accessor_values(
            doc,
            binary,
            doc["animations"][0]["samplers"][
                next(
                    c["sampler"]
                    for c in doc["animations"][0]["channels"]
                    if c["target"]["path"] == "rotation"
                )
            ]["output"],
            4,
        )
        np.testing.assert_allclose(translation[:3], (-0.7, 2.7, 1.7), atol=1e-7)
        bind_rotation = doc["nodes"][0]["rotation"]
        expected_animated_rotation = normalize(
            qmul(
                convert_quaternion(rotation),
                qmul(
                    qinv(convert_quaternion(reference_rotation["base"])), bind_rotation
                ),
            )
        )
        np.testing.assert_allclose(
            animated_rotation[:4], expected_animated_rotation, atol=1e-7
        )
        np.testing.assert_allclose(
            animated_rotation[4:], expected_animated_rotation, atol=1e-7
        )
        self.assertEqual(channels["translation"]["type"], "VEC3")


if __name__ == "__main__":
    unittest.main()
