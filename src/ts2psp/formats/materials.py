"""Resolve prop shaders to their referenced SURF, independent of file order."""

import math
import struct

from ts2psp.formats.surfaces import decode_surface


def prop_materials(data, records):
    surfaces, shaders = {}, {}
    for index, (tag, off, size, _, _) in enumerate(records):
        if tag != b"surf":
            continue
        try:
            png = decode_surface(data, off, size)
        except (ValueError, struct.error):
            png = None
        texture = (f"surface_{index}", png) if png else None
        for prefix in (0, 32):
            surfaces[off + 128 + prefix] = texture
    for index, (tag, off, size, _, _) in enumerate(records):
        if tag != b"gshd":
            continue
        head = off + 128
        texture, surface_address = None, None
        for prefix in (0, 32):
            at = head + prefix + 0x64
            if at + 4 > min(len(data), off + 128 + size):
                continue
            target = at + struct.unpack_from("<i", data, at)[0]
            if target in surfaces:
                head += prefix
                texture, surface_address = surfaces[target], target
                break
        rgb = struct.unpack_from("<3f", data, head + 8)
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in rgb):
            rgb = (1, 1, 1)
        material = {
            "texture": texture,
            "surface_address": surface_address,
            "base_color": (*rgb, 1.0),
            "shader_index": index,
        }
        for prefix in (0, 32):
            shaders[off + 128 + prefix] = material
    return shaders


def assign_material(part, material, uvs):
    """Store one material per mesh part; repeating UVs stay repeating on the GPU."""
    material = material or {}
    part["texture"] = material.get("texture")
    part["base_color"] = (
        (1, 1, 1, 1) if part["texture"] else material.get("base_color", (1, 1, 1, 1))
    )
    part["color"] = tuple(round(v * 255) for v in part["base_color"])
    part["repeat_texture"] = any(
        uv is not None and any(v < -0.001 or v > 1.001 for v in uv) for uv in uvs
    )
    if material.get("surface_address") is not None and part["texture"] is None:
        part["texture_warning"] = "Referenced SURF could not be decoded"
