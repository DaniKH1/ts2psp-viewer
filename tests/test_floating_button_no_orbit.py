from ts2psp.ui.floating_button import FloatingButton

"""Holding a floating button must not orbit or pan the camera.

Drags that start on a floating button are ignored by the controller; drags
that start on empty viewport still work, including one that wanders over a
button mid-drag.
"""
import types
import tkinter as tk

import ts2psp.app as app
from ts2psp.ui.camera import OrbitController


class Event:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class StubTab:
    def __init__(self, canvas):
        self.canvas = canvas
        self.floating_buttons = []
        self._over_floating_button = types.MethodType(
            app.CharacterTab._over_floating_button, self
        )


def main():
    root = tk.Tk()
    root.withdraw()
    app._install_sims_theme(root)
    canvas = tk.Canvas(
        root,
        width=400,
        height=300,
        background="#1b1f2a",
        highlightthickness=0,
        borderwidth=0,
    )
    canvas.pack()

    tab = StubTab(canvas)
    factory = root._sims_floating_button_factory
    measure = root._sims_floating_text_width
    grid = FloatingButton(
        canvas, factory, "Grid", lambda: None, measure("Grid") + 28, 32
    )
    grid.place(300, 20)
    tab.floating_buttons.append(grid)
    root.update_idletasks()

    camera = OrbitController(canvas, lambda: None, yaw=1.0, pitch=0.2, zoom=2.0)
    camera.ignores = tab._over_floating_button
    # move_focus() is a no-op without a view scale, so the pan case needs one.
    camera.set_view((0.0, 0.0, 0.0), 320.0)

    # --- a press on the button must not start a drag ------------------------
    box = grid.bbox()
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    yaw0, pitch0, zoom0 = camera.yaw, camera.pitch, camera.zoom
    focus0 = camera.focus_offset

    camera.start_orbit(Event(cx, cy))
    assert camera.drag is None, "a press on a button started an orbit drag"
    assert camera.drag_mode is None, camera.drag_mode

    # Simulate the mouse moving while the button is held.
    for step in range(1, 6):
        camera.move_orbit(Event(cx + step * 12, cy + step * 5))
    camera.start_pan(Event(cx, cy))
    assert camera.drag is None, "a press on a button started a pan drag"
    for step in range(1, 6):
        camera.move_pan(Event(cx + step * 12, cy + step * 5))

    assert camera.yaw == yaw0, f"yaw moved to {camera.yaw}"
    assert camera.pitch == pitch0, f"pitch moved to {camera.pitch}"
    assert camera.zoom == zoom0, f"zoom moved to {camera.zoom}"
    assert camera.focus_offset == focus0, "the focus panned"
    print("drag on the button: camera untouched")

    # --- a press on empty viewport must still orbit ------------------------
    camera.start_orbit(Event(50, 200))
    assert camera.drag == (50, 200), camera.drag
    assert camera.drag_mode == "orbit", camera.drag_mode
    camera.move_orbit(Event(110, 220))
    assert camera.yaw != yaw0, "a drag on empty viewport did not orbit"
    print(f"drag on the viewport: yaw {yaw0:.3f} -> {camera.yaw:.3f}")
    camera.end_drag()

    camera.start_pan(Event(50, 200))
    assert camera.drag_mode == "pan", camera.drag_mode
    camera.move_pan(Event(70, 230))
    assert camera.focus_offset != focus0, "a pan on empty viewport did nothing"
    print("pan on the viewport: focus moved")
    camera.end_drag()

    # --- a drag that starts outside keeps working over the button ---------
    camera.start_orbit(Event(50, 200))
    camera.move_orbit(Event(cx, cy))
    assert camera.drag is not None, "the drag stopped when crossing a button"
    print("drag crossing a button: still active")

    # --- hit() must match the bbox ----------------------------------------
    inside_x = int((box[0] + box[2]) // 2)
    inside_y = int((box[1] + box[3]) // 2)
    assert grid.hit(inside_x, inside_y), "hit() missed the button centre"
    assert not grid.hit(5, 5), "hit() matched a point far from the button"
    assert not grid.hit(int(box[2] + 20), inside_y), "hit() matched beyond the edge"
    print("hit() matches the bbox")

    root.destroy()
    print("\nall checks passed")


if __name__ == "__main__":
    main()
