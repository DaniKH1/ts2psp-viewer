"""Floating viewport button drawn as a canvas item.

A ttk.Button always paints its background over the whole widget rectangle, so
over the rendered 3D scene a dark rectangle shows around the rounded button
texture. Drawing the button as an item of the viewport canvas removes the
widget from the picture: the transparent corners of the texture simply do not
paint and the scene shows through.

``factory(text, width, height, selected)`` must return a mapping of PIL images
for the states ``normal``, ``active`` (hover) and ``pressed``.
"""

import tkinter as tk


def place_right_buttons(width, buttons, *, top=10, gap=12, row_gap=12, margin=10):
    """Place viewport buttons against the right edge, wrapping when needed.

    ``buttons`` are ordered from the rightmost button inward. Spanish labels
    can be wider than English ones, so keep them inside the canvas by moving
    overflow buttons to another row instead of letting their x coordinate go
    negative.
    """
    available = max(1, width - 2 * margin)
    rows = [[]]
    row_widths = [0]
    for button in buttons:
        extra = button.reqwidth + (gap if rows[-1] else 0)
        if rows[-1] and row_widths[-1] + extra > available:
            rows.append([])
            row_widths.append(0)
            extra = button.reqwidth
        rows[-1].append(button)
        row_widths[-1] += extra

    y = top
    for row in rows:
        x = width - margin
        row_height = 0
        for button in row:
            x -= button.reqwidth
            button.place(x, y)
            x -= gap
            row_height = max(row_height, button.reqheight)
        y += row_height + row_gap
    return y


class FloatingButton:
    """Rounded button painted directly onto a canvas."""

    def __init__(self, canvas, factory, text, command, width, height, font=None):
        self.canvas = canvas
        self.factory = factory
        self.text = text
        self.command = command
        self.font = font
        self.width = int(width)
        self.height = int(height)
        self.enabled = True
        self.selected = False
        self._hovered = False
        self._pressed = False
        self._photos = {}
        self._cache_key = None
        self.item = canvas.create_image(0, 0, anchor="nw")
        self._build()
        self._bind()

    # ---- geometry ---------------------------------------------------------
    @property
    def reqwidth(self):
        return self.width

    @property
    def reqheight(self):
        return self.height

    def bbox(self):
        """Widget-equivalent bbox, for hit testing: (x0, y0, x1, y1)."""
        x, y = self.canvas.coords(self.item)
        return (x, y, x + self.width, y + self.height)

    def hit(self, x, y):
        """True when the canvas point falls on this button."""
        x0, y0, x1, y1 = self.bbox()
        return x0 <= x <= x1 and y0 <= y <= y1

    def place(self, x, y):
        self.canvas.coords(self.item, x, y)

    def destroy(self):
        self.canvas.delete(self.item)
        self._photos.clear()

    # ---- appearance -------------------------------------------------------
    def _build(self):
        key = (self.text, self.width, self.height, self.selected)
        if key == self._cache_key:
            return
        self._cache_key = key
        images = self.factory(self.text, self.width, self.height, self.selected)
        for state, image in images.items():
            self._photos[state] = tk.PhotoImage(
                master=self.canvas, data=_png_bytes(image)
            )
        self._apply()

    def _state_name(self):
        if not self.enabled:
            return "normal"
        if self._pressed:
            return "pressed"
        if self._hovered:
            return "active"
        return "normal"

    def _apply(self):
        photo = self._photos.get(self._state_name()) or self._photos["normal"]
        self.canvas.itemconfigure(self.item, image=photo)

    def refresh(self):
        """Re-render, e.g. after the text or the selected flag changed."""
        self._build()

    def set_text(self, text, width=None):
        """Cambia la etiqueta y vuelve a pintar el boton.

        El ancho hay que recalcularlo: la pastilla se dimensiona al construirla
        con el texto que tenia entonces, y si se alarga sin mas el texto se sale
        por debajo. Quien llama le pasa el ancho nuevo ya medido.
        """
        if self.text == text and (width is None or width == self.width):
            return
        self.text = text
        if width is not None:
            self.width = int(width)
        self.refresh()

    def set_selected(self, selected):
        if self.selected != selected:
            self.selected = bool(selected)
            self.refresh()

    def set_enabled(self, enabled):
        if self.enabled != enabled:
            self.enabled = bool(enabled)
            self._apply()

    # ---- interaction ------------------------------------------------------
    def _inside(self, event):
        x0, y0, x1, y1 = self.bbox()
        return x0 <= event.x <= x1 and y0 <= event.y <= y1

    def _bind(self):
        canvas = self.canvas
        self._tags = ("floating_button",)

    def bind_hover(self, handler):
        self.canvas.tag_bind(self.item, "<Enter>", handler)

    def install(self, enter, leave, press, release):
        """Attach the pointer handlers; the canvas forwards them by hit test."""
        self.canvas.tag_bind(self.item, "<Enter>", enter)
        self.canvas.tag_bind(self.item, "<Leave>", leave)
        self.canvas.tag_bind(self.item, "<ButtonPress-1>", press)
        self.canvas.tag_bind(self.item, "<ButtonRelease-1>", release)

    def hover(self, hovered):
        if self._hovered != hovered:
            self._hovered = hovered
            self._apply()

    def press(self, pressed):
        """Set the pressed flag; returns whether it was pressed before."""
        was = self._pressed
        if was != bool(pressed):
            self._pressed = bool(pressed)
            self._apply()
        return was


def _png_bytes(image):
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
