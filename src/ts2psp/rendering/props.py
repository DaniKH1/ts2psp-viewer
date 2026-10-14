"""Prop loading and previews."""

import numpy as np
from PIL import ImageDraw

from ts2psp.formats.props import parse_rdms, parse_prop_bmsh
import ts2psp.rendering.opengl as gl


def prop_scene(parts, key, show_textures=True):
    """Escena GL del prop, cacheada por clave.

    Se expone aparte porque al reproducir una animacion hay que reutilizar la
    misma escena y solo cambiarle los vertices: si se pidiera por clave dentro
    de `render_prop`, la cache devolveria la de reposo y el prop no se moveria.
    """
    textures = {}
    if show_textures:
        for part in parts:
            texture = part.get("texture")
            if texture and texture[1]:
                textures[texture[0]] = texture
    return gl.escena(
        ("prop", key, show_textures), parts, textures, "prop", fondo=(28, 31, 41)
    )


def posed_positions(parts):
    """Vertices de las partes en un solo array, en el orden que espera la escena."""
    total = sum(len(part["vertices"]) for part in parts)
    salida = np.empty((total, 3), np.float32)
    k = 0
    for part in parts:
        vertices = np.array([v[0] for v in part["vertices"]], np.float32)
        salida[k : k + len(vertices)] = vertices
        k += len(vertices)
    return salida


def render_prop(
    parts,
    width=820,
    height=820,
    yaw=0.4,
    zoom=1.0,
    show_textures=True,
    pitch=0.0,
    pan=(0, 0),
    key=None,
    camara=None,
    show_grid=True,
    bones=None,
    show_bones=False,
    objeto=None,
):
    """Dibuja un objeto con la GPU. `key` lo identifica en la caché.

    No hay render por CPU: si el equipo no puede acelerar avisa con gl.SinOpenGL y el
    llamante lo enseña en su barra de estado.

    `bones` es el esqueleto devuelto por `prop_skeleton`; con `show_bones` se
    dibuja encima, igual que en el visor de personajes.

    `objeto` es la escena a reutilizar; si no se pasa se busca por clave, con lo
    que las mallas nuevas de una llamada anterior se ignoran.
    """
    scene = objeto or prop_scene(parts, key, show_textures)
    scene.mostrar_grid = show_grid
    image = scene.render(width, height, yaw, pitch, zoom, pan, camara)
    if show_bones and bones:
        overlay = ImageDraw.Draw(image)
        points = [
            scene.proyectar(
                tuple(bone["position"]), width, height, yaw, pitch, zoom, pan, camara
            )
            for bone in bones
        ]
        for index, bone in enumerate(bones):
            if bone["parent"] >= 0:
                overlay.line(
                    (points[bone["parent"]], points[index]),
                    fill=(88, 235, 220),
                    width=2,
                )
        for x, y in points:
            overlay.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(225, 255, 250))
    return image


def bones_over(parts, bones, margen=1.3):
    """Solo los nodos que caen sobre la pieza.

    El nodo raiz de un prop no es una articulation: es el ancla donde el juego
    coloca el objeto, y suele estar lejos del modelo. dibujarla produce lineas
    cruzadas desde un punto flotante que no corresponde a nada visible.
    """
    points = [vertex[0] for part in parts for vertex in part["vertices"]]
    if not points or not bones:
        return ()
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    lo = (min(xs), min(ys), min(zs))
    hi = (max(xs), max(ys), max(zs))
    margen_caja = [abs(hi[k] - lo[k]) * (margen - 1) / 2 for k in range(3)]
    dentro = [
        bone
        for bone in bones
        if all(
            lo[k] - margen_caja[k] <= bone["position"][k] <= hi[k] + margen_caja[k]
            for k in range(3)
        )
    ]
    # Al quitar nodos los indices dejan de valer: se remapean, y el que se
    # queda sin padre dibujado se dibuja como raiz suelta.
    nuevo = {bone["index"]: i for i, bone in enumerate(dentro)}
    return tuple({**bone, "parent": nuevo.get(bone["parent"], -1)} for bone in dentro)


def load_parts(data, include_helpers=False, opaco=False, ruta=None):
    if b"rdms" in data[:128]:
        return [
            part
            for part in parse_rdms(data, opaco=opaco, ruta=ruta)[0]
            if include_helpers or not part.get("helper")
        ]
    parts = []
    for part in parse_prop_bmsh(data, ruta=ruta):
        # Los UV llegan en crudo desde `parse_prop_bmsh`, igual que al export,
        # y se convierten aqui una sola vez.
        vertices = [
            (
                tuple(x / 1000.0 for x in pos),
                None,
                ((uv[0] + 32768) / 4096.0, 1 - (uv[1] + 32768) / 4096.0),
                None,
            )
            for pos, _, uv, _ in part["vertices"]
        ]
        parts.append({**part, "vertices": vertices})
    return parts
