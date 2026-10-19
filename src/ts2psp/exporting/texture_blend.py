"""Bake two diffuse layers into portable glTF textures at export time."""

import io
import math
from pathlib import PurePosixPath

import numpy as np
from PIL import Image


def sample_texture(image, uv, repeat):
    """Match OpenGL's linear sampling, including wrapped texture borders."""
    height, width = image.shape[:2]
    xy = uv * (width, height) - 0.5
    low = np.floor(xy).astype(np.int64)
    f = xy - low
    x, y = low[..., 0], low[..., 1]
    if repeat:
        x0, x1, y0, y1 = x % width, (x + 1) % width, y % height, (y + 1) % height
    else:
        x0, x1 = x.clip(0, width - 1), (x + 1).clip(0, width - 1)
        y0, y1 = y.clip(0, height - 1), (y + 1).clip(0, height - 1)
    fx, fy = f[..., :1], f[..., 1:]
    return (1 - fy) * ((1 - fx) * image[y0, x0] + fx * image[y0, x1]) + fy * (
        (1 - fx) * image[y1, x0] + fx * image[y1, x1]
    )


def mix_layers(first, second, weight):
    # PSP vertex alpha marks coverage of the second diffuse layer.
    coverage = np.clip((1 - weight[..., None]) * first[..., 3:4], 0, 1)
    alpha = coverage + second[..., 3:4] * (1 - coverage)
    rgb = first[..., :3] * coverage + second[..., :3] * second[..., 3:4] * (
        1 - coverage
    )
    return np.concatenate((rgb / np.maximum(alpha, 1e-6), alpha), axis=-1)


def bake_texture_blend(part):
    """Keep geometry and lighting; put layer coverage in a separate PNG atlas."""
    if not part.get("texture_blend"):
        return part
    triangles = np.asarray(part["indices"], dtype=np.int64).reshape(-1, 3)
    columns = math.ceil(math.sqrt(len(triangles)))
    tile = max(8, min(128, 4096 // columns))
    rows = math.ceil(len(triangles) / columns)
    gutter, span = 2, tile - 5
    width, height = columns * tile, rows * tile
    atlas = np.zeros((height, width, 4), dtype=np.uint8)
    y, x = np.mgrid[:tile, :tile]
    bary = np.stack(
        (1 - (x + y - 2 * gutter) / span, (x - gutter) / span, (y - gutter) / span),
        axis=-1,
    )
    bary = np.maximum(bary, 0)
    bary /= bary.sum(axis=-1, keepdims=True)
    textures = [layer["texture"] for layer in part["texture_layers"]]
    images = [
        np.asarray(Image.open(io.BytesIO(t[1])).convert("RGBA"), dtype=np.float64) / 255
        for t in textures
    ]
    vertices = part["vertices"]
    uv0 = np.asarray([v[2] for v in vertices])
    uv1 = np.asarray(part.get("secondary_uvs") or uv0)
    weights = np.asarray(part["blend_weights"])
    repeat = part.get("repeat_texture", False)
    baked, local, source_uv0, source_uv1, source_weights = [], [], [], [], []
    for face, triangle in enumerate(triangles):
        a = sample_texture(images[0], bary @ uv0[triangle], repeat)
        b = sample_texture(images[1], bary @ uv1[triangle], repeat)
        pixels = mix_layers(a, b, bary @ weights[triangle])
        ox, oy = face % columns * tile, face // columns * tile
        atlas[oy : oy + tile, ox : ox + tile] = np.rint(pixels.clip(0, 1) * 255).astype(
            np.uint8
        )
        for corner, index in enumerate(triangle):
            pos, normal, _, color = vertices[index]
            uv = (
                (ox + gutter + 0.5 + (span if corner == 1 else 0)) / width,
                (oy + gutter + 0.5 + (span if corner == 2 else 0)) / height,
            )
            if color is not None:
                # The source RGB is not a tint for layered terrain; alpha is
                # consumed into the atlas above. Keep the baked texture neutral.
                baked_color = (1.0, 1.0, 1.0, 1.0)
            else:
                baked_color = None
            baked.append((pos, normal, uv, baked_color))
            if "local_vertices" in part:
                local.append(part["local_vertices"][index])
            source_uv0.append(uv0[index])
            source_uv1.append(uv1[index])
            source_weights.append(float(weights[index]))
    output = io.BytesIO()
    Image.fromarray(atlas).save(output, format="PNG")
    names = [t[0] for t in textures]
    label = "__".join(PurePosixPath(name).stem for name in names)
    result = {
        **part,
        "vertices": baked,
        "indices": list(range(len(baked))),
        "texture": (f"blends/{label}__{part['name']}.png", output.getvalue()),
        "texture_blend": False,
        "source_repeat_texture": repeat,
        "repeat_texture": False,
        "source_uv0": source_uv0,
        "source_uv1": source_uv1,
        "source_blend_weights": source_weights,
        "blend_weights": source_weights,
        "secondary_uvs": source_uv1,
        "baked_texture_layers": textures,
    }
    if local:
        # The rigid skin exporter replaces world positions with these positions.
        result["local_vertices"] = [
            (v[0], v[1], baked[i][2], baked[i][3]) for i, v in enumerate(local)
        ]
    return result
