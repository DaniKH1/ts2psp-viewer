"""Mouse and keyboard orbit controls shared by all model previews."""

import math
import tkinter as tk

import numpy as np
from PIL import ImageTk

CAMERA_HINT = (
    "Mouse: left drag to orbit, middle/right drag to pan, wheel to zoom. "
    "Click the view to use arrow keys to orbit, WASD to pan, Q/E to zoom, and R to reset."
)

# Mom Viewer uses a 48-degree perspective camera and changes its orbit radius to zoom.
FOV = 48.0

# Mom Viewer allows the camera to approach to 0.05 model units.
DISTANCIA_MINIMA = 0.05
PLANO_CERCANO = 0.01
ZOOM_MAXIMO = 50000.0
ZOOM_ENTRADA = 0.88
ZOOM_SALIDA = 1.14


def escala_profundidad(profundidad, escala, zoom, alto):
    """Proyección en perspectiva al acercar la cámara al punto de mira.

    El campo de visión permanece fijo; el zoom acorta la distancia de cámara. Los
    puntos delante del plano cercano dejan de dibujarse cuando la cámara los atraviesa.

    Acepta un número o un array de numpy, que es como lo usa el renderer de GPU.
    """
    if FOV <= 0:
        return escala * zoom
    focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
    zoom = min(zoom, ZOOM_MAXIMO, focal / (escala * DISTANCIA_MINIMA))
    distancia = focal / (escala * zoom)
    return focal / np.maximum(PLANO_CERCANO, distancia + profundidad)


class Viewport(tk.Canvas):
    """Preview area whose size comes from the window, never from the rendered image.

    The preview is drawn as a canvas item instead of a label image, so the rendered size
    never feeds back into the geometry manager: orbiting, panning or zooming cannot make
    the window grow. Being the canvas itself, the widget also keeps receiving the mouse and
    keyboard events of the camera controller wherever the pointer is over it. ``view_size``
    reports the pixels to render and ``on_resize`` fires only when the visible area really
    changed, so resizing the window redraws the preview.
    """

    def __init__(
        self,
        master,
        minimum=(700, 700),
        maximum=None,
        background="#1b1f2a",
        highlightthickness=0,
        borderwidth=0,
        **kwargs,
    ):
        super().__init__(
            master,
            width=1,
            height=1,
            background=background,
            highlightthickness=highlightthickness,
            borderwidth=borderwidth,
            **kwargs,
        )
        self.minimum = minimum
        self.maximum = maximum
        self.on_resize = None
        self._size = None
        self._photo = None
        self._item = self.create_image(0, 0, anchor="center")
        self.pack(fill="both", expand=True)
        self.bind("<Configure>", self._resized)

    def _resized(self, event):
        if (event.width, event.height) == self._size:
            return
        self._size = (event.width, event.height)
        self.coords(self._item, event.width // 2, event.height // 2)
        if self.on_resize:
            self.on_resize()

    def view_size(self):
        """Pixel size for the next render: the visible area, clamped to min/max."""
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return self.minimum
        if self.maximum:
            return min(width, self.maximum[0]), min(height, self.maximum[1])
        return width, height

    def set_image(self, image):
        # master=self: sin el, PhotoImage se crea en el root por defecto y este
        # canvas, que puede vivir en otro Toplevel, acaba con un pyimage que ya
        # no existe.
        photo = ImageTk.PhotoImage(image, master=self)
        self._photo = photo
        self.itemconfigure(self._item, image=photo)

    def clear(self):
        self._photo = None
        self.itemconfigure(self._item, image="")


class OrbitController:
    def __init__(self, widget, changed, yaw=0.0, pitch=0.0, zoom=1.0, ignores=None):
        self.widget = widget
        self.changed = changed
        # Predicado opcional para descartar los eventos que caen sobre un
        # control superpuesto, como los botones flotantes del visor: sin esto,
        # mantener pulsado un botón arrastraba la cámara.
        self.ignores = ignores
        self.initial = (yaw, pitch, zoom)
        self.yaw = yaw
        self.pitch = pitch
        self.zoom = zoom
        self.zoom_tope = ZOOM_MAXIMO
        self.focus_offset = (0.0, 0.0, 0.0)
        self._view_pivot = None
        self._view_scale = None
        self.drag = None
        self.drag_mode = None
        widget.bind("<ButtonPress-1>", self.start_orbit)
        widget.bind("<B1-Motion>", self.move_orbit)
        widget.bind("<ButtonRelease-1>", self.end_drag)
        widget.bind("<ButtonPress-3>", self.start_pan)
        widget.bind("<B3-Motion>", self.move_pan)
        widget.bind("<ButtonRelease-3>", self.end_drag)
        widget.bind("<ButtonPress-2>", self.start_pan)
        widget.bind("<B2-Motion>", self.move_pan)
        widget.bind("<ButtonRelease-2>", self.end_drag)
        widget.bind("<Enter>", self.enter)
        widget.bind("<Leave>", self.leave)
        widget.bind("<MouseWheel>", self.wheel)
        widget.bind("<Button-4>", lambda event: self.wheel_linux(1))
        widget.bind("<Button-5>", lambda event: self.wheel_linux(-1))
        widget.bind("<KeyPress>", self.key)
        widget.configure(cursor="arrow")

    def enter(self, _event=None):
        if self.drag_mode is None:
            self.widget.configure(cursor="arrow")

    def leave(self, _event=None):
        if self.drag_mode is None:
            self.widget.configure(cursor="arrow")

    def _descartado(self, event):
        """True si el evento debe ignorarse por caer sobre un control."""
        return bool(self.ignores and self.ignores(event))

    def end_drag(self, _event=None):
        self.drag = None
        self.drag_mode = None
        self.widget.configure(cursor="arrow")

    @property
    def pan(self):
        return 0.0, 0.0

    def set_view(self, pivot, scale):
        self._view_pivot = tuple(float(v) for v in pivot)
        self._view_scale = scale

    def move_focus(self, dx, dy):
        if self._view_scale is None or self._view_scale <= 0:
            return
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        unidades = 1.0 / (self._view_scale * self.zoom)
        distancia_derecha = -dx * unidades
        distancia_arriba = dy * unidades
        derecha = (cy, -sy, 0.0)
        arriba = (-sp * sy, -sp * cy, cp)
        self.focus_offset = tuple(
            self.focus_offset[i]
            + distancia_derecha * derecha[i]
            + distancia_arriba * arriba[i]
            for i in range(3)
        )

    def zoom_centered(self, zoom):
        """Acerca o aleja la cámara manteniendo fijo el centro de la vista."""
        zoom = min(zoom, self.zoom_tope)
        if zoom == self.zoom:
            return False
        self.zoom = zoom
        return True

    def start_orbit(self, event):
        if self._descartado(event):
            return
        self.widget.focus_set()
        self.drag = (event.x, event.y)
        self.drag_mode = "orbit"
        # Al orbitar no hay cursor: el modelo gira bajo el puntero.
        self.widget.configure(cursor="none")

    def move_orbit(self, event):
        if self.drag is None:
            return
        dx, dy = event.x - self.drag[0], event.y - self.drag[1]
        self.drag = (event.x, event.y)
        self.yaw = (self.yaw + dx * 0.008) % (2 * math.pi)
        # arrastrar hacia abajo baja el modelo: la rotación vertical va invertida
        self.pitch = max(-1.48, min(1.48, self.pitch - dy * 0.008))
        self.changed()

    def start_pan(self, event):
        if self._descartado(event):
            return
        self.widget.focus_set()
        self.drag = (event.x, event.y)
        self.drag_mode = "pan"
        # Cursor de mover de Windows: la flecha de cuatro puntas. Tk la llama
        # 'fleur' y en Windows corresponde a IDC_MOVE.
        self.widget.configure(cursor="fleur")

    def move_pan(self, event):
        if self.drag is None:
            return
        dx, dy = event.x - self.drag[0], event.y - self.drag[1]
        self.drag = (event.x, event.y)
        self.move_focus(dx, dy)
        self.changed()

    def wheel_linux(self, direction):
        self.zoom_rueda(direction)

    def zoom_rueda(self, pasos):
        if not pasos:
            return
        paso = 1 / ZOOM_ENTRADA if pasos > 0 else 1 / ZOOM_SALIDA
        nuevo = max(0.15, min(self.zoom_tope, self.zoom * paso ** abs(pasos)))
        if self.zoom_centered(nuevo):
            self.changed()

    def wheel(self, event):
        if event.delta:
            self.zoom_rueda(event.delta / 120.0)

    def key(self, event):
        key = event.keysym.lower()
        if key == "left":
            self.yaw -= 0.12
        elif key == "right":
            self.yaw += 0.12
        elif key == "up":
            self.pitch = min(1.48, self.pitch - 0.12)
        elif key == "down":
            self.pitch = max(-1.48, self.pitch + 0.12)
        elif key == "a":
            self.move_focus(-25, 0)
        elif key == "d":
            self.move_focus(25, 0)
        elif key == "w":
            self.move_focus(0, -25)
        elif key == "s":
            self.move_focus(0, 25)
        elif key in ("q", "minus"):
            self.zoom_centered(max(0.15, self.zoom / ZOOM_SALIDA))
        elif key in ("e", "plus", "equal"):
            self.zoom_centered(min(self.zoom_tope, self.zoom / ZOOM_ENTRADA))
        elif key in ("home", "r"):
            self.reset()
            return "break"
        else:
            return
        self.yaw %= 2 * math.pi
        self.changed()
        return "break"

    def reset(self):
        self.yaw, self.pitch, self.zoom = self.initial
        self.focus_offset = (0.0, 0.0, 0.0)
        self.changed()
