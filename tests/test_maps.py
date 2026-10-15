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

        En el cementerio los nodos valen la identidad, asi que aplicarla no cambia
        nada: se aplica siempre.
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

    def test_the_view_is_redrawn_when_the_same_number_of_pieces_change(self):
        """Apagar y encender piezas tiene que redibujar aunque el numero no varye.

        La escena se reutiliza entre fotogramas y solo se vuelve a montar cuando
        cambia su clave. Si en la clave va cuantas piezas hay encendidas en vez de
        cuales son, apagar una y luego otra deja el mismo numero que al
        principio: la escena se creia la misma, no se redibuja, y al volver a
        encender la primera seguia sin verse.
        """
        from ts2psp.ui.maps import firma_de_piezas

        total = 404
        ocultas = set()
        firmas = []
        for paso in ('pieza_47', 'pieza_51', 'pieza_47', 'pieza_62'):
            if paso in ocultas:
                ocultas.discard(paso)
            else:
                ocultas.add(paso)
            firmas.append(firma_de_piezas(ocultas))

        # Las cuentas se repiten y las firmas no: sin ellas la escena se reutiliza.
        cuentas = [total - len(f) for f in firmas]
        self.assertEqual(cuentas, [403, 402, 403, 402], 'las cuentas se repiten')
        self.assertNotEqual(
            firmas[0], firmas[2], 'apagar una y encender otra no es lo mismo'
        )
        self.assertEqual(len(set(firmas)), 4, 'cada conjunto apagado, su escena')

        # Y el orden de los nombres no cuenta: es un conjunto.
        self.assertEqual(
            firma_de_piezas({'b', 'a'}), firma_de_piezas({'a', 'b'})
        )

    def test_the_sky_banner_of_the_deadtree_levels_is_turned_around(self):
        """El cartel del cielo lleva las caras al reves y se da la vuelta al leerlo.

        El visor de mapas recorta las caras traseras, y con la cara buena hacia
        atras el cartel no se ve desde donde toca. El volumen con signo solo lo
        delata en las mallas cerradas; en un plano no dice nada, porque el signo
        depende de lo lejos que este el origen y no de hacia donde mire la cara.
        Por eso van en una lista a mano.

        Es la misma malla en los dos niveles: el pieza_581 del cementerio y el
        pieza_588 del hub, un cuadrado de 10 m con alfa, suelto en el cielo.
        """
        import numpy as np

        from ts2psp.formats.props import CARAS_AL_REVES

        def normal_media(part):
            puntos = np.array([v[0] for v in part["vertices"]], dtype=np.float64)
            indices = np.array(part["indices"], dtype=np.int64).reshape(-1, 3)
            normales = np.cross(
                puntos[indices[:, 1]] - puntos[indices[:, 0]],
                puntos[indices[:, 2]] - puntos[indices[:, 0]],
            )
            pesos = np.linalg.norm(normales, axis=1)
            total = (normales * pesos[:, None]).sum(axis=0)
            return total / np.linalg.norm(total)

        for nivel, pieza in (
            ("levels/deadtree/graveyard", "pieza_581"),
            ("levels/deadtree/hub", "pieza_588"),
        ):
            self.assertEqual(CARAS_AL_REVES[nivel], {pieza}, nivel)
            con_ruta = {
                p["name"]: p
                for p in parse_rdms(
                    self.datos(nivel),
                    apply_scene=True,
                    vertex_colors=False,
                    ruta=nivel,
                )[0]
            }
            sin_ruta = {
                p["name"]: p
                for p in parse_rdms(
                    self.datos(nivel), apply_scene=True, vertex_colors=False
                )[0]
            }
            np.testing.assert_allclose(
                normal_media(con_ruta[pieza]),
                -normal_media(sin_ruta[pieza]),
                atol=1e-6,
                err_msg=f"{nivel}/{pieza}: la normal no se ha dado la vuelta",
            )
            # Solo ese cartel: el resto del nivel llega igual.
            otras = [n for n in con_ruta if n != pieza]
            for nombre in otras[::37]:
                self.assertEqual(
                    con_ruta[nombre]["indices"], sin_ruta[nombre]["indices"], nombre
                )

        # Y ningun otro nivel toca la lista.
        self.assertNotIn("levels/gas_station/garage", CARAS_AL_REVES)

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
