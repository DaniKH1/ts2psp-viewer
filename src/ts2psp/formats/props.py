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
from ts2psp.formats.materials import assign_material, prop_materials
from ts2psp.formats.prop_scene import (
    node_hierarchy,
    rigid_placements,
    transform_vertices,
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


def prop_joints(data):
    """Esqueleto del prop y que hueso corresponde a cada malla.

    Devuelve (huesos, anclajes). `huesos` es una lista de dicts con `index` y
    `parent` en indices originales del recurso, y `anclajes` mapea el indice de
    nodo original de cada malla al hueso del que cuelga.

    El grafo crudo trae muchas mas ramas de las que interesan: el juego encadena
    localizadores y sus hijos en el mismo punto. Aqui se colapsan las que
    coinciden con su padre y se podan las hojas que no llevan malla, que en un
    esqueleto para editar no aportan nada.
    """
    try:
        records = sections(data)
    except ValueError:
        return (), {}
    try:
        nodes, world_matrix, _, _ = node_hierarchy(data, records)
    except ValueError:
        return (), {}
    crudos = []
    for index, (_, _, parent, _) in enumerate(nodes):
        try:
            world = world_matrix(index)
        except ValueError:
            continue
        crudos.append({
            "index": index,
            "parent": parent if 0 <= parent < index else -1,
            "position": world[:3, 3],
        })
    return _limpia(crudos, _nodos_con_malla(data, records))


def prop_skeleton(data):
    """Solo el esqueleto, sin el mapa de anclajes."""
    return prop_joints(data)[0]


def _nodos_con_malla(data, records):
    """Indices de nodo a los que cuelga alguna malla."""
    con = set()
    try:
        placements = rigid_placements(data, records)
    except ValueError:
        return con
    for anclajes in placements.values():
        con.update(indice for indice, _ in anclajes)
    return con


def _limpia(bones, con_malla, tolerancia=1e-4):
    """Esqueleto minimo: lo justo para colgar las mallas.

    El grafo crudo trae de mas. Un nodo que esta justo encima de su padre no
    aporta nada: en reposo son el mismo punto, asi que sus hijos y su malla se
    cuelgan del padre sin cambio ninguno. Y un nodo que no sostiene ninguna malla
    tampoco hace falta: es un manejador o una ancla de colocacion, que al abrir
    el archivo se ven como huesos vacios colgando de la nada.
    """
    representante = {}
    supervivientes = []
    posiciones = {}
    for bone in bones:
        original = bone["index"]
        padre = bone["parent"]
        if padre >= 0 and np.allclose(
            bone["position"], posiciones.get(padre, bone["position"]),
            atol=tolerancia,
        ):
            representante[original] = representante.get(padre, padre)
            continue
        bone = {**bone, "parent": representante.get(padre, -1)}
        representante[original] = original
        supervivientes.append(bone)
        posiciones[original] = bone["position"]

    # la malla pasa al nodo que se queda, asi que es ese el que hay que conservar
    portadores = {representante.get(nodo, nodo) for nodo in con_malla}
    if portadores:
        # esqueleto minimo: los que llevan malla y los que los sostienen
        padre_de = {bone["index"]: bone["parent"] for bone in supervivientes}
        utiles = set()
        for nodo in portadores:
            while nodo >= 0 and nodo not in utiles:
                utiles.add(nodo)
                nodo = padre_de.get(nodo, -1)
    else:
        # sin mallas no hay nada que colgar, pero el grafo sigue siendo info
        utiles = {bone["index"] for bone in supervivientes}
    filtered = [bone for bone in supervivientes if bone["index"] in utiles]

    puesto = {bone["index"]: i for i, bone in enumerate(filtered)}
    anclajes = {
        nodo: puesto[representante[nodo]]
        for nodo in con_malla
        if representante[nodo] in puesto
    }
    return tuple(filtered), anclajes


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


def decode_color(raw, code):
    if code in (4, 5, 6):
        value = int.from_bytes(raw, "little")
        if code == 4:  # PSP RGB565
            return (
                (value & 31) / 31,
                ((value >> 5) & 63) / 63,
                ((value >> 11) & 31) / 31,
                1.0,
            )
        if code == 5:  # PSP RGBA5551
            return (
                (value & 31) / 31,
                ((value >> 5) & 31) / 31,
                ((value >> 10) & 31) / 31,
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
    cursor = tex_size + color_size + normal_size
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


def parse_rdms(data, apply_scene=True):
    output = []
    rejected = []
    records = sections(data)
    materials = prop_materials(data, records)
    placements = rigid_placements(data, records) if apply_scene else {}
    for i, (tag, off, size, _, _) in enumerate(records):
        if tag != b"rdms":
            continue
        shift = next(
            (
                extra
                for extra in (0, 32)
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
        fmt = "<" + ("B" if index_width == 1 else "H") * index_count
        indices = struct.unpack_from(fmt, data, index_at)
        if max(indices) >= vertex_count:
            rejected.append((i, "index range"))
            continue
        vertices = []
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
                color = decode_color(raw[cursor : cursor + width], color_code)
                cursor += width
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
            vertices.append((pos, normal, uv, color))
        triangles = triangle_strip(indices)
        if triangles:
            part = {
                "name": f"pieza_{i}",
                "vertices": vertices,
                "indices": triangles,
                "vtype": vtype,
            }
            shader_at = off + 128 + shift
            shader = shader_at + struct.unpack_from("<i", data, shader_at)[0]
            assign_material(part, materials.get(shader), [v[2] for v in vertices])
            anclajes = placements.get(off + 128, [])
            if not anclajes:
                anclajes = [(-1, np.eye(4))]
            for instance, (node_index, matrix) in enumerate(anclajes):
                name = (
                    part["name"]
                    if instance == 0
                    else f'{part["name"]}_instance_{instance}'
                )
                output.append(
                    {
                        **part,
                        "name": name,
                        "node": node_index,
                        "vertices": transform_vertices(vertices, matrix),
                    }
                )
    return output, rejected


def parse_prop_bmsh(data):
    """Keep BMSH skin weights and bind each part to its own shader."""
    parts = parse_mesh(data)
    materials = prop_materials(data, sections(data))
    off, _, _ = section(data, b"bmsh")
    for index, part in enumerate(parts):
        part["name"] = "principal" if index == 0 else f"pieza_{index}"
        shader_at = off + 128 + 0x38 + index * 0x30
        shader = shader_at + struct.unpack_from("<i", data, shader_at)[0]
        uvs = [
            ((v[2][0] + 32768) / 4096.0, 1 - (v[2][1] + 32768) / 4096.0)
            for v in part["vertices"]
        ]
        assign_material(part, materials.get(shader), uvs)
    return parts


def write_rigid_glb(parts, name, target, bones=(), anclajes=None):
    glb = GLB()
    skin_index = None
    por_nodo = {}
    if bones:
        # Todo en base glTF: el esqueleto y las mallas tienen que compartirla.
        orden = {bone["index"]: i for i, bone in enumerate(bones)}
        joints = [
            (
                f'{name}/nodo_{bone["index"]}',
                orden.get(bone["parent"], -1),
                convert_vector3(bone["position"]),
            )
            for bone in bones
        ]
        skin_index = glb.add_joint_skeleton(joints)
        skin_joints = glb.doc["skins"][skin_index]["joints"]
        if anclajes is None:
            anclajes = orden
        por_nodo = {
            nodo: skin_joints[posicion]
            for nodo, posicion in anclajes.items()
            if posicion < len(skin_joints)
        }
    for part in parts:
        vertices = part["vertices"]
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
        if all(v[3] is not None for v in vertices):
            vals = [x for _, _, _, color in vertices for x in color]
            attrs["COLOR_0"] = glb.accessor(
                struct.pack("<" + "f" * len(vals), *vals), 5126, "VEC4", count, 34962
            )
        # Piel rigida: cada vertice pegado a peso 1.0 a su articulacion. Sin
        # esto el skin existe pero la malla no esta skineada y al abrir el GLB
        # no sale ningun vertex group.
        articulation = por_nodo.get(part.get("node", -1))
        if skin_index is not None and articulation is not None:
            attrs["JOINTS_0"] = glb.accessor(
                bytes([articulation]) * (4 * count), 5121, "VEC4", count, 34962
            )
            attrs["WEIGHTS_0"] = glb.accessor(
                struct.pack("<4f", 1.0, 0.0, 0.0, 0.0) * count,
                5126, "VEC4", count, 34962,
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
        has_alpha = all(v[3] is not None for v in vertices) and any(
            v[3][3] < 0.999 for v in vertices
        )
        if texture and "TEXCOORD_0" in attrs:
            pbr["baseColorTexture"] = {
                "index": glb.add_texture(texture[0], texture[1]),
                "texCoord": 0,
            }
            with Image.open(io.BytesIO(texture[1])) as image:
                has_alpha |= image.convert("RGBA").getchannel("A").getextrema()[0] < 255
        material = {
            "name": f'{name}/{part["name"]}',
            "doubleSided": True,
            "pbrMetallicRoughness": pbr,
        }
        if has_alpha:
            material["alphaMode"] = "BLEND"
        glb.doc["materials"].append(material)
        mesh_idx = len(glb.doc["meshes"])
        glb.doc["meshes"].append(
            {
                "name": f'{name}/{part["name"]}',
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
        node_idx = len(glb.doc["nodes"])
        mesh_node = {"name": f'{name}/{part["name"]}', "mesh": mesh_idx}
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


def convert_prop(data, name, target):
    if b"rdms" in data[:128]:
        parts, rejected = parse_rdms(data)
        if not parts:
            raise ValueError(f"No compatible RDMS parts: {rejected[:3]}")
        bones, anclajes = prop_joints(data)
        glb = write_rigid_glb(parts, name, target, bones=bones, anclajes=anclajes)
        return {
            "name": name,
            "format": "RDMS",
            "bones": len(bones),
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
        parts = parse_prop_bmsh(data)
        bones = parse_bones(data)
        glb = GLB()
        skin = glb.add_skin(bones)
        textures = {part["name"]: part["texture"] for part in parts}
        glb.add_mesh(name, parts, skin, len(bones), textures)
        glb.save(target)
        return {
            "name": name,
            "format": "BMSH",
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
            z.read(row["archivo_zip"]), row["ruta_interna"], args.output
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
