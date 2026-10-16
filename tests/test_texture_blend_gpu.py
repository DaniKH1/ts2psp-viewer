"""Check terrain layer coverage, UV sets and GL state on the actual GPU."""

import io
import tkinter as tk
from unittest.mock import patch

import numpy as np
from PIL import Image

from ts2psp.rendering.props import prop_scene
from ts2psp.exporting.texture_blend import bake_texture_blend
import ts2psp.rendering.opengl as gl


def png(pixels):
    output = io.BytesIO()
    Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(output, format="PNG")
    return output.getvalue()


def main():
    root = tk.Tk()
    root.withdraw()
    first = ("first", png([[(255, 0, 0, 255)]]))
    second = ("second", png([[(0, 0, 255, 255), (255, 255, 0, 255)]]))
    positions = ((-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1))
    frames = 0
    try:
        for index, (weight, uv, expected) in enumerate(
            (
                (0, (0.25, 0.5), (0, 0, 255)),
                (0.5, (0.25, 0.5), (128, 0, 128)),
                (1, (0.25, 0.5), (255, 0, 0)),
                (0, (0.75, 0.5), (255, 255, 0)),
            )
        ):
            part = {
                "name": "layered",
                "vertices": [
                    (p, (0, 1, 0), (0, 0), (1, 1, 1, weight)) for p in positions
                ],
                "indices": [0, 1, 2, 0, 2, 3],
                "color": (255, 255, 255, 255),
                "alpha_mode": "OPAQUE",
                "texture": first,
                "texture_layers": [{"texture": first}, {"texture": second}],
                "texture_blend": True,
                "blend_weights": [weight] * 4,
                "secondary_uvs": [uv] * 4,
            }
            scene = prop_scene([part], ("blend-test", index))
            scene.mostrar_grid = False
            geometry, coords = scene.posiciones, scene._blend_coords
            with patch.object(
                scene, "_subir_textura", wraps=scene._subir_textura
            ) as upload:
                for enabled in (True, False, True):
                    scene.fijar_vertex_colors(enabled)
                    image = scene.render(128, 128, 0, 0, 1)
                    np.testing.assert_allclose(
                        image.getpixel((64, 64)), expected, atol=2
                    )
                    assert (
                        scene.posiciones is geometry and scene._blend_coords is coords
                    )
                    frames += 1
                assert upload.call_count == 0
        part["vertices"] = [
            (p, (0, 1, 0), uv, (1, 1, 1, weight))
            for p, uv, weight in zip(
                positions, ((0, 0), (2, 0), (2, 2), (0, 2)), (0, 0.25, 0.75, 1)
            )
        ]
        part["blend_weights"] = [0, 0.25, 0.75, 1]
        part["secondary_uvs"] = [(0, 0), (0, 3), (3, 3), (3, 0)]
        part["repeat_texture"] = True
        source = prop_scene([part], "source-for-atlas")
        baked = prop_scene([bake_texture_blend(part)], "portable-atlas")
        for scene in (source, baked):
            scene.mostrar_grid = False
            scene.fijar_vertex_colors(True)
        reference = np.asarray(source.render(256, 256, 0, 0, 1), dtype=float)
        portable = np.asarray(baked.render(256, 256, 0, 0, 1), dtype=float)
        assert np.abs(reference - portable).mean() < 2.0, np.abs(
            reference - portable
        ).mean()
        plain = {
            "name": "plain",
            "vertices": [(p, None, None, None) for p in positions],
            "indices": [0, 1, 2, 0, 2, 3],
            "color": (0, 255, 0, 255),
        }
        scene = prop_scene([plain], "after-layered")
        scene.mostrar_grid = False
        np.testing.assert_array_equal(
            scene.render(128, 128, 0, 0, 1).getpixel((64, 64)), (0, 255, 0)
        )
        print(
            f"GPU terrain: {frames} layer frames, independent UVs, no holes or reloads; GL state restored"
        )
    finally:
        gl.forget_all()
        root.destroy()


if __name__ == "__main__":
    main()
