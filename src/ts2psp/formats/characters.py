"""Convert extracted The Sims 2 PSP .res character meshes to rigged GLB.

Input: sims2_psp_personajes_recursos.zip generated from the local ISO.
Optional texture archive: sims2_psp_personajes_texturas_png.zip.
Requires numpy and Pillow for composing textures when needed.
"""

import argparse
import csv
from collections import Counter, defaultdict
from functools import lru_cache
import io
import json
import math
import re
import struct
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from ts2psp.core.coordinates import convert_row_matrix, convert_vector3
from ts2psp.exporting.glb import GLB
from ts2psp.formats.accessories import (
    accessory_mesh_paths,
    accessory_texture,
    select_accessory_parts,
)


def u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def section(data, tag):
    for at in range(max(0, len(data) - 4096), len(data) - 20):
        if data[at : at + 4] != tag:
            continue
        off, size = struct.unpack_from("<II", data, at + 4)
        if 0 < off < len(data) and 0 < size <= len(data) - off:
            return off, size, u32(data, at + 12)
    raise ValueError(f"Section {tag!r} not found")


def triangle_strip(indices):
    out = []
    for i in range(len(indices) - 2):
        a, b, c = indices[i : i + 3]
        if a == b or a == c or b == c:
            continue
        out.extend((a, b, c) if i % 2 == 0 else (b, a, c))
    return out


def character_morph_name(name, female_face_lod5=False):
    if female_face_lod5:
        return {
            "t_botgrow": "t_botmove",
            "t_botmove": "t_botgrow",
            "t_topgrow": "t_botmove",
        }.get(name, name)
    return name


MALE_FACE_FBX_CHANNELS = (
    "t_topgrow",
    "t_botmove",
    "t_botgrow",
    "y_nose",
    "z_nose",
    "x_nose",
    "R_growl",
    "L_growl",
    "x_mouth",
    "y_mouth",
    "zb_mouth",
    "zt_mouth",
    "R_xcorner",
    "L_xcorner",
    "R_ycorner",
    "L_ycorner",
    "R_zcorner",
    "L_zcorner",
    "pinch",
    "R_scrunch",
    "L_scrunch",
    "R_brow",
    "L_brow",
    "R_eye",
    "L_eye",
    "R_cheekpuff",
    "L_cheekpuff",
)


def decode_vertices(data, ptr, count, weight_count, stride, morph_count):
    weight_bytes = weight_count + (weight_count % 2)
    sample_size = weight_bytes + 14
    if stride != sample_size * morph_count:
        raise ValueError(
            f"Incompatible morph size: {stride} != {sample_size} * {morph_count}"
        )
    if ptr + count * stride > len(data):
        raise ValueError("Vertices outside resource bounds")
    vertices = []
    morphs = [[] for _ in range(morph_count - 1)]
    for n in range(count):
        at = ptr + n * stride
        raw_weights = data[at : at + weight_count]
        if sum(raw_weights) != 128:
            raise ValueError(f"Invalid weights at vertex {n}: {sum(raw_weights)}")
        uv = struct.unpack_from("<hh", data, at + weight_bytes)
        normal = struct.unpack_from("<bbb", data, at + weight_bytes + 4)
        pos = struct.unpack_from("<hhh", data, at + weight_bytes + 8)
        vertices.append((pos, normal, uv, raw_weights))
        for j in range(1, morph_count):
            delta = struct.unpack_from(
                "<hhh", data, at + j * sample_size + weight_bytes + 8
            )
            morphs[j - 1].append(delta)
    return vertices, morphs


def parse_mesh(data, female_face_lod5=False, male_face_lod5=False):
    off, _, part_count = section(data, b"bmsh")
    header = off + 0x80
    palette_count = u32(data, header + 0x10)
    if not 1 <= palette_count <= 8:
        raise ValueError("Invalid base palette")
    palptr = header + 0x14 + u32(data, header + 0x14)
    palette = [u32(data, palptr + 4 * j) for j in range(palette_count)]
    icount = u32(data, header + 0x20)
    extra_palette_count = u32(data, header + 0x18)
    if extra_palette_count > 32:
        raise ValueError("Invalid auxiliary palette")
    extra_palette = [
        u32(data, palptr + 4 * (palette_count + j)) for j in range(extra_palette_count)
    ]
    iptr = palptr + 4 * (palette_count + extra_palette_count)
    vcount = u32(data, header + 0x28)
    vptr = header + 0x2C + u32(data, header + 0x2C)
    first_vtype = u32(data, header + 0x30)
    base_slots = ((first_vtype >> 14) & 7) + 1
    base_morphs = ((first_vtype >> 18) & 7) + 1
    base_stride = u32(data, header + 0x34)
    specs = [
        (
            "principal",
            palette,
            extra_palette,
            iptr,
            icount,
            vptr,
            vcount,
            2 if vcount > 256 else 1,
            base_slots,
            base_stride,
            base_morphs,
        )
    ]
    # BMSH's table count includes the principal mesh described above.
    for n in range(max(0, part_count - 1)):
        p = header + 0x30 * (n + 1)
        palette_count = u32(data, p + 0x10)
        if not 1 <= palette_count <= 8:
            raise ValueError(f"Invalid palette in part {n}")
        palptr = p + 0x14 + u32(data, p + 0x14)
        palette = [u32(data, palptr + 4 * j) for j in range(palette_count)]
        extra_count = u32(data, p + 0x18)
        if extra_count > 32:
            raise ValueError(f"Invalid auxiliary palette in part {n}")
        extra_palette = []
        if extra_count:
            extra_ptr = p + 0x1C + u32(data, p + 0x1C)
            extra_palette = [u32(data, extra_ptr + 4 * j) for j in range(extra_count)]
        icount = u32(data, p + 0x20)
        iptr = p + 0x24 + u32(data, p + 0x24)
        vcount = u32(data, p + 0x28)
        vptr = p + 0x2C + u32(data, p + 0x2C)
        # VTYPE/stride precede the following descriptor's data block.
        vtype = u32(data, p + 0x30)
        slots = ((vtype >> 14) & 7) + 1
        morphs = ((vtype >> 18) & 7) + 1
        stride = u32(data, p + 0x34)
        index_size = 2 if vcount > 256 else 1
        specs.append(
            (
                f"pieza_{n + 1}",
                palette,
                extra_palette,
                iptr,
                icount,
                vptr,
                vcount,
                index_size,
                slots,
                stride,
                morphs,
            )
        )
    parts = []
    blend_names = [
        x.decode("ascii").split("/")[-1]
        for x in re.findall(rb"__(?:Face|BotTeeth|TopTeeth)Blends__/[^\x00]+", data)
    ]
    for (
        name,
        palette,
        morph_ids,
        iptr,
        icount,
        vptr,
        vcount,
        index_size,
        slots,
        stride,
        morph_count,
    ) in specs:
        if not 0 < vcount < 100000 or not 0 < icount < 100000:
            raise ValueError(f"Invalid counters in {name}")
        if iptr + icount * index_size > len(data):
            raise ValueError(f"Indices outside resource bounds in {name}")
        fmt = "<" + ("H" if index_size == 2 else "B") * icount
        indices = list(struct.unpack_from(fmt, data, iptr))
        if max(indices) >= vcount:
            raise ValueError(f"Vertex index out of range in {name}")
        try:
            verts, morphs = decode_vertices(
                data, vptr, vcount, slots, stride, morph_count
            )
        except ValueError as exc:
            # Some resources advertise 8 weights for a final four-weight block.
            alt_slots = min(len(palette), 4)
            alt_stride = alt_slots + (alt_slots % 2) + 14
            try:
                verts, morphs = decode_vertices(
                    data, vptr, vcount, alt_slots, alt_stride, 1
                )
                slots = alt_slots
            except ValueError:
                raise ValueError(
                    f"{name} ptr={vptr:#x} palette={len(palette)} slots={slots} stride={stride}: {exc}"
                ) from exc
        triangles = triangle_strip(indices)
        if not triangles:
            raise ValueError(f"No triangles in {name}")
        if len(morph_ids) != len(morphs):
            raise ValueError(f"Invalid shape count in {name}")
        morph_names = []
        for i in morph_ids:
            name_index = i - 3 if female_face_lod5 and 9 <= i <= 29 else i
            if male_face_lod5:
                channel_index = i + 2 if 4 <= i <= 23 else None
                if i == 24:
                    name = "L_cheekpuff"
                    channel_index = None
                elif i == 26:
                    name = MALE_FACE_FBX_CHANNELS[0]
                    channel_index = None
                elif i == 28:
                    name = "t_botmove"
                    channel_index = None
                elif i == 29:
                    name = "t_botgrow"
                    channel_index = None
                if channel_index is not None:
                    name = MALE_FACE_FBX_CHANNELS[channel_index]
                elif i not in (24, 26, 28, 29):
                    name = f"shape_{i}"
            else:
                name = (
                    blend_names[name_index]
                    if 0 <= name_index < len(blend_names)
                    else f"shape_{i}"
                )
            morph_names.append(character_morph_name(name, female_face_lod5))
        parts.append(
            {
                "name": name,
                "palette": palette[:slots],
                "vertices": verts,
                "morph_positions": morphs,
                "morph_names": morph_names,
                "morph_ids": morph_ids,
                "indices": triangles,
                "source_indices": len(indices),
            }
        )
    return parts


def parse_character_mesh(path, data):
    face_path = path.strip("/").casefold()
    female_face_lod5 = face_path == "face/afface"
    male_face_lod5 = face_path == "face/amface"
    parts = parse_mesh(
        data, female_face_lod5=female_face_lod5, male_face_lod5=male_face_lod5
    )
    return parts


def parse_character_parts(row, path, data):
    """Select outfit parts consistently for preview and export."""
    return select_accessory_parts(row, path, parse_character_mesh(path, data))


def face_base_path(archetype_path):
    name = archetype_path.rsplit("/", 1)[-1]
    if name.startswith("afarch"):
        return "face/afface"
    if name.startswith("amarch"):
        return "face/amface"
    raise ValueError(f"Not a face archetype: {archetype_path}")


def is_face_archetype(path):
    return path.startswith("face/cas/") and path.rsplit("/", 1)[-1].startswith(
        ("afarch", "amarch")
    )


def available_face_archetypes(z, base_path):
    if base_path not in ("face/afface", "face/amface"):
        return []
    prefix = "afarch" if base_path == "face/afface" else "amarch"
    resource_prefix = "mallas/characters/bodyanims/face/cas/"
    canonical = Counter(
        vertex[2]
        for vertex in compact_face_geometry(
            z.read(resource_prefix + prefix + "base.res")
        )[0]
    )
    paths = []
    for name in z.namelist():
        if (
            not name.startswith(resource_prefix + prefix)
            or not name.endswith(".res")
            or name.endswith("noteeth.res")
        ):
            continue
        vertices, _, _ = compact_face_geometry(z.read(name))
        if Counter(vertex[2] for vertex in vertices) == canonical:
            paths.append(f"face/cas/{name[len(resource_prefix):-4]}")
    return sorted(set(paths))


def chosen_face_archetype(z, row, override=None):
    base = row.get("bodyHead", "")
    if base not in ("face/afface", "face/amface"):
        return None
    available = available_face_archetypes(z, base)
    if not available:
        return None
    if override is not None:
        if override not in available:
            raise ValueError(f"Face archetype unavailable: {override}")
        return override
    declared = row.get("faceArchetype", "").strip()
    if not declared:
        # Older extracted archives lack this CSV column; the character record
        # still carries the exact value in its first Lua block.
        record = z.read(row["archivo_zip"])
        match = re.search(
            rb'(?m)^[ \t]*faceArchetype\s*=\s*["\'](a[fm]arch[a-z]+)["\']', record
        )
        if match:
            declared = match.group(1).decode("ascii")
    if not declared:
        return None
    selected = "face/cas/" + declared
    if selected in available:
        return selected
    neutral = "face/cas/" + ("afarchemed" if base == "face/afface" else "amarchemed")
    return neutral if neutral in available else available[0]


@lru_cache(maxsize=160)
def compact_face_geometry(data):
    """Read a CAS face or modifier mesh without assigning a skeleton."""
    off, _, count = section(data, b"bmsh")
    if not 1 <= count <= 8:
        raise ValueError("Unexpected face archetype format")
    h = off + 0x80
    vertices, triangles = [], []
    source_index_count = 0
    for part_number in range(count):
        part_header = h + 0x30 * part_number
        index_count = u32(data, part_header)
        index_at = part_header + 4 + u32(data, part_header + 4)
        vertex_count = u32(data, part_header + 8)
        vertex_at = part_header + 12 + u32(data, part_header + 12)
        vtype = u32(data, part_header + 16)
        stride = u32(data, part_header + 20)
        index_size = 1 if vertex_count <= 256 else 2
        if (
            vtype not in (0x12000B22, 0x12001322)
            or stride != 16
            or not 2 < index_count < 10000
            or not 2 < vertex_count < 10000
            or index_at + index_size * index_count > len(data)
        ):
            raise ValueError("Invalid face archetype header")
        indices = struct.unpack_from(
            "<" + ("B" if index_size == 1 else "H") * index_count, data, index_at
        )
        if max(indices) >= vertex_count:
            raise ValueError("Face archetype index out of range")
        offset = len(vertices)
        part_vertices, _ = decode_vertices(data, vertex_at, vertex_count, 1, stride, 1)
        vertices.extend(part_vertices)
        triangles.extend(offset + index for index in triangle_strip(indices))
        source_index_count += index_count
    return tuple(vertices), tuple(triangles), source_index_count


def face_linked_shapes(archive, selected_path):
    """Find CAS archetype and facial modifier meshes linked to this face."""
    base_path = face_base_path(selected_path)
    prefix = "mallas/characters/bodyanims/face/cas/"
    archetypes = [
        (
            path.rsplit("/", 1)[-1],
            archive.read(prefix + path.rsplit("/", 1)[-1] + ".res"),
        )
        for path in available_face_archetypes(archive, base_path)
        if path != selected_path
    ]
    modifiers = [
        (name[len(prefix) : -4], archive.read(name))
        for name in sorted(archive.namelist())
        if name.startswith(prefix + "uumod") and name.endswith(".res")
    ]
    canonical = archive.read(prefix + "afarchbase.res")
    return archetypes, modifiers, canonical


def _vertices_by_uv(vertices):
    grouped = defaultdict(list)
    for vertex in vertices:
        grouped[vertex[2]].append(vertex)
    return grouped


def _closest_vertex(position, candidates):
    return min(
        candidates,
        key=lambda vertex: sum(
            (position[axis] - vertex[0][axis]) ** 2 for axis in range(3)
        ),
    )


def parse_face_archetype(data, base_parts, linked_shapes=None):
    """Rig a compact CAS face and attach expression and CAS shape keys."""
    vertices, triangles, source_index_count = compact_face_geometry(data)
    vertices = list(vertices)
    triangles = list(triangles)
    sources = {}
    for part in base_parts:
        for index, vertex in enumerate(part["vertices"]):
            sources.setdefault(vertex[2], []).append((part, index, vertex))
    matched = []
    used_bones = set()
    morph_labels = {}
    for vertex in vertices:
        candidates = sources.get(vertex[2])
        if not candidates:
            raise ValueError("The archetype does not match the base face UVs")
        part, index, source = min(
            candidates,
            key=lambda item: sum(
                (vertex[0][axis] - item[2][0][axis]) ** 2 for axis in range(3)
            ),
        )
        matched.append((part, index, source))
        used_bones.update(
            bone for bone, weight in zip(part["palette"], source[3]) if weight
        )
        morph_labels.update(zip(part["morph_ids"], part["morph_names"]))
    palette = sorted(used_bones)
    if not palette or len(palette) > 8:
        raise ValueError("Incompatible face bone palette")
    morph_ids = sorted(morph_labels)
    output_vertices = []
    morphs = [[] for _ in morph_ids]
    for vertex, (part, index, source) in zip(vertices, matched):
        weights = {
            bone: weight for bone, weight in zip(part["palette"], source[3]) if weight
        }
        raw_weights = bytes(weights.get(bone, 0) for bone in palette)
        if sum(raw_weights) != 128:
            raise ValueError("Invalid archetype face weights")
        output_vertices.append((vertex[0], vertex[1], vertex[2], raw_weights))
        for out, morph_id in zip(morphs, morph_ids):
            source_morph = (
                part["morph_ids"].index(morph_id)
                if morph_id in part["morph_ids"]
                else None
            )
            out.append(
                part["morph_positions"][source_morph][index]
                if source_morph is not None
                else (0, 0, 0)
            )
    if linked_shapes is not None:
        archetypes, modifiers, canonical_data = linked_shapes
        selected_uvs = Counter(vertex[2] for vertex in vertices)
        next_id = 1000
        for name, target_data in archetypes:
            target_vertices, _, _ = compact_face_geometry(target_data)
            if Counter(vertex[2] for vertex in target_vertices) != selected_uvs:
                continue
            targets_by_uv = _vertices_by_uv(target_vertices)
            deltas = []
            for vertex in vertices:
                target = _closest_vertex(vertex[0], targets_by_uv[vertex[2]])
                deltas.append(
                    tuple(target[0][axis] - vertex[0][axis] for axis in range(3))
                )
            if any(delta != (0, 0, 0) for delta in deltas):
                morph_ids.append(next_id)
                morphs.append(deltas)
                morph_labels[next_id] = "Face archetype: " + name
                next_id += 1
        canonical_vertices, _, _ = compact_face_geometry(canonical_data)
        canonical_by_uv = _vertices_by_uv(canonical_vertices)
        next_id = 2000
        for name, modifier_data in modifiers:
            modifier_vertices, _, _ = compact_face_geometry(modifier_data)
            modifier_by_uv = _vertices_by_uv(modifier_vertices)
            deltas = []
            for vertex in vertices:
                candidates = modifier_by_uv.get(vertex[2])
                if not candidates:
                    deltas.append((0, 0, 0))
                    continue
                canonical = _closest_vertex(vertex[0], canonical_by_uv[vertex[2]])
                target = _closest_vertex(canonical[0], candidates)
                deltas.append(
                    tuple(target[0][axis] - canonical[0][axis] for axis in range(3))
                )
            if any(delta != (0, 0, 0) for delta in deltas):
                morph_ids.append(next_id)
                morphs.append(deltas)
                morph_labels[next_id] = "Face modifier: " + name.removeprefix("uumod")
                next_id += 1
    return [
        {
            "name": "face_archetype",
            "palette": palette,
            "vertices": output_vertices,
            "indices": triangles,
            "source_indices": source_index_count,
            "morph_positions": morphs,
            "morph_ids": morph_ids,
            "morph_names": [morph_labels[morph_id] for morph_id in morph_ids],
        }
    ]


def parse_bones(data):
    off, _, _ = section(data, b"body")
    count = u32(data, off + 0x80)
    if not 1 <= count <= 256:
        raise ValueError("Invalid bone count")
    bones = []
    for i in range(count):
        at = off + 0xB0 + i * 0xA0
        name_at = at + u32(data, at)
        name_end = data.index(0, name_at)
        name = data[name_at:name_end].decode("ascii", "replace")
        parent = struct.unpack_from("<h", data, at + 8)[0]
        if parent >= i:
            raise ValueError("Invalid bone hierarchy")
        ibm = np.array(
            struct.unpack_from("<16f", data, at + 0x60), dtype=np.float64
        ).reshape(4, 4)
        world = np.linalg.inv(ibm)
        bones.append({"name": name, "parent": parent, "ibm": ibm, "world": world})
    return bones


def arm_bind_offset(bones, body_parts):
    """Measure the common vertical gap between the hand skin and its joints."""
    names = {bone["name"]: i for i, bone in enumerate(bones)}
    if not all(
        f"__{side}_Clavicle__" in names and f"__{side}_Hand__" in names
        for side in ("R", "L")
    ):
        return 0.0
    gaps = []
    for side in ("R", "L"):
        hand = names[f"__{side}_Hand__"]
        weighted_z = weight_total = 0
        for part in body_parts:
            for vertex in part["vertices"]:
                for bone_id, weight in zip(part["palette"], vertex[3]):
                    if bone_id == hand and weight:
                        weighted_z += vertex[0][2] * weight
                        weight_total += weight
        if weight_total:
            gaps.append(
                weighted_z / (1000.0 * weight_total) - bones[hand]["world"][3, 2]
            )
    if len(gaps) != 2 or abs(gaps[0] - gaps[1]) > 0.025:
        return 0.0
    gap = sum(gaps) / 2
    return float(gap) if 0.015 <= gap <= 0.07 else 0.0


def align_arm_bind_pose(bones, offset):
    """Move both arm branches into the mesh, preserving a valid GLB bind pose."""
    if not offset:
        return bones
    names = {bone["name"]: i for i, bone in enumerate(bones)}
    roots = {
        names[f"__{side}_Clavicle__"]
        for side in ("R", "L")
        if f"__{side}_Clavicle__" in names
    }
    if len(roots) != 2:
        return bones
    aligned = []
    for i, bone in enumerate(bones):
        parent = i
        while parent >= 0 and parent not in roots:
            parent = bones[parent]["parent"]
        world = bone["world"].copy()
        if parent in roots:
            world[3, 2] += offset
        aligned.append({**bone, "world": world, "ibm": np.linalg.inv(world)})
    return aligned


# Que parte del hueco se corrige en los huesos de la mano. El hueso no esta en
# el centro de su piel sino en la articulacion, y la articulacion queda hacia
# dentro de los dedos: llevar el hueso al centroide los deja enterrados en la
# palma. La mitad es lo que mas se acerca sin pasarse.
HUEGO_MANO_FRACCION = 0.5


def descendientes(bones, raiz):
    """Indices de `raiz` y de todo lo que cuelga de el."""
    hijos = {}
    for i, bone in enumerate(bones):
        hijos.setdefault(bone["parent"], []).append(i)
    fuera = set()
    pila = [raiz]
    while pila:
        i = pila.pop()
        if i in fuera:
            continue
        fuera.add(i)
        pila.extend(hijos.get(i, ()))
    return fuera


def hand_bind_offset(bones, body_parts):
    """Cuanto hacia dentro de su piel caen los huesos de cada mano, en metros.

    Es un error distinto del vertical que mide `arm_bind_offset`: aqui los
    huesos salen bien de alto pero metidos en la mano, tres o cuatro
    centimetro. Se mide por rama comparando el centro de la piel que la mueve
    con el centro de sus huesos.

    De la rama se saca la muneca y solo quedan los dedos. La muneca pesa
    mucho en el centro y su piel la arrastra hacia dentro el puño de la manga:
    en Doctor Dominion eso separaba las dos manos 15 mm (la derecha 62, la
    izquierda 13) cuando los dedos de una y otra coinciden en 2 mm. Los dedos
    son las articulaciones que se mueven y los que hay que mirar.

    Devuelve un diccionario lado -> metros a empujar hacia fuera. Vacio si no
    hay manos, si la piel no las mueve o si las dos manos no coinciden: una
    medida que solo sale de un brazo no es un error sistematico sino otra malla.
    """
    names = {bone["name"]: i for i, bone in enumerate(bones)}
    offsets = {}
    for side, fuera in (("R", 1.0), ("L", -1.0)):
        raiz = f"__{side}_Hand__"
        if raiz not in names:
            return {}
        rama = descendientes(bones, names[raiz]) - {names[raiz]}
        if not rama:
            return {}
        acumulada = np.zeros(3)
        peso = 0.0
        for part in body_parts:
            for vertex in part["vertices"]:
                for bone_id, weight in zip(part["palette"], vertex[3]):
                    if bone_id in rama and weight:
                        acumulada += np.asarray(vertex[0]) * weight
                        peso += weight
        if not peso:
            return {}
        piel = acumulada / peso / 1000.0
        centros = sum(bones[i]["world"][3, :3] for i in rama) / len(rama)
        offsets[side] = float((piel[0] - centros[0]) * fuera)
    if abs(offsets["R"] - offsets["L"]) > 0.015:
        return {}
    hueco = (offsets["R"] + offsets["L"]) / 2
    return {side: hueco for side in offsets} if 0.01 <= hueco <= 0.10 else {}


def align_hand_bind_pose(bones, offsets):
    """Empuja los huesos de las manos hacia fuera, dentro de la piel que mueven.

    Solo se mueve la rama de la muñeca: el brazo se queda donde lo puso
    `align_arm_bind_pose`. Como alli, se recalcula la inversa de la enlace para
    que el GLB siga teniendo una pose de referencia valida.
    """
    if not offsets:
        return bones
    names = {bone["name"]: i for i, bone in enumerate(bones)}
    ramas = []
    for side, fuera in (("R", 1.0), ("L", -1.0)):
        raiz = f"__{side}_Hand__"
        if raiz not in names or side not in offsets:
            return bones
        paso = offsets[side] * fuera * HUEGO_MANO_FRACCION
        if paso:
            ramas.append((descendientes(bones, names[raiz]), paso))
    if not ramas:
        return bones
    pasos = {}
    for ids, paso in ramas:
        for i in ids:
            pasos[i] = paso
    aligned = []
    for i, bone in enumerate(bones):
        world = bone["world"].copy()
        if i in pasos:
            world[3, 0] += pasos[i]
        aligned.append({**bone, "world": world, "ibm": np.linalg.inv(world)})
    return aligned


# Nombre que se ve en las listas de personajes. El slug de la ficha NO cambia:
# es lo que da nombre a las texturas (`composed_textures/top/<slug>`), a las
# mallas, al GLB de la cache y a los ficheros exportados. Solo cambia lo que se
# pinta, y al seleccionar se traduce de vuelta al slug.
NOMBRES_PUBLICOS = {
    "anniehowell": "Annie Howell",
    "ashtonfelt": "Ashton Felt",
    "bellagoth": "Bella Goth",
    "berettawinchester": "Beretta Winchester",
    "betsyshelton": "Betsy Shelton",
    "bodyreaper": "Body Reaper",
    "bulldratch": "Bull Dratch",
    "buzzgrunt": "Buzz Grunt",
    "catelynlancaster": "Catelyn Lancaster",
    "chetcrawley": "Chet Crawley",
    "circebeaker": "Circe Beaker",
    "clancyhardwick": "Clancy Hardwick",
    "clintheckler": "Clint Heckler",
    "cowthulu": "Beelzebeef",
    "cristianallard": "Cristian Allard",
    "dantehudd": "Dante Hudd",
    "dennisphilips": "Dennis Philips",
    "deputyduncan": "Deputy Duncan",
    "dixiestills": "Dixie Stills",
    "doctordominion": "Doctor Dominion",
    "dominicnewlow": "Dominic Newlow",
    "eadiebutler": "Eadie Butler",
    "eltoro": "El Toro",
    "emilyemory": "Emily Emory",
    "emmadarcy": "Emma Darcy",
    "erinphilips": "Erin Philips",
    "fplayer": "fplayer",
    "georgemarlow": "George Marlow",
    "gimibranko": "Gimi Branko",
    "hazeldoss": "Hazel Doss",
    "heberdarcy": "Heber Darcy",
    "hoothowell": "Hoot Howell",
    "isaacrossum": "Isaac Rossum",
    "janekoch": "Jane Koch",
    "jenniesmith": "Jennie Smith",
    "jesseleevarmint": "Jesse Lee Varmint",
    "jillsmith": "Jill Smith",
    "johnnysmith": "Johnny Smith",
    "kerstenspooks": "Kersten Spooks",
    "kristinalove": "Kristina Love",
    "lazlocurious": "Lazlo Curious",
    "lincolnbroadsheet": "Lincoln Broadsheet",
    "lokibeaker": "Loki Beaker",
    "mamboloa": "Mambo Loa",
    "marieaulait": "Marie Au Lait",
    "mib1": "Men in Black 1",
    "mib2": "Men in Black 2",
    "mib3": "Men in Black 3",
    "mib4": "Men in Black 4",
    "mplayer": "mplayer",
    "nervoussubject": "Nervous Subject",
    "nightbeast": "Night Beast",
    "opheliaspecter": "Ophelia Specter",
    "orderlyblack": "Orderly Black",
    "orderlywhite": "Orderly White",
    "organghost": "Organ Ghost",
    "oscardelfuego": "Oscar Del Fuego",
    "pascalcurious": "Pascal Curious",
    "penelopekline": "Penelope Kline",
    "pitaflorica": "Pita Florica",
    "player": "Tina Mansfield (Alt)",
    "pt9": "Pollination Tech #9",
    "redsands": "Red Sands",
    "rickwong": "Rick Wong",
    "rippgrunt": "Ripp Grunt",
    "robertarossum": "Roberta Rossum",
    "rolandcalonzo": "Roland Calonzo",
    "rolandcalonzo-kine": "Roland Calonzo (Kine)",
    "sarastarr": "Sara Starr",
    "scootsturnberry": "Scoots Turnberry",
    "sinjinbalani": "Sinjin Balani",
    "sinjinbalani-kine": "Sinjin Balani (Kine)",
    "stenluger": "Sten Luger",
    "ta7": "Transit Admin #7",
    "tankgrunt": "Tank Grunt",
    "territortuga": "Terri Tortuga",
    "texfolsom": "Tex Folsom",
    "tinamansfield": "Tina Mansfield",
    "tunaktun": "Tunak Tun",
    "tureenallard": "Tureen Allard",
    "tychocurious": "Tycho Curious",
    "vaughanferretino": "Vaughan Ferretino",
    "vidcundcurious": "Vidcund Curious",
    "virginyafeng": "Virginya Feng",
    "virginyafeng_vamp": "Virginya Feng (Vampire)",
    "yevacomonova": "Yeva Comonova",
    "zenmu": "Zen Mu",
    "zinamarlowe": "Zina Marlowe",
}

# Personajes con ficha y malla pero que no se ofrecen en las listas. Se ocultan
# solo al listar: el slug sigue valiendo para el resto, y las reglas que deciden
# el personaje de un clip siguen hablando de el.
PERSONAJES_OCULTOS = ("fplayer", "mplayer")

_SLUG_PUBLICOS = {publico: slug for slug, publico in NOMBRES_PUBLICOS.items()}


def nombre_publico(slug):
    """Nombre visible de un personaje; si no esta renombrado, el propio slug."""
    return NOMBRES_PUBLICOS.get(slug, slug)


def slug_publico(nombre):
    """Slug de un personaje a partir del nombre visible de la lista."""
    return _SLUG_PUBLICOS.get(nombre, nombre)


def load_character_rows(z):
    with z.open("personajes.csv") as stream:
        return list(csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig")))


def select_character_row(rows, character):
    matches = [
        row for row in rows if row["personaje"].casefold() == character.casefold()
    ]
    if character.casefold() == "zinamarlowe":
        dress = next(
            (
                row
                for row in matches
                if row.get("bodyTorso") == "body/afbodydress"
                and not row.get("bodyLegs")
            ),
            None,
        )
        if dress is not None:
            return dress
    return max(
        matches,
        key=lambda row: sum(
            bool(row.get(key))
            for key in ["bodyTorso", "bodyLegs", "bodyHead", "bodyHair"]
        ),
    )


SPECIAL_CHARACTER_MESHES = {
    "cowthulu": ("body/aubodycowthulu", 23, "aubodycowthulu", "aubodycowthulu", "_"),
    "nightbeast": ("body/aubodynightbeast", 29, "aubodynightbeast", "nightbeast", ""),
}

# Los bebes no traen torso/piernas/cabeza/pelo: el recurso de especie declara
# una sola malla con `bodySingle`, y dentro van cuerpo y cara. El CSV no trae esa
# columna, asi que la malla se lee del propio .res de la ficha.
_BODY_SINGLE = re.compile(r"""bodySingle\s*=\s*["']([^"']+)["']""")

# Mallas que llevan cuerpo y cara en el mismo .res: hay que repartir las piezas
# entre las dos texturas. Se declara a mano porque el recurso es binario y no
# trae los nombres de las piezas. Las de cara se distinguen porque son las unicas
# que reparten el cuadro entero de UV (la textura de cara es una foto, no un atlas).
MALLAS_CARA_Y_CUERPO = {
    "body/bubodydiaper": (
        "body/bubodydiaper/bubodynaked-nude-s5",
        "body/bubodydiaper/buface-s5",
        ("pieza_8",),
    ),
}


def species_body_single(archive, row):
    """Malla unica declarada en el recurso de especie, o '' si no la tiene."""
    archivo = row.get("archivo_zip") or ""
    if not archivo:
        return ""
    try:
        texto = archive.read(archivo).decode("latin1")
    except KeyError:
        return ""
    match = _BODY_SINGLE.search(re.sub(r"--[^\r\n]*", "", texto))
    return match.group(1) if match else ""


def character_mesh_paths(z, row, archetype=None):
    paths = list(
        dict.fromkeys(
            row.get(k, "")
            for k in ["bodyTorso", "bodyLegs", "bodyHead", "bodyHair"]
            if row.get(k, "")
        )
    )
    if paths:
        selected = chosen_face_archetype(z, row, archetype)
        if selected:
            paths = [selected if path == row["bodyHead"] else path for path in paths]
        return list(dict.fromkeys(paths + accessory_mesh_paths(z, row)))
    special = SPECIAL_CHARACTER_MESHES.get(row.get("personaje", "").casefold())
    if special:
        path = special[0]
        resource = "mallas/characters/bodyanims/" + path + ".res"
        if resource in z.namelist():
            return [path]
    # Sin mallas en la ficha puede ser un bebe, que va con una sola.
    single = species_body_single(z, row)
    if single:
        resource = "mallas/characters/bodyanims/" + single + ".res"
        if resource in z.namelist():
            return [single]
    return []


def character_texture_key(row, path, part_name):
    partida = MALLAS_CARA_Y_CUERPO.get(path)
    if partida is not None:
        _, _, partes_cara = partida
        return path + ("#cara" if part_name in partes_cara else "#cuerpo")
    special = SPECIAL_CHARACTER_MESHES.get(row.get("personaje", "").casefold())
    if special and path == special[0]:
        part_index = (
            int(part_name.removeprefix("pieza_"))
            if part_name.startswith("pieza_")
            else 0
        )
        return path + ("#head" if part_index <= special[1] else "#body")
    return path


def character_mesh_texture(library, row, path, part_name):
    accessory = accessory_texture(library, path, part_name)
    if accessory is not None:
        return accessory
    special = SPECIAL_CHARACTER_MESHES.get(row.get("personaje", "").casefold())
    if special and path == special[0]:
        kind = (
            "head"
            if character_texture_key(row, path, part_name).endswith("#head")
            else "body"
        )
        name = f"body/{special[2]}/{special[3]}{special[4]}{kind}"
        png = library.read(name)
        return (name, png) if png is not None else None
    partida = MALLAS_CARA_Y_CUERPO.get(path)
    if partida is not None:
        cuerpo, cara, partes_cara = partida
        name = cara if part_name in partes_cara else cuerpo
        png = library.read(name)
        return (name, png) if png is not None else None
    if is_face_archetype(path):
        return library.character_texture(row, "head")
    for key, kind in [
        ("bodyTorso", "top"),
        ("bodyLegs", "legs"),
        ("bodyHead", "head"),
        ("bodyHair", "hair"),
    ]:
        if row.get(key) == path:
            result = library.character_texture(row, kind)
            if kind == "top" and path.startswith("body/"):
                character = row["personaje"]
                composed = f"composed_textures/top/{character}"
                if result is None or result[0] != composed:
                    body_atlas = f"{path}/{character}"
                    png = library.read(body_atlas)
                    if png is not None:
                        return body_atlas, png
            return result
    return None


def find_mesh(z, path):
    suffix = "mallas/characters/bodyanims/" + path.strip("/") + ".res"
    try:
        return z.read(suffix)
    except KeyError:
        raise ValueError(f"Resource not found: {path}") from None


CHARACTER_MESH_VARIANTS = {
    (
        "betsyshelton",
        "top/aftophalterwaste",
    ): "mallas/characters/bodyanims/top/aftophalterwaste__entry102880.res",
    (
        "penelopekline",
        "top/aftophalterwaste",
    ): "mallas/characters/bodyanims/top/aftophalterwaste__entry102880.res",
    (
        "tureenallard",
        "top/afcrewneckcollarshirt",
    ): "mallas/characters/bodyanims/top/afcrewneckcollarshirt__entry102784.res",
}


def find_character_mesh(z, row, path):
    base = find_mesh(z, path)
    variant = CHARACTER_MESH_VARIANTS.get((row["personaje"].casefold(), path))
    if variant is None:
        return base
    try:
        return z.read(variant)
    except KeyError:
        raise ValueError(f"Alternative resource not found: {variant}") from None


class TextureLibrary:
    PREFIX = "texturas/characters/bodyanims/"

    def __init__(self, archive):
        self.archive = archive
        self.names = set(archive.namelist())

    def read(self, path):
        key = self.PREFIX + path.strip("/") + ".png"
        if key not in self.names:
            return None
        data = self.archive.read(key)
        alpha_key = self.PREFIX + path.strip("/") + "-alpha.png"
        if alpha_key not in self.names:
            return data
        base = Image.open(io.BytesIO(data)).convert("RGBA")
        alpha = self._alpha_channel(self.archive.read(alpha_key))
        if alpha.size != base.size:
            alpha = alpha.resize(base.size, Image.Resampling.NEAREST)
        base.putalpha(ImageChops.multiply(base.getchannel("A"), alpha))
        output = io.BytesIO()
        base.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _alpha_channel(data):
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        alpha = image.getchannel("A")
        return image.convert("L") if alpha.getextrema() == (255, 255) else alpha

    def with_alpha(self, texture, alpha_path):
        alpha_data = self.read(alpha_path)
        if alpha_data is None:
            return texture
        base = Image.open(io.BytesIO(texture[1])).convert("RGBA")
        alpha = self._alpha_channel(alpha_data)
        if alpha.size != base.size:
            alpha = alpha.resize(base.size, Image.Resampling.NEAREST)
        base.putalpha(ImageChops.multiply(base.getchannel("A"), alpha))
        output = io.BytesIO()
        base.save(output, format="PNG")
        return texture[0], output.getvalue()

    @staticmethod
    def _es_calva(ruta):
        """True si la ruta apunta a la malla de calva."""
        return "hairbald" in str(ruta or "").casefold()

    def _alfa_del_pelo(self, row):
        """Alfa del pelo deducido de su propia malla, si existe.

        La ficha de algunos personajes (player entre ellos) llega sin
        `textureHairAlpha`, pero la textura de recorte del pelo sí esta en la
        biblioteca y sigue la convencion `<malla>/<nombre sin prefijo>-alpha`:
        hair/afhairlongshaggy -> hair/afhairlongshaggy/hairlongshaggy-alpha.
        """
        mesh = row.get("bodyHair", "")
        if not mesh or self._es_calva(mesh):
            return ""
        base = mesh.rsplit("/", 1)[-1]
        for genero in ("af", "am", "au"):
            if base.startswith(genero):
                base = base[len(genero) :]
                break
        return f"{mesh}/{base}-alpha"

    def character_texture(self, row, category):
        character = row["personaje"]
        fields = {
            "top": ["textureTorso_0", "textureTorso_1"],
            "legs": ["textureLegs_0", "textureLegs_1"],
            "head": [
                "textureHead_0",
                "textureHead_1",
                "textureHead_2",
                "textureHead_3",
                "textureHead_4",
            ],
            "hair": ["textureHair_0", "textureHair_1"],
        }[category]
        composed = f"composed_textures/{category}/{character}"
        png = self.read(composed)
        if png is not None and not (
            character.casefold() == "zinamarlowe" and category == "hair"
        ):
            result = (composed, png)
        else:
            # `textureHair_0` es la ranura de repuesto y a veces trae la calva
            # (`hair/afhairbald/ufhairbald-skin-s1`) aunque el personaje luzca
            # pelo de verdad. Compositar esa capa encima tapa el pelo con piel.
            descartar_calva = category == "hair" and not self._es_calva(
                row.get("bodyHair", "")
            )
            layers = []
            for field in fields:
                source = row.get(field, "")
                if not source:
                    continue
                if descartar_calva and self._es_calva(source):
                    continue
                data = self.read(source)
                if data is not None:
                    layers.append((source, data))
            if not layers:
                return None
            if len(layers) == 1:
                result = layers[0]
            else:
                images = [
                    Image.open(io.BytesIO(data)).convert("RGBA") for _, data in layers
                ]
                base = images[0]
                for overlay in images[1:]:
                    if overlay.size != base.size:
                        overlay = overlay.resize(base.size, Image.Resampling.NEAREST)
                    base = Image.alpha_composite(base, overlay)
                output = io.BytesIO()
                base.save(output, format="PNG")
                result = (f"composed/{category}/{character}", output.getvalue())

        alpha_fields = {
            "top": ("textureTorsoAlpha", "textureTorso_Alpha"),
            "legs": ("textureLegsAlpha",),
            "hair": ("textureHairAlpha",),
        }.get(category, ())
        if category == "hair" and not any(row.get(f, "") for f in alpha_fields):
            deducido = self._alfa_del_pelo(row)
            if deducido and self.read(deducido) is not None:
                alpha_fields = (deducido,)
        for field in alpha_fields:
            alpha_path = row.get(field, "") if field in row else field
            if alpha_path:
                result = self.with_alpha(result, alpha_path)
        return result


def convert_character(
    zip_path, character, output_path, texture_zip_path=None, archetype=None
):
    with zipfile.ZipFile(zip_path) as z:
        rows = load_character_rows(z)
        matches = [r for r in rows if r["personaje"].casefold() == character.casefold()]
        if not matches:
            raise ValueError(f"Character not found: {character}")
        row = select_character_row(rows, character)
        paths = character_mesh_paths(z, row, archetype)
        if not paths:
            raise ValueError("This character record has no 3D parts")
        resources = [(p, find_character_mesh(z, row, p)) for p in paths]
        face_sources = {
            p: find_mesh(z, face_base_path(p)) for p in paths if is_face_archetype(p)
        }
        linked_faces = {p: face_linked_shapes(z, p) for p in face_sources}
    if texture_zip_path is None:
        candidate = Path(zip_path).with_name("sims2_psp_personajes_texturas_png.zip")
        texture_zip_path = candidate if candidate.exists() else None
    mesh_parts = {}
    for name, data in resources:
        if is_face_archetype(name):
            base = face_base_path(name)
            source = face_sources[name]
            mesh_parts[name] = parse_face_archetype(
                data, parse_character_mesh(base, source), linked_faces[name]
            )
        else:
            mesh_parts[name] = parse_character_parts(row, name, data)
    part_textures = {}
    if texture_zip_path is not None:
        with zipfile.ZipFile(texture_zip_path) as textures:
            library = TextureLibrary(textures)
            part_textures = {
                name: {
                    part["name"]: character_mesh_texture(
                        library, row, name, part["name"]
                    )
                    for part in parts
                }
                for name, parts in mesh_parts.items()
            }
    rigs = [parse_bones(face_sources.get(name, data)) for name, data in resources]
    torso_name = row.get("bodyTorso")
    if torso_name in mesh_parts:
        torso_index = paths.index(torso_name)
        offset = arm_bind_offset(rigs[torso_index], mesh_parts[torso_name])
        rigs = [align_arm_bind_pose(rig, offset) for rig in rigs]
        # El mismo ajuste que al ver el personaje quieto. Sin el, el
        # esqueleto que se pinta encima del clip sale con los huesos de la
        # mano metidos dentro de la palma, y al mover los brazos se nota.
        hueco = hand_bind_offset(rigs[torso_index], mesh_parts[torso_name])
        rigs = [align_hand_bind_pose(rig, hueco) for rig in rigs]
    glb = GLB()
    skin_rigs = []
    for (name, data), rig in zip(resources, rigs):
        skin = next(
            (
                i
                for i, saved in enumerate(skin_rigs)
                if [x["name"] for x in rig] == [x["name"] for x in saved]
                and all(
                    np.allclose(a["ibm"], b["ibm"], atol=1e-5)
                    for a, b in zip(rig, saved)
                )
            ),
            None,
        )
        if skin is None:
            skin = glb.add_skin(rig)
            skin_rigs.append(rig)
        glb.add_mesh(name, mesh_parts[name], skin, len(rig), part_textures.get(name))
    glb.save(output_path)
    return {
        "character": character,
        "pieces": paths,
        "bones": len(rigs[0]),
        "skeletons": len(skin_rigs),
        "vertices": sum(
            len(part["vertices"]) for parts in mesh_parts.values() for part in parts
        ),
        "triangles": sum(
            len(part["indices"]) // 3 for parts in mesh_parts.values() for part in parts
        ),
        "textured_pieces": len(glb.doc.get("textures", [])),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Extract rigged character GLB files from The Sims 2 PSP"
    )
    ap.add_argument("zip", type=Path, help="ZIP of resources extracted from the ISO")
    ap.add_argument("character", help="Character name, for example bellagoth")
    ap.add_argument("output", type=Path, help="Output GLB file")
    ap.add_argument(
        "--textures", type=Path, help="PNG texture ZIP (default: adjacent archive)"
    )
    args = ap.parse_args()
    result = convert_character(args.zip, args.character, args.output, args.textures)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

