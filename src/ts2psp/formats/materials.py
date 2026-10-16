"""Resolve prop shaders to their referenced SURF, independent of file order."""

import math
import struct
import io
import hashlib

import numpy as np
from PIL import Image

from ts2psp.formats.surfaces import read_surface
from ts2psp.formats.prop_scene import _header_sizes


def _canal_alfa(texture_png):
    """El canal alfa de una textura, cacheado.

    Un prop tiene muchas piezas sobre la misma textura, y decodificar el PNG en
    cada una costaba mas que rasterizar los triangulos. La clave es la identidad
    del objeto y se guarda tambien una referencia a el, para que un `id` reciclado
    no devuelva el alfa de otra textura.
    """
    clave = id(texture_png)
    entrada = _ALFA_CACHE.get(clave)
    if entrada is not None and entrada[0] is texture_png:
        return entrada[1]
    if len(_ALFA_CACHE) > 64:
        _ALFA_CACHE.clear()
    alfa = np.asarray(Image.open(io.BytesIO(texture_png)).convert("RGBA"))[:, :, 3]
    _ALFA_CACHE[clave] = (texture_png, alfa)
    return alfa


def alfa_tocado(texture_png, uvs, indices):
    """El alfa mas bajo que la malla pisa de verdad, no el de la textura entera.

    Un atlas reparte la imagen entre muchas mallas, asi que el alfa minimo del
    PNG entero no dice nada sobre una pieza concreta: una lamina opaca puede
    estar pegada a un recorte de alpiste y salir ambas como translucidas.

    Aqui se rasterizan los triangulos de la malla sobre la textura y se mira solo
    lo que cubren. Asi cada pieza decide por su cuenta, sin listas a mano.

    Las mallas que se salen de [0,1] dan la vuelta, porque se dibujan con
    GL_REPEAT, asi que el triangulo se rasteriza una vez por vuelta. Si no, de
    una malla que recorre el atlas entero solo se miraria el trozo que cae
    dentro de la textura, y se clasificaria por una parte suelta.
    """
    alfa = _canal_alfa(texture_png)
    alto, ancho = alfa.shape
    cubierta = np.zeros(alfa.shape, dtype=bool)
    # Se calcula una sola vez: comparar el alfa entero por cada triangulo salia
    # carisimo en las mallas que dan muchas vueltas, que son justo las que
    # llegan a ese caso.
    alguna_transparente = bool((alfa < 255).any())
    # el v de los UV viene ya invertido por el cargador, igual que al rasterizar
    puntos = [(u, 1.0 - v) for u, v in ((uv or (0.0, 0.0)) for uv in uvs)]
    # `triangle_strip` aplana los triangulos en una sola lista de indices
    indices = list(indices or ())
    for k in range(0, len(indices) - 2, 3):
        try:
            vertices = [puntos[i] for i in indices[k : k + 3]]
        except (IndexError, TypeError):
            continue
        vueltas_u = _vueltas([v[0] for v in vertices])
        vueltas_v = _vueltas([v[1] for v in vertices])
        if vueltas_u is None or vueltas_v is None:
            # El triangulo da demasiadas vueltas: si la textura tiene algo
            # transparente, una malla que la recorre entera lo pisa.
            cubierta[:] = alguna_transparente
            continue
        for vuelta_u in vueltas_u:
            for vuelta_v in vueltas_v:
                _rasterizar(
                    cubierta, alfa, [(u - vuelta_u, v - vuelta_v) for u, v in vertices]
                )
    if not cubierta.any():
        return 255
    return int(alfa[cubierta].min())


def _vueltas(coordenadas):
    """Los desplazamientos enteros que hacen caber un triangulo dentro de [0,1].

    Las mallas que se salen de [0,1] se dibujan con GL_REPEAT, asi que hay que
    rasterizar una vez por vuelta. Sin esto, un triangulo que va de -3.65 a 0.5
    solo se miraria en su tramo final y la malla se clasificaria por una parte
    suelta del atlas.

    Una malla que da vueltas y vueltas sobre el atlas llega a pedir miles de
    rasterizados por triangulo. Para esos casos se mira la textura entera: es lo
    mismo que preguntarse 'pisa esta malla algo transparente', y para una malla
    que la recorre entera la respuesta ya es que si.
    """
    primero = int(math.floor(min(coordenadas)))
    ultimo = int(math.floor(max(coordenadas)))
    if ultimo - primero + 1 > VUELTAS_MAXIMAS:
        return None
    return range(primero, ultimo + 1)


def _rasterizar(cubierta, alfa, triangulo):
    """Marca en `cubierta` los pixeles de la textura que cubre el triangulo."""
    (u0, v0), (u1, v1), (u2, v2) = triangulo
    alto, ancho = alfa.shape
    x0 = max(0, int(math.floor(min(u0, u1, u2) * ancho)))
    x1 = min(ancho, int(math.ceil(max(u0, u1, u2) * ancho)))
    y0 = max(0, int(math.floor(min(v0, v1, v2) * alto)))
    y1 = min(alto, int(math.ceil(max(v0, v1, v2) * alto)))
    if x1 <= x0 or y1 <= y0:
        return
    # se resuelve el peso de cada vertice del triangulo en el pixel, que es la
    # inversa 2x2 del mapa de UV; con coordenadas casi iguales el determinante se
    # acerca a cero y el triangulo es degenerado
    du1, dv1 = (u1 - u0) * ancho, (v1 - v0) * alto
    du2, dv2 = (u2 - u0) * ancho, (v2 - v0) * alto
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-9:
        return
    px = np.arange(x0, x1) + 0.5
    py = np.arange(y0, y1) + 0.5
    # se indexa como [fila, columna] = [y, x], que es como se corta la textura
    ox = (px - u0 * ancho)[None, :]
    oy = (py - v0 * alto)[:, None]
    s = (ox * dv2 - oy * du2) / det
    t = (du1 * oy - dv1 * ox) / det
    dentro = (s >= -1e-6) & (t >= -1e-6) & (s + t <= 1 + 1e-6)
    cubierta[y0:y1, x0:x1] |= dentro


# El pase de mezcla dibuja despues de lo opaco, apaga la escritura de profundidad
# y ordena cada malla solo por su profundidad media. Un prop opaco que entra ahi se
# ve recortado aunque su textura sea opaca: las piezas se transparentan unas a
# otras en vez de taparse. El panel de botones del microondas se perdia entero.
#
# Hay tres grupos en el renderizador, y el problema no era como decidir "translucido
# o no", sino que para los props solo habia dos. Una textura casi opaca no es
# translucida, pero tampoco es opaca: conserva un canal alfa con algun pixel bajo.
# Esa banda va al grupo intermedio, el de recorte (MASK, prueba de alfa con la
# escritura de profundidad puesta). No descarta nada, porque por encima del corte
# todos los pixeles pasan el filtro de 0.5, pero se ocluye bien.
#
# El corte sale del catalogo: el alfa minimo cae por debajo de 176 en las texturas
# de recorte de verdad y se queda a partir de 192 en las que solo tienen el alfa
# redondeado a la baja. Entre 176 y 191 no hay ninguna pieza.
ALFA_CORTE = 190

# Cuantas vueltas de la textura se rasterizan como mucho por triangulo. Pasado
# ese punto se responde mirando la textura entera: son mallas que dan vueltas y
# vueltas sobre el atlas y preguntarles pixel a pixel costaba mas que el resto
# del arranque junto.
VUELTAS_MAXIMAS = 4

_ALFA_CACHE = {}

# Piezas a las que el autodetectado no les acierta y se les fuerza el grupo.
#
# Aqui solo entra lo que se ha reportado y comprobado a ojo. El criterio general
# es el de `alfa_tocado`, que decide pieza por pieza; esto es la excepcion para
# los casos en que el dato de la textura no refleja lo que se ve.
#
# La clave es la ruta interna del prop y el valor el grupo que se quiere:
#   "OPAQUE"  opaca del todo, sin mezcla ni recorte de alfa
#   "MASK"    recorte de alfa: descarta lo que baje de 0.5 y escribe profundidad
#   "BLEND"   mezcla, que apaga la escritura de profundidad
# Con "*" se aplica a todas las piezas del prop en vez de a una sola.
#
# El timestopper son 24 piezas superpuestas con la misma textura de recorte y
# todas en el pase de mezcla, que apaga la escritura de profundidad: se ven unas
# a traves de otras. Su pieza principal pisa de verdad la parte transparente del
# atlas, asi que el autodetectado la deja mezclada y hay que decirselo a mano.
MODO_PIEZAS = {
    "objects/accessories_perks/fx_timestopper": {"pieza_2": "OPAQUE"},
    "objects/accessories_perks/fx_cologne": {"pieza_2": "OPAQUE"},
    "objects/accessories_perks/fx_timestopper_anim_open": {"*": "MASK"},
    "objects/accessories_perks/fx_truthserum": {"*": "OPAQUE"},
    "objects/appliances/fx_appliancefoodprocessor": {
        "pieza_6": "OPAQUE",
        "pieza_7": "OPAQUE",
        "pieza_8": "BLEND",
    },
    "objects/aspirations/moneytree/body/moneytree": {"pieza_1": "MASK"},
    # hay dos props distintos con el mismo nombre de archivo, uno en la carpeta
    # del oficio y otro en la de plantas. Los dos necesitan la pieza 7 a recorte
    "objects/aspirations/moneytree/fx_money_tree": {"pieza_7": "MASK"},
    "objects/plants/fx_money_tree": {"pieza_7": "MASK"},
    # el kine y su version colgante son el mismo mueble con distinto soporte
    "objects/cutsceneobjects/fx_lamp_kine": {"pieza_3": "OPAQUE"},
    "objects/cutsceneobjects/fx_lamp_swinging": {"pieza_3": "OPAQUE"},
}


def opaca(png):
    """Opaque color copy for explicitly identified non-opacity alpha maps."""
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    r, g, b, _ = image.split()
    salida = io.BytesIO()
    Image.merge("RGBA", (r, g, b, Image.new("L", image.size, 255))).save(
        salida, format="PNG"
    )
    return salida.getvalue()


def surface_textures(data, records, opaco=False):
    """Name textures by their internal bitmap paths, disambiguating variants."""
    used = {}
    for index, (tag, off, size, _, _) in enumerate(records):
        if tag != b"surf":
            continue
        try:
            png, source = read_surface(data, off, size)
        except (ValueError, struct.error):
            png, source = None, ""
        texture = None
        if png:
            png = opaca(png) if opaco else png
            name = source or f"unnamed_surface_{index}"
            if name in used and used[name] != png:
                stem, dot, suffix = name.rpartition(".")
                digest = hashlib.sha256(png).hexdigest()[:12]
                name = f"{stem}__{digest}.{suffix}" if dot else f"{name}__{digest}"
            used[name] = png
            texture = (name, png)
        yield off, texture, source


def prop_materials(data, records, opaco=False):
    surfaces, sources, shaders = {}, {}, {}
    headers = _header_sizes(data)
    for off, texture, source in surface_textures(data, records, opaco):
        for header in headers:
            surfaces[off + header] = texture
            sources[off + header] = source
    for index, (tag, off, size, _, _) in enumerate(records):
        if tag != b"gshd":
            continue
        # Untextured shaders have no SURF pointer to identify their layout.
        # Use the container header instead of interpreting the preceding bytes.
        head = off + headers[0]
        texture, surface_address = None, None
        for header in headers:
            at = off + header + 0x64
            if at + 4 > min(len(data), off + headers[0] + size):
                continue
            target = at + struct.unpack_from("<i", data, at)[0]
            if target in surfaces:
                head = off + header
                texture, surface_address = surfaces[target], target
                break
        rgb = struct.unpack_from("<3f", data, head + 8)
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in rgb):
            rgb = (1, 1, 1)
        opacity = struct.unpack_from("<f", data, head + 0x40)[0]
        if not math.isfinite(opacity) or not -1e-6 <= opacity <= 1 + 1e-6:
            raise ValueError("Invalid prop material opacity")
        opacity = min(1.0, max(0.0, opacity))
        flags = struct.unpack_from("<I", data, head + 4)[0]
        layer_count = struct.unpack_from("<I", data, head + 0x5C)[0]
        layers = []
        if 0 < layer_count <= 8:
            for layer_index in range(layer_count):
                layer = head + 0x60 + layer_index * 0x78
                if layer + 0x78 > min(len(data), off + headers[0] + size):
                    break
                target = layer + 4 + struct.unpack_from("<i", data, layer + 4)[0]
                layers.append(
                    {
                        "texture": surfaces.get(target),
                        "source": sources.get(target, ""),
                        "mode": struct.unpack_from("<I", data, layer + 0x10)[0],
                        "parameter": struct.unpack_from("<f", data, layer + 0x18)[0],
                    }
                )
        material = {
            "texture": texture,
            "surface_address": surface_address,
            "base_color": (*rgb, opacity),
            "shader_index": index,
            "source_texture_path": sources.get(surface_address, ""),
            "texture_layers": layers,
            # 0x1c is the two-diffuse-layer technique, unlike reflection layers.
            "texture_blend": flags & 0x1C == 0x1C
            and len(layers) == 2
            and all(layer["texture"] for layer in layers),
        }
        for header in headers:
            shaders[off + header] = material
    return shaders


def assign_material(part, material, uvs, modo=None):
    """Store one material per mesh part; repeating UVs stay repeating on the GPU."""
    material = material or {}
    part["texture"] = material.get("texture")
    part["texture_layers"] = material.get("texture_layers", [])
    part["texture_blend"] = material.get("texture_blend", False)
    if part["texture_blend"]:
        # Alpha selects between diffuse layers; it does not cut holes in terrain.
        part["blend_weights"] = part.get("blend_weights") or [
            v[3][3] if "palette" not in part and v[3] is not None else 1.0
            for v in part["vertices"]
        ]
    part["source_texture_path"] = material.get("source_texture_path", "")
    part["base_color"] = material.get("base_color", (1, 1, 1, 1))
    part["material_kind"] = "texture" if part["texture"] else "solid_color"
    # tres grupos: opaco, recorte (prueba de alfa, escribe profundidad) y mezcla
    # (sin escritura de profundidad, para el cristal y el agua de verdad)
    if modo:
        # marcado a mano en MODO_PIEZAS. Hay que decirlo explicito y no solo
        # quitar la clave, porque si el material no dice nada el renderizador
        # cae en mezcla por el simple hecho de tener la textura con alfa
        part["alpha_mode"] = modo
    elif part["base_color"][3] < 1:
        # el shader dice que es translucido: se respeta
        part["alpha_mode"] = "BLEND"
    elif part["texture"] is None or any(uv is None for uv in uvs):
        part["alpha_mode"] = None if part["texture"] is None else "BLEND"
    else:
        # el grupo sale del alfa que pisa la malla, no del de la textura entera
        coverage_texture = (
            part["texture_layers"][1]["texture"]
            if part["texture_blend"]
            else part["texture"]
        )
        if int(_canal_alfa(coverage_texture[1]).min()) == 255:
            # Atajo: si la textura entera es opaca, la malla solo puede pisar
            # pixeles opacos. Es el caso mas comun y no hace falta rasterizar
            # nada, que es justo lo que costaba: casi 5 s de los 10 del arranque.
            alfa = 255
        else:
            alfa = alfa_tocado(coverage_texture[1], uvs, part.get("indices"))
        part["alfa_tocado"] = alfa
        if alfa < ALFA_CORTE:
            part["alpha_mode"] = "BLEND"
        elif alfa < 255:
            # casi opaca: al recorte, para que conserve los bordes suaves sin
            # apagarse la escritura de profundidad
            part["alpha_mode"] = "MASK"
        else:
            part["alpha_mode"] = "OPAQUE"
    part["color"] = tuple(round(v * 255) for v in part["base_color"])
    part["alpha_mode_without_vertex_colors"] = part["alpha_mode"]
    vertex_mode = part.get("vertex_alpha_mode")
    if vertex_mode and not modo and not part["texture_blend"]:
        if vertex_mode == "BLEND" or part["alpha_mode"] in (None, "OPAQUE"):
            part["alpha_mode"] = vertex_mode
    part["repeat_texture"] = any(
        uv is not None and any(v < -0.001 or v > 1.001 for v in uv)
        for uv in (*uvs, *part.get("secondary_uvs", []))
    )
    if material.get("surface_address") is not None and part["texture"] is None:
        part["texture_warning"] = "Referenced SURF could not be decoded"
    if not material:
        part["texture_warning"] = "Mesh shader could not be resolved"
