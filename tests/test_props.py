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


class PropBoneTests(unittest.TestCase):
    """Los props articulados usan seccion `node`, no `body` como los personajes."""

    def read(self, ruta):
        with zipfile.ZipFile(ARCHIVE) as archive:
            return archive.read(f"recursos/{ruta}.res")

    def test_satellite_node_count_is_the_real_one(self):
        """El recuento sale de la jerarquia, no de una firma: 14 crudos, 4 utiles."""
        data = self.read("objects/roberta/fx_rightarm_satellite")
        self.assertEqual(len(node_hierarchy(data, sections(data))[0]), 14)
        self.assertEqual(prop_node_count(data), 4)

    def test_satellite_has_no_node_names_in_the_resource(self):
        """El grafo existe pero sus nombres no: se announce como tal, no se inventan."""
        data = self.read("objects/roberta/fx_rightarm_satellite")
        self.assertEqual(prop_node_names(data), ())
        self.assertEqual(prop_bones(data), (4, ()))

    def test_names_come_from_strg_and_drop_shape_groups(self):
        data = self.read("objects/accessories_perks/fx_cologne")
        self.assertEqual(prop_node_count(data), 1)
        self.assertEqual(prop_node_names(data),
                         ("__R_hand_grip__", "__slot_spray_fx__"))
        # el resto de strg son rutas de textura, no nodos
        self.assertFalse(any("Shape" in n for n in prop_node_names(data)))
        self.assertFalse(any(".tif" in n for n in prop_node_names(data)))

    def test_roberta_head_exposes_its_grip(self):
        data = self.read("objects/roberta/fx_head")
        self.assertEqual(prop_node_count(data), 1)
        self.assertIn("__L_Hand_grip__", prop_node_names(data))

    def test_furniture_can_have_a_real_node_graph_too(self):
        """No todos los muebles son planos: esta silla tiene 51 nodos, 2 articulaciones."""
        data = self.read("objects/chairs/fx_chair_dining_medieval")
        self.assertEqual(len(node_hierarchy(data, sections(data))[0]), 51)
        self.assertEqual(prop_node_count(data), 2)

    def test_a_bed_is_not_flat_after_all(self):
        """La cama tambien tiene jerarquia: 76 nodos, no era un mueble plano."""
        data = self.read("objects/beds/fx_beddoublebasic")
        self.assertEqual(len(node_hierarchy(data, sections(data))[0]), 76)
        self.assertEqual(prop_node_count(data), 2)

    def test_skeleton_parents_always_point_backwards(self):
        """Un padre mas atras o la raiz: nunca adelante, que daria ciclo."""
        for ruta in ("objects/roberta/fx_rightarm_satellite",
                     "objects/accessories_perks/fx_cologne",
                     "objects/stereos/fx_stereoboombox_table"):
            data = self.read(ruta)
            bones = prop_skeleton(data)
            self.assertTrue(bones, ruta)
            for bone in bones:
                self.assertTrue(
                    bone["parent"] == -1 or 0 <= bone["parent"] < bone["index"],
                    (ruta, bone),
                )

    def test_bones_over_drops_the_placement_anchor_and_remaps_parents(self):
        """La raiz de colocacion esta lejos de la pieza y no se dibuja."""
        data = self.read("objects/accessories_garden/fx_wateringcan")
        parts = load_parts(data)
        todos = prop_skeleton(data)
        sobre = bones_over(parts, todos)
        self.assertLess(len(sobre), len(todos))
        for bone in sobre:
            self.assertTrue(
                bone["parent"] == -1 or 0 <= bone["parent"] < len(sobre),
                bone,
            )
        puntos = [v[0] for p in parts for v in p["vertices"]]
        for bone in sobre:
            for axis in range(3):
                self.assertTrue(
                    min(p[axis] for p in puntos) - 1
                    <= bone["position"][axis]
                    <= max(p[axis] for p in puntos) + 1,
                    bone,
                )

    def test_every_animatable_prop_reports_something_sensible(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            rows = [n for n in archive.namelist()
                    if n.endswith(".res") and "roberta" in n]
            for ruta in rows:
                with self.subTest(prop=ruta):
                    count, names = prop_bones(archive.read(ruta))
                    self.assertIsInstance(count, int, ruta)
                    self.assertGreaterEqual(count, 1, ruta)
                    self.assertTrue(len(names) <= count, ruta)
                    self.assertEqual(len(set(names)), len(names), ruta)

    def test_names_follow_the_sims_naming_convention(self):
        """Los nombres tienen familia reconocible: raiz, agarre, slot, pieza."""
        data = self.read("objects/accessories_garden/fx_wateringcan")
        names = prop_node_names(data)
        self.assertIn("__root_trans__", names)
        self.assertIn("__root_rot__", names)
        self.assertIn("__slot_wateringCan_water_fx__", names)

    def test_a_prop_cannot_report_more_names_than_nodes(self):
        """Guarda contra falsos positivos: los nombres son un subconjunto."""
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in archive.namelist():
                if not ruta.endswith(".res"):
                    continue
                count, names = prop_bones(archive.read(ruta))
                if count is None:
                    continue
                with self.subTest(prop=ruta):
                    self.assertLessEqual(len(names), count)

    def test_exported_glb_carries_the_skeleton(self):
        """El prop sale con skin y las mallas colgadas de su articulacion."""
        with zipfile.ZipFile(ARCHIVE) as archive:
            data = archive.read(
                "recursos/objects/roberta/fx_rightarm_satellite.res"
            )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "satellite.glb"
            info = convert_prop(data, "satellite", target)
            doc, _ = open_glb(target)
        self.assertEqual(info["bones"], 4)
        self.assertEqual(len(doc["skins"]), 1)
        joints = doc["skins"][0]["joints"]
        self.assertEqual(len(joints), 4)
        nodes = doc["nodes"]
        for node in nodes:
            if "mesh" in node:
                self.assertNotIn(
                    "matrix",
                    node,
                    "las mallas van en espacio mundo, sin transformar",
                )
        raices = doc["scenes"][0]["nodes"]
        mallas = [i for i, node in enumerate(nodes) if "mesh" in node]
        for malla in mallas:
            self.assertIn(malla, raices)

    def test_every_prop_exports_without_losing_its_meshes(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            rutas = [n for n in archive.namelist() if n.endswith(".res")]
            for ruta in rutas:
                data = archive.read(ruta)
                if b"rdms" not in data[:128]:
                    continue
                with self.subTest(prop=ruta):
                    with tempfile.TemporaryDirectory() as directory:
                        target = Path(directory) / "p.glb"
                        info = convert_prop(data, "p", target)
                    self.assertGreater(info["parts"], 0, ruta)

    def test_exported_bones_land_in_the_translation_slots(self):
        """El nodo de hueso lleva su traslacion en `translation`, suelta.

        Regresion real: montar la matriz con convencion de columna vectoria
        dejaba los huesos apilados en el (0,0,0) al abrir el GLB. Ahora el
        esqueleto se escribe ya en base glTF, sin matrices que Somehow cuadren.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            data = archive.read(
                "recursos/objects/roberta/fx_rightarm_satellite.res"
            )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "satellite.glb"
            convert_prop(data, "satellite", target)
            doc, _ = open_glb(target)
        joints = doc["skins"][0]["joints"]
        self.assertTrue(joints)
        for joint in joints:
            node = doc["nodes"][joint]
            self.assertNotIn("matrix", node)
            self.assertEqual(len(node["translation"]), 3)
            self.assertTrue(
                all(isinstance(v, (int, float)) for v in node["translation"])
            )
            self.assertNotIn("rotation", node)
            self.assertNotIn("scale", node)

    def test_exported_bones_reproduce_the_source_positions(self):
        """Recorrer el arbol del glb debe devolver las posiciones del origen."""
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in ("objects/roberta/fx_rightarm_satellite",
                         "objects/accessories_perks/fx_cologne",
                         "objects/accessories_garden/fx_wateringcan"):
                data = archive.read(f"recursos/{ruta}.res")
                origen = prop_skeleton(data)
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "p.glb"
                    convert_prop(data, "p", target)
                    doc, _ = open_glb(target)
                nodes = doc["nodes"]
                joints = doc["skins"][0]["joints"]
                self.assertEqual(len(joints), len(origen))
                parents = {
                    child: index
                    for index, node in enumerate(nodes)
                    for child in node.get("children", [])
                }

                def local(index):
                    matrix = np.eye(4)
                    if "translation" in nodes[index]:
                        matrix[:3, 3] = nodes[index]["translation"]
                    return matrix

                for position, joint in enumerate(joints):
                    matrix = local(joint)
                    cursor = joint
                    while cursor in parents:
                        cursor = parents[cursor]
                        matrix = local(cursor) @ matrix
                    with self.subTest(prop=ruta, joint=position):
                        self.assertTrue(
                            np.allclose(
                                matrix[:3, 3],
                                convert_vector3(origen[position]["position"]),
                                atol=1e-4,
                            ),
                            (matrix[:3, 3], origen[position]["position"]),
                        )

    def test_exported_mesh_uses_the_gltf_basis_like_the_skeleton(self):
        """Malla y huesos en el mismo espacio: si no, al abrir se ven separados.

        Regresion real: el esqueleto salia convertido a glTF y las mallas en el
        Z del PSP, con lo que el prop se abia girado y apartado del armature.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            data = archive.read("recursos/objects/roberta/fx_head.res")
        parts, _ = parse_rdms(data)
        self.assertTrue(parts)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "head.glb"
            convert_prop(data, "head", target)
            doc, _ = open_glb(target)
        lows, highs = [], []
        for node in doc["nodes"]:
            if "mesh" not in node:
                continue
            for primitive in doc["meshes"][node["mesh"]]["primitives"]:
                accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
                lows.append(accessor["min"])
                highs.append(accessor["max"])
        self.assertTrue(lows)
        for axis, name in enumerate("xyz"):
            esperado_min = min(
                convert_vector3(v[0])[axis] for p in parts for v in p["vertices"]
            )
            esperado_max = max(
                convert_vector3(v[0])[axis] for p in parts for v in p["vertices"]
            )
            with self.subTest(axis=name):
                self.assertAlmostEqual(min(c[axis] for c in lows),
                                       esperado_min, places=5)
                self.assertAlmostEqual(max(c[axis] for c in highs),
                                       esperado_max, places=5)

    def test_exported_bones_sit_inside_the_exported_mesh(self):
        """Ni hueso ni malla fuera de sitio: comparten espacio."""
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in ("objects/roberta/fx_head",
                         "objects/accessories_perks/fx_cologne"):
                data = archive.read(f"recursos/{ruta}.res")
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "p.glb"
                    convert_prop(data, "p", target)
                    doc, _ = open_glb(target)
                nodes = doc["nodes"]
                parents = {
                    child: index
                    for index, node in enumerate(nodes)
                    for child in node.get("children", [])
                }
                lows, highs = [], []
                for node in nodes:
                    if "mesh" not in node:
                        continue
                    for primitive in doc["meshes"][node["mesh"]]["primitives"]:
                        accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
                        lows.append(accessor["min"])
                        highs.append(accessor["max"])
                lo = np.array(lows).min(axis=0)
                hi = np.array(highs).max(axis=0)

                def local(index):
                    matrix = np.eye(4)
                    if "translation" in nodes[index]:
                        matrix[:3, 3] = nodes[index]["translation"]
                    return matrix

                for joint in doc["skins"][0]["joints"]:
                    matrix = local(joint)
                    cursor = joint
                    while cursor in parents:
                        cursor = parents[cursor]
                        matrix = local(cursor) @ matrix
                    with self.subTest(prop=ruta, joint=joint):
                        for axis in range(3):
                            self.assertGreaterEqual(matrix[axis, 3], lo[axis] - 0.6)
                            self.assertLessEqual(matrix[axis, 3], hi[axis] + 0.6)


def test_meshes_stay_put_where_the_preview_shows_them(self):
        """Las mallas no se mueven al exportar: ya estan en espacio mundo.

        Regresion real: al colgar cada malla de su articulacion, el lector glTF
        componia la transformacion del hueso encima de unos vertices que ya
        estaban en mundo y el prop aparecia desplazado al abrirlo.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in ("objects/chests/fx_dtgypsy_chest",
                         "objects/roberta/fx_rightarm_satellite",
                         "objects/accessories_garden/fx_wateringcan"):
                data = archive.read(f"recursos/{ruta}.res")
                parts, _ = parse_rdms(data)
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "p.glb"
                    convert_prop(data, "p", target)
                    doc, _ = open_glb(target)
                nodes = doc["nodes"]
                raices = set(doc["scenes"][0]["nodes"])
                hijos = {
                    child
                    for node in nodes
                    for child in node.get("children", [])
                }
                for index, node in enumerate(nodes):
                    if "mesh" not in node:
                        continue
                    with self.subTest(prop=ruta, mesh=index):
                        # raiz de la escena y no hija de ninguna articulacion
                        self.assertIn(index, raices)
                        self.assertNotIn(index, hijos)
                        accessor = doc["accessors"][
                            doc["meshes"][node["mesh"]]["primitives"][0]
                            ["attributes"]["POSITION"]
                        ]
                        for axis in range(3):
                            esperado = [
                                convert_vector3(v[0])[axis]
                                for p in parts
                                for v in p["vertices"]
                            ]
                            self.assertAlmostEqual(
                                accessor["min"][axis], min(esperado), places=5
                            )


def test_meshes_keep_their_vertex_groups(self):
        """Cada malla skineada a peso 1.0 sobre su articulacion.

        Regresion real: se emitia el skin pero las mallas sin JOINTS_0 ni
        WEIGHTS_0, con lo que al abrir el GLB no salia ningun vertex group.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in ("objects/chests/fx_dtgypsy_chest",
                         "objects/roberta/fx_rightarm_satellite",
                         "objects/accessories_garden/fx_wateringcan"):
                data = archive.read(f"recursos/{ruta}.res")
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "p.glb"
                    convert_prop(data, "p", target)
                    doc, _ = open_glb(target)
                nodes = doc['nodes']
                skins = doc['skins'][0]['joints']
                mallas = [i for i, node in enumerate(nodes) if "mesh" in node]
                self.assertTrue(mallas, ruta)
                for indice in mallas:
                    with self.subTest(prop=ruta, mesh=indice):
                        self.assertIn("skin", nodes[indice])
                        for primitive in doc['meshes'][
                            nodes[indice]['mesh']
                        ]["primitives"]:
                            attrs = primitive['attributes']
                            self.assertIn('JOINTS_0', attrs)
                            self.assertIn('WEIGHTS_0', attrs)
                            count = doc['accessors'][
                                attrs['JOINTS_0']
                            ]['count']
                            self.assertEqual(
                                doc['accessors'][attrs['WEIGHTS_0']]['count'],
                                count,
                            )
                            self.assertEqual(count % 4, 0)
                            self.assertGreaterEqual(count, 4)
                self.assertTrue(skins)


def test_rest_pose_leaves_the_meshes_where_they_are(self):
        """global(joint) @ ibm debe dar identidad: si no, la malla se mueve.

        Comprueba la propiedad de la pose de reposo sobre el glb de verdad:
        si el esqueleto y su inversa de ligadura no se cancelan, al abrir el
        modelo aparece desplazado aunque todo lo demas cuadre.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in ("objects/chests/fx_dtgypsy_chest",
                         "objects/roberta/fx_rightarm_satellite",
                         "objects/accessories_garden/fx_wateringcan"):
                data = archive.read(f"recursos/{ruta}.res")
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "p.glb"
                    convert_prop(data, "p", target)
                    doc, binary = open_glb(target)
                nodes = doc['nodes']
                skin = doc['skins'][0]
                joints = skin['joints']
                accessor = doc['accessors'][skin['inverseBindMatrices']]
                view = doc['bufferViews'][accessor['bufferView']]
                crudo = np.frombuffer(
                    binary,
                    '<f4',
                    count=accessor['count'] * 16,
                    offset=view.get('byteOffset', 0),
                )
                ibms = crudo.reshape(accessor['count'], 4, 4).transpose(0, 2, 1)
                parents = {}
                for index, node in enumerate(nodes):
                    for child in node.get("children", []):
                        parents[child] = index

                def local(index):
                    matrix = np.eye(4)
                    if 'translation' in nodes[index]:
                        matrix[:3, 3] = nodes[index]['translation']
                    return matrix

                def global_matrix(index):
                    matrix = local(index)
                    cursor = index
                    while cursor in parents:
                        cursor = parents[cursor]
                        matrix = local(cursor) @ matrix
                    return matrix

                for position, joint in enumerate(joints):
                    with self.subTest(prop=ruta, joint=position):
                        self.assertTrue(
                            np.allclose(
                                global_matrix(joint) @ ibms[position],
                                np.eye(4),
                                atol=1e-5,
                            ),
                            (ruta, position),
                        )


def test_coincident_helper_nodes_are_collapsed(self):
        """De los 16 nodos del cofre solo quedan los que sostienen las mallas.

        El juego encadena localizadores y sus hijos en el mismo punto. En
        reposo son indistinguibles, asi que su malla se cuelga del padre y el
        nodo sobra; y las ramas que no sostienen ninguna malla tambien.
        """
        with zipfile.ZipFile(ARCHIVE) as archive:
            data = archive.read("recursos/objects/chests/fx_dtgypsy_chest.res")
            bones, anclajes = prop_joints(data)
            records = sections(data)
            nodes, _, _, _ = node_hierarchy(data, records)
            placements = rigid_placements(data, records)
            con_malla = {i for a in placements.values() for i, _ in a}
        self.assertEqual(len(nodes), 16)
        self.assertEqual(len(bones), 5)
        # las tres piezas se reparten las cinco articulaciones, sin quedarse fuera
        self.assertEqual(con_malla, set(anclajes))
        self.assertLessEqual(set(anclajes.values()), set(range(len(bones))))
        # y no queda ningun padre colgando de un nodo eliminado
        indices = {bone["index"] for bone in bones}
        for bone in bones:
            if bone["parent"] >= 0:
                self.assertIn(bone["parent"], indices)


def test_every_mesh_keeps_a_joint_across_the_catalog(self):
        """Ninguna malla puede quedarse sin hueso en todo el catalogo."""
        with zipfile.ZipFile(ARCHIVE) as archive:
            for ruta in archive.namelist():
                if not ruta.endswith(".res"):
                    continue
                data = archive.read(ruta)
                if b"rdms" not in data[:128]:
                    continue
                try:
                    placements = rigid_placements(data, sections(data))
                except ValueError:
                    continue
                if not placements:
                    continue
                con_malla = {i for a in placements.values() for i, _ in a}
                bones, anclajes = prop_joints(data)
                with self.subTest(prop=ruta):
                    self.assertEqual(con_malla, set(anclajes))
                    self.assertTrue(bones)


class PropAnimationTests(unittest.TestCase):
    """Las curvas que cada prop lleva dentro de su propia seccion `node`."""

    ARCHIVO = "recursos/objects/roberta/fx_rightarm_satellite.res"
    CON_ANIMACION = "recursos/objects/accessories_perks/fx_timestopper.res"
    SIN_ANIMACION = "recursos/objects/chests/fx_dtgypsy_chest.res"

    def read(self, nombre):
        with zipfile.ZipFile(ARCHIVE) as archive:
            return archive.read(nombre)

    def test_el_satelite_trae_dos_canales_de_diez_segundos(self):
        clip = prop_clip(self.read(self.ARCHIVO))
        self.assertIsNotNone(clip)
        self.assertEqual(len(clip["channels"]), 2)
        self.assertAlmostEqual(clip["duration"], 10.0, places=3)
        for canal in clip["channels"]:
            self.assertEqual(canal["times"][0], 0.0)
            self.assertEqual(len(canal["times"]), len(canal["values"]))
            self.assertTrue(all(a < b for a, b in zip(canal["times"], canal["times"][1:])))

    def test_un_cofre_no_trae_ninguna_curva(self):
        self.assertIsNone(prop_clip(self.read(self.SIN_ANIMACION)))

    def test_el_timestopper_tiene_tres_y_medio_segundos(self):
        clip = prop_clip(self.read(self.CON_ANIMACION))
        self.assertIsNotNone(clip)
        self.assertEqual(len(clip["channels"]), 13)
        self.assertAlmostEqual(clip["duration"], 3.5, places=3)

    def test_las_claves_se_interpolan_entre_instantes(self):
        canal = {"times": (0.0, 1.0, 2.0), "values": (10.0, 20.0, 40.0)}
        self.assertAlmostEqual(muestra(canal, -1.0), 10.0)
        self.assertAlmostEqual(muestra(canal, 0.0), 10.0)
        self.assertAlmostEqual(muestra(canal, 0.5), 15.0)
        self.assertAlmostEqual(muestra(canal, 1.5), 30.0)
        self.assertAlmostEqual(muestra(canal, 99.0), 40.0)

    def test_la_pose_se_cierra_volviendo_al_reposo(self):
        data = self.read(self.ARCHIVO)
        clip = prop_clip(data)
        partes = load_parts(data)
        reposo, _ = pose(partes, data, clip, 0.0)
        final, _ = pose(partes, data, clip, clip["duration"])
        for a, b in zip(reposo, final):
            self.assertEqual(len(a["vertices"]), len(b["vertices"]))
            for va, vb in zip(a["vertices"], b["vertices"]):
                for x, y in zip(va[0], vb[0]):
                    self.assertAlmostEqual(x, y, places=5)

    def test_la_pose_mueve_el_prop_a_medio_clip(self):
        data = self.read(self.ARCHIVO)
        clip = prop_clip(data)
        partes = load_parts(data)
        reposo, _ = pose(partes, data, clip, 0.0)
        medio, _ = pose(partes, data, clip, clip["duration"] / 2)
        distinto = any(
            abs(va[0][0] - vb[0][0]) > 1e-4 for a, b in zip(reposo, medio)
            for va, vb in zip(a["vertices"], b["vertices"])
        )
        self.assertTrue(distinto, "el clip mueve el prop")

    def test_los_canales_se_reparten_entre_nodos_que_se_pueden_mover(self):
        data = self.read(self.ARCHIVO)
        clip = prop_clip(data)
        reparto = reparte_canales(data, clip)
        records = sections(data)
        nodes, _, _, _ = node_hierarchy(data, records)
        for nodo in reparto.values():
            self.assertLess(nodo, len(nodes))
            self.assertEqual(nodes[nodo][1], 10, "solo se mueven los que tienen transform")

    def test_el_catalogo_dice_cuantos_props_traen_curvas(self):
        with zipfile.ZipFile(ARCHIVE) as archive:
            with zipfile.ZipFile(ARCHIVE) as otro:
                rows = list(
                    csv.DictReader(
                        io.StringIO(otro.read("inventario_props.csv").decode("utf-8-sig"))
                    )
                )
            from ts2psp.paths import default_data_base

            base = default_data_base()
            carpeta = next(
                (
                    d
                    for d in (Path(base), base / "data")
                    if (d / "sims2_psp_props_recursos.zip").is_file()
                ),
                None,
            )
            if carpeta is None:
                self.skipTest("el archivo de props no esta junto a los datos")
        encontrados = prop_animations(carpeta / "sims2_psp_props_recursos.zip", rows)
        self.assertEqual(len(encontrados), 128)
        self.assertIn(
            "objects/roberta/fx_rightarm_satellite",
            [r["ruta_interna"] for r in encontrados],
        )
