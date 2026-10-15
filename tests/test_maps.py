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
            max(v[0][k] for v in part["vertices"]) - min(v[0][k] for v in part["vertices"])
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

    def test_the_levels_own_props_have_no_transform_to_apply(self):
        """Por que las farolas y los bancos salen amontonados en el origen.

        Escribo esto porque parece un fallo del parser y no lo es, y se pierde
        mucho tiempo si no se sabe: el nodo de cada malla del nivel cuelga de
        una cadena de transformadas que vale la identidad. Los 874 nodos de tipo
        10 del cementerio tienen numeros de verdad, pero ninguno es ancestro de
        una malla. La colocacion la pone el Lua del nivel (`graveyard_script.init`)
        al cargar, y sus coordenadas no estan en este recurso.
        """
        import numpy as np

        from ts2psp.formats.prop_scene import _relative

        datos = self.datos("levels/deadtree/graveyard")
        meshes = {
            off + 128 + prefix: off + 128
            for tag, off, _, _, _ in sections(datos)
            if tag == b"rdms"
            for prefix in (0, 32)
        }
        totales = identidades = 0
        for nodes, world, head, end in node_hierarchies(datos, sections(datos)):
            for index, (at, kind, _, _) in enumerate(nodes):
                if kind != 25 or at + 40 > end:
                    continue
                group_count = struct.unpack_from("<I", datos, at + 32)[0]
                groups = _relative(datos, at + 36)
                if group_count > 128 or not head <= groups <= end - group_count * 44:
                    continue
                for group in range(group_count):
                    block = groups + group * 44
                    lod_count = struct.unpack_from("<I", datos, block + 36)[0]
                    lods = _relative(datos, block + 40)
                    if not (0 < lod_count <= 64 and head <= lods <= end - lod_count * 8):
                        continue
                    if _relative(datos, lods) not in meshes:
                        continue
                    totales += 1
                    if np.allclose(world(index), np.eye(4)):
                        identidades += 1

        self.assertGreater(totales, 300, "el nivel tiene mallas colocadas")
        self.assertGreater(
            identidades,
            totales * 0.7,
            "casi todas las mallas del nivel cuelgan de la identidad",
        )

    def test_the_furniture_the_level_places_on_load_is_left_out(self):
        """Esas piezas no tienen sitio en el recurso, asi que no se dibujan.

        El juego coloca el mobiliario del solar con un Lua de nivel, no con
        coordenadas en el .res: la cabecera rdms de esas piezas trae el origen a
        cero y se ven amontonadas en el centro. Se comprueba en los 21 niveles
        que el filtro quita muchas en el cuartel y deja casi intacto el menu
        principal, que es el unico nivel que ya se veia bien.
        """
        from ts2psp.ui.maps import es_sin_colocar

        cuento = {}
        for row in self.rows:
            partes = self.parts_of(row["ruta_interna"])
            cuento[row["ruta_interna"]] = (
                sum(1 for p in partes if es_sin_colocar(p)),
                len(partes),
            )

        cuartel = cuento["levels/division_47/barracks"]
        self.assertGreater(cuartel[0], cuartel[1] // 2, "el cuartel va medio amontonado")

        menu = cuento["levels/interface/mainmenu"]
        self.assertLess(menu[0], menu[1] // 20, "el menu principal casi no tiene")

        # Y ninguna pieza colocada se quita: las del mausoleo tienen su origen
        # lejos del centro y siguen en pantalla.
        piezas = self.parts_of("levels/deadtree/graveyard")
        colocadas = [p for p in piezas if not es_sin_colocar(p)]
        self.assertGreater(len(colocadas), 250)
        for parte in colocadas[:20]:
            self.assertGreater(max(abs(v) for v in parte["origin"]), 0.99)

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
