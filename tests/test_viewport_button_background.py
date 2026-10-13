from ts2psp.ui.floating_button import FloatingButton

"""Floating viewport buttons must not paint a rectangle over the 3D scene.

Renders the buttons as canvas items on top of a busy background and checks the
corners, where the rounded texture is transparent, still show the background.
Also exercises hover, press and the toggle's selected state.
"""
import sys
import time
import tkinter as tk

from PIL import Image

import ts2psp.app as app

BACKGROUND = "#ff00ff"  # any leak from the widget shows up immediately
WIDGET_BACKGROUND = (27, 31, 42)  # #1b1f2a, what ttk used to paint


def grab(widget, path):
    widget.update_idletasks()
    widget.update()
    time.sleep(0.4)
    widget.update()
    import PIL.ImageGrab

    box = (
        widget.winfo_rootx(),
        widget.winfo_rooty(),
        widget.winfo_rootx() + widget.winfo_width(),
        widget.winfo_rooty() + widget.winfo_height(),
    )
    image = PIL.ImageGrab.grab(bbox=box)
    image.save(path)
    return image


def corner_pixels(image, box):
    """The four corners of a widget bbox inside the grabbed canvas."""
    x0, y0, x1, y1 = box
    points = [(x0 + 1, y0 + 1), (x1 - 2, y0 + 1), (x0 + 1, y1 - 2), (x1 - 2, y1 - 2)]
    return [image.getpixel(p) for p in points]


def is_background(pixel):
    """True when the pixel is the canvas or one of the test grid lines.

    The corner must show whatever is behind the button, never the dark widget
    background ttk used to paint.
    """
    r, g, b = pixel[:3]
    magenta = r > 180 and b > 180 and g < 110
    green = g > 180 and r < 110 and b < 110
    yellow = r > 180 and g > 180 and b < 110
    return magenta or green or yellow


def looks_like_widget_background(pixel):
    return all(abs(a - b) <= 12 for a, b in zip(pixel[:3], WIDGET_BACKGROUND))


def main():
    root = tk.Tk()
    root.geometry("420x260+40+40")
    root.attributes("-topmost", True)
    app._install_sims_theme(root)
    root.deiconify()
    root.lift()
    root.update()

    canvas = tk.Canvas(
        root,
        width=400,
        height=240,
        background=BACKGROUND,
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack()
    # Something busy behind, so a dark rectangle cannot hide.
    for column in range(0, 400, 25):
        canvas.create_line(column, 0, column, 240, fill="#00ff00")
    for row in range(0, 240, 25):
        canvas.create_line(0, row, 400, row, fill="#ffff00")
    root.update()

    factory = root._sims_floating_button_factory
    measure = root._sims_floating_text_width
    fired = []
    plain = FloatingButton(
        canvas,
        factory,
        "Reset view",
        lambda: fired.append("reset"),
        measure("Reset view") + 28,
        32,
    )
    toggle = FloatingButton(
        canvas, factory, "Grid", lambda: fired.append("grid"), measure("Grid") + 28, 32
    )
    toggle.set_selected(True)
    plain.place(20, 20)
    toggle.place(20, 70)
    root.update_idletasks()
    root.update()

    image = grab(canvas, "debug_floating_buttons.png")
    pixels = list(image.convert("RGB").getdata())
    leaks = sum(1 for p in pixels if not is_background(p) and p[0] < 90)
    print(f"non-background dark pixels on the canvas: {leaks}")

    for name, button in (("plain", plain), ("toggle", toggle)):
        box = button.bbox()
        corners = corner_pixels(image, box)
        clean = all(is_background(p) for p in corners)
        dark = [p for p in corners if looks_like_widget_background(p)]
        print(
            f"{name:<7} bbox={box} corners={corners} "
            f"transparent={clean} dark_leaks={dark}"
        )
        assert clean and not dark, f"{name}: widget background leaked at the corners"

    # State changes must repaint, and the corners must stay transparent.
    plain.hover(True)
    root.update_idletasks()
    root.update()
    image2 = grab(canvas, "debug_floating_hover.png")
    plain.press(True)
    root.update_idletasks()
    root.update()
    image3 = grab(canvas, "debug_floating_press.png")
    box = plain.bbox()
    corners = corner_pixels(image3, box)
    print(
        f"pressed corners={corners} transparent="
        f"{all(is_background(p) for p in corners)}"
    )
    assert all(is_background(p) for p in corners), "press leaked a background"

    def pixels(img):
        return list(img.convert("RGB").getdata())

    print("hover differs from idle:", pixels(image) != pixels(image2))
    print("press differs from hover:", pixels(image2) != pixels(image3))
    assert pixels(image) != pixels(image2), "hover did not change the button"
    assert pixels(image2) != pixels(image3), "press did not change the button"

    plain.press(False)
    plain.command()
    toggle.command()
    print("commands fired:", fired)
    root.destroy()


if __name__ == "__main__":
    main()
