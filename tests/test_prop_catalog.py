"""Regressions found by reviewing the complete local prop catalogue."""

import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from ts2psp.formats.props import ALFA_ILEGIBLE, sections, parse_rdms, convert_prop
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

    def test_listed_props_get_their_broken_alpha_forced_opaque(self):
        """Las puertas se dibujaban invisibles por el alfa mal descifrado.

        El lector BC3 sigue sin estar resuelto, asi que el arreglo es una lista
        corta de props en vez de un cambio general. Este test fija las dos
        cosas: que la lista funciona, y que fuera de ella nada se toca.
        """
        import io as _io

        from PIL import Image

        for ruta in ("doors/fx_d47mechdoor", "doors/fx_elevatordoors"):
            data = self.resource(ruta)
            with self.subTest(prop=ruta):
                roto = [
                    part
                    for part in load_parts(data)
                    if part.get("texture")
                    and np.asarray(
                        Image.open(_io.BytesIO(part["texture"][1])).convert("RGBA")
                    )[:, :, 3].max() < 255
                ]
                self.assertTrue(roto, "sin esto el parche no arregla nada")
                for part in load_parts(data, opaco=True):
                    if not part.get("texture"):
                        continue
                    alfa = np.asarray(
                        Image.open(_io.BytesIO(part["texture"][1])).convert("RGBA")
                    )[:, :, 3]
                    self.assertEqual(int(alfa.min()), 255)
                    self.assertEqual(part.get("alpha_mode"), "OPAQUE")

    def test_opaque_textures_do_not_get_sent_to_the_blend_pass(self):
        """Una textura casi opaca no es translucida.

        El pase de mezcla dibuja despues de lo opaco, con la escritura de
        profundidad apagada y ordenandose solo por profundidad media, asi que
        un prop opaco que entra ahi se recorta aunque no parezca transparente.
        Con el criterio de "un pixel por debajo de 255 basta" entraban 95 piezas
        de unos 30 props, entre ellas el espejo de mano y el arbol del dinero.
        """
        for nombre, esperada in (
            ("accessories_perks/fx_handmirror", 0),
            ("glassware/fx_glasswareblueglass", 2),
        ):
            data = self.resource(nombre)
            mezcla = sum(
                1
                for parte in load_parts(data, include_helpers=True)
                if parte.get("alpha_mode") == "BLEND"
            )
            with self.subTest(prop=nombre):
                self.assertEqual(mezcla, esperada)

    def test_nearly_opaque_textures_use_alpha_clip_not_blending(self):
        """Cada malla decide por el alfa que pisa de verdad, no por el del atlas.

        El pase de mezcla apaga la escritura de profundidad y ordena cada malla
        solo por su profundidad media, asi que las piezas de un prop se ven unas
        a traves de otras. Con las texturas casi opacas en ese pase, el panel de
        botones del microondas no se veia y la placa de la luz de pared aparecia
        velada.

        El criterio es el alfa minimo de la parte de la textura que la malla
        pisa de verdad, porque un atlas reparte la imagen entre muchas mallas:
        una lamina opaca pegada a un recorte de alpiste salen ambas como
        translucidas si solo se mira el PNG entero.
        """
        # la lampara entera pisa una franja casi opaca (alfa 219-230): recorte
        lampara = [
            parte
            for parte in load_parts(
                self.resource("lamps_wall/fx_lighting_wall_value_02"),
                include_helpers=True,
            )
            if parte.get("texture") is not None
        ]
        self.assertTrue(lampara)
        for parte in lampara:
            self.assertEqual(parte.get("alpha_mode"), "MASK")
            self.assertGreater(parte["alfa_tocado"], 190)

        # el microondas mezcla los tres grupos en el mismo prop
        micro = {
            parte.get("alpha_mode")
            for parte in load_parts(
                self.resource("microwave/fx_microwavecheap"), include_helpers=True
            )
            if parte.get("texture") is not None
        }
        self.assertEqual(micro, {"BLEND", "MASK", "OPAQUE"})

        # el cristal, en cambio, se tiene que seguir mezclando
        cristal = [
            parte
            for parte in load_parts(
                self.resource("glassware/fx_glasswareblueglass"),
                include_helpers=True,
            )
            if parte.get("texture") is not None
        ]
        self.assertTrue(cristal)
        for parte in cristal:
            self.assertEqual(parte.get("alpha_mode"), "BLEND")

    def test_timestopper_main_mesh_is_forced_opaque(self):
        """La pieza principal del timestopper se dibuja opaca a proposito.

        El autodetectado la deja mezclada, porque sus triangulos pisan de verdad
        la parte transparente del atlas. Pero son 24 piezas superpuestas sobre la
        misma textura y en el pase de mezcla se ven unas a traves de otras, asi
        que esta se fuerza a mano en MALLAS_OPACAS.
        """
        ruta = "objects/accessories_perks/fx_timestopper"
        automatico = load_parts(
            self.resource(ruta.split("objects/", 1)[1]), include_helpers=True
        )
        principal = [p for p in automatico if p["name"] == "pieza_2"][0]
        self.assertEqual(principal.get("alpha_mode"), "BLEND")
        self.assertEqual(principal.get("alfa_tocado"), 0)

        forzado = load_parts(
            self.resource(ruta.split("objects/", 1)[1]), include_helpers=True, ruta=ruta
        )
        principal = [p for p in forzado if p["name"] == "pieza_2"][0]
        self.assertEqual(principal.get("alpha_mode"), "OPAQUE")

        # las otras 23 se dejan como las clasifique el autodetectado
        resto_a = {
            p["name"]: p.get("alpha_mode") for p in automatico if p["name"] != "pieza_2"
        }
        resto_f = {
            p["name"]: p.get("alpha_mode") for p in forzado if p["name"] != "pieza_2"
        }
        self.assertEqual(resto_a, resto_f)

    def test_repeating_meshes_are_sampled_with_wraparound(self):
        """Una malla que se sale de [0,1] da la vuelta, como hace la GPU.

        El cologne recorre el atlas dando vueltas (u de -3.65 a 3.27). Al medir
        el alfa que pisa hay que rasterizar una vez por vuelta: si se recorta a
        [0,1] se mira solo un trozo y la pieza se clasifica por una parte suelta
        del atlas, que es justo lo que hacia antes.
        """
        ruta = "objects/accessories_perks/fx_cologne"
        piezas = load_parts(
            self.resource(ruta.split("objects/", 1)[1]), include_helpers=True
        )
        principal = [p for p in piezas if p["name"] == "pieza_2"][0]
        self.assertTrue(principal.get("repeat_texture"))
        u = np.array([v[2][0] for v in principal["vertices"]])
        self.assertLess(u.min(), 0.0)
        self.assertGreater(u.max(), 1.0)

    def test_cologne_main_mesh_is_forced_opaque(self):
        ruta = "objects/accessories_perks/fx_cologne"
        datos = self.resource(ruta.split("objects/", 1)[1])
        forzado = load_parts(datos, include_helpers=True, ruta=ruta)
        principal = [p for p in forzado if p["name"] == "pieza_2"][0]
        self.assertEqual(principal.get("alpha_mode"), "OPAQUE")

    def test_forced_modes_cover_rdms_and_bmsh(self):
        """MODO_PIEZAS se respeta en los dos formatos de prop.

        Los RDMS entran por `parse_rdms` y los BMSH por `parse_prop_bmsh`, que
        son caminos distintos y antes solo el primero recibia la ruta interna.
        Con "*" se aplica a todas las piezas del prop.
        """
        casos = {
            # ruta interna: (recurso, piezas forzadas y su grupo)
            "objects/accessories_perks/fx_timestopper": (
                "accessories_perks/fx_timestopper", {"pieza_2": "OPAQUE"}),
            "objects/accessories_perks/fx_cologne": (
                "accessories_perks/fx_cologne", {"pieza_2": "OPAQUE"}),
            "objects/accessories_perks/fx_timestopper_anim_open": (
                "accessories_perks/fx_timestopper_anim_open", {"*": "MASK"}),
            "objects/accessories_perks/fx_truthserum": (
                "accessories_perks/fx_truthserum", {"*": "OPAQUE"}),
            "objects/appliances/fx_appliancefoodprocessor": (
                "appliances/fx_appliancefoodprocessor",
                {"pieza_6": "OPAQUE", "pieza_7": "OPAQUE", "pieza_8": "BLEND"}),
            "objects/aspirations/moneytree/body/moneytree": (
                "aspirations/moneytree/body/moneytree", {"pieza_1": "MASK"}),
            "objects/aspirations/moneytree/fx_money_tree": (
                "aspirations/moneytree/fx_money_tree", {"pieza_7": "MASK"}),
            "objects/plants/fx_money_tree": (
                "plants/fx_money_tree", {"pieza_7": "MASK"}),
            "objects/cutsceneobjects/fx_lamp_kine": (
                "cutsceneobjects/fx_lamp_kine", {"pieza_3": "OPAQUE"}),
            "objects/cutsceneobjects/fx_lamp_swinging": (
                "cutsceneobjects/fx_lamp_swinging", {"pieza_3": "OPAQUE"}),
        }
        for ruta, (recurso, esperadas) in casos.items():
            datos = self.resource(recurso)
            automatico = {
                p["name"]: p.get("alpha_mode")
                for p in load_parts(datos, include_helpers=True)
            }
            forzado = {
                p["name"]: p.get("alpha_mode")
                for p in load_parts(datos, include_helpers=True, ruta=ruta)
            }
            with self.subTest(prop=ruta):
                self.assertTrue(forzado)
                self.assertEqual(sorted(forzado), sorted(automatico))
                for nombre, modo in forzado.items():
                    if "*" in esperadas:
                        self.assertEqual(modo, esperadas["*"], nombre)
                    elif nombre in esperadas:
                        self.assertEqual(modo, esperadas[nombre], nombre)
                    else:
                        # lo demas se queda como lo clasificara el autodetectado
                        self.assertEqual(modo, automatico[nombre], nombre)

    def test_toilet_textures_are_not_left_swizzled(self):
        """El bit que marca el swizzle no siempre es el 8.

        Los inodoros lo llevan en el 9, asi que sus texturas se leian sin
        deshacer el swizzle y salian en rayas horizontales. Se comprueba que la
        imagen ya es coherente: una textura coherente tiene filas vecinas
        parecida, y una esparramada por el swizzle no.
        """
        import io as _io

        from PIL import Image

        for nombre in ("fx_toiletvalue", "fx_toiletvalue_01", "fx_toiletvalue_02"):
            data = self.resource(f"toilets/{nombre}")
            with self.subTest(prop=nombre):
                texturizadas = [
                    part
                    for part in load_parts(data)
                    if part.get("texture") and part["name"] == "pieza_2"
                ]
                self.assertTrue(texturizadas)
                imagen = np.asarray(
                    Image.open(
                        _io.BytesIO(texturizadas[0]["texture"][1])
                    ).convert("RGB")
                ).astype(np.int16)
                # coste entre filas y columnas vecinas; a mas pequeno, mas
                # coherente. Una textura de 128x128lechada pasa de miles.
                coste = float(
                    np.abs(np.diff(imagen, axis=1)).mean()
                    + np.abs(np.diff(imagen, axis=0)).mean()
                )
                self.assertLess(coste, 60.0, "la textura sigue swizzled")

    def test_the_opacity_patch_does_not_reach_other_props(self):
        """Un prop fuera de la lista no cambia por mucho que se le pida."""
        data = self.resource("chests/fx_dtgypsy_chest")
        self.assertNotIn("objects/chests/fx_dtgypsy_chest", ALFA_ILEGIBLE)
        normal = [p.get("texture")[1] for p in load_parts(data) if p.get("texture")]
        forzado = [p.get("texture")[1] for p in load_parts(data, opaco=True) if p.get("texture")]
        self.assertEqual(normal, forzado)

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
