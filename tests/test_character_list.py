"""Focused regression tests for characters with nonstandard mesh assignments."""

import io
import csv
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from ts2psp.rendering.animation import (
    accessor,
    glb_piezas,
    posed_meshes,
    validate_preview_frame,
)
from ts2psp.formats.animations import (
    REFERENCE_F,
    attach_animations,
    matrix_trs,
    normalize,
    open_glb,
    parse_banm,
    sample,
)
from ts2psp.core.coordinates import convert_quaternion, convert_vector3
from ts2psp.formats.characters import (
    TextureLibrary,
    convert_character,
    align_hand_bind_pose,
    arm_bind_offset,
    descendientes,
    hand_bind_offset,
    HUEGO_MANO_FRACCION,
    available_face_archetypes,
    chosen_face_archetype,
    character_mesh_paths,
    character_mesh_texture,
    character_morph_name,
    character_texture_key,
    find_character_mesh,
    find_mesh,
    load_character_rows,
    MALLAS_CARA_Y_CUERPO,
    PERSONAJES_OCULTOS,
    nombre_publico,
    slug_publico,
    parse_bones,
    parse_character_mesh,
    parse_character_parts,
    parse_mesh,
    select_character_row,
    species_body_single,
)
from ts2psp.rendering.characters import (
    _shape_positions,
    list_characters,
    load_character,
    shape_key_options,
)
import ts2psp.rendering.opengl as gl

ROOT = Path(__file__).resolve().parent.parent
import os

DATA_DIR = Path(os.environ.get("TS2_PSP_DATA_DIR", ROOT / "data"))
CHARACTERS = DATA_DIR / "sims2_psp_personajes_recursos.zip"
ANIMATIONS = DATA_DIR / "sims2_psp_animaciones_banm.zip"


@unittest.skipUnless(CHARACTERS.is_file(), "Set TS2_PSP_DATA_DIR for character checks")
class CharacterListTests(unittest.TestCase):
    @unittest.skipUnless(
        ANIMATIONS.is_file(), "Set TS2_PSP_DATA_DIR for animation checks"
    )
    def test_emily_hat_follows_head_animation_without_deformation(self):
        with zipfile.ZipFile(ANIMATIONS) as archive:
            rows = csv.DictReader(
                io.TextIOWrapper(archive.open("animaciones.csv"), encoding="utf-8-sig")
            )
            row = next(row for row in rows if row["name"] == REFERENCE_F)
            clip = parse_banm(archive.read(row["file"]))
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "emily.glb"
            animated = Path(directory) / "emily_animated.glb"
            convert_character(CHARACTERS, "emilyemory", model)
            attach_animations(model, [clip], None, animated, fps=20, fallback=clip)
            doc, binary = open_glb(animated)
            frames = [
                next(
                    vertices
                    for name, vertices, _ in posed_meshes(
                        doc, binary, doc["animations"][0], time
                    )
                    if "hair/afhairhatmaid" in name
                )
                for time in (0, 1)
            ]
            self.assertGreater(float(np.max(np.abs(frames[1] - frames[0]))), 0.001)
            np.testing.assert_allclose(
                np.linalg.norm(frames[0] - frames[0][0], axis=1),
                np.linalg.norm(frames[1] - frames[1][0], axis=1),
                atol=1e-6,
            )

    def test_emily_loads_and_exports_the_maid_hat_with_its_head_weights(self):
        path = "hair/afhairhatmaid"
        paths, parts, bones, textures = load_character(CHARACTERS, "emilyemory")
        self.assertIn(path, paths)
        self.assertIn("hair/afhairshortlayered", paths)
        hat = [part for mesh, part in parts if mesh == path]
        self.assertEqual(len(hat), 1)
        self.assertEqual(len(hat[0]["vertices"]), 13)
        self.assertEqual(
            [bones[index]["name"] for index in hat[0]["palette"]], ["__Head__"]
        )
        self.assertTrue(
            all(tuple(vertex[3]) == (128,) for vertex in hat[0]["vertices"])
        )
        self.assertEqual(textures[path][:, :, 3].min(), 0)
        self.assertEqual(textures[path][:, :, 3].max(), 255)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "emily.glb"
            convert_character(CHARACTERS, "emilyemory", target)
            doc, binary = open_glb(target)
            meshes = [mesh for mesh in doc["meshes"] if path in mesh["name"]]
            self.assertEqual(len(meshes), 1)
            primitive = meshes[0]["primitives"][0]
            attributes = primitive["attributes"]
            self.assertEqual(doc["accessors"][attributes["POSITION"]]["count"], 13)
            head = next(i for i, bone in enumerate(bones) if bone["name"] == "__Head__")
            np.testing.assert_array_equal(
                accessor(doc, binary, attributes["JOINTS_0"])[:, 0], head
            )
            material = doc["materials"][primitive["material"]]
            self.assertEqual(material["alphaMode"], "MASK")
            self.assertIn("baseColorTexture", material["pbrMetallicRoughness"])
            self.assertEqual(len(doc["skins"]), 1)

    def test_face_archetypes_keep_their_linked_cas_shape_keys(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            rows = load_character_rows(archive)
            emily = select_character_row(rows, "emilyemory")
            self.assertEqual(
                chosen_face_archetype(archive, emily), "face/cas/afarchheart"
            )
            choices = available_face_archetypes(archive, "face/afface")
            self.assertIn("face/cas/afarchcmed", choices)
            self.assertIn("face/cas/afarchheart", choices)
            self.assertGreaterEqual(len(choices), 27)

        paths, parts, _, _ = load_character(CHARACTERS, "emilyemory")
        self.assertIn("face/cas/afarchheart", paths)
        face = next(
            (path, part) for path, part in parts if path == "face/cas/afarchheart"
        )
        names = face[1]["morph_names"]
        self.assertIn("Face archetype: afarchcmed", names)
        self.assertIn("Face modifier: nosebackfwd", names)
        self.assertEqual(sum(name.startswith("Face modifier: ") for name in names), 22)
        self.assertEqual(len(names), len(face[1]["morph_positions"]))
        options = shape_key_options([face], preserve_morph_ids=True)
        shape_id = next(
            identity
            for name, identity in options
            if name == "Face modifier: nosebackfwd"
        )
        neutral = _shape_positions([face], "", 0.0, shape_id)
        modified = _shape_positions([face], "", 1.0, shape_id)
        self.assertEqual(sum(a != b for a, b in zip(neutral, modified)), 16)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "emily.glb"
            convert_character(CHARACTERS, "emilyemory", target)
            doc, _ = open_glb(target)
            face_mesh = next(
                mesh for mesh in doc["meshes"] if "face/cas/afarchheart" in mesh["name"]
            )
            self.assertIn(
                "Face modifier: nosebackfwd", face_mesh["extras"]["targetNames"]
            )
            self.assertIn(
                "Face archetype: afarchcmed", face_mesh["extras"]["targetNames"]
            )

    def test_all_catalog_characters_load(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)
        self.assertEqual(len(names), 86)
        for name in names:
            with self.subTest(character=name):
                paths, parts, bones, _ = load_character(CHARACTERS, name)
                self.assertTrue(paths and parts and bones)

    def test_every_listed_character_has_a_public_name(self):
        """Que entre un personaje nuevo no se quede con el slug en las listas."""
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)
        self.assertEqual([n for n in names if nombre_publico(n) == n], [])
        self.assertEqual(slug_publico("Tycho Curious"), "tychocurious")
        self.assertEqual(slug_publico("Tina Mansfield (Alt)"), "player")
        # y que el nombre nuevo no se confunda con el de Tina normal
        self.assertEqual(slug_publico("Tina Mansfield"), "tinamansfield")

    def test_hidden_characters_stay_out_of_the_lists_but_still_load(self):
        """Ocultarlos es solo de las listas: el slug sigue funcionando."""
        self.assertEqual(PERSONAJES_OCULTOS, ("fplayer", "mplayer"))
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)
        for slug in PERSONAJES_OCULTOS:
            self.assertNotIn(slug, names)
            paths, parts, bones, _ = load_character(CHARACTERS, slug)
            self.assertTrue(paths and parts and bones, slug)
        # player no se oculta: es el que usan los clips que dicen 'player'
        self.assertIn("player", names)

    def test_baby_uses_body_single_mesh_from_the_species_resource(self):
        """Tycho no trae mallas en el CSV: van declaradas en su recurso."""
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)
            row = next(r for r in load_character_rows(archive)
                       if r["personaje"] == "tychocurious")
            for campo in ("bodyTorso", "bodyLegs", "bodyHead", "bodyHair"):
                self.assertFalse(row.get(campo), campo)
            self.assertEqual(species_body_single(archive, row), "body/bubodydiaper")
            self.assertEqual(character_mesh_paths(archive, row),
                             ["body/bubodydiaper"])
        self.assertIn("tychocurious", names)
        # los objetos con bodySingle (camas, libro, coche) siguen fuera
        for fuera in ("beddoublebasic", "book", "moneytree", "plumbobcar"):
            self.assertNotIn(fuera, names)

    def test_baby_face_parts_use_the_face_texture(self):
        """La malla del bebe lleva cuerpo y cara: se reparten por pieza."""
        paths, parts, bones, textures = load_character(CHARACTERS, "tychocurious")
        self.assertEqual(paths, ["body/bubodydiaper"])
        self.assertEqual(len(bones), 37)
        cuerpo, cara, partes_cara = MALLAS_CARA_Y_CUERPO["body/bubodydiaper"]
        self.assertEqual(partes_cara, ("pieza_8",))
        self.assertIn("body/bubodydiaper#cara", textures)
        self.assertIn("body/bubodydiaper#cuerpo", textures)
        self.assertTrue(cara.endswith("buface-s5"))
        self.assertTrue(cuerpo.endswith("bubodynaked-nude-s5"))
        self.assertEqual(character_texture_key(None, paths[0], "pieza_8"),
                         "body/bubodydiaper#cara")
        self.assertEqual(character_texture_key(None, paths[0], "principal"),
                         "body/bubodydiaper#cuerpo")

    def test_zenmu_uses_two_part_asian_face_archetype(self):
        paths, parts, bones, textures = load_character(CHARACTERS, "zenmu")
        faces = [part for path, part in parts if path == "face/cas/amarcheasi"]
        self.assertEqual(len(faces), 1)
        self.assertEqual(len(faces[0]["vertices"]), 318)
        self.assertTrue(faces[0]["indices"])
        self.assertTrue(faces[0]["morph_positions"])
        self.assertNotIn("face/amface", paths)
        self.assertIn("face/cas/amarcheasi", textures)
        self.assertTrue(bones)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "zenmu.glb"
            convert_character(CHARACTERS, "zenmu", target)
            doc, _ = open_glb(target)
            self.assertTrue(
                any("face/cas/amarcheasi" in mesh["name"] for mesh in doc["meshes"])
            )

    def test_undeclared_face_keeps_its_original_mesh(self):
        paths, parts, bones, textures = load_character(CHARACTERS, "bellagoth")
        faces = [(path, part) for path, part in parts if path.startswith("face/")]
        self.assertEqual({path for path, _ in faces}, {"face/afface"})
        self.assertEqual(len(faces), 14)
        self.assertTrue(faces[0][1]["morph_positions"])
        self.assertIn("face/afface", textures)
        self.assertEqual(len(bones), 47)

        chosen, parts_cer, _, _ = load_character(
            CHARACTERS, "bellagoth", archetype="face/cas/afarchecer"
        )
        self.assertIn("face/cas/afarchecer", chosen)
        self.assertNotIn("face/afface", chosen)
        self.assertEqual(
            len([part for path, part in parts_cer if path == "face/cas/afarchecer"]), 1
        )

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bella.glb"
            convert_character(CHARACTERS, "bellagoth", target)
            doc, _ = open_glb(target)
            self.assertTrue(
                any(
                    "face/afface" in mesh["name"]
                    and mesh.get("extras", {}).get("targetNames")
                    for mesh in doc["meshes"]
                )
            )
            self.assertFalse(any("face/cas/" in mesh["name"] for mesh in doc["meshes"]))

    def test_tunaktun_uses_declared_face_archetype(self):
        paths, parts, _, _ = load_character(CHARACTERS, "tunaktun")
        self.assertIn("face/cas/amarchcpol", paths)
        self.assertNotIn("face/amface", paths)
        face = next(part for path, part in parts if path == "face/cas/amarchcpol")
        self.assertEqual(len(face["vertices"]), 318)
        self.assertIn("Face modifier: nosebackfwd", face["morph_names"])

    def test_humanoid_arm_joints_align_with_hand_mesh(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            row = select_character_row(load_character_rows(archive), "bellagoth")
            data = find_character_mesh(archive, row, row["bodyTorso"])
            raw_bones = parse_bones(data)
            body_parts = parse_character_mesh(row["bodyTorso"], data)
        offset = arm_bind_offset(raw_bones, body_parts)
        self.assertGreater(offset, 0.03)
        self.assertLess(offset, 0.05)
        _, _, fitted_bones, _ = load_character(CHARACTERS, "bellagoth")
        for side in ("R", "L"):
            hand = next(
                i
                for i, bone in enumerate(raw_bones)
                if bone["name"] == f"__{side}_Hand__"
            )
            self.assertAlmostEqual(
                fitted_bones[hand]["world"][3, 2] - raw_bones[hand]["world"][3, 2],
                offset,
            )
            np.testing.assert_allclose(
                fitted_bones[hand]["world"] @ fitted_bones[hand]["ibm"],
                np.eye(4),
                atol=1e-6,
            )
        pelvis = next(
            i for i, bone in enumerate(raw_bones) if bone["name"] == "__Pelvis__"
        )
        np.testing.assert_allclose(
            fitted_bones[pelvis]["world"], raw_bones[pelvis]["world"]
        )

    def test_human_hand_joints_sit_inside_the_hand_skin(self):
        """Los huesos de la mano caen dentro de la mano que mueven.

        En el fichero vienen tres o cuatro centimetro metidos hacia dentro: el
        hueso de la muneca queda incluso antes de donde empieza la piel. Es un
        error distinto del vertical que mide `arm_bind_offset`, y por eso hace
        falta su propia correccion.
        """
        with zipfile.ZipFile(CHARACTERS) as archive:
            row = select_character_row(load_character_rows(archive), "bellagoth")
            data = find_character_mesh(archive, row, row["bodyTorso"])
            raw_bones = parse_bones(data)
            body_parts = parse_character_mesh(row["bodyTorso"], data)

        hueco = hand_bind_offset(raw_bones, body_parts)
        self.assertEqual(set(hueco), {"R", "L"})
        for lado in ("R", "L"):
            self.assertGreater(hueco[lado], 0.03)
            self.assertLess(hueco[lado], 0.07)

        _, parts, fitted_bones, _ = load_character(CHARACTERS, "bellagoth")
        names = {bone["name"]: i for i, bone in enumerate(fitted_bones)}
        for lado, fuera in (("R", 1.0), ("L", -1.0)):
            muneca = names[f"__{lado}_Hand__"]
            rama = descendientes(fitted_bones, muneca)
            self.assertGreater(len(rama), 5)
            # solo se mueve la rama de la muneca, no el brazo entero
            antebrazo = names[f"__{lado}_Forearm__"]
            self.assertNotIn(antebrazo, rama)
            self.assertAlmostEqual(
                fitted_bones[antebrazo]["world"][3, 0],
                raw_bones[antebrazo]["world"][3, 0],
                places=6,
            )
            # y se mueve hacia fuera la misma distancia en todos, que es lo que
            # dice el hueco medido por la mitad
            pasos = {
                round(
                    (fitted_bones[i]["world"][3, 0] - raw_bones[i]["world"][3, 0])
                    * fuera,
                    6,
                )
                for i in rama
            }
            self.assertEqual(len(pasos), 1, "la rama se mueve toda por igual")
            paso = pasos.pop()
            self.assertGreater(paso, 0.01)
            self.assertLess(paso, 0.05)
            for i in rama:
                # la inversa de enlace se recalcula, para que el GLB siga
                # teniendo una pose de referencia valida
                np.testing.assert_allclose(
                    fitted_bones[i]["world"] @ fitted_bones[i]["ibm"],
                    np.eye(4),
                    atol=1e-6,
                )

            # y con eso el esqueleto queda dentro de la mano: la muneca
            # entra en el principio y la punta de los dedos antes que la piel.
            # Todo en coordenadas "hacia fuera del cuerpo", para que las dos
            # manos se puedan mirar igual.
            xs = [
                v[0][0] / 1000.0 * fuera
                for _, part in parts
                for v in part["vertices"]
                if any(b in rama and w for b, w in zip(part["palette"], v[3]))
            ]
            largo = max(xs) - min(xs)
            self.assertGreater(largo, 0.1)
            muneca = fitted_bones[names[f"__{lado}_Hand__"]]["world"][3, 0] * fuera
            self.assertGreater(muneca, min(xs))
            punta = max(fitted_bones[i]["world"][3, 0] * fuera for i in rama)
            self.assertLess(punta, max(xs))
            # la ultima falange se queda sin hueso, como debe ser
            self.assertGreater((punta - min(xs)) / largo, 0.6)
            self.assertLess((punta - min(xs)) / largo, 0.9)

    def test_hand_correction_needs_both_arms_to_agree(self):
        """Una medida que solo sale de un brazo no es un error sistematico."""
        with zipfile.ZipFile(CHARACTERS) as archive:
            row = select_character_row(load_character_rows(archive), "bellagoth")
            data = find_character_mesh(archive, row, row["bodyTorso"])
            bones = parse_bones(data)
            body_parts = parse_character_mesh(row["bodyTorso"], data)

        self.assertTrue(hand_bind_offset(bones, body_parts))
        # sin piel que mueva los huesos no hay nada que medir
        self.assertEqual(hand_bind_offset(bones, []), {})
        # y con las dos manos en el mismo lado no hay ningun hueco real
        for bone in bones:
            if bone["name"].startswith("__L_"):
                bone["name"] = bone["name"].replace("__L_", "__R_")
        self.assertEqual(hand_bind_offset(bones, body_parts), {})

    def test_hand_offset_ignores_a_dragged_wrist(self):
        """La muneca no cuenta para medir el hueco.

        En Doctor Dominion la piel de la muneca derecha cae 62 mm hacia fuera y
        la de la izquierda 13, cuando los dedos de una y otra coinciden en 2 mm.
        Medir con la muneca dentro descartaba al personaje entero, y se quedaba
        sin corregir: es el unico caso del catalogo en el que se veia.
        """
        with zipfile.ZipFile(CHARACTERS) as archive:
            row = select_character_row(load_character_rows(archive), "doctordominion")
            data = find_character_mesh(archive, row, row["bodyTorso"])
            bones = parse_bones(data)
            body_parts = parse_character_parts(row, row["bodyTorso"], data)

        names = {bone["name"]: i for i, bone in enumerate(bones)}
        for side, fuera in (("R", 1.0), ("L", -1.0)):
            rama = descendientes(bones, names[f"__{side}_Hand__"]) - {
                names[f"__{side}_Hand__"]
            }
            acumulado = np.zeros(3)
            peso = 0.0
            for part in body_parts:
                for vertex in part["vertices"]:
                    for bone_id, weight in zip(part["palette"], vertex[3]):
                        if bone_id in rama and weight:
                            acumulado += np.asarray(vertex[0]) * weight
                            peso += weight
            piel = acumulado / peso / 1000.0
            centros = sum(bones[i]["world"][3, :3] for i in rama) / len(rama)
            self.assertGreater((piel[0] - centros[0]) * fuera, 0.02)

        hueco = hand_bind_offset(bones, body_parts)
        self.assertEqual(set(hueco), {"R", "L"})
        for lado in ("R", "L"):
            self.assertGreater(hueco[lado], 0.03)
            self.assertLess(hueco[lado], 0.06)

        _, _, fitted, _ = load_character(CHARACTERS, "doctordominion")
        fitted_names = {bone["name"]: i for i, bone in enumerate(fitted)}
        for side, fuera in (("R", 1.0), ("L", -1.0)):
            self.assertAlmostEqual(
                fitted[fitted_names[f"__{side}_Hand__"]]["world"][3, 0]
                - bones[names[f"__{side}_Hand__"]]["world"][3, 0],
                hueco[side] * fuera * HUEGO_MANO_FRACCION,
                places=6,
            )

    def test_cowthulu_is_listed_once_and_loadable(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)

        self.assertEqual(names.count("cowthulu"), 1)
        paths, parts, bones, textures = load_character(CHARACTERS, "cowthulu")
        self.assertEqual(paths, ["body/aubodycowthulu"])
        self.assertTrue(parts)
        self.assertTrue(bones)
        self.assertEqual(
            set(textures), {"body/aubodycowthulu#head", "body/aubodycowthulu#body"}
        )
        self.assertEqual(
            {key: sum(path == key for path, _ in parts) for key in textures},
            {"body/aubodycowthulu#head": 24, "body/aubodycowthulu#body": 11},
        )

    def test_cowthulu_can_be_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cowthulu.glb"
            result = convert_character(CHARACTERS, "cowthulu", output)

            self.assertEqual(result["character"], "cowthulu")
            self.assertEqual(result["pieces"], ["body/aubodycowthulu"])
            self.assertTrue(output.is_file())
            with output.open("rb") as stream:
                _, _, _ = struct.unpack("<4sII", stream.read(12))
                json_length, _ = struct.unpack("<I4s", stream.read(8))
                document = json.loads(stream.read(json_length))
            self.assertEqual(
                {image["name"] for image in document["images"]},
                {
                    "body/aubodycowthulu/aubodycowthulu_body",
                    "body/aubodycowthulu/aubodycowthulu_head",
                },
            )
            self.assertTrue(
                all(
                    "baseColorTexture" in material["pbrMetallicRoughness"]
                    for material in document["materials"]
                )
            )

    def test_nightbeast_is_listed_once_and_loadable_with_textures(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            names = list_characters(archive)

        self.assertEqual(names.count("nightbeast"), 1)
        paths, parts, bones, textures = load_character(CHARACTERS, "nightbeast")
        self.assertEqual(paths, ["body/aubodynightbeast"])
        self.assertTrue(parts)
        self.assertTrue(bones)
        self.assertEqual(
            set(textures), {"body/aubodynightbeast#head", "body/aubodynightbeast#body"}
        )
        self.assertEqual(
            {key: sum(path == key for path, _ in parts) for key in textures},
            {"body/aubodynightbeast#head": 30, "body/aubodynightbeast#body": 8},
        )
        self.assertTrue(all(image.shape[2] == 4 for image in textures.values()))

    def test_nightbeast_can_be_exported_with_textures(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nightbeast.glb"
            result = convert_character(CHARACTERS, "nightbeast", output)

            self.assertEqual(result["character"], "nightbeast")
            self.assertEqual(result["pieces"], ["body/aubodynightbeast"])
            self.assertTrue(output.is_file())
            with output.open("rb") as stream:
                _, _, _ = struct.unpack("<4sII", stream.read(12))
                json_length, _ = struct.unpack("<I4s", stream.read(8))
                document = json.loads(stream.read(json_length))
            self.assertEqual(
                {image["name"] for image in document["images"]},
                {
                    "body/aubodynightbeast/nightbeasthead",
                    "body/aubodynightbeast/nightbeastbody",
                },
            )
            self.assertTrue(
                all(
                    "baseColorTexture" in material["pbrMetallicRoughness"]
                    for material in document["materials"]
                )
            )
            self.assertTrue(
                all(
                    material["alphaMode"] == "MASK"
                    for material in document["materials"]
                )
            )

    def test_nightbeast_body_texture_uses_repeat_wrapping(self):
        _, parts, _, textures = load_character(CHARACTERS, "nightbeast")
        body = [(key, part) for key, part in parts if key.endswith("#body")]
        self.assertEqual(len(body), 8)
        self.assertTrue(all(part["repeat_texture"] for _, part in body))

        scene = gl.Escena.__new__(gl.Escena)
        scene.tipo = "personaje"
        uploaded = []
        scene._subir_textura = lambda texture, voltear=False, repeat=False: (
            uploaded.append(repeat) or len(uploaded)
        )
        scene._tiene_alpha = lambda texture: True
        scene._cargar(parts, textures)
        self.assertIn(True, uploaded)

    def test_lincoln_torso_uses_alpha_and_disables_backface_culling(self):
        _, parts, _, textures = load_character(CHARACTERS, "lincolnbroadsheet")
        body_parts = [
            (key, part) for key, part in parts if key == "body/ambodyhovercodpiece"
        ]
        self.assertTrue(body_parts)
        self.assertTrue(all(part["double_sided"] for _, part in body_parts))
        alpha = textures["body/ambodyhovercodpiece"][:, :, 3]
        self.assertLess(int(alpha.min()), 255)

        scene = gl.Escena.__new__(gl.Escena)
        scene.tipo = "personaje"
        scene._subir_textura = lambda texture, voltear=False, repeat=False: 1
        scene._tiene_alpha = lambda texture: True
        scene._cargar(body_parts[:1], textures)
        self.assertTrue(scene.dibujos[0][3])
        self.assertTrue(scene.dibujos[0][2])

    def test_lincoln_torso_alpha_is_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lincolnbroadsheet.glb"
            convert_character(CHARACTERS, "lincolnbroadsheet", output)
            with output.open("rb") as stream:
                stream.read(12)
                json_length, _ = struct.unpack("<I4s", stream.read(8))
                document = json.loads(stream.read(json_length))
            torso_materials = [
                material
                for material in document["materials"]
                if material["name"].startswith("body/ambodyhovercodpiece /")
            ]
            self.assertTrue(torso_materials)
            self.assertTrue(
                all(material["doubleSided"] for material in torso_materials)
            )
            self.assertTrue(
                all(material["alphaMode"] == "MASK" for material in torso_materials)
            )

    def test_reported_top_meshes_use_the_character_body_variant(self):
        with zipfile.ZipFile(CHARACTERS) as archive:
            for character, path in [
                ("betsyshelton", "top/aftophalterwaste"),
                ("penelopekline", "top/aftophalterwaste"),
                ("tureenallard", "top/afcrewneckcollarshirt"),
            ]:
                selected = find_character_mesh(archive, {"personaje": character}, path)
                default = find_mesh(archive, path)
                self.assertNotEqual(selected, default, character)
                parts = parse_character_mesh(path, selected)
                vertices = [vertex[0] for part in parts for vertex in part["vertices"]]
                width = max(vertex[0] for vertex in vertices) - min(
                    vertex[0] for vertex in vertices
                )
                self.assertGreater(width, 1800, character)
                self.assertEqual(len(parts), 6, character)
            path = "top/aftophalterwaste"
            self.assertEqual(
                find_character_mesh(archive, {"personaje": "anothercharacter"}, path),
                find_mesh(archive, path),
            )

    def test_dixie_uses_final_body_atlas_and_zina_uses_dress_and_hair(self):
        with (
            zipfile.ZipFile(CHARACTERS) as archive,
            zipfile.ZipFile(
                CHARACTERS.with_name("sims2_psp_personajes_texturas_png.zip")
            ) as textures,
        ):
            rows = load_character_rows(archive)
            dixie = select_character_row(rows, "dixiestills")
            zina = select_character_row(rows, "zinamarlowe")
            library = TextureLibrary(textures)

            dixie_body = character_mesh_texture(
                library, dixie, "body/afbodydixiestills", "principal"
            )
            self.assertEqual(dixie_body[0], "body/afbodydixiestills/dixiestills")
            self.assertEqual(
                dixie_body[1], library.read("body/afbodydixiestills/dixiestills")
            )
            bodyreaper = select_character_row(rows, "bodyreaper")
            self.assertEqual(
                library.character_texture(bodyreaper, "top")[0],
                "composed_textures/top/bodyreaper",
            )

            dixie_paths, _, _, dixie_textures = load_character(
                CHARACTERS, "dixiestills"
            )
            self.assertTrue(
                np.array_equal(
                    dixie_textures["body/afbodydixiestills"],
                    np.asarray(Image.open(io.BytesIO(dixie_body[1])).convert("RGBA")),
                )
            )
            self.assertIn("body/afbodydixiestills", dixie_paths)
            self.assertEqual(
                character_mesh_texture(
                    library, dixie, "body/afbodydixiestills", "principal"
                )[0],
                "body/afbodydixiestills/dixiestills",
            )

            erin = select_character_row(rows, "erinphilips")
            erin_body = character_mesh_texture(
                library, erin, "body/afbodyerinphilips", "principal"
            )
            self.assertEqual(erin_body[0], "body/afbodyerinphilips/erinphilips")
            erin_paths, _, _, erin_textures = load_character(CHARACTERS, "erinphilips")
            self.assertIn("body/afbodyerinphilips", erin_paths)
            self.assertTrue(
                np.array_equal(
                    erin_textures["body/afbodyerinphilips"],
                    np.asarray(Image.open(io.BytesIO(erin_body[1])).convert("RGBA")),
                )
            )

            zina_paths = [
                "body/afbodydress",
                "face/cas/afarchecer",
                "hair/afhairmediumcenterpart",
            ]
            self.assertFalse(zina.get("bodyLegs"))
            self.assertEqual(
                library.character_texture(zina, "hair")[0],
                "hair/afhairmediumcenterpart/ufhairmediumcenterpart-black",
            )
            self.assertEqual(
                library.character_texture(zina, "head")[0],
                "composed_textures/head/zinamarlowe",
            )
            hair_image = Image.open(
                io.BytesIO(library.character_texture(zina, "hair")[1])
            ).convert("RGBA")
            self.assertGreater(len(hair_image.getcolors(16384)), 1)
            loaded_paths, parts, _, loaded_textures = load_character(
                CHARACTERS, "zinamarlowe"
            )
            self.assertEqual(loaded_paths, zina_paths)
            self.assertNotIn("face/afface", loaded_paths)
            self.assertNotIn("bottom/afbottombaggypants", loaded_paths)
            self.assertIn("hair/afhairmediumcenterpart", loaded_textures)
            self.assertTrue(parts)
            with tempfile.TemporaryDirectory() as directory:
                result = convert_character(
                    CHARACTERS, "zinamarlowe", Path(directory) / "zinamarlowe.glb"
                )
            self.assertEqual(result["pieces"], zina_paths)

    def test_existing_composed_textures_are_preferred(self):
        with (
            zipfile.ZipFile(CHARACTERS) as archive,
            zipfile.ZipFile(
                CHARACTERS.with_name("sims2_psp_personajes_texturas_png.zip")
            ) as textures,
        ):
            rows = load_character_rows(archive)
            library = TextureLibrary(textures)
            checked = 0
            for name in textures.namelist():
                prefix = "texturas/characters/bodyanims/composed_textures/"
                if not name.startswith(prefix) or not name.endswith(".png"):
                    continue
                category, filename = name[len(prefix) :].split("/")
                character = filename[:-4]
                if category not in ("top", "legs", "head", "hair"):
                    continue
                if character == "zinamarlowe" and category == "hair":
                    continue
                row = select_character_row(rows, character)
                result = library.character_texture(row, category)
                self.assertIsNotNone(result, name)
                self.assertEqual(
                    result[0], f"composed_textures/{category}/{character}", name
                )
                checked += 1
            self.assertGreater(checked, 100)

    def test_female_torso_morph_names_are_corrected_only_for_female_lod5(self):
        self.assertEqual(
            character_morph_name("t_botgrow", female_face_lod5=True), "t_botmove"
        )
        self.assertEqual(
            character_morph_name("t_botmove", female_face_lod5=True), "t_botgrow"
        )
        self.assertEqual(
            character_morph_name("t_topgrow", female_face_lod5=True), "t_botmove"
        )
        self.assertEqual(character_morph_name("t_botmove"), "t_botmove")
        self.assertEqual(character_morph_name("t_topgrow"), "t_topgrow")
        self.assertEqual(character_morph_name("t_botgrow"), "t_botgrow")

    def test_all_female_morph_ids_are_listed_and_individually_selectable(self):
        _, parts, _, _ = load_character(CHARACTERS, "anniehowell")
        face_parts = [
            (path, part)
            for path, part in parts
            if part.get("shape_scope") == "face/afface"
        ]
        options = shape_key_options(face_parts, preserve_morph_ids=True)
        self.assertEqual(
            [name for name, _ in options],
            sorted((name for name, _ in options), key=str.casefold),
        )
        listed_ids = {identity for _, identity in options}
        available_ids = {
            ("face/afface", morph_id)
            for _, part in face_parts
            for morph_id in part["morph_ids"]
        }
        self.assertEqual(listed_ids, available_ids)
        labels = {identity: name for name, identity in options}
        self.assertEqual(labels[("face/afface", 1)], "t_botgrow")
        self.assertEqual(labels[("face/afface", 2)], "t_botmove")

        part_pair = next(
            (path, part)
            for path, part in face_parts
            if 1 in part["morph_ids"] and 2 in part["morph_ids"]
        )
        _, part = part_pair
        # la cabeza por defecto puede venir oculta (sustituida por el arquetipo
        # facial); la matemática de morphs se comprueba sobre la parte en crudo
        part = {key: value for key, value in part.items() if key != "oculta"}
        part_pair = (part_pair[0], part)
        for morph_id in (1, 2):
            selected = _shape_positions([part_pair], "", 1.0, ("face/afface", morph_id))
            morph_index = part["morph_ids"].index(morph_id)
            position, _, _, _ = part["vertices"][0]
            delta = part["morph_positions"][morph_index][0]
            self.assertEqual(
                selected[0],
                tuple((position[axis] + delta[axis]) / 1000.0 for axis in range(3)),
            )

    def test_male_face_morph_names_match_reference_fbx_channels(self):
        fbx_names = [
            "t_topgrow",
            "t_botmove",
            "t_botgrow",
            "y_nose",
            "z_nose",
            "x_nose",
            "R_growl",
            "L_growl",
            "x_mouth",
            "y_mouth",
            "zb_mouth",
            "zt_mouth",
            "R_xcorner",
            "L_xcorner",
            "R_ycorner",
            "L_ycorner",
            "R_zcorner",
            "L_zcorner",
            "pinch",
            "R_scrunch",
            "L_scrunch",
            "R_brow",
            "L_brow",
            "R_eye",
            "L_eye",
            "R_cheekpuff",
            "L_cheekpuff",
        ]
        with zipfile.ZipFile(CHARACTERS) as archive:
            rows = load_character_rows(archive)
            row = next(item for item in rows if item["personaje"] == "doctordominion")
            path = row["bodyHead"]
            data = find_mesh(archive, path)

        original = parse_mesh(data)
        character = parse_character_mesh(path, data)
        self.assertEqual(path, "face/amface")
        for source_part, parsed_part in zip(original, character):
            self.assertEqual(source_part["morph_ids"], parsed_part["morph_ids"])
            expected_names = []
            for morph_id in parsed_part["morph_ids"]:
                if 4 <= morph_id <= 23:
                    expected_names.append(fbx_names[morph_id + 2])
                elif morph_id == 24:
                    expected_names.append("L_cheekpuff")
                elif morph_id == 26:
                    expected_names.append("t_topgrow")
                elif morph_id == 28:
                    expected_names.append("t_botmove")
                elif morph_id == 29:
                    expected_names.append("t_botgrow")
                else:
                    expected_names.append(f"shape_{morph_id}")
            self.assertEqual(parsed_part["morph_names"], expected_names)
            for name in parsed_part["morph_names"]:
                self.assertTrue(
                    name.startswith("shape_")
                    or name.casefold()
                    in {fbx_name.casefold() for fbx_name in fbx_names}
                )

    def test_reported_characters_load_with_textures(self):
        for character in [
            "betsyshelton",
            "bodyreaper",
            "dixiestills",
            "penelopekline",
            "tureenallard",
            "zinamarlowe",
        ]:
            with self.subTest(character=character):
                paths, parts, bones, textures = load_character(CHARACTERS, character)
                self.assertTrue(paths)
                self.assertTrue(parts)
                self.assertTrue(bones)
                for path in paths:
                    texture_key = path
                    if character == "bodyreaper":
                        texture_key = path
                    self.assertIn(texture_key, textures, character)
                self.assertTrue(all(image.shape[2] == 4 for image in textures.values()))

    def test_creature_clips_preview_with_their_own_skeletons(self):
        with zipfile.ZipFile(ANIMATIONS) as archive:
            catalog = json.loads(archive.read("catalog.json"))
            for category, character in [
                ("cowthulu_anims", "cowthulu"),
                ("nightbeast_anims", "nightbeast"),
            ]:
                with (
                    self.subTest(character=character),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    row = next(item for item in catalog if item["category"] == category)
                    clip = parse_banm(archive.read(row["file"]))
                    reference = None
                    reference_pose = None
                    if character == "cowthulu":
                        suffix = "-mon" if row["name"].endswith("-mon") else ""
                        ref_row = next(
                            item
                            for item in catalog
                            if item["name"].endswith(f"a-cowthulu-idle-breathe{suffix}")
                        )
                        fallback = parse_banm(archive.read(ref_row["file"]))
                    else:
                        fallback = None
                    model = Path(directory) / f"{character}.glb"
                    output = Path(directory) / f"{character}_preview.glb"
                    convert_character(CHARACTERS, character, model)
                    attach_animations(
                        model, [clip], reference, output, fps=20, fallback=fallback
                    )
                    document, binary = open_glb(output)
                    self.assertTrue(document["animations"])
                    if character == "cowthulu":
                        channels = document["animations"][0]["channels"]
                        tracks = {
                            (track["name"], track["kind"]): track
                            for track in fallback["tracks"]
                            if track["kind"] != "weights"
                        }
                        tracks.update(
                            {
                                (track["name"], track["kind"]): track
                                for track in clip["tracks"]
                            }
                        )
                        for channel in channels:
                            target = channel["target"]
                            if target["path"] not in ("translation", "rotation"):
                                continue
                            node_index = target["node"]
                            node_name = document["nodes"][node_index]["name"]
                            sampler = document["animations"][0]["samplers"][
                                channel["sampler"]
                            ]
                            values = accessor(document, binary, sampler["output"])[0]
                            track = tracks[node_name, target["path"]]
                            if target["path"] == "translation":
                                expected = convert_vector3(sample(track, 0))
                                np.testing.assert_allclose(values, expected, atol=1e-5)
                            elif target["path"] == "rotation":
                                expected = convert_quaternion(
                                    normalize(sample(track, 0))
                                )
                                np.testing.assert_allclose(
                                    np.abs(np.dot(values, expected)), 1.0, atol=1e-5
                                )
                    meshes = validate_preview_frame(
                        document, binary, document["animations"][0]
                    )
                    self.assertTrue(meshes)
                    self.assertTrue(
                        all(np.isfinite(vertices).all() for _, vertices, _ in meshes)
                    )
                    if character == "nightbeast":
                        pieces, textures = glb_piezas(document, binary, meshes)
                        body = [
                            (key, part)
                            for key, part in pieces
                            if part["nombre"].startswith(
                                "body/aubodynightbeast / pieza_"
                            )
                            and int(part["nombre"].rsplit("pieza_", 1)[-1]) > 29
                        ]
                        self.assertEqual(len(body), 8)
                        self.assertTrue(all(part["repeat_texture"] for _, part in body))
                        self.assertIn("body/aubodynightbeast/nightbeastbody", textures)

                        scene = gl.Escena.__new__(gl.Escena)
                        scene.tipo = "personaje_glb"
                        uploaded = []
                        scene._subir_textura = (
                            lambda texture, voltear=False, repeat=False: (
                                uploaded.append(repeat) or len(uploaded)
                            )
                        )
                        scene._tiene_alpha = lambda texture: True
                        scene._cargar(pieces, textures)
                        self.assertTrue(any(uploaded))

    def test_invalid_animation_preview_is_rejected_without_evaluation(self):
        with self.assertRaisesRegex(ValueError, "Invalid animation duration"):
            validate_preview_frame(
                {"nodes": [], "skins": []},
                b"",
                {"extras": {"sourceDuration": 0}, "channels": []},
            )
        with self.assertRaisesRegex(ValueError, "has no channels"):
            validate_preview_frame(
                {"nodes": [], "skins": []},
                b"",
                {"extras": {"sourceDuration": 1}, "channels": []},
            )


if __name__ == "__main__":
    unittest.main()
