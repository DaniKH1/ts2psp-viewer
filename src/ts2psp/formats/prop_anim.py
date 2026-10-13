"""Las curvas de animacion que lleva un prop dentro de su propio recurso.

Los clips de personaje son recursos BANM aparte y se listan solos. Un prop no
tiene ninguno: su animacion va en la cabecera de la seccion `node`, con una
entrada por canal.

    palabras  0..1  numero de nodos y desplazamiento de la tabla de indices
    palabras  2..7  constantes; salen iguales en los 751 props del catalogo
    desde la 8,     una entrada por canal mientras el recuento sea positivo:

                        [?, numero de claves, desplazamiento a las claves]

                    y el desplazamiento se resuelve desde la propia palabra que
                    lo guarda, igual que el resto de punteros del formato.

    despues,        las claves: registros de 16 bytes con
                    [instante, valor, 0, 0], en segundos y en la unidad que
                    traiga el canal.

129 de los 751 props del catalago tienen canales y se leen sin un solo fallo.

Lo que NO se ha averiguado es a que nodo pertenece cada canal: las entradas no
llevan el indice y los nodos no apuntan a sus curvas. `reparte_canales` las
asigna por orden a los nodos que tienen transform. Es una conjetura -un canal
por articulacion, en el mismo orden en que se enumeran-, no algo verificado. Si
al reproducir un prop las piezas se mueven de forma que no cuadra con lo que
deberian, el sitio a revisar es esa funcion y el eje del giro, aqui abajo.
"""

import math
import struct

import numpy as np

from ts2psp.formats.props import sections
from ts2psp.formats.prop_scene import (
    node_hierarchy,
    transform_vertices,
    _relative,
)

PALABRAS_CABECERA = 8
PALABRAS_ENTRADA = 3
FLOATS_CLAVE = 4
MAX_CLAVES = 4096
CON_TRANSFORM = 10


def prop_clip(data):
    """Curvas del prop, o None si no lleva animacion.

    Devuelve {"duration": float, "channels": [{"times", "values"}]}. La
    duracion es el ultimo instante de la curva mas larga.
    """
    try:
        records = sections(data)
        nodes, _, head, end = node_hierarchy(data, records)
    except ValueError:
        return None
    if head is None:
        return None
    tabla = _relative(data, head + 4)
    canales = []
    at = head + 4 * PALABRAS_CABECERA
    while at + 4 * PALABRAS_ENTRADA <= tabla:
        _, claves, desplazamiento = struct.unpack_from("<3i", data, at)
        if not 0 < claves <= MAX_CLAVES:
            break
        destino = at + 8 + desplazamiento
        if not head <= destino <= end - 4 * FLOATS_CLAVE * claves:
            break
        plano = struct.unpack_from("<%df" % (FLOATS_CLAVE * claves), data, destino)
        times = plano[0::FLOATS_CLAVE]
        values = plano[1::FLOATS_CLAVE]
        if any(a >= b for a, b in zip(times, times[1:])):
            break
        canales.append({"times": times, "values": values})
        at += 4 * PALABRAS_ENTRADA
    if not canales:
        return None
    return {
        "duration": max(canal["times"][-1] for canal in canales),
        "channels": canales,
    }


def prop_animations(path, rows):
    """Que filas del inventario llevan curvas, para montar la lista.

    Se lee el zip una vez y se recorre en memoria: con `ZipFile.read` entrada a
    entrada se relee el indice central 751 veces.
    """
    import zipfile

    encontrados = []
    with zipfile.ZipFile(path) as archivo:
        for row in rows:
            try:
                data = archivo.read(row["archivo_zip"])
            except KeyError:
                continue
            if prop_clip(data) is not None:
                encontrados.append(row)
    return encontrados


def sample(canal, t):
    """Valor del canal en el instante t, interpolando entre claves."""
    times = canal["times"]
    values = canal["values"]
    if t <= times[0]:
        return values[0]
    for i in range(1, len(times)):
        if t > times[i]:
            continue
        antes, ahora = times[i - 1], times[i]
        if ahora == antes:
            return values[i]
        u = (t - antes) / (ahora - antes)
        return values[i - 1] + (values[i] - values[i - 1]) * u
    return values[-1]


def reparte_canales(data, clip):
    """Nodo al que pertenece cada canal. Conjetura: por orden.

    Se asignan en el mismo orden en que aparecen a los nodos que tienen
    transform, que son los unicos que se pueden mover. Si sobran canales se
    quedan sin nodo; si sobran nodos, se quedan quietos.
    """
    try:
        records = sections(data)
        nodes, _, _, head = node_hierarchy(data, records)
    except ValueError:
        return {}
    if not nodes:
        return {}
    candidatos = [i for i, (_, kind, _, _) in enumerate(nodes) if kind == CON_TRANSFORM]
    return {
        canal: nodo
        for canal, nodo in enumerate(candidatos[: len(clip["channels"])])
    }


def _giro(angulo):
    matriz = np.eye(4)
    c, s = math.cos(angulo), math.sin(angulo)
    matriz[:3, :3] = ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    return matriz


def pose(parts, data, clip, t, reparto=None):
    """Partes colocadas en el instante t, y donde queda cada hueso.

    El giro va en el transform local del nodo, para que rotee sobre su propio
    origen y no sobre el de su padre. Como `parse_rdms` deja las mallas ya
    colocadas en mundo, lo que se aplica es la diferencia entre la pose y la de
    reposo.
    """
    try:
        records = sections(data)
        nodes, world_matrix, _, _ = node_hierarchy(data, records)
    except ValueError:
        return parts, {}
    if not nodes:
        return parts, {}
    if reparto is None:
        reparto = reparte_canales(data, clip)
    # `reparte_canales` va de canal a nodo; aqui se necesita al reves.
    por_nodo = {nodo: canal for canal, nodo in reparto.items()}

    def local(index):
        base = nodes[index][3]
        if index in por_nodo:
            base = base @ _giro(sample(clip["channels"][por_nodo[index]], t))
        return base

    cache = {}

    def compuesta(index):
        if index not in cache:
            padre = nodes[index][2]
            cache[index] = (
                compuesta(padre) @ local(index)
                if 0 <= padre < len(nodes)
                else local(index)
            )
        return cache[index]

    matrices = {i: compuesta(i) for i in range(len(nodes))}
    salida = []
    for part in parts:
        indice = part.get("node", -1)
        if indice not in matrices:
            salida.append(part)
            continue
        try:
            delta = matrices[indice] @ np.linalg.inv(world_matrix(indice))
        except (np.linalg.LinAlgError, ValueError):
            salida.append(part)
            continue
        salida.append({**part, "vertices": transform_vertices(part["vertices"], delta)})
    huesos = {i: matrices[i][:3, 3] for i in matrices}
    return salida, huesos