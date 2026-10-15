"""The maps viewer draws the whole level, and the sky dome detection."""

import csv
import io
import os
import unittest
import zipfile
from pathlib import Path

from ts2psp.formats.props import parse_rdms
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

    def parts_of(self, ruta):
        row = next(r for r in self.rows if r["ruta_interna"] == ruta)
        with zipfile.ZipFile(ARCHIVE) as archive:
            blob = archive.read(row["archivo_zip"])
        return parse_rdms(blob, apply_scene=False)[0]

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
