"""Read Sims 2 PSP rigid (RDMS) prop meshes and export UV / Vertex Color GLB."""

import argparse
import csv
import io
import json
import math
import re
import struct
import zipfile
from pathlib import Path

from PIL import Image
import numpy as np

from ts2psp.formats.characters import (
    GLB,
    parse_bones,
    parse_mesh,
    triangle_strip,
    section,
)
from ts2psp.core.coordinates import convert_vector3
from ts2psp.core.names import part_label
from ts2psp.exporting.texture_blend import bake_texture_blend
from ts2psp.formats.materials import (
    MODO_PIEZAS,
    assign_material,
    prop_materials,
)

# Solid doors use texture alpha for a surface effect rather than visibility.
# Keep this explicit until more shader techniques have been decoded. The PSP
# BC3 block layout is already correct; other textures must retain their alpha.
ALFA_ILEGIBLE = frozenset(
    {
        "objects/doors/fx_d47mechdoor",
        "objects/doors/fx_elevatordoors",
    }
)
from ts2psp.formats.prop_scene import (
    node_hierarchy,
    rigid_placements,
    rigid_placement_details,
    RigidPlacement,
    _header_sizes,
    transform_vertices,
    transform_pivot,
    transform_values,
    transform_matrix,
    is_transform,
    mesh_is_helper,
    node_name,
    rest_values,
)


def sections(data):
    if not data.startswith(b"res\n") or len(data) < 32:
        raise ValueError("No es un recurso RES")
    footer = struct.unpack_from("<I", data, 0x14)[0]
    if footer + 8 > len(data):
        raise ValueError("Section index out of range")
    count = struct.unpack_from("<I", data, footer)[0]
    if count > 10000 or footer + 8 + count * 20 > len(data):
        raise ValueError("Invalid section index")
    return [
        struct.unpack_from("<4s4I", data, footer + 8 + 20 * i) for i in range(count)
    ]


NODO_NOMBRE = re.compile(rb"__[A-Za-z][A-Za-z0-9_]{1,29}__\x00")


def prop_node_names(data):
    """Nombres de los nodos del prop, leidos de la seccion `strg`.

    Los props no guardan el nombre dentro del registro del nodo (como hacen los
    personajes): van aparte, en `strg`, junto a las rutas de textura. Se
    descartan los terminados en `Shape`, que son grupos de malla y no nodos.
    """
    try:
        off, size, _ = section(data, b"strg")
    except ValueError:
        return ()
    found = []
    for match in NODO_NOMBRE.finditer(data, off, off + size):
        name = match.group()[:-1].decode("ascii", "replace")
        if name.endswith("Shape") or name in found:
            continue
        found.append(name)
    return tuple(found)


def prop_node_count(data):
    """Cuantos nodos tiene el prop, o None si no se puede asegurar."""
    skeleton = prop_skeleton(data)
    return len(skeleton) or None


def prop_joints(data, include_helpers=False):
    """Keep mesh ancestors and their full bind transforms, including pivots."""
    if b"bmsh" in data[:128] and b"rdms" not in data[:128]:
        rig = parse_bones(data)
        return (
            tuple(
                {
                    "index": i,
                    "parent": bone["parent"],
                    "name": bone["name"],
                    "world": bone["world"].T,
                    "position": bone["world"][3, :3].copy(),
                    "pivot": np.zeros(3),
                    "node_world": bone["world"].T,
                }
                for i, bone in enumerate(rig)
            ),
            {},
        )
    records = sections(data)
    nodes, world_matrix, _, _ = node_hierarchy(data, records)
    carriers = {
        part["node"]
        for part in parse_rdms(data)[0]
        if part["node"] >= 0 and (include_helpers or not part.get("helper"))
    }
    kept, anchors = set(), {}
    for carrier in sorted(carriers):
        cursor, visited = carrier, set()
        while 0 <= cursor < len(nodes):
            if cursor in visited:
                raise ValueError("Cyclic prop skeleton")
            visited.add(cursor)
            if is_transform(data, nodes[cursor]):
                kept.add(cursor)
                anchors.setdefault(carrier, cursor)
            cursor = nodes[cursor][2]
    bones = []
    bind_cache = {}

    def bind_world(index):
        if index not in bind_cache:
            node = nodes[index]
            local = node[3]
            if is_transform(data, node):
                values = rest_values(data, node)
                # Bind at unit scale. Tiny or zero effect scales otherwise lose
                # float32 vertex precision when an animation expands the mesh.
                values[7:10] = [1.0, 1.0, 1.0]
                local = transform_matrix(data, node, values)
            parent = node[2]
            bind_cache[index] = (
                bind_world(parent) @ local if 0 <= parent < len(nodes) else local
            )
        return bind_cache[index]

    for index in sorted(kept):
        parent = nodes[index][2]
        while 0 <= parent < len(nodes) and parent not in kept:
            parent = nodes[parent][2]
        pivot = transform_pivot(data, nodes[index])
        offset = np.eye(4)
        offset[:3, 3] = pivot
        node_world = bind_world(index)
        world = node_world @ offset
        name_at = nodes[index][0] + 4
        name_at += struct.unpack_from("<i", data, name_at)[0]
        name = (
            data[name_at : name_at + 256].split(b"\0", 1)[0].decode("ascii", "replace")
        )
        bones.append(
            {
                "index": index,
                "parent": parent if parent in kept else -1,
                "name": name or f"node_{index}",
                "world": world,
                "position": (world_matrix(index) @ offset)[:3, 3].copy(),
                "pivot": pivot,
                "node_world": node_world,
            }
        )
    order = {bone["index"]: index for index, bone in enumerate(bones)}
    return tuple(bones), {carrier: order[index] for carrier, index in anchors.items()}


def prop_skeleton(data):
    return prop_joints(data)[0]


def prop_bones(data):
    """Lo que se puede saber de los huesos de un prop.

    Devuelve el recuento y los nombres por separado porque no siempre vienen
    los dos: hay props con el grafo de nodos pero sin ningun nombre guardado.

    Si salen mas nombres que nodos es que el recuento no era real (la firma
    puede dar 1 en un mueble grande), asi que se descarta: mas vale no decir
    nada que decir que hay un solo hueso.
    """
    count = prop_node_count(data)
    names = prop_node_names(data)
    if count is not None and len(names) > count:
        return None, names
    return count, names


def _color5(value):
    value &= 31
    return ((value << 3) | (value >> 2)) / 255.0


def _color6(value):
    value &= 63
    return ((value << 2) | (value >> 4)) / 255.0


def decode_color(raw, code):
    """PSP color channels expand to bytes by repeating the high bits."""
    if code in (4, 5, 6):
        value = int.from_bytes(raw, "little")
        if code == 4:  # PSP RGB565
            return (
                _color5(value),
                _color6(value >> 5),
                _color5(value >> 11),
                1.0,
            )
        if code == 5:  # PSP RGBA5551
            return (
                _color5(value),
                _color5(value >> 5),
                _color5(value >> 10),
                (value >> 15) & 1,
            )
        return (
            (value & 15) / 15,
            ((value >> 4) & 15) / 15,
            ((value >> 8) & 15) / 15,
            ((value >> 12) & 15) / 15,
        )
    if code == 7:  # PSP RGBA8888
        return tuple(v / 255 for v in raw)
    raise ValueError(f"Formato de Vertex Color {code} no soportado")


def vertex_layout(vtype):
    tex_code = vtype & 3
    color_code = (vtype >> 2) & 7
    normal_code = (vtype >> 5) & 3
    pos_code = (vtype >> 7) & 3
    tex_size = {0: 0, 1: 2, 2: 4, 3: 8}[tex_code]
    color_size = {0: 0, 4: 2, 5: 2, 6: 2, 7: 4}.get(color_code)
    normal_size = {0: 0, 1: 3, 2: 6, 3: 12}[normal_code]
    pos_size = {1: 3, 2: 6, 3: 12}.get(pos_code)
    if color_size is None or pos_size is None:
        raise ValueError("VTYPE no soportado")
    color_align = 4 if color_code == 7 else 2 if color_code else 1
    color_at = (tex_size + color_align - 1) // color_align * color_align
    normal_align = {0: 1, 1: 1, 2: 2, 3: 4}[normal_code]
    normal_at = (
        (color_at + color_size + normal_align - 1) // normal_align * normal_align
    )
    cursor = normal_at + normal_size
    align = {1: 1, 2: 2, 3: 4}[pos_code]
    pos_at = (cursor + align - 1) // align * align
    vertex_align = max(
        align,
        {0: 1, 1: 1, 2: 2, 3: 4}[tex_code],
        4 if color_code == 7 else 2 if color_code else 1,
        {0: 1, 1: 1, 2: 2, 3: 4}[normal_code],
    )
    stride = (pos_at + pos_size + vertex_align - 1) // vertex_align * vertex_align
    return tex_code, color_code, normal_code, pos_code, pos_at, stride


def rdms_indices(
    data, index_at, index_count, vertex_count, index_width, end, vertex_at=None
):
    """Use the allocated index span to detect incorrectly advertised U8 data.

    Fallen bookcases store U16 indices despite their U8 VTYPE. An actual U8
    index can reach 255; unused vertices alone do not justify changing width.
    """

    def leer(ancho):
        total = index_count * ancho
        if total > end - index_at:
            return None
        fmt = "<" + ("B" if ancho == 1 else "H") * index_count
        return struct.unpack_from(fmt, data, index_at)

    indices = leer(index_width)
    if indices is None or max(indices) >= vertex_count:
        return None
    if (
        index_width == 1
        and vertex_count > 128
        and index_count > 16
        and vertex_at is not None
        and 0 <= vertex_at - index_at - 2 * index_count < 16
    ):
        otro = leer(2)
        if otro is not None and max(otro) < vertex_count:
            indices = otro
    return indices


# Piezas a las que hay que dar la vuelta a las caras.
#
# El visor de mapas recorta las caras traseras, y una malla con los triangulos al
# reves no se ve desde el lado bueno. El volumen con signo las delata, pero solo
# cuando la malla es cerrada: en un plano no dice nada, porque el signo depende de
# lo lejos que este el origen y no de hacia donde mire la cara. Las de plano se
# anotan una a una segun se ven, como las demas excepciones del visor.
CARAS_AL_REVES = {
    # Suelo de madera de la sala de Crear un Sim: es un plano en Z constante
    # con los triangulos mirando hacia -Z; al ser plano, el volumen con signo
    # no permite detectar el sentido y se corrige por pieza.
    "levels/cas": {"pieza_221"},
    # Un cartel de 10 x 10 m con alfa, suelto en el cielo: en el cementerio es el
    # pieza_581 y en el hub el pieza_588, la misma malla duplicada en los dos
    # niveles. Es un plano de dos triangulos mirando a +y, y el volumen con signo
    # no dice nada de un plano: el signo depende de lo lejos que este el origen.
    "levels/deadtree/graveyard": {"pieza_581"},
    "levels/deadtree/hub": {"pieza_588"},
    # La cupula interior del monumento: 22 vertices, 475 x 452 m de nubes. Los
    # 20 triangulos miran hacia FUERA, y una cupula se ve desde dentro, con lo
    # que al recortar las caras traseras el cielo salia vacio.
    "levels/the_monument": {"pieza_96"},
}


def _dar_la_vuelta(triangulos):
    """Cada triangulo con sus dos ultimos vertices cambiados de sitio."""
    salida = list(triangulos)
    for k in range(0, len(salida) - 2, 3):
        salida[k + 1], salida[k + 2] = salida[k + 2], salida[k + 1]
    return salida


def parse_rdms(data, apply_scene=True, opaco=False, ruta=None, vertex_colors=True):
    """Las piezas de un rdms.

    Con `vertex_colors=False` se descartan los colores de vertice. En un nivel
    multiplican el color del material y lo tiñen: muchos materiales llegan con
    un gris a media luz y el mobiliario sale apagado. El visor de mapas los quita.
    """
    output = []
    rejected = []
    records = sections(data)
    materials = prop_materials(data, records, opaco=opaco)
    placements = rigid_placement_details(data, records) if apply_scene else {}
    has_nodes = any(row[0] == b"node" for row in records) if apply_scene else False
    for i, (tag, off, size, _, _) in enumerate(records):
        if tag != b"rdms":
            continue
        shift = next(
            (
                extra
                for extra in (header - 128 for header in _header_sizes(data))
                if off + 0xC4 + extra <= len(data)
                and struct.unpack_from("<I", data, off + 0xB8 + extra)[0] >> 24 == 0x12
            ),
            None,
        )
        if shift is None:
            rejected.append((i, "VTYPE no soportado"))
            continue
        # A few resources omit one or two padding bytes from the declared size.
        end = off + 128 + shift + size + 4
        if off + 0xC4 + shift > len(data) or end > len(data):
            rejected.append((i, "section bounds"))
            continue
        index_count = struct.unpack_from("<I", data, off + 0x88 + shift)[0]
        vertex_count = struct.unpack_from("<I", data, off + 0x90 + shift)[0]
        index_at = (
            off + 0x8C + shift + struct.unpack_from("<I", data, off + 0x8C + shift)[0]
        )
        vertex_at = (
            off + 0x98 + shift + struct.unpack_from("<I", data, off + 0x98 + shift)[0]
        )
        vtype = struct.unpack_from("<I", data, off + 0xB8 + shift)[0]
        stride = struct.unpack_from("<I", data, off + 0xC0 + shift)[0]
        try:
            tex_code, color_code, normal_code, pos_code, pos_at, expected = (
                vertex_layout(vtype)
            )
        except ValueError as exc:
            rejected.append((i, str(exc)))
            continue
        index_code = (vtype >> 11) & 3
        index_width = {1: 1, 2: 2}.get(index_code)
        if (
            not 2 < index_count < 1000000
            or not 2 < vertex_count < 1000000
            or index_width is None
            or stride != expected
            or index_at + index_count * index_width > end
            or vertex_at + vertex_count * stride > end
        ):
            rejected.append(
                (i, f"layout vt={vtype:#x} stride={stride} expected={expected}")
            )
            continue
        indices = rdms_indices(
            data,
            index_at,
            index_count,
            vertex_count,
            index_width,
            end,
            vertex_at=vertex_at,
        )
        if indices is None:
            rejected.append((i, "index range"))
            continue
        vertices = []
        source_alphas = []
        head = off + 128 + shift
        origin = struct.unpack_from("<3f", data, head + 0x1C)
        position_scale = struct.unpack_from("<f", data, head + 0x28)[0]
        uv_offset = struct.unpack_from("<2f", data, head + 0x44)
        uv_scale = struct.unpack_from("<f", data, head + 0x4C)[0]
        for j in range(vertex_count):
            raw = data[vertex_at + j * stride : vertex_at + (j + 1) * stride]
            cursor = 0
            if tex_code == 0:
                uv = None
            elif tex_code == 1:
                uv = (raw[0] / 128.0, raw[1] / 128.0)
                cursor = 2
            elif tex_code == 2:
                u, v = struct.unpack_from("<HH", raw)
                uv = (u / 32768.0, v / 32768.0)
                cursor = 4
            else:
                uv = struct.unpack_from("<ff", raw)
                cursor = 8
            if uv is not None:
                uv = (
                    uv_offset[0] + uv[0] * uv_scale,
                    1 - (uv_offset[1] + uv[1] * uv_scale),
                )
            color = None
            if color_code:
                width = 4 if color_code == 7 else 2
                cursor = (cursor + width - 1) // width * width
                color = decode_color(raw[cursor : cursor + width], color_code)
                cursor += width
            normal_align = {0: 1, 1: 1, 2: 2, 3: 4}[normal_code]
            cursor = (cursor + normal_align - 1) // normal_align * normal_align
            if normal_code == 1:
                normal = struct.unpack_from("<bbb", raw, cursor)
                length = max(1.0, math.sqrt(sum(x * x for x in normal)))
                normal = tuple(x / length for x in normal)
            elif normal_code == 2:
                normal = struct.unpack_from("<hhh", raw, cursor)
                length = max(1.0, math.sqrt(sum(x * x for x in normal)))
                normal = tuple(x / length for x in normal)
            elif normal_code == 3:
                normal = struct.unpack_from("<fff", raw, cursor)
            else:
                normal = None
            if pos_code == 1:
                pos = tuple(v / 128.0 for v in struct.unpack_from("<bbb", raw, pos_at))
            elif pos_code == 2:
                pos = tuple(
                    v / 32768.0 for v in struct.unpack_from("<hhh", raw, pos_at)
                )
            else:
                pos = struct.unpack_from("<fff", raw, pos_at)
            pos = tuple(origin[k] + pos[k] * position_scale for k in range(3))
            source_alphas.append(color[3] if color is not None else 1.0)
            vertices.append((pos, normal, uv, color if vertex_colors else None))
        triangles = triangle_strip(indices)
        secondary_uvs = []
        secondary_offset = struct.unpack_from("<I", data, head + 0x54)[0]
        secondary_at = vertex_at + secondary_offset
        if secondary_offset:
            second_type = struct.unpack_from("<I", data, head + 0x50)[0]
            second_stride = struct.unpack_from("<I", data, head + 0x58)[0]
            second_code = second_type & 3
            if (
                second_code
                and second_stride == vertex_layout(second_type)[-1]
                and secondary_at + vertex_count * second_stride <= end
            ):
                u0, v0, scale = struct.unpack_from("<3f", data, head + 0x5C)
                for j in range(vertex_count):
                    at = secondary_at + j * second_stride
                    if second_code == 1:
                        u, v = data[at] / 128.0, data[at + 1] / 128.0
                    elif second_code == 2:
                        u, v = (
                            value / 32768.0
                            for value in struct.unpack_from("<2H", data, at)
                        )
                    else:
                        u, v = struct.unpack_from("<2f", data, at)
                    secondary_uvs.append((u0 + u * scale, 1 - (v0 + v * scale)))
        if f"pieza_{i}" in CARAS_AL_REVES.get(ruta or "", ()):
            triangles = _dar_la_vuelta(triangles)
        if triangles:
            part = {
                "name": f"pieza_{i}",
                "vertices": vertices,
                "indices": triangles,
                "vtype": vtype,
                "secondary_uvs": secondary_uvs,
                "blend_weights": source_alphas,
                "secondary_uv_source": (
                    "stream"
                    if secondary_uvs
                    else ("shared" if not secondary_offset else "unreadable")
                ),
            }
            alphas = [v[3][3] for v in vertices if v[3] is not None]
            if any(a < 1 for a in alphas):
                part["vertex_alpha_mode"] = (
                    "BLEND" if any(0 < a < 1 for a in alphas) else "MASK"
                )
            shader_at = off + 128 + shift
            shader = shader_at + struct.unpack_from("<i", data, shader_at)[0]
            forzado = MODO_PIEZAS.get(ruta or "", {})
            assign_material(
                part,
                materials.get(shader),
                [v[2] for v in vertices],
                modo=forzado.get(f"pieza_{i}", forzado.get("*")),
            )
            if part["texture_blend"] and not secondary_offset:
                part["secondary_uvs"] = [v[2] for v in vertices]
            anclajes = placements.get(off + 128, [])
            if off + 128 in placements and not anclajes:
                continue
            if not anclajes:
                anclajes = [RigidPlacement(-1, np.eye(4), [])]
            for instance, placement in enumerate(anclajes):
                node_index, matrix, nodes = (
                    placement.node,
                    placement.matrix,
                    placement.nodes,
                )
                name = (
                    part["name"]
                    if instance == 0
                    else f'{part["name"]}_instance_{instance}'
                )
                trans_verts = transform_vertices(vertices, matrix)
                output.append(
                    {
                        **part,
                        "name": name,
                        "node": node_index,
                        "node_name": (
                            node_name(data, nodes[node_index])
                            if 0 <= node_index < len(nodes)
                            else ""
                        ),
                        "helper": mesh_is_helper(
                            data, nodes, node_index, textured=bool(part["texture"])
                        )
                        or (has_nodes and node_index < 0),
                        "resource_path": placement.resource,
                        "placement_node": placement.instance,
                        "local_vertices": vertices,
                        "vertices": trans_verts,
                        # RDMS compression origin, before NODE placement.
                        "origin": tuple(float(v) for v in origin),
                    }
                )
    return output, rejected


def parse_prop_bmsh(data, ruta=None):
    """Keep BMSH skin weights and bind each part to its own shader.

    Los vertices se dejan con los UV EN CRUDO a proposito: cada consumidor los
    convierte una vez, `load_parts` para la vista previa y `GLB.add_mesh` para
    el export. Si se normalizasen aqui, el export los pasaria por la conversion
    dos veces y los UV salirian a la mitad.
    """
    parts = parse_mesh(data)
    # GU texture coordinates are unsigned shorts, biased around 0x8000.
    # Keep the legacy +32768 consumer convention across the signed boundary.
    for part in parts:
        part["vertices"] = [
            (pos, normal, tuple((v & 65535) - 65536 for v in uv), weights)
            for pos, normal, uv, weights in part["vertices"]
        ]
    materials = prop_materials(data, sections(data))
    off, _, _ = section(data, b"bmsh")
    forzado = MODO_PIEZAS.get(ruta or "", {})
    for index, part in enumerate(parts):
        part["name"] = "principal" if index == 0 else f"pieza_{index}"
        shader_at = off + 128 + 0x38 + index * 0x30
        shader = shader_at + struct.unpack_from("<i", data, shader_at)[0]
        uvs = [
            ((v[2][0] + 32768) / 4096.0, 1 - (v[2][1] + 32768) / 4096.0)
            for v in part["vertices"]
        ]
        assign_material(
            part,
            materials.get(shader),
            uvs,
            modo=forzado.get(part["name"], forzado.get("*")),
        )
    return parts


def write_rigid_glb(parts, name, target, bones=(), anclajes=None):
    glb = GLB()
    skin_index = None
    por_nodo = {}
    if bones:
        order = {bone["index"]: index for index, bone in enumerate(bones)}
        rig = [
            {
                "name": bone["name"],
                "parent": order.get(bone["parent"], -1),
                "world": bone["world"].T,
                "ibm": np.linalg.inv(bone["world"]).T,
            }
            for bone in bones
        ]
        skin_index = glb.add_skin(rig)
        # JOINTS_0 stores positions in skin.joints, never document node IDs.
        por_nodo = anclajes if anclajes is not None else order
    for part in parts:
        mesh_name = f"{name}/{part_label(part)}"
        part = bake_texture_blend(part)
        vertices = part["vertices"]
        anchor = por_nodo.get(part.get("node", -1))
        if anchor is not None and "local_vertices" in part:
            vertices = transform_vertices(
                part["local_vertices"], bones[anchor]["node_world"]
            )
        count = len(vertices)
        # El esqueleto sale ya en base glTF (ver add_skin), asi que las mallas
        # tambien: si no, quedan en el Z del PSP y se ven giradas al abrirlo.
        positions = [x for pos, _, _, _ in vertices for x in convert_vector3(pos)]
        attrs = {
            "POSITION": glb.accessor(
                struct.pack("<" + "f" * len(positions), *positions),
                5126,
                "VEC3",
                count,
                34962,
                [min(positions[i::3]) for i in range(3)],
                [max(positions[i::3]) for i in range(3)],
            )
        }
        if all(v[1] is not None for v in vertices):
            vals = [x for _, normal, _, _ in vertices for x in convert_vector3(normal)]
            attrs["NORMAL"] = glb.accessor(
                struct.pack("<" + "f" * len(vals), *vals), 5126, "VEC3", count, 34962
            )
        if all(v[2] is not None for v in vertices):
            vals = [x for _, _, uv, _ in vertices for x in uv]
            attrs["TEXCOORD_0"] = glb.accessor(
                struct.pack("<" + "f" * len(vals), *vals), 5126, "VEC2", count, 34962
            )
        # `COLOR_0` sale en TODAS las mallas, no solo en las que traian color de
        # vertice. De las 9444 piezas visibles de los 21 niveles, 331715 vertices
        # lo tienen y el resto son mallas cuyo VTYPE no declara canal de color
        # (`vtype >> 2 & 7` sale 0 y el `stride` del archivo coincide con el que
        # calcula el decodificador sin color): sus muros, su suelo y buena parte
        # del mobiliario. Para esas se escribe el color del material, que es el
        # tinte que el visor les aplica igual, de modo que el GLB lleva vertex
        # color en todas partes y lo que se abre en Blender se ve como el visor.
        color_material = tuple(
            c / 255.0 for c in (part.get("color") or (255, 255, 255, 255))
        )
        vals = []
        for pos, normal, uv, color in vertices:
            if color is not None:
                c = tuple(color)
            else:
                c = color_material
            vals.extend(c)
        attrs["COLOR_0"] = glb.accessor(
            struct.pack("<" + "f" * len(vals), *vals), 5126, "VEC4", count, 34962
        )
        for key, field, kind in (
            ("_PSP_UV0", "source_uv0", "VEC2"),
            ("_PSP_UV1", "source_uv1", "VEC2"),
            ("_PSP_BLEND", "source_blend_weights", "SCALAR"),
        ):
            if field in part:
                values = np.asarray(part[field], dtype="<f4")
                attrs[key] = glb.accessor(values.tobytes(), 5126, kind, count, 34962)
        # Piel rigida: cada vertice pegado a peso 1.0 a su articulacion. Sin
        # esto el skin existe pero la malla no esta skineada y al abrir el GLB
        # no sale ningun vertex group.
        articulation = por_nodo.get(part.get("node", -1))
        if skin_index is not None and articulation is not None:
            attrs["JOINTS_0"] = glb.accessor(
                struct.pack("<4H", articulation, 0, 0, 0) * count,
                5123,
                "VEC4",
                count,
                34962,
            )
            attrs["WEIGHTS_0"] = glb.accessor(
                struct.pack("<4f", 1.0, 0.0, 0.0, 0.0) * count,
                5126,
                "VEC4",
                count,
                34962,
            )
        indices = part["indices"]
        component = 5123 if count < 65536 else 5125
        ix = glb.accessor(
            struct.pack(
                "<" + ("H" if component == 5123 else "I") * len(indices), *indices
            ),
            component,
            "SCALAR",
            len(indices),
            34963,
        )
        material_idx = len(glb.doc["materials"])
        pbr = {
            "baseColorFactor": list(part.get("base_color", (1, 1, 1, 1))),
            "metallicFactor": 0,
            "roughnessFactor": 1,
        }
        texture = part.get("texture")
        has_alpha = pbr["baseColorFactor"][3] < 1 or (
            all(v[3] is not None for v in vertices)
            and any(v[3][3] < 0.999 for v in vertices)
        )
        if texture and "TEXCOORD_0" in attrs:
            pbr["baseColorTexture"] = {
                "index": glb.add_texture(
                    texture[0], texture[1], part.get("repeat_texture")
                ),
                "texCoord": 0,
            }
            with Image.open(io.BytesIO(texture[1])) as image:
                has_alpha |= image.convert("RGBA").getchannel("A").getextrema()[0] < 255
        material = {
            "name": mesh_name,
            "doubleSided": True,
            "pbrMetallicRoughness": pbr,
        }
        material["alphaMode"] = part.get("alpha_mode") or (
            "BLEND" if has_alpha else "OPAQUE"
        )
        glb.doc["materials"].append(material)
        mesh_idx = len(glb.doc["meshes"])
        glb.doc["meshes"].append(
            {
                "name": mesh_name,
                "extras": {"sourcePart": part["name"]},
                "primitives": [
                    {
                        "attributes": attrs,
                        "indices": ix,
                        "material": material_idx,
                        "mode": 4,
                    }
                ],
            }
        )
        if part.get("uv_source") or part.get("texture_warning"):
            glb.doc["meshes"][-1]["extras"].update(
                {
                    "uvSource": part.get("uv_source", "source"),
                    "materialNote": part.get("texture_warning", ""),
                }
            )
        if texture:
            glb.doc["meshes"][-1]["extras"]["sourceTexture"] = texture[0]
        if part.get("baked_texture_layers"):
            originals = part["baked_texture_layers"]
            glb.doc["meshes"][-1]["extras"]["textureBlend"] = {
                "sourceTextures": [t[0] for t in originals],
                "textureIndices": [
                    glb.add_texture(t[0], t[1], part.get("source_repeat_texture"))
                    for t in originals
                ],
                "weightAttribute": "_PSP_BLEND",
                "uvAttributes": ["_PSP_UV0", "_PSP_UV1"],
                "atlas": "TEXCOORD_0",
                "repeatSourceTextures": part.get("source_repeat_texture", False),
            }
        node_idx = len(glb.doc["nodes"])
        mesh_node = {"name": mesh_name, "mesh": mesh_idx}
        if part.get("resource_path"):
            mesh_node["extras"] = {
                "sourceResource": part["resource_path"],
                "sourcePlacement": part["placement_node"],
            }
        # Las mallas NO se cuelgan del esqueleto: parse_rdms ya las deja en
        # espacio mundo (les aplica la matriz de su nodo), y colgar-las
        # componia la transformacion local del hueso encima y las desplazaba.
        # Se enganchan con `skin` y los vertex groups, que es lo correcto.
        if "JOINTS_0" in attrs:
            mesh_node["skin"] = skin_index
        glb.doc["nodes"].append(mesh_node)
        glb.doc["scenes"][0]["nodes"].append(node_idx)
    glb.save(target)
    return glb


def convert_prop(
    data,
    name,
    target,
    include_helpers=False,
    assets=None,
    animation=None,
    include_animation=True,
):
    if b"rdms" in data[:128]:
        parts, rejected = parse_rdms(data, opaco=name in ALFA_ILEGIBLE, ruta=name)
        if not include_helpers:
            parts = [part for part in parts if not part.get("helper")]
        if not parts:
            raise ValueError(f"No compatible RDMS parts: {rejected[:3]}")
        if assets:
            assets.repair(parts, name)
        bones, anclajes = prop_joints(data, include_helpers=include_helpers)
        glb = write_rigid_glb(parts, name, target, bones=bones, anclajes=anclajes)
        from ts2psp.exporting.prop_animation import add_prop_animation

        animation_clip = None
        animation_bindings = None
        if include_animation and animation and assets:
            animation_clip = next(
                (
                    clip
                    for clip in assets.clips(name, data)
                    if clip["name"] == animation and clip.get("kind") == "node"
                ),
                None,
            )
            if animation_clip:
                animation_bindings = animation_clip["bindings"]
        animations = (
            add_prop_animation(
                glb,
                data,
                bones,
                animation or name,
                clip=animation_clip,
                bindings=animation_bindings,
            )
            if include_animation
            else 0
        )
        glb.save(target)
        return {
            "name": name,
            "format": "RDMS",
            "bones": len(bones),
            "animations": animations,
            "parts": len(parts),
            "vertices": sum(len(x["vertices"]) for x in parts),
            "triangles": sum(len(x["indices"]) // 3 for x in parts),
            "color_parts": sum(
                any(v[3] is not None for v in x["vertices"]) for x in parts
            ),
            "uv_parts": sum(
                any(v[2] is not None for v in x["vertices"]) for x in parts
            ),
            "textured_parts": sum(
                bool(x.get("texture")) and x["vertices"][0][2] is not None
                for x in parts
            ),
            "skipped_parts": rejected,
        }
    if b"bmsh" in data[:128]:
        parts = parse_prop_bmsh(data, ruta=name)
        bones = parse_bones(data)
        glb = GLB()
        skin = glb.add_skin(bones)
        textures = {part["name"]: part["texture"] for part in parts}
        glb.add_mesh(name, parts, skin, len(bones), textures)
        glb.save(target)
        clips = assets.clips(name) if assets and include_animation else []
        if clips:
            from ts2psp.formats.animations import attach_animations

            reference = next(
                (c for c in clips if c["name"].endswith("idle-breathe")), clips[0]
            )
            selected = (
                [c for c in clips if c["name"] == animation] if animation else clips
            )
            attach_animations(target, selected, reference, target)
        return {
            "name": name,
            "format": "BMSH",
            "bones": len(bones),
            "animations": len(selected) if clips else 0,
            "parts": len(parts),
            "vertices": sum(len(p["vertices"]) for p in parts),
            "triangles": sum(len(p["indices"]) // 3 for p in parts),
            "color_parts": 0,
            "uv_parts": len(parts),
            "textured_parts": sum(bool(p["texture"]) for p in parts),
            "skipped_parts": [],
        }
    raise ValueError("Resource has no RDMS/BMSH mesh")


def main():
    from ts2psp.formats.prop_assets import PropAssets

    ap = argparse.ArgumentParser(description="Extrae props de Los Sims 2 PSP a GLB")
    ap.add_argument("archive", type=Path)
    ap.add_argument("entry", help="ID del inventario_props.csv")
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    with zipfile.ZipFile(args.archive) as z:
        rows = list(
            csv.DictReader(
                io.StringIO(z.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        row = next((r for r in rows if r["entry"] == args.entry), None)
        if row is None:
            raise ValueError("ID no encontrado")
        result = convert_prop(
            z.read(row["archivo_zip"]),
            row["ruta_interna"],
            args.output,
            assets=PropAssets(z),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
