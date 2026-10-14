"""Actual GPU checks for low opacity, reversed faces and material tint."""

import tkinter as tk
import numpy as np

from ts2psp.rendering.opengl import Escena


def main():
    root = tk.Tk()
    root.withdraw()
    vertices = [
        ((-1, 0, -1), None, (0, 0), None),
        ((1, 0, -1), None, (1, 0), None),
        ((1, 0, 1), None, (1, 1), None),
        ((-1, 0, 1), None, (0, 1), None),
    ]
    try:
        for indices in ([0, 1, 2, 0, 2, 3], [2, 1, 0, 3, 2, 0]):
            scene = Escena(
                [
                    {
                        "vertices": vertices,
                        "indices": indices,
                        "color": (255, 0, 0, 64),
                        "alpha_mode": "BLEND",
                    }
                ],
                tipo="prop",
                fondo=(0, 0, 255),
            )
            scene.mostrar_grid = False
            image = scene.render(128, 128, 0, 0, 1)
            # Quarter-opacity red over blue must remain visible from both sides.
            np.testing.assert_allclose(image.getpixel((64, 64)), (64, 0, 191), atol=2)
        image = np.full((1, 1, 4), 255, dtype=np.uint8)
        scene = Escena(
            [
                (
                    "shared",
                    {
                        "vertices": vertices,
                        "indices": indices,
                        "color": (51, 102, 153, 255),
                    },
                )
            ],
            {"shared": image},
            tipo="prop",
        )
        scene.mostrar_grid = False
        np.testing.assert_allclose(
            scene.render(128, 128, 0, 0, 1).getpixel((64, 64)), (51, 102, 153), atol=2
        )
        print("Prop GPU: partial opacity, both face directions and textured tint OK")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
