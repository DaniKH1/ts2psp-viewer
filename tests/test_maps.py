"""The maps viewer draws the whole level, and the sky dome detection."""

import csv
import io
import os
import struct
import unittest
import zipfile
from pathlib import Path

from ts2psp.formats.props import parse_rdms, sections
from ts2psp.formats.prop_scene import node_hierarchies, rigid_placements
from ts2psp.ui.maps import CUPULA_LADO, CUPULA_VERTICES, es_cupula

DATA = Path(
    os.environ.get("TS2_PSP_DATA_DIR", Path(__file__).resolve().parent.parent / "data")
)
ARCHIVE = DATA / "sims2_psp_mapas_recursos.zip"


class SkyDomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ARCHIVE.exists():
            raise unittest.SkipTest("faltan los recursos de mapas")
        with zipfile.ZipFile(ARCHIVE) as archive:
            cls.rows = list(
                csv.DictReader(
                    io.StringIO(
                        archive.read("inventario_mapas.csv").decode("utf-8-sig")
                    )
                )
            )

    def datos(self, ruta):
        row = next(r for r in self.rows if r["ruta_interna"] == ruta)
        with zipfile.ZipFile(ARCHIVE) as archive:
            return archive.read(row["archivo_zip"])

    def parts_of(self, ruta):
        return parse_rdms(self.datos(ruta), apply_scene=False)[0]

    def lado(self, part):
        return max(
            max(v[0][k] for v in part["vertices"])
            - min(v[0][k] for v in part["vertices"])
            for k in range(3)
        )

    def test_only_the_dome_is_left_out_of_the_framing(self):
        """La cupula son las piezas de 528 de lado, y nada mas.

        Se dejan fuera del encuadre porque `_escala` abarca la escena entera
        segun su radio: con una esfera de 528 de lado dentro, el solar entero
        queda en un punto de dos pixeles. Pero el filtro solo puede tocar la
        cupula, asi que tiene que quitar exactamente las piezas de ese tamano y
        ninguna de las demas.
        """
        con_cupula = 0
        for row in self.rows:
            with self.subTest(mapa=row["ruta_interna"]):
                parts = self.parts_of(row["ruta_interna"])
                dome = [p for p in parts if abs(self.lado(p) - CUPULA_LADO) < 0.5]
                fuera = [p for p in parts if es_cupula(p)]

                # Todo lo que se quita es una cupula, y toda cupula se quita.
                self.assertEqual(len(fuera), len(dome), "filtra justo las cupulas")
                for p in fuera:
                    self.assertEqual(len(p["vertices"]), CUPULA_VERTICES)

                if dome:
                    con_cupula += 1
                    # Siempre son dos: el cielo y su pareja, identicas.
                    self.assertEqual(len(dome), 2)
                    # Y son las dos mayores de largo, con muchisima diferencia:
                    # en el cementerio la siguiente pieza mide 100.
                    self.assertGreater(
                        self.lado(sorted(parts, key=self.lado, reverse=True)[2]), 10
                    )
                else:
                    # Este mapa no tiene cupula: no se puede quitar nada.
                    self.assertEqual(parts, [p for p in parts if not es_cupula(p)])

        # Si la mitad de los mapas no los sacara, el filtro estaria roto.
        self.assertGreaterEqual(con_cupula, 10)

    def test_the_level_scene_is_read_and_does_not_explode(self):
        """Un nivel reparte su grafo en hasta 31 secciones `node`.

        Con mirar solo la primera, `apply_scene=True` reventaba ademas con
        "NODE transform outside its section" y el visor no podia ni intentarlo.
        """
        partes, rechazadas = parse_rdms(
            self.datos("levels/deadtree/graveyard"), apply_scene=True
        )
        self.assertFalse(rechazadas)
        self.assertGreater(len(partes), 300)

        secciones = node_hierarchies(
            self.datos("levels/deadtree/graveyard"),
            sections(self.datos("levels/deadtree/graveyard")),
        )
        self.assertEqual(len(secciones), 31)

        # Y las colocaciones existen: 361 referencias a mallas.
        colocaciones = rigid_placements(
            self.datos("levels/deadtree/graveyard"),
            sections(self.datos("levels/deadtree/graveyard")),
        )
        self.assertGreater(len(colocaciones), 300)

    def test_referenced_furniture_has_authored_world_placements(self):
        """Compression origins describe prototypes, not their map placements."""
        import numpy as np
        from ts2psp.formats.prop_scene import level_prop_references

        data = self.datos("levels/division_47/barracks")
        references = level_prop_references(data, sections(data))
        beds = [
            r for r in references if r["resource"].endswith("/fx_bedsinglebarracks")
        ]
        self.assertEqual(len(beds), 4)
        self.assertTrue(all(r["resolved"] for r in beds))
        np.testing.assert_allclose(beds[0]["position"], (-1.75, 3, 0), atol=1e-5)
        np.testing.assert_allclose(beds[-1]["position"], (8.75, 7.608456, 0), atol=1e-5)
        np.testing.assert_allclose(
            np.array(beds[-1]["matrix"])[:3, :3],
            ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
            atol=1e-5,
        )
        parts, rejected = parse_rdms(data)
        self.assertFalse(rejected)
        furniture = [
            p for p in parts if p["resource_path"].endswith("/fx_bedsinglebarracks")
        ]
        copies = {}
        for part in furniture:
            copies.setdefault(part["placement_node"], []).append(part)
        self.assertEqual(len(copies), 4)
        self.assertTrue(all(len(group) == 5 for group in copies.values()))
        first = next(iter(copies.values()))[0]
        last = list(copies.values())[-1][0]
        # Same source vertices, translated and turned by the final bed marker.
        np.testing.assert_allclose(
            [v[0] for v in last["vertices"]],
            [
                (-v[0][1] + 11.75, v[0][0] + 9.358456, v[0][2])
                for v in first["vertices"]
            ],
            atol=1e-4,
        )
        showers = [
            p for p in parts if p["resource_path"].endswith("/fx_shower_communal")
        ]
        self.assertEqual(len({p["placement_node"] for p in showers}), 3)

    def test_all_levels_expose_references_and_missing_targets(self):
        from ts2psp.formats.prop_scene import level_prop_references

        total = resolved = 0
        missing = set()
        for row in self.rows:
            with self.subTest(level=row["ruta_interna"]):
                data = self.datos(row["ruta_interna"])
                records = sections(data)
                refs = level_prop_references(data, records)
                self.assertTrue(refs)
                node_heads = {
                    off + 160
                    for tag, off, *_ in records
                    if tag == b"node" and struct.unpack_from("<I", data, off + 160)[0]
                }
                self.assertEqual(
                    {graph[2] for graph in node_hierarchies(data, records)}, node_heads
                )
                self.assertEqual(
                    len(refs), len({(r["graph"], r["node"]) for r in refs})
                )
                total += len(refs)
                resolved += sum(ref["resolved"] for ref in refs)
                missing.update(ref["resource"] for ref in refs if not ref["resolved"])
        self.assertGreater(total, 1400)
        self.assertGreater(resolved / total, 0.99)
        self.assertIn(
            "objects/lamps_ceiling/fx_lightingceilingchandeliermoderndirty", missing
        )

    def test_placement_report_can_be_exported_from_the_map_tab(self):
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from ts2psp.ui.maps import MapTab

        tab = SimpleNamespace(
            current={"ruta_interna": "levels/division_47/barracks"},
            data=self.datos("levels/division_47/barracks"),
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "placements.json"
            with patch(
                "ts2psp.ui.maps.filedialog.asksaveasfilename", return_value=str(target)
            ):
                MapTab.export_placements(tab)
            report = json.loads(target.read_text())
            self.assertIn("Z up", report["coordinates"])
            self.assertEqual(report["map"], tab.current["ruta_interna"])
            self.assertEqual(len(report["references"]), 93)

    def test_map_glb_preserves_instance_coordinates_and_source_names(self):
        import tempfile
        import numpy as np
        from ts2psp.core.coordinates import convert_vector3
        from ts2psp.formats.props import write_rigid_glb
        from ts2psp.formats.animations import open_glb
        from ts2psp.rendering.animation import accessor

        parts, _ = parse_rdms(self.datos("levels/division_47/barracks"))
        parts = [
            p for p in parts if p["resource_path"].endswith("/fx_bedsinglebarracks")
        ]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "beds.glb"
            write_rigid_glb(parts, "beds", target)
            doc, binary = open_glb(target)
        self.assertEqual(len(doc["nodes"]), 20)
        for node, part in zip(doc["nodes"], parts):
            self.assertEqual(node["extras"]["sourceResource"], part["resource_path"])
            primitive = doc["meshes"][node["mesh"]]["primitives"][0]
            np.testing.assert_allclose(
                accessor(doc, binary, primitive["attributes"]["POSITION"]),
                [convert_vector3(v[0]) for v in part["vertices"]],
                atol=1e-5,
            )

    def test_compression_origin_is_only_a_legacy_diagnostic(self):
        """A zero RDMS origin must never be treated as an unplaced object."""
        from ts2psp.ui.maps import es_sin_colocar

        cuento = {}
        for row in self.rows:
            partes = self.parts_of(row["ruta_interna"])
            cuento[row["ruta_interna"]] = (
                sum(1 for p in partes if es_sin_colocar(p)),
                len(partes),
            )

        cuartel = cuento["levels/division_47/barracks"]
        self.assertGreater(
            cuartel[0], cuartel[1] // 2, "many Barracks prototypes have a zero origin"
        )

        menu = cuento["levels/interface/mainmenu"]
        self.assertLess(menu[0], menu[1] // 20, "el menu principal casi no tiene")

        # Other meshes use compression origins far from the local centre.
        piezas = self.parts_of("levels/deadtree/graveyard")
        colocadas = [p for p in piezas if not es_sin_colocar(p)]
        self.assertGreater(len(colocadas), 250)
        for parte in colocadas[:20]:
            self.assertGreater(max(abs(v) for v in parte["origin"]), 0.99)

    def test_levels_drop_their_vertex_colours(self):
        """En un nivel los colores de vertice tiñen el material y hay que quitarlos.

        Multiplican el color del shader, y el material del cementerio llega con un
        gris a media luz: con los colores puestos los arboles salen de oliva
        oscuro y sin ellos verdes. El visor de mapas los quita de entrada, con un
        boton para volver a ponerlos.
        """
        piezas, _ = parse_rdms(self.datos("levels/deadtree/graveyard"))
        con_color = sum(
            1 for p in piezas if any(v[3] is not None for v in p["vertices"])
        )
        self.assertGreater(con_color, 50, "el nivel si trae colores de vertice")

        piezas, _ = parse_rdms(
            self.datos("levels/deadtree/graveyard"), vertex_colors=False
        )
        self.assertFalse(
            [v[3] for p in piezas for v in p["vertices"] if v[3] is not None],
            "con vertex_colors=False no debe quedar ninguno",
        )
        # Y las posiciones no se tocan: solo el color.
        con_pos, _ = parse_rdms(self.datos("levels/deadtree/graveyard"))
        self.assertEqual(
            [p["vertices"][0][0] for p in piezas],
            [p["vertices"][0][0] for p in con_pos],
        )

    def test_the_node_graph_places_the_levels_meshes(self):
        """El grafo de nodos coloca las mallas del nivel, y hay que aplicarlo.

        En el menu principal 216 de las 217 mallas que alcanza el grafo llegan con
        su propia traslacion, 68 de ellas muros (una se mueve 43.1 en x), mientras
        el cesped y la carretera se quedan quietos. Sin aplicar la escena la casa
        se dibuja encima de la carretera y las paredes no cierran con el suelo.

        Object reference transforms are checked separately with the Barracks beds.
        """
        import numpy as np

        def muros(piezas):
            fuera = []
            for p in piezas:
                pts = np.array([v[0] for v in p["vertices"]])
                lado = pts.max(axis=0) - pts.min(axis=0)
                if lado.min() < 0.6 and 2.0 < lado[2] < 3.2:
                    fuera.append(p)
            return fuera

        base = muros(
            parse_rdms(self.datos("levels/interface/mainmenu"), apply_scene=False)[0]
        )
        con_escena = muros(
            parse_rdms(self.datos("levels/interface/mainmenu"), apply_scene=True)[0]
        )
        self.assertEqual(len(base), len(con_escena))

        indice = {p["name"]: p for p in base}
        movidos = 0
        for p in con_escena:
            q = indice[p["name"]]
            a = np.array([v[0] for v in q["vertices"]])
            b = np.array([v[0] for v in p["vertices"]])
            self.assertEqual(a.shape, b.shape)
            if np.abs(a - b).max() > 0.01:
                movidos += 1
        self.assertGreater(movidos, 50, "el menu principal coloca sus muros")

        # Y el terreno no se mueve: si se moviera, la casa seguiria encima.
        for nombre in ("pieza_598", "pieza_601", "pieza_603", "pieza_605"):
            a = next(
                p
                for p in parse_rdms(
                    self.datos("levels/interface/mainmenu"), apply_scene=False
                )[0]
                if p["name"] == nombre
            )
            b = next(
                p
                for p in parse_rdms(
                    self.datos("levels/interface/mainmenu"), apply_scene=True
                )[0]
                if p["name"] == nombre
            )
            np.testing.assert_allclose(
                np.array([v[0] for v in a["vertices"]]),
                np.array([v[0] for v in b["vertices"]]),
                atol=0.01,
                err_msg=f"{nombre} es terreno y no debe moverse",
            )

        # En el cementerio la escena no cambia nada, que es por lo que se puede
        # aplicar sin miedo en todos los niveles.
        cemetery = self.datos("levels/deadtree/graveyard")
        a, _ = parse_rdms(cemetery)
        b, _ = parse_rdms(cemetery, apply_scene=True)
        for p, q in zip(a, b):
            if p["name"] == q["name"] and len(p["vertices"]) == len(q["vertices"]):
                np.testing.assert_allclose(
                    np.array([v[0] for v in p["vertices"]]),
                    np.array([v[0] for v in q["vertices"]]),
                    atol=0.01,
                )

    def test_the_pieces_with_their_faces_around_are_turned_around(self):
        """Tres piezas de los niveles llevan las caras al reves y se dan la vuelta.

        El visor de mapas recorta las caras traseras, y con la cara buena hacia
        atras la pieza no se ve desde donde toca. Ninguna la delata el volumen con
        signo: en un plano el signo depende de lo lejos que este el origen, y en
        una cupula lo que cuenta es hacia dentro o hacia fuera. Por eso van en
        una lista a mano.

        El cartel del cielo es la misma malla en los dos niveles de Deadtree: el
        pieza_581 del cementerio y el pieza_588 del hub, un cuadrado de 10 m con
        alfa. Y en The Monument esta la cupula interior de 475 x 452 m, con los
        veinte triangulos mirando hacia fuera.
        """
        import numpy as np

        from ts2psp.formats.props import CARAS_AL_REVES

        CARTA = ("levels/deadtree/graveyard", "pieza_581")
        CARTA_HUB = ("levels/deadtree/hub", "pieza_588")
        CUPULA = ("levels/the_monument", "pieza_96")

        def puntos_de(part):
            puntos = np.array([v[0] for v in part["vertices"]], dtype=np.float64)
            indices = np.array(part["indices"], dtype=np.int64).reshape(-1, 3)
            normales = np.cross(
                puntos[indices[:, 1]] - puntos[indices[:, 0]],
                puntos[indices[:, 2]] - puntos[indices[:, 0]],
            )
            pesos = np.linalg.norm(normales, axis=1)
            total = (normales * pesos[:, None]).sum(axis=0)
            return puntos, indices, normales, total / np.linalg.norm(total)

        def leer(nivel, con_ruta):
            piezas = parse_rdms(
                self.datos(nivel),
                apply_scene=True,
                vertex_colors=False,
                ruta=nivel if con_ruta else None,
            )[0]
            return {p["name"]: p for p in piezas}

        for nivel, pieza in (CARTA, CARTA_HUB, CUPULA):
            self.assertIn(pieza, CARAS_AL_REVES[nivel], nivel)
            antes = leer(nivel, False)[pieza]
            despues = leer(nivel, True)[pieza]
            _, _, _, normal_antes = puntos_de(antes)
            _, _, _, normal_despues = puntos_de(despues)
            np.testing.assert_allclose(
                normal_despues,
                -normal_antes,
                atol=1e-6,
                err_msg=f"{nivel}/{pieza}: la normal no se ha dado la vuelta",
            )
            self.assertEqual(len(antes["vertices"]), len(despues["vertices"]), nivel)

        # Y solo esas tres: el resto de cada nivel llega igual.
        for nivel, (_n, excepcion) in (
            ("levels/deadtree/graveyard", CARTA),
            ("levels/deadtree/hub", CARTA_HUB),
            ("levels/the_monument", CUPULA),
        ):
            con_ruta, sin_ruta = leer(nivel, True), leer(nivel, False)
            for nombre in [n for n in con_ruta if n != excepcion][::53]:
                self.assertEqual(
                    con_ruta[nombre]["indices"], sin_ruta[nombre]["indices"], nombre
                )
            self.assertNotIn(nivel, ("levels/gas_station/garage",))

    def test_a_level_without_a_dome_keeps_every_piece(self):
        parts = self.parts_of("levels/division_47/barracks")
        self.assertTrue(parts)
        self.assertEqual([p for p in parts if es_cupula(p)], [])

    def test_the_count_of_vertices_alone_does_not_make_a_dome(self):
        """Hay piezas de 392 vertices que no son la cupula.

        En la gasolinera hay tres y solo dos son el cielo; en el subnivel
        secreto hay una y no llega a medir ni la mitad. Por eso el filtro mira
        tambien el tamano, y no solo el numero de vertices.
        """
        partes = self.parts_of("levels/gas_station/hub")
        mismo_vertice = [p for p in partes if len(p["vertices"]) == CUPULA_VERTICES]
        self.assertEqual(len(mismo_vertice), 3)
        self.assertEqual(len([p for p in mismo_vertice if es_cupula(p)]), 2)

        secreto = self.parts_of("levels/division_47/secret_sublevel")
        con_392 = [p for p in secreto if len(p["vertices"]) == CUPULA_VERTICES]
        self.assertEqual(len(con_392), 1)
        self.assertFalse(es_cupula(con_392[0]))
        self.assertLess(self.lado(con_392[0]), CUPULA_LADO / 2)

    def test_the_terrain_of_a_level_is_not_mistaken_for_the_dome(self):
        """El terreno mas llano del Monumento mide 476, casi lo de la cupula.

        Lo que los separa es el numero de vertices: la cupula es una esfera de
        392 y el plano del terreno son 22. Si se mirara solo el tamano, el
        terreno desapareceria del mapa.
        """
        parts = self.parts_of("levels/the_monument")
        planos = [p for p in parts if len(p["vertices"]) == 22 and self.lado(p) > 400]
        self.assertTrue(planos, "el plano grande sigue en el recurso")
        for plano in planos:
            self.assertFalse(es_cupula(plano))
        self.assertEqual(len([p for p in parts if es_cupula(p)]), 2)


if __name__ == "__main__":
    unittest.main()
