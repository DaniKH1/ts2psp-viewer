from ts2psp.paths import resource_path
from ts2psp.ui.theme import SHAPE_TEXT_GRADIENT

"""Check the shape-key preset rendering.

Covers three things that are easy to break while tuning the look:
  * the unselected presets use the new texture, the selected ones the original
  * unselected numbers carry the #000081 -> #005d9e vertical gradient, while
    selected numbers stay flat white and carry no gradient
  * the diffuse shadow still darkens the edge of the glyph
"""
import tkinter as tk
from pathlib import Path

from PIL import Image

import ts2psp.app as app

STATES = ("normal", "hover", "selected", "selected_hover", "disabled")
BRIGHTNESS = {"normal": 1.0, "hover": 1.13, "selected": 1.16, "selected_hover": 1.28}
VALUES = (-1, 0, 1)
PROBE = (10, 10)  # inside the disc, clear of the glyph
WHITE_FILL = (230, 248, 228)  # #e6f8e4
TOLERANCE = 34
CANVAS = 40  # final size of a preset
SUPERSAMPLE = 120  # the theme renders at 40*3 before downscaling


def luma(pixel):
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def close(a, b, tolerance=TOLERANCE):
    return all(abs(x - y) <= tolerance for x, y in zip(a[:3], b[:3]))


def expected_body(state, new_ref, old_ref):
    """Colour the button body should have, allowing for the state tweaks."""
    reference = old_ref if state.startswith("selected") else new_ref
    if state == "disabled":
        grey = round(0.299 * reference[0] + 0.587 * reference[1] + 0.114 * reference[2])
        return (grey, grey, grey)
    factor = BRIGHTNESS[state]
    return tuple(min(255, round(c * factor)) for c in reference[:3])


def main():
    root = tk.Tk()
    root.withdraw()
    app._install_sims_theme(root)
    images = root._sims_shape_preset_images
    rasters = root._sims_shape_preset_rasters

    assert set(images) == set(VALUES), sorted(images)
    for value in VALUES:
        assert set(images[value]) == set(STATES), sorted(images[value])

    assets = resource_path("assets/ui")
    new = Image.open(assets / "shape_key_button_unselected.png").convert("RGBA")
    old = Image.open(assets / "shape_key_button.png").convert("RGBA")

    assert (
        new.getchannel("A").tobytes() == old.getchannel("A").tobytes()
    ), "textures differ in shape, not only in colour"
    assert new.tobytes() != old.tobytes(), "the two textures are identical"

    new_ref = new.getpixel((9, 9))
    old_ref = old.getpixel((9, 9))
    print(f"unselected texture ref {new_ref[:3]}")
    print(f"selected   texture ref {old_ref[:3]}\n")

    # --- the button body picks the right texture -------------------------------
    failures = []
    for value in VALUES:
        for state in STATES:
            got = rasters[value][state].convert("RGB").getpixel(PROBE)
            want = expected_body(state, new_ref, old_ref)
            ok = close(got, want)
            if not ok:
                failures.append((value, state, got, want))
    print(
        f"body texture: {len(VALUES) * len(STATES) - len(failures)}"
        f"/{len(VALUES) * len(STATES)} states OK"
    )
    if failures:
        raise SystemExit(f"wrong body texture: {failures}")

    # --- locate the glyph by differencing against the bare texture -------------
    def bare():
        return new.resize((SUPERSAMPLE, SUPERSAMPLE), Image.Resampling.LANCZOS).resize(
            (CANVAS, CANVAS), Image.Resampling.LANCZOS
        )

    reference = bare()

    def glyph_points(image):
        """Pixels of the rendered preset that differ from the bare texture."""
        points = []
        for y in range(CANVAS):
            for x in range(CANVAS):
                here = image.getpixel((x, y))
                if here[3] < 200:  # transparent, RGB is meaningless
                    continue
                there = reference.getpixel((x, y))
                if max(abs(a - b) for a, b in zip(here[:3], there[:3])) > 70:
                    points.append((x, y))
        return points

    gradient_top = SHAPE_TEXT_GRADIENT[0][1]
    gradient_bottom = SHAPE_TEXT_GRADIENT[-1][1]
    print(f"gradient: {gradient_top} -> {gradient_bottom}\n")

    for state in ("normal", "hover"):
        image = rasters[0][state]
        points = glyph_points(image)
        assert points, f"{state}: no glyph detected"
        top = min(points, key=lambda p: p[1])
        bottom = max(points, key=lambda p: p[1])
        upper = image.getpixel(top)[:3]
        lower = image.getpixel(bottom)[:3]
        print(f"{state:<9} glyph {top}={upper}  {bottom}={lower}")
        assert close(upper, gradient_top, 60), f"{state}: glyph top is not #000081-ish"
        assert close(
            lower, gradient_bottom, 60
        ), f"{state}: glyph bottom is not #005d9e-ish"
        assert luma(upper) < luma(
            lower
        ), f"{state}: gradient does not lighten downwards"

    for state in ("selected", "selected_hover"):
        image = rasters[0][state]
        points = glyph_points(image)
        assert points, f"{state}: no glyph detected"
        brightest = max((image.getpixel(p)[:3] for p in points), key=luma)
        print(f"{state:<9} brightest glyph pixel {brightest}")
        assert close(brightest, WHITE_FILL), f"{state}: selected fill is not flat white"
        # The gradient must not leak into the selected fill: neither of its
        # colours may appear anywhere in the glyph.
        for colour, name in (
            (gradient_top, "gradient top"),
            (gradient_bottom, "gradient bottom"),
        ):
            leaked = [p for p in points if close(image.getpixel(p)[:3], colour, 25)]
            assert not leaked, (
                f"{state}: {name} {colour} leaked into the fill " f"at {leaked[:3]}"
            )

    # --- the diffuse shadow ----------------------------------------------------
    shaded = rasters[0]["normal"].convert("RGB")
    bare_rgb = reference.convert("RGB")
    drops = []
    for y in range(CANVAS):
        for x in range(CANVAS):
            radius = ((x - 20) ** 2 + (y - 20) ** 2) ** 0.5
            if 9 <= radius <= 11:
                drops.append(
                    luma(bare_rgb.getpixel((x, y))) - luma(shaded.getpixel((x, y)))
                )
    mean_drop = sum(drops) / len(drops)
    print(f"\nmean darkening at the glyph edge: {mean_drop:.1f}/255")
    assert mean_drop > 2.5, f"shadow does not darken the glyph edge ({mean_drop:.1f})"

    root.destroy()
    print("\nall checks passed")


if __name__ == "__main__":
    main()
