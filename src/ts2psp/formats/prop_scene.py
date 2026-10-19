"""Read the static NODE hierarchy and its RDMS mesh instances."""

import math
import struct
from dataclasses import dataclass

import numpy as np


def _relative(data, at):
    return at + struct.unpack_from("<i", data, at)[0]


def _header_sizes(data):
    """Prefer the container's declared header over layout guesses."""
    declared = struct.unpack_from("<I", data, 12)[0] if len(data) >= 16 else 0
    return (
        tuple(dict.fromkeys((declared, 128, 160)))
        if declared in (96, 128, 160)
        else (128, 160)
    )


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
    return node[1] == 10 and data[at : at + 16].split(b"\0", 1)[0] in (
        b"transform",
        b"joint",
    )


def is_joint(data, node):
    at = _relative(data, node[0])
    return data[at : at + 6] == b"joint\0"


def node_name(data, node):
    at = _relative(data, node[0] + 4)
    return data[at : at + 256].split(b"\0", 1)[0].decode("ascii", "replace")


def rest_values(data, node):
    values = list(transform_values(data, node))
    # The accessory's stored assembly offsets separate its lid and housing.
    # Animation evaluation deliberately keeps the original authored values.
    if b"objects/accessories_perks/fx_trackingdevice\0" in data and node_name(
        data, node
    ) in ("device", "flap"):
        values[3] = 0.0
    return values


def _quaternion_matrix(values):
    x, y, z, w = values
    norm = x * x + y * y + z * z + w * w
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("Invalid NODE joint quaternion")
    k = 2.0 / norm
    return np.array(
        (
            (1 - k * (y * y + z * z), k * (x * y - z * w), k * (x * z + y * w)),
            (k * (x * y + z * w), 1 - k * (x * x + z * z), k * (y * z - x * w)),
            (k * (x * z - y * w), k * (y * z + x * w), 1 - k * (x * x + y * y)),
        )
    )


def mesh_is_helper(data, nodes, index, textured=False):
    seen = set()
    while 0 <= index < len(nodes):
        if index in seen:
            raise ValueError("Cyclic helper ancestry")
        seen.add(index)
        name = node_name(data, nodes[index]).rsplit(":", 1)[-1].casefold()
        if (
            name == "__maya_only__"
            or name.startswith("pickablepolys")
            or (not textured and name.startswith("footprintpolygon"))
        ):
            return True
        index = nodes[index][2]
    return False


def transform_pivot(data, node):
    return np.array(struct.unpack_from("<3f", data, node[0] + 56))


def transform_matrix(data, node, values=None, parent_scale=None):
    at = node[0]
    if values is None:
        values = transform_values(data, node)
    pivots = struct.unpack_from("<12f", data, at + 32)
    if not all(math.isfinite(value) for value in (*values, *pivots)):
        raise ValueError("Nonfinite NODE transform")
    if is_joint(data, node):
        # Joint records store rotate-axis and joint-orient quaternions separately.
        rotate_axis = _quaternion_matrix(struct.unpack_from("<4f", data, at + 88))
        orient = _quaternion_matrix(struct.unpack_from("<4f", data, at + 104))
        rotation_values = (*values[:7], 1.0, 1.0, 1.0)
        rotation = _transform(rotation_values)[:3, :3]
        matrix = np.eye(4)
        matrix[:3, :3] = (orient @ rotation @ rotate_axis) * values[7:10]
        if parent_scale is not None and struct.unpack_from("<I", data, at + 84)[0]:
            scale = np.asarray(parent_scale)
            inverse = np.divide(1.0, scale, out=np.zeros(3), where=scale != 0)
            matrix[:3, :3] = inverse[:, None] * matrix[:3, :3]
        matrix[:3, 3] = values[1:4]
        return matrix
    # Stored order is scale pivot/offset, then rotate pivot/offset. Rotation
    # compensation belongs outside R; swapping them separates handles/panels.
    return _transform(values, pivots[6:9], pivots[:3], pivots[9:12], pivots[3:6])


def _node_section(data, off, size, end, rest_pose=True):
    """Los nodos de una seccion `node`, con su tabla de matrices del mundo."""
    for header in _header_sizes(data):
        head = off + header
        if head + 8 > end:
            continue
        count = struct.unpack_from("<I", data, head)[0]
        table = _relative(data, head + 4)
        if 0 < count <= 4096 and head <= table <= end - 4 * count:
            break
    else:
        return None
    nodes = []
    for index in range(count):
        at = _relative(data, table + index * 4)
        if not head <= at <= end - 32:
            # Un registro ilegible no se salta: `parent` es un indice sobre
            # esta misma lista, asi que quitarlo descuadaria todos los de
            # abajo. Se deja un marcador sin transformada, que para un nodo
            # suelto es lo mismo que no colocarlo. Se apunta a `head` y no al
            # valor leido: una direccion a la basura no la debe desreferenciar
            # nadie mas abajo.
            nodes.append((head, 0, -1, np.eye(4)))
            continue
        kind = struct.unpack_from("<I", data, at + 20)[0]
        parent = struct.unpack_from("<i", data, at + 28)[0]
        local = np.eye(4)
        if is_transform(data, (at, kind, parent, local)):
            payload = _relative(data, at + 8)
            # En un nivel hay unos pocos nodos cuya tabla de transformadas
            # cae justo detras del final de la seccion de nodos, unos 16
            # bytes mas alla de donde acaba `size`. Con la comprobacion
            # estricta, una sola de esas echaba abajo las 361 colocaciones
            # del nivel entero y el mapa salia entero amontonado en el
            # origen. Se pasa en vez de reventar: son nodos que no llevan
            # mallas (las que las llevan son de tipo 25 y si comprueban
            # bien), y quedarse sin transformada es mejor que perder el
            # nivel. Si los numeros no son finitos, tambien.
            if head <= payload <= end - 40 and at + 80 <= end:
                values = struct.unpack_from("<10f", data, payload)
                if all(math.isfinite(value) for value in values):
                    local = transform_matrix(
                        data,
                        (at, kind, parent, local),
                        (
                            rest_values(data, (at, kind, parent, local))
                            if rest_pose
                            else values
                        ),
                    )
        nodes.append((at, kind, parent, local))
    for index, node in enumerate(nodes):
        parent = node[2]
        if (
            head <= node[0] <= end - 32
            and is_joint(data, node)
            and 0 <= parent < len(nodes)
            and is_transform(data, nodes[parent])
        ):
            scale = transform_values(data, nodes[parent])[7:10]
            nodes[index] = (
                *node[:3],
                transform_matrix(data, node, parent_scale=scale),
            )
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


def _node_sections(data, records, rest_pose=True):
    """Cada seccion `node` del recurso, con el final que le toca de verdad.

    El tamaño que declara la cabecera se queda corto: en los niveles, la tabla de
    transformadas de los ultimos nodos cae detras, y con el limite declarado una
    sola de esas entradas echaba abajo las colocaciones del nivel entero y el
    mapa salia amontonado en el origen.
    """
    finales = sorted(off for _, off, _, _, _ in records)
    for tag, off, size, _, _ in records:
        if tag != b"node":
            continue
        # `records` no tiene por que venir en orden de archivo, asi que el
        # siguiente se busca por valor y no por posicion.
        siguiente = next((o for o in finales if o > off), len(data))
        end = min(len(data), max(off + _header_sizes(data)[0] + size, siguiente))
        seccion = _node_section(data, off, size, end, rest_pose=rest_pose)
        if seccion is not None:
            yield seccion


def node_hierarchy(data, records, rest_pose=True):
    """Return NODE records, lazy world-matrix lookup and section bounds.

    Solo la primera seccion `node`, que es lo que tiene un prop. Un nivel
    reparte su grafo en hasta 31, y para esos esta `node_hierarchies`.

    Only visited branches are validated: nonspatial records can contain cycles.
    """
    for seccion in _node_sections(data, records, rest_pose=rest_pose):
        return seccion
    return [], None, None, None


def node_hierarchies(data, records):
    """Todas las secciones `node` del recurso, una por seccion.

    Un nivel reparte su grafo en hasta 31 secciones `node`. Con mirar solo la
    primera se perdian las 361 colocaciones de las mallas, y las farolas, vallas
    y bancos del nivel salian todos amontonados en el origen.
    """
    return [s for s in _node_sections(data, records) if s is not None]


def daynight_light_profile(data, records, light_name="DayNightLight"):
    """Return a directional light's authored transform and RGBA settings.

    Maya light transforms point along local -Z. Diffuse shading uses the
    opposite vector, from a surface toward the source. The light shape stores
    RGB and intensity in its attribute block immediately after the leading
    Maya plug header.
    """
    for nodes, world_matrix, _, _ in node_hierarchies(data, records):
        for index, node in enumerate(nodes):
            if node[1] != 10 or not is_transform(data, node):
                continue
            name = node_name(data, node).rsplit(":", 1)[-1]
            if name.casefold() != light_name.casefold():
                continue
            direction = world_matrix(index)[:3, :3] @ np.array((0.0, 0.0, 1.0))
            length = float(np.linalg.norm(direction))
            if not math.isfinite(length) or length <= 1e-8:
                continue
            shape = next(
                (
                    child
                    for child in nodes
                    if child[1] == 10
                    and child[2] == index
                    and node_name(data, child).rsplit(":", 1)[-1].casefold()
                    == f"{light_name}shape".casefold()
                ),
                None,
            )
            if shape is None:
                continue
            attributes = _relative(data, shape[0] + 8)
            if attributes < 0 or attributes + 32 > len(data):
                continue
            color_intensity = struct.unpack_from("<4f", data, attributes + 16)
            if not all(math.isfinite(value) for value in color_intensity):
                continue
            return {
                "direction": tuple(float(value / length) for value in direction),
                "color": tuple(float(value) for value in color_intensity[:3]),
                "intensity": float(color_intensity[3]),
            }
    return None


def daynight_light_direction(data, records):
    """Backward-compatible access to the authored daytime light vector."""
    profile = daynight_light_profile(data, records)
    return profile["direction"] if profile else None


_DAYNIGHT_LIGHT_GROUPS = {
    0x30002: "DayNightLight",
    0x70002: "DayNightLight1",
    0x90002: "DayNightLight2",
}
_DAYNIGHT_TRACK_CHANNELS = {0x403: 0, 0x503: 1, 0x603: 2, 0x703: 3}


def _sample_daynight_curve(keys, frame):
    """Sample one authored 3-key cubic curve using its stored tangents."""
    if not keys:
        return None
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    for left, right in zip(keys, keys[1:]):
        x0, y0, _, out_tangent = left
        x1, y1, in_tangent, _ = right
        if x0 <= frame <= x1:
            span = x1 - x0
            if span <= 0:
                return y1
            t = (frame - x0) / span
            h00 = 2 * t**3 - 3 * t**2 + 1
            h10 = t**3 - 2 * t**2 + t
            h01 = -2 * t**3 + 3 * t**2
            h11 = t**3 - t**2
            return (
                h00 * y0
                + h10 * span * out_tangent
                + h01 * y1
                + h11 * span * in_tangent
            )
    return keys[-1][1]


def _daynight_frame(hour):
    """Map the original day/night state schedule to the rig's 0/30/60 keys."""
    hour = float(hour) % 24.0
    if 7.0 <= hour < 8.0:  # Dawn: night key to day key.
        return 30.0 + 30.0 * (hour - 7.0)
    if 21.0 <= hour < 22.0:  # Dusk: day key to night key.
        return 30.0 * (hour - 21.0)
    if 8.0 <= hour < 21.0:
        return 0.0
    return 30.0


def daynight_rig_profiles(data, records, hour=12.0):
    """Read the three original DNL lights and sample their day/night curves.

    The level rig stores light transforms and a sequencer with 12 cubic curves:
    RGB plus intensity for each of three directional lights. Values and tangent
    data are read from the source resource; only `hour` selects a point on those
    curves.
    """
    profiles = {}
    for name in _DAYNIGHT_LIGHT_GROUPS.values():
        profile = daynight_light_profile(data, records, name)
        if profile:
            profiles[name] = {**profile, "color": list(profile["color"])}

    sequencer = None
    for nodes, _, _, _ in node_hierarchies(data, records):
        sequencer = next(
            (
                node
                for node in nodes
                if node[1] == 10
                and node_name(data, node).rsplit(":", 1)[-1].casefold()
                == "day_night_sequencer"
            ),
            None,
        )
        if sequencer:
            break
    if sequencer is None or sequencer[0] + 20 > len(data):
        return profiles

    table = _relative(data, sequencer[0] + 16)
    if table < 0 or table + 12 * 24 > len(data):
        return profiles
    curves = {}
    for index in range(12):
        row = table + index * 24
        descriptor = row + 4 + struct.unpack_from("<i", data, row + 4)[0]
        if descriptor < 0 or descriptor + 12 > len(data):
            continue
        _, interpolation, key_offset = struct.unpack_from("<fIi", data, descriptor)
        key_data = descriptor + 8 + key_offset
        if interpolation != 3 or key_data < 0 or key_data + 3 * 16 > len(data):
            continue
        keys = [struct.unpack_from("<4f", data, key_data + key * 16) for key in range(3)]
        track_id, group = struct.unpack_from("<II", data, row + 12)
        light_name = _DAYNIGHT_LIGHT_GROUPS.get(group)
        channel = _DAYNIGHT_TRACK_CHANNELS.get(track_id)
        if light_name is not None and channel is not None:
            curves.setdefault(light_name, {})[channel] = keys

    frame = _daynight_frame(hour)
    for name, profile in profiles.items():
        for channel, keys in curves.get(name, {}).items():
            value = _sample_daynight_curve(keys, frame)
            if value is None:
                continue
            if channel < 3:
                profile["color"][channel] = float(value)
            else:
                profile["intensity"] = float(value)
    return profiles


@dataclass
class RigidPlacement:
    node: int
    matrix: np.ndarray
    nodes: list
    resource: str = ""
    instance: str = ""


def _resource_path(path):
    path = path.replace("\\", "/").casefold()
    return path[:-3] if path.endswith(".ma") else path


def _string(data, at):
    if not 0 <= at < len(data):
        return ""
    end = data.find(b"\0", at, min(len(data), at + 1024))
    if end < 0:
        return ""
    return data[at:end].decode("ascii", "replace")


def _graph_resources(data, records, graphs):
    """NDBG identifies the Maya resource whose NODE section follows it."""
    sources = {}
    ordered = sorted(records, key=lambda row: row[1])
    for index, (tag, off, size, _, _) in enumerate(ordered):
        if tag != b"ndbg" or size < 12:
            continue
        head = off + _header_sizes(data)[0]
        if head + size > len(data):
            continue
        source = _resource_path(_string(data, _relative(data, head)))
        following = next(
            (row for row in ordered[index + 1 :] if row[0] in (b"node", b"ndbg")), None
        )
        if following is None or following[0] != b"node":
            continue
        for graph in graphs:
            if graph[2] - following[1] in _header_sizes(data):
                sources[graph[2]] = source
                break
    return sources


def _graph_references(data, graph):
    nodes, world, _, end = graph
    for index, node in enumerate(nodes):
        at, kind, _, _ = node
        if kind != 31 or at + 44 > end:
            continue
        path = _resource_path(_string(data, _relative(data, at + 40)))
        if not path or "/" not in path:
            continue
        yield index, path, world(index)


def level_prop_references(data, records):
    """Authored placements in PSP coordinates (X/Y ground, Z up)."""
    graphs = node_hierarchies(data, records)
    by_head = {graph[2]: graph for graph in graphs}
    sources = _graph_resources(data, records, graphs)
    targets = {path: head for head, path in sources.items()}
    result = []
    for tag, off, size, _, _ in records:
        head = off + _header_sizes(data)[0]
        if tag != b"levl" or size < 8 or head + 8 > len(data):
            continue
        graph = by_head.get(_relative(data, head + 4))
        if graph is None:
            continue
        for index, path, matrix in _graph_references(data, graph):
            result.append(
                {
                    "node": index,
                    "name": node_name(data, graph[0][index]),
                    "resource": path,
                    "position": matrix[:3, 3].tolist(),
                    "matrix": matrix.tolist(),
                    "resolved": path in targets,
                    "graph": graph[2],
                    "target_graph": targets.get(path),
                }
            )
    return result


def level_spawn_points(data, records):
    """Named character/NPC spawn transforms authored in the level NODE graph."""
    graphs = node_hierarchies(data, records)
    by_head = {graph[2]: graph for graph in graphs}
    result = []
    for tag, off, size, _, _ in records:
        head = off + _header_sizes(data)[0]
        if tag != b"levl" or size < 8 or head + 8 > len(data):
            continue
        graph = by_head.get(_relative(data, head + 4))
        if graph is None:
            continue
        nodes, world, graph_head, end = graph
        player_locator_groups = {
            index
            for index, node in enumerate(nodes)
            if is_transform(data, node)
            and node_name(data, node).casefold() == "playerlocators"
        }
        for index, node in enumerate(nodes):
            at, _, _, _ = node
            if not is_transform(data, node) or at + 24 > end:
                continue
            name = node_name(data, node)
            folded = name.casefold()
            is_hidden_spot = "hidden" in folded
            # Character start locators are grouped under playerlocators. Some
            # (including Library's Lincoln/Adolfo locator) do not contain the
            # words "start" or "spawn" in their authored node name.
            is_player_locator = node[2] in player_locator_groups
            if (
                "start" not in folded
                and "spawn" not in folded
                and not is_hidden_spot
                and not folded.startswith("objects:zombiecontroller")
                and not is_player_locator
            ):
                continue
            matrix = world(index)
            point = {
                "node": index,
                "name": name,
                "position": matrix[:3, 3].tolist(),
                "matrix": matrix.tolist(),
                "graph": graph_head,
                "kind": "hidden" if is_hidden_spot else "spawn",
            }
            if "lincoln" in folded:
                point["character_slug"] = "lincolnbroadsheet"
            result.append(point)
    # Some actors are represented by locators inside their prop's NODE graph,
    # rather than by a top-level level spawn transform. Graveyard's gothic
    # organ has a __ghost__ locator used for its apparition.
    for reference in level_prop_references(data, records):
        if reference.get("resource") != "objects/pianos/fx_piano_gothic":
            continue
        target = reference.get("target_graph")
        graph = by_head.get(target)
        if graph is None:
            continue
        nodes, local_world, graph_head, end = graph
        for index, node in enumerate(nodes):
            if node_name(data, node).casefold() != "__ghost__":
                continue
            at = node[0]
            if not is_transform(data, node) or at + 24 > end:
                continue
            matrix = np.asarray(reference["matrix"], dtype=np.float64) @ local_world(
                index
            )
            result.append(
                {
                    "node": index,
                    "name": "organ::__ghost__",
                    "position": matrix[:3, 3].tolist(),
                    "matrix": matrix.tolist(),
                    "graph": graph_head,
                }
            )
    return result


def level_camera_light_points(data, records, rig_data=None, rig_records=None):
    """Return authored camera and light transforms in the level and its DNL rig."""
    result = []
    seen = set()

    def add(kind, name, matrix):
        matrix = np.asarray(matrix, dtype=np.float64)
        position = matrix[:3, 3]
        key = (kind, name.casefold(), tuple(np.round(position, 4)))
        if key in seen:
            return next(
                item
                for item in result
                if item.get("kind") == kind
                and item["name"].casefold() == name.casefold()
                and tuple(np.round(item["position"], 4)) == key[2]
            )
        seen.add(key)
        marker = {
            "kind": kind,
            "name": name,
            "position": position.tolist(),
            "matrix": matrix.tolist(),
        }
        result.append(marker)
        return marker

    def scan(source, source_records, matrix_for_graph=None):
        if source is None or source_records is None:
            return
        for nodes, world, _, _ in node_hierarchies(source, source_records):
            light_shapes = {}
            for index, node in enumerate(nodes):
                if node[1] != 10 or is_transform(source, node):
                    continue
                shape_name = node_name(source, node).casefold()
                parent = node[2]
                if (
                    parent is not None
                    and 0 <= parent < len(nodes)
                    and "light" in shape_name
                    and "shape" in shape_name
                ):
                    light_shapes.setdefault(parent, []).append(node)

            for index, node in enumerate(nodes):
                if not is_transform(source, node):
                    continue
                name = node_name(source, node)
                folded = name.casefold()
                matrix = world(index)
                if matrix_for_graph is not None:
                    matrix = matrix_for_graph @ matrix

                # FOV controls are children of the actual camera locator in
                # Kine and Monument. Use that parent once instead of drawing
                # dozens of coincident camera controls.
                if "fovcontrol" in folded:
                    parent = node[2]
                    if parent is not None and 0 <= parent < len(nodes):
                        parent_node = nodes[parent]
                        parent_name = node_name(source, parent_node)
                        parent_folded = parent_name.casefold()
                        if (
                            is_transform(source, parent_node)
                            and parent_folded.startswith("cam")
                            and parent_folded[3:].isdigit()
                        ):
                            parent_matrix = world(parent)
                            if matrix_for_graph is not None:
                                parent_matrix = matrix_for_graph @ parent_matrix
                            add("camera", parent_name, parent_matrix)
                    continue

                if (
                    "camera" in folded
                    and "shape" not in folded
                    and "group" not in folded
                    and folded != "objects:cameras"
                ):
                    add("camera", name, matrix)

                for shape in light_shapes.get(index, ()):
                    shape_name = node_name(source, shape).rsplit(":", 1)[-1]
                    shape_folded = shape_name.casefold()
                    light_identity = f"{folded} {shape_folded}"
                    light_type = next(
                        (
                            candidate
                            for candidate in ("ambient", "directional", "point", "spot")
                            if candidate in light_identity
                        ),
                        "directional"
                        if "daynightlight" in light_identity
                        else "unknown",
                    )
                    light_type_inferred = light_type == "unknown"
                    if light_type_inferred:
                        # Level resources often serialize Maya lights as generic
                        # `light` shapes. Those are local emitters; use point
                        # falloff until a more specific type is encoded.
                        light_type = "point"
                    attributes = _relative(source, shape[0] + 8)
                    if attributes < 0 or attributes + 32 > len(source):
                        continue
                    color_intensity = struct.unpack_from(
                        "<4f", source, attributes + 16
                    )
                    if not all(math.isfinite(value) for value in color_intensity):
                        continue
                    marker = add(
                        "light",
                        name,
                        matrix,
                    )
                    marker.update(
                        {
                            "light_type": light_type,
                            "light_type_inferred": light_type_inferred,
                            "color": [float(value) for value in color_intensity[:3]],
                            "intensity": float(color_intensity[3]),
                        }
                    )
                    if light_type == "directional":
                        # Directional emitters use their world-space local +Z
                        # axis (towards the source), never their translation.
                        direction = matrix[:3, :3] @ np.array((0.0, 0.0, 1.0))
                        length = float(np.linalg.norm(direction))
                        if math.isfinite(length) and length > 1e-8:
                            marker["direction"] = (direction / length).tolist()

    scan(data, records)

    if rig_data is not None and rig_records is not None:
        rig_reference = next(
            (
                reference
                for reference in level_prop_references(data, records)
                if reference.get("resource", "").startswith(
                    "objects/light_rigs/fx_dnl_"
                )
            ),
            None,
        )
        placement = (
            np.asarray(rig_reference["matrix"], dtype=np.float64)
            if rig_reference
            else np.eye(4)
        )
        scan(rig_data, rig_records, placement)
    return result


def rigid_placement_details(data, records):
    """Mesh owners plus referenced prop instances, retaining each local graph."""
    meshes = {
        off + 128 + prefix: off + 128
        for tag, off, _, _, _ in records
        if tag == b"rdms"
        for prefix in (0, 32)
    }
    instances = {}
    graphs = node_hierarchies(data, records)
    local_instances = {}
    # Todas las secciones, no solo la primera: un nivel tiene hasta 31 y las
    # mallas de un solo subgroupo aparecen en varias.
    for nodes, world_matrix, head, end in graphs:
        local_instances[head] = {}
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
                        placements = local_instances[head].setdefault(meshes[mesh], [])
                        # Keep lower LODs out of the same preview/export.
                        if lod == 0:
                            placements.append(
                                RigidPlacement(index, world_matrix(index), nodes)
                            )
    references = (
        level_prop_references(data, records)
        if any(row[0] == b"levl" for row in records)
        else []
    )
    if not references:
        for group in local_instances.values():
            for mesh, placements in group.items():
                instances.setdefault(mesh, []).extend(placements)
        return instances

    # Embedded object graphs are prototypes. Only reachable references create
    # instances; otherwise the same furniture also appears at the origin.
    for group in local_instances.values():
        for mesh in group:
            instances.setdefault(mesh, [])
    by_head = {graph[2]: graph for graph in graphs}
    sources = _graph_resources(data, records, graphs)
    targets = {path: head for head, path in sources.items()}

    def place(head, matrix, resource="", instance="", ancestors=()):
        if head in ancestors or len(ancestors) >= 32:
            return
        for mesh, placements in local_instances[head].items():
            instances[mesh].extend(
                RigidPlacement(p.node, matrix @ p.matrix, p.nodes, resource, instance)
                for p in placements
            )
        for index, path, local in _graph_references(data, by_head[head]):
            target = targets.get(path)
            if target is not None:
                name = node_name(data, by_head[head][0][index])
                place(
                    target,
                    matrix @ local,
                    path,
                    f"{instance}/{name}#{index}".lstrip("/"),
                    (*ancestors, head),
                )

    for head in dict.fromkeys(ref["graph"] for ref in references):
        place(head, np.eye(4))
    return instances


def rigid_placements(data, records):
    """Return each RDMS instance's owning node and world transform."""
    return {
        mesh: [(p.node, p.matrix) for p in placements]
        for mesh, placements in rigid_placement_details(data, records).items()
    }


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
