"""Read the static NODE hierarchy and its RDMS mesh instances."""

import math
import struct

import numpy as np


def _relative(data, at):
    return at + struct.unpack_from("<i", data, at)[0]


def _transform(
    values,
    rotate_pivot=(0, 0, 0),
    scale_pivot=(0, 0, 0),
    rotate_offset=(0, 0, 0),
    scale_offset=(0, 0, 0),
):
    translation, rotation, scale = values[1:4], values[4:7], values[7:10]
    x, y, z = rotation
    cx, sx, cy, sy, cz, sz = (
        math.cos(x),
        math.sin(x),
        math.cos(y),
        math.sin(y),
        math.cos(z),
        math.sin(z),
    )
    rx = np.array(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
    ry = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
    rz = np.array(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
    matrix = np.eye(4)
    rotation = rz @ ry @ rx
    matrix[:3, :3] = rotation * scale
    rp, sp = np.asarray(rotate_pivot), np.asarray(scale_pivot)
    # Maya-style pivots: T(t) T(rpt) T(rp) R T(-rp) T(spt) T(sp) S T(-sp).
    matrix[:3, 3] = (
        np.asarray(translation)
        + rotate_offset
        + rp
        + rotation @ (-rp + scale_offset + sp - np.asarray(scale) * sp)
    )
    return matrix


def transform_values(data, node):
    return struct.unpack_from("<10f", data, _relative(data, node[0] + 8))


def is_transform(data, node):
    at = _relative(data, node[0])
    # Type 10 is also used by light records, whose payload is not a transform.
    return node[1] == 10 and data[at : at + 10] == b"transform\0"


def transform_pivot(data, node):
    return np.array(struct.unpack_from("<3f", data, node[0] + 32))


def transform_matrix(data, node, values=None):
    at = node[0]
    if values is None:
        values = transform_values(data, node)
    pivots = struct.unpack_from("<12f", data, at + 32)
    if not all(math.isfinite(value) for value in (*values, *pivots)):
        raise ValueError("Nonfinite NODE transform")
    return _transform(values, pivots[:3], pivots[6:9], pivots[3:6], pivots[9:12])


def node_hierarchy(data, records):
    """Nodos del prop y como composed en mundo.

    Devuelve (nodes, world_matrix, head, end). `nodes` es una lista de
    (at, kind, parent, local) en el orden del indice de la seccion `node`, y
    `world_matrix(index)` da su matriz 4x4 final. Es la misma lectura que usa
    `rigid_instances`, extraida para poder dibujar el esqueleto.

    `world_matrix` es perezoso a proposito: hay props con ciclos en ramas que
    solo se recorren si se piden, y recorrerlos todos daria error.
    """
    for tag, off, size, _, _ in records:
        if tag != b"node":
            continue
        end = min(len(data), off + 128 + size)
        for prefix in (0, 32):
            head = off + 128 + prefix
            if head + 8 > end:
                continue
            count = struct.unpack_from("<I", data, head)[0]
            table = _relative(data, head + 4)
            if 0 < count <= 4096 and head <= table <= end - 4 * count:
                break
        else:
            continue
        nodes = []
        for index in range(count):
            at = _relative(data, table + index * 4)
            if not head <= at <= end - 32:
                raise ValueError("NODE record outside its section")
            kind = struct.unpack_from("<I", data, at + 20)[0]
            parent = struct.unpack_from("<i", data, at + 28)[0]
            local = np.eye(4)
            if is_transform(data, (at, kind, parent, local)):
                payload = _relative(data, at + 8)
                if not head <= payload <= end - 40:
                    raise ValueError("NODE transform outside its section")
                values = struct.unpack_from("<10f", data, payload)
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("Nonfinite NODE transform")
                if at + 80 > end:
                    raise ValueError("NODE pivots outside its section")
                local = transform_matrix(data, (at, kind, parent, local), values)
            nodes.append((at, kind, parent, local))
        world, visiting = {}, set()

        def world_matrix(index):
            if index in world:
                return world[index]
            if index in visiting:
                raise ValueError("Cyclic NODE hierarchy")
            visiting.add(index)
            _, _, parent, local = nodes[index]
            world[index] = (
                world_matrix(parent) @ local if 0 <= parent < len(nodes) else local
            )
            visiting.remove(index)
            return world[index]

        return nodes, world_matrix, head, end
    return [], None, None, None


def rigid_placements(data, records):
    """World transforms por malla, con el nodo al que pertenece cada una.

    Igual que `rigid_instances`, pero devolviendo tambien el indice de nodo:
    hace falta para colgar cada malla de su articulacion al exportar.
    """
    meshes = {
        off + 128 + prefix: off + 128
        for tag, off, _, _, _ in records
        if tag == b"rdms"
        for prefix in (0, 32)
    }
    instances = {}
    nodes, world_matrix, head, end = node_hierarchy(data, records)
    for index, (at, kind, _, _) in enumerate(nodes):
        if kind != 25 or at + 40 > end:
            continue
        group_count = struct.unpack_from("<I", data, at + 32)[0]
        groups = _relative(data, at + 36)
        if group_count > 128 or not head <= groups <= end - group_count * 44:
            continue
        for group in range(group_count):
            block = groups + group * 44
            lod_count = struct.unpack_from("<I", data, block + 36)[0]
            lods = _relative(data, block + 40)
            if not 0 < lod_count <= 64 or not head <= lods <= end - lod_count * 8:
                continue
            for lod in range(lod_count):
                mesh = _relative(data, lods + lod * 8)
                if mesh in meshes:
                    placements = instances.setdefault(meshes[mesh], [])
                    # Keep lower LODs out of the same preview/export.
                    if lod == 0:
                        placements.append((index, world_matrix(index)))
    return instances


def rigid_instances(data, records):
    """Return world transforms keyed by the RDMS header address."""
    return {
        mesh: [matrix for _, matrix in placements]
        for mesh, placements in rigid_placements(data, records).items()
    }


def transform_vertices(vertices, matrix):
    normal_matrix = np.linalg.pinv(matrix[:3, :3]).T
    output = []
    for position, normal, uv, color in vertices:
        position = tuple((matrix @ (*position, 1.0))[:3])
        if normal is not None:
            vector = normal_matrix @ normal
            length = np.linalg.norm(vector)
            normal = tuple(vector / length) if length else (0, 0, 1)
        output.append((position, normal, uv, color))
    return output
