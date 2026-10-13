"""The highlight must depend on whether the green button is showing.

When the button is selected the green texture is visible, and hover/press
brighten that same green, because jumping to another texture changed the
colour under the mouse. When the normal (lavender) button is showing, hover
uses the dark blue edge highlight and pressing flashes the active texture.
"""

import tkinter as tk
from collections import Counter

import ts2psp.app as app

STATES = ("normal", "active", "pressed")


def luma(pixel):
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def hue(pixel):
    r, g, b = pixel[:3]
    high, low = max(r, g, b), min(r, g, b)
    if high == low:
        return 0.0
    delta = high - low
    if high == r:
        value = ((g - b) / delta) % 6
    elif high == g:
        value = (b - r) / delta + 2
    else:
        value = (r - g) / delta + 4
    return value * 60


def hue_drift(a, b):
    drift = abs(a - b) % 360
    return min(drift, 360 - drift)


def body(image):
    pixels = [p for p in image.convert("RGBA").getdata() if p[3] > 250]
    assert pixels, "no opaque pixels"
    mean = sum(map(luma, pixels)) / len(pixels)
    mixed = Counter(round(hue(p)) for p in pixels).most_common(1)[0][0]
    return mean, mixed


def report(label, images):
    normal_luma, normal_hue = body(images["normal"])
    hover_luma, hover_hue = body(images["active"])
    press_luma, press_hue = body(images["pressed"])
    print(label)
    print(f"  normal {normal_luma:6.1f} hue {normal_hue:4d}")
    print(f"  hover  {hover_luma:6.1f} hue {hover_hue:4d}")
    print(f"  press  {press_luma:6.1f} hue {press_hue:4d}")
    return (normal_luma, normal_hue), (hover_luma, hover_hue), (press_luma, press_hue)


def main():
    root = tk.Tk()
    root.withdraw()
    app._install_sims_theme(root)
    factory = root._sims_floating_button_factory
    failures = []

    # --- green button visible: hover and press brighten the same green -----
    green = factory("Grid", 70, 32, True)
    assert set(green) == set(STATES), sorted(green)
    (gl, gh), (al, ah), (pl, ph) = report("selected (green visible)", green)
    if ah is None or hue_drift(ah, gh) > 12:
        failures.append(("selected", "hover is not the same green"))
    if al <= gl:
        failures.append(("selected", "hover not brighter"))
    if ph is None or hue_drift(ph, gh) > 20:
        failures.append(("selected", "press left the green family"))
    if pl <= al:
        failures.append(("selected", "press not brighter than hover"))

    # --- normal button visible: dark blue edge on hover, active on press ---
    for label, width in (("normal toggle", 90), ("plain button", 130)):
        plain = factory(
            "Bones" if label == "normal toggle" else "Reset view", width, 32, False
        )
        (nl, nh), (hal, hah), (pal, pah) = report(label, plain)
        if abs(hal - nl) < 1:
            failures.append((label, "hover did not change the image"))
        if hal >= nl:
            failures.append((label, "hover is not the darker edge highlight"))
        if hue_drift(hah, nh) > 20:
            failures.append((label, "hover changed hue, expected the same blue"))
        if hue_drift(pah, nh) < 20:
            failures.append((label, "press did not flash the active texture"))

    root.destroy()
    if failures:
        raise SystemExit(f"{len(failures)} problem(s): {failures}")
    print("\nall checks passed")


if __name__ == "__main__":
    main()
