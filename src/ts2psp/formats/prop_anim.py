"""Embedded NODE curves, explicit property links and pivot-aware prop poses."""

from bisect import bisect_right
import math
import struct
import zipfile

import numpy as np

from ts2psp.formats.props import sections, prop_joints
from ts2psp.formats.prop_scene import (
    node_hierarchy,
    transform_vertices,
    transform_values,
    transform_matrix,
    _relative,
    is_transform,
    is_joint,
    rest_values,
)


def prop_clip(data):
    """Read scalar curves with their source addresses and Hermite tangents."""
    nodes, _, head, end = node_hierarchy(data, sections(data))
    if head is None:
        return None
    table = _relative(data, head + 4)
    channels = []
    at = head + 32
    while at + 12 <= table:
        flags, count, offset = struct.unpack_from("<3i", data, at)
        if not 0 < count <= 4096:
            break
        keys = at + 8 + offset
        if not head <= keys <= end - 16 * count:
            break
        values = struct.unpack_from(f"<{4 * count}f", data, keys)
        times = values[::4]
        if not all(math.isfinite(x) for x in (*times, *values[1::4])):
            break
        if any(a >= b for a, b in zip(times, times[1:])):
            break
        channels.append(
            {
                "source": at,
                "flags": flags,
                "times": times,
                "values": values[1::4],
                "in_tangents": values[2::4],
                "out_tangents": values[3::4],
            }
        )
        at += 12
    if not channels:
        return None
    return {
        "duration": max(channel["times"][-1] for channel in channels),
        "channels": channels,
    }


def iter_prop_animations(path, rows):
    """Va soltando cada prop animado en vez de la lista entera.

    Recorrer los 767 .res son un par de segundos. Devolverlos de uno en uno
    deja que quien lo llame reparta el trabajo y pueda ir pintando la lista sin
    tener que esperar al final.
    """
    with zipfile.ZipFile(path) as archive:
        from ts2psp.formats.prop_assets import PropAssets

        assets = PropAssets(archive)
        for row in rows:
            data = archive.read(row["archivo_zip"])
            if b"bmsh" in data[:128]:
                for external in assets.animation_rows(row["ruta_interna"]):
                    yield {**row, "animation_name": external["name"]}
            clip = prop_clip(data)
            if clip:
                animated = {link["node"] for link in curve_bindings(data, clip)}
                visible = {bone["index"] for bone in prop_joints(data)[0]}
                if animated & visible:
                    yield row


def prop_animations(path, rows):
    return list(iter_prop_animations(path, rows))


def sample(channel, t):
    """Hermite interpolation; infinite outgoing tangents encode held values."""
    times, values = channel["times"], channel["values"]
    offset = 0.0
    if t > times[-1] and len(times) > 1:
        mode = (channel.get("flags", 0) >> 8) & 255
        period = times[-1] - times[0]
        if mode in (2, 3) and period > 0:
            cycles = math.floor((t - times[0]) / period)
            t = times[0] + (t - times[0]) % period
            if mode == 3:
                offset = cycles * (values[-1] - values[0])
    if t <= times[0]:
        return values[0] + offset
    if t >= times[-1]:
        return values[-1] + offset
    i = bisect_right(times, t) - 1
    dt = times[i + 1] - times[i]
    u = (t - times[i]) / dt
    incoming, outgoing = channel.get("in_tangents"), channel.get("out_tangents")
    if (channel.get("flags", 0) & 255) == 2:
        return values[i] + offset
    if incoming is None or outgoing is None:
        return values[i] + (values[i + 1] - values[i]) * u + offset
    a, b = outgoing[i], incoming[i + 1]
    if not math.isfinite(a) or not math.isfinite(b):
        return values[i] + offset
    return (
        (2 * u**3 - 3 * u**2 + 1) * values[i]
        + (u**3 - 2 * u**2 + u) * dt * a
        + (-2 * u**3 + 3 * u**2) * values[i + 1]
        + (u**3 - u**2) * dt * b
        + offset
    )


def curve_bindings(data, clip):
    """Follow each transform's linked list, including multiple XYZ properties."""
    if clip is None:
        return ()
    nodes, _, head, end = node_hierarchy(data, sections(data))
    curves = {channel["source"]: i for i, channel in enumerate(clip["channels"])}
    output = []
    for index, (at, kind, _, _) in enumerate(nodes):
        if not is_transform(data, (at, kind, None, None)):
            continue
        pointer = at + 24
        seen = set()
        while struct.unpack_from("<i", data, pointer)[0] != 1:
            link = _relative(data, pointer)
            if not head <= link <= end - 24 or link in seen:
                raise ValueError("Invalid NODE property link")
            seen.add(link)
            descriptor, target = struct.unpack_from("<2I", data, link + 12)
            component = (descriptor >> 8) & 255
            channel = curves.get(_relative(data, link + 4))
            if target >> 16 != index:
                raise ValueError("NODE property link targets another node")
            if (
                channel is not None
                and descriptor & 255 in (3, 6)
                and 1 <= component <= 9
            ):
                factor = struct.unpack_from("<f", data, link + 8)[0]
                if not math.isfinite(factor):
                    raise ValueError("Nonfinite NODE channel factor")
                output.append(
                    {
                        "channel": channel,
                        "node": index,
                        "component": component,
                        "factor": factor,
                    }
                )
            pointer = link + 20
    return tuple(output)


def reparte_canales(data, clip):
    """Compatibility map for callers; axes and factors live in curve_bindings."""
    return {
        binding["channel"]: binding["node"] for binding in curve_bindings(data, clip)
    }


class PropAnimator:
    """Compile once per selection, then evaluate local properties per frame."""

    def __init__(self, data, clip=None):
        self.data = data
        self.clip = clip if clip is not None else prop_clip(data)
        self.nodes, self.rest_world, _, _ = node_hierarchy(
            data, sections(data), rest_pose=False
        )
        self.rest_overrides = {
            i
            for i, node in enumerate(self.nodes)
            if is_transform(data, node)
            and tuple(rest_values(data, node)) != tuple(transform_values(data, node))
        }
        self.bindings = curve_bindings(data, self.clip)
        self.values = {
            i: transform_values(data, node)
            for i, node in enumerate(self.nodes)
            if is_transform(data, node)
        }
        self.by_node = {}
        for binding in self.bindings:
            self.by_node.setdefault(binding["node"], []).append(binding)

    def world_matrices(self, t, indices):
        cache, visiting = {}, set()

        def world(index):
            if index in cache:
                return cache[index]
            if index in visiting:
                raise ValueError("Cyclic animated NODE hierarchy")
            visiting.add(index)
            node = self.nodes[index]
            local = self.local_matrix(index, t)
            parent = node[2]
            cache[index] = (
                world(parent) @ local if 0 <= parent < len(self.nodes) else local
            )
            visiting.remove(index)
            return cache[index]

        return {i: world(i) for i in indices}

    def local_matrix(self, index, t):
        joint = is_joint(self.data, self.nodes[index])
        if index not in self.by_node and not joint:
            return self.nodes[index][3]
        values = list(self.values[index])
        for binding in self.by_node.get(index, ()):
            # Curves replace the stored property; they are not extra rotations.
            values[binding["component"]] = (
                sample(self.clip["channels"][binding["channel"]], t) * binding["factor"]
            )
        parent = self.nodes[index][2]
        scale = None
        if joint and parent in self.values:
            parent_values = list(self.values[parent])
            for binding in self.by_node.get(parent, ()):
                if 7 <= binding["component"] <= 9:
                    parent_values[binding["component"]] = (
                        sample(self.clip["channels"][binding["channel"]], t)
                        * binding["factor"]
                    )
            scale = parent_values[7:10]
        return transform_matrix(
            self.data, self.nodes[index], values, parent_scale=scale
        )

    def joint_local_matrix(self, bone, by_index, t):
        matrix = (
            transform_matrix(
                self.data,
                self.nodes[bone["index"]],
                rest_values(self.data, self.nodes[bone["index"]]),
            )
            if t is None
            else self.local_matrix(bone["index"], t)
        ).copy()
        offset = np.eye(4)
        offset[:3, 3] = bone["pivot"]
        matrix = matrix @ offset
        if bone["parent"] in by_index:
            matrix[:3, 3] -= by_index[bone["parent"]]["pivot"]
        return matrix

    def pose(self, parts, t, bones=()):
        indices = {part.get("node", -1) for part in parts} | {
            bone["index"] for bone in bones
        }
        indices.discard(-1)
        matrices = self.world_matrices(t, indices)
        output = []
        for part in parts:
            index = part.get("node", -1)
            if index not in matrices:
                output.append(part)
                continue
            if "local_vertices" in part:
                vertices = transform_vertices(part["local_vertices"], matrices[index])
            else:
                delta = matrices[index] @ np.linalg.inv(self.rest_world(index))
                vertices = transform_vertices(part["vertices"], delta)
            output.append({**part, "vertices": vertices})
        positions = {
            bone["index"]: (matrices[bone["index"]] @ (*bone["pivot"], 1.0))[:3]
            for bone in bones
        }
        return output, positions


def pose(parts, data, clip, t, reparto=None):
    """Evaluate the actual resource bindings; reparto is kept for older callers."""
    animator = PropAnimator(data, clip)
    parts, _ = animator.pose(parts, t)
    matrices = animator.world_matrices(
        t, (i for i, node in enumerate(animator.nodes) if is_transform(data, node))
    )
    return parts, {i: matrix[:3, 3] for i, matrix in matrices.items()}
