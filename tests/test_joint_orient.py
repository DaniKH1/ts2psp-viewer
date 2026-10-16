"""BANM joint rotations must retain fixed BODY orientations."""

import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import numpy as np

from ts2psp.formats.animations import (
    attach_animations,
    bone_rotation,
    open_glb,
    parse_banm,
)
from ts2psp.formats.characters import convert_character, parse_bones, section
from ts2psp.rendering.animation import (
    accessor,
    posed_meshes,
    posiciones_visorde_z_up,
    quat_matrix,
)

DATA = Path(
    os.environ.get("TS2_PSP_DATA_DIR", Path(__file__).resolve().parents[1] / "data")
)
CHARACTERS = DATA / "sims2_psp_personajes_recursos.zip"
ANIMATIONS = DATA / "sims2_psp_animaciones_banm.zip"


def constant_track(name, kind, values):
    return {
        "name": name,
        "kind": kind,
        "base": values,
        "times": (),
        "curves": (),
        "varying": 0,
        "animated_components": (),
    }


class OrientationOrderTests(unittest.TestCase):
    def test_orientation_precedes_animated_rotation(self):
        angle = np.sqrt(0.5)
        orient, animated = (angle, 0, 0, angle), (0, angle, 0, angle)
        # Supply the track in PSP basis; its converted rotation is about Z.
        result = bone_rotation(
            constant_track("joint", "rotation", animated),
            0,
            {"extras": {"pspJointOrient": orient}},
        )
        expected = quat_matrix(orient) @ quat_matrix((0, 0, angle, angle))
        np.testing.assert_allclose(quat_matrix(result), expected, atol=1e-8)


@unittest.skipUnless(
    CHARACTERS.exists() and ANIMATIONS.exists(),
    "Set TS2_PSP_DATA_DIR for creature animation checks",
)
class CreatureOrientationTests(unittest.TestCase):
    def test_authored_rest_channels_reconstruct_the_mesh_without_deformation(self):
        for character in ("cowthulu", "nightbeast"):
            with (
                self.subTest(character=character),
                tempfile.TemporaryDirectory() as folder,
            ):
                with zipfile.ZipFile(CHARACTERS) as archive:
                    data = archive.read(
                        f"mallas/characters/bodyanims/body/aubody{character}.res"
                    )
                off, _, _ = section(data, b"body")
                tracks = []
                for index, bone in enumerate(parse_bones(data)):
                    at = off + 0xB0 + index * 0xA0
                    tracks.extend(
                        (
                            constant_track(
                                bone["name"],
                                "rotation",
                                struct.unpack_from("<4f", data, at + 0x30),
                            ),
                            constant_track(
                                bone["name"],
                                "translation",
                                struct.unpack_from("<3f", data, at + 0x50),
                            ),
                        )
                    )
                clip = {"name": "authored-rest", "duration": 1, "tracks": tracks}
                model, animated = (
                    Path(folder) / "model.glb",
                    Path(folder) / "animated.glb",
                )
                convert_character(CHARACTERS, character, model)
                doc, binary = open_glb(model)
                expected = [
                    posiciones_visorde_z_up(
                        accessor(
                            doc, binary, mesh["primitives"][0]["attributes"]["POSITION"]
                        )
                    )
                    for mesh in doc["meshes"]
                ]
                attach_animations(model, [clip], None, animated, fps=1)
                doc, binary = open_glb(animated)
                for (_, vertices, _), original in zip(
                    posed_meshes(doc, binary, doc["animations"][0], 0), expected
                ):
                    np.testing.assert_allclose(vertices, original, atol=2e-5)

    def test_all_cowthulu_clips_preserve_fixed_spine_orientation(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            zipfile.ZipFile(ANIMATIONS) as archive,
        ):
            catalog = json.loads(archive.read("catalog.json"))
            rows = [r for r in catalog if r["category"] == "cowthulu_anims"]
            clips = [parse_banm(archive.read(row["file"])) for row in rows]
            model, animated = Path(folder) / "model.glb", Path(folder) / "animated.glb"
            convert_character(CHARACTERS, "cowthulu", model)
            for clip in clips:
                with self.subTest(clip=clip["name"]):
                    suffix = "-mon" if clip["name"].endswith("-mon") else ""
                    fallback = next(
                        c
                        for c in clips
                        if c["name"].endswith("/a-cowthulu-idle-breathe" + suffix)
                    )
                    attach_animations(
                        model, [clip], None, animated, fps=20, fallback=fallback
                    )
                    doc, binary = open_glb(animated)
                    # With orientation retained, spine and left clavicle channels
                    # stay near their authored coordinate basis throughout a clip.
                    spine = next(
                        i
                        for i, n in enumerate(doc["nodes"])
                        if n["name"] == "__spine0__"
                    )
                    channel = next(
                        c
                        for c in doc["animations"][0]["channels"]
                        if c["target"] == {"node": spine, "path": "rotation"}
                    )
                    values = accessor(
                        doc,
                        binary,
                        doc["animations"][0]["samplers"][channel["sampler"]]["output"],
                    )
                    orient = doc["nodes"][spine]["extras"]["pspJointOrient"]
                    self.assertGreater(np.min(np.abs(values @ np.array(orient))), 0.75)
                    for t in (0, clip["duration"] * 0.5, clip["duration"]):
                        self.assertTrue(
                            all(
                                np.isfinite(v).all()
                                for _, v, _ in posed_meshes(
                                    doc, binary, doc["animations"][0], t
                                )
                            )
                        )
