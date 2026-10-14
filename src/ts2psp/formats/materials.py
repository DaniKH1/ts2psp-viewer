"""Resolve prop shaders to their referenced SURF, independent of file order."""

import math
import struct
import io

from PIL import Image

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
        opacity = struct.unpack_from("<f", data, head + 0x40)[0]
        if not math.isfinite(opacity) or not -1e-6 <= opacity <= 1 + 1e-6:
            raise ValueError("Invalid prop material opacity")
        opacity = min(1.0, max(0.0, opacity))
        material = {
            "texture": texture,
            "surface_address": surface_address,
            "base_color": (*rgb, opacity),
            "shader_index": index,
            "texture_alpha": bool(
                texture
                and Image.open(io.BytesIO(texture[1]))
                .convert("RGBA")
                .getchannel("A")
                .getextrema()[0]
                < 255
            ),
        }
        for prefix in (0, 32):
            shaders[off + 128 + prefix] = material
    return shaders


def assign_material(part, material, uvs):
    """Store one material per mesh part; repeating UVs stay repeating on the GPU."""
    material = material or {}
    part["texture"] = material.get("texture")
    part["base_color"] = material.get("base_color", (1, 1, 1, 1))
    part["material_kind"] = "texture" if part["texture"] else "solid_color"
    if part["base_color"][3] < 1 or material.get("texture_alpha"):
        part["alpha_mode"] = "BLEND"
    part["color"] = tuple(round(v * 255) for v in part["base_color"])
    part["repeat_texture"] = any(
        uv is not None and any(v < -0.001 or v > 1.001 for v in uv) for uv in uvs
    )
    if material.get("surface_address") is not None and part["texture"] is None:
        part["texture_warning"] = "Referenced SURF could not be decoded"
    if not material:
        part["texture_warning"] = "Mesh shader could not be resolved"
