"""Wiring of the floating viewport controls in CharacterTab.

Exercises the real _floating_button, _position_viewport_controls and the
release handler on a stub tab, so the placement maths and the click semantics
are covered without needing the game resources.
"""

import sys
import types
import tkinter as tk

import ts2psp.app as app


class StubTab:
    """Only the bits the floating controls touch."""

    def __init__(self, canvas, toplevel):
        self.canvas = canvas
        self.toplevel = toplevel
        self.floating_buttons = []
        self.fired = []


def main():
    root = tk.Tk()
    root.geometry("500x300+40+40")
    root.attributes("-topmost", True)
    app._install_sims_theme(root)
    root.deiconify()
    root.lift()
    root.update()
    canvas = tk.Canvas(
        root,
        width=480,
        height=280,
        background="#1b1f2a",
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack()

    tab = StubTab(canvas, root)
    tab._floating_button = types.MethodType(app.CharacterTab._floating_button, tab)
    tab._floating_enter = types.MethodType(app.CharacterTab._floating_enter, tab)
    tab._floating_leave = types.MethodType(app.CharacterTab._floating_leave, tab)
    tab._floating_press = types.MethodType(app.CharacterTab._floating_press, tab)
    tab._floating_release = types.MethodType(app.CharacterTab._floating_release, tab)
    tab._position_viewport_controls = types.MethodType(
        app.CharacterTab._position_viewport_controls, tab
    )
    tab.winfo_toplevel = lambda: root

    export = tab._floating_button("Export as...", lambda: tab.fired.append("export"))
    reset = tab._floating_button("Reset view", lambda: tab.fired.append("reset"))
    bones = tab._floating_button("Bones", lambda: tab.fired.append("bones"))
    grid = tab._floating_button("Grid", lambda: tab.fired.append("grid"))
    tab.export_button = export
    tab.recenter_button = reset
    tab.bones_button = bones
    tab.grid_button = grid
    tab.status_toolbar_window = canvas.create_window(10, 0, anchor="sw")
    assert len(tab.floating_buttons) == 4, len(tab.floating_buttons)

    class Event:
        width = 480
        height = 280

    tab._position_viewport_controls(Event())

    boxes = {
        name: button.bbox()
        for name, button in (
            ("export", export),
            ("reset", reset),
            ("bones", bones),
            ("grid", grid),
        )
    }
    for name, box in boxes.items():
        print(f"{name:<6} bbox={box}")

    # Top right corner: grid hugs the right edge, bones sits to its left.
    assert abs(boxes["grid"][2] - (480 - 10)) < 1, boxes["grid"]
    assert abs(boxes["bones"][2] - (boxes["grid"][0] - 12)) < 1, boxes["bones"]
    assert boxes["reset"][1] > boxes["grid"][1], boxes
    assert boxes["export"] == (10, 10, boxes["export"][2], boxes["export"][3]), boxes[
        "export"
    ]
    assert boxes["export"][0] == 10 and boxes["export"][1] == 10, boxes["export"]

    # No two buttons may overlap, on either axis.
    def overlaps(a, b):
        return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])

    names = list(boxes)
    for index, a_name in enumerate(names):
        for b_name in names[index + 1 :]:
            assert not overlaps(
                boxes[a_name], boxes[b_name]
            ), f"{a_name} overlaps {b_name}"

    # Click semantics: press then release fires once; release alone does not.
    tab._floating_press(reset, None)
    assert tab.fired == [], tab.fired
    tab._floating_release(reset, None)
    assert tab.fired == ["reset"], tab.fired
    tab._floating_release(reset, None)
    assert tab.fired == ["reset"], "release without press fired the command"

    # Hover then leave must restore the idle image.
    before = canvas.itemcget(reset.item, "image")
    tab._floating_enter(reset, None)
    hovered = canvas.itemcget(reset.item, "image")
    assert hovered != before, "hover did not change the image"
    tab._floating_leave(reset, None)
    assert canvas.itemcget(reset.item, "image") == before, "leave did not restore"

    # The toggle switches between the normal and the selected texture. Image names
    # are not comparable: rebuilding gives new PhotoImage objects, so the pixels
    # are what must match. Read the canvas image directly to avoid screen timing.
    def image_bytes(button):
        root.update_idletasks()
        image = canvas.itemcget(button.item, "image")
        return root.tk.call(image, "data", "-format", "png")

    idle_pixels = image_bytes(grid)
    grid.set_selected(not grid.selected)
    selected_pixels = image_bytes(grid)
    assert selected_pixels != idle_pixels, "toggle did not repaint"
    grid.set_selected(not grid.selected)
    restored_pixels = image_bytes(grid)
    assert restored_pixels == idle_pixels, "toggle did not restore"

    # Every item sits on the canvas, no widget windows were created.
    assert canvas.find_all() and reset.item in canvas.find_all()

    print("\nall wiring checks passed")
    root.destroy()


if __name__ == "__main__":
    main()
