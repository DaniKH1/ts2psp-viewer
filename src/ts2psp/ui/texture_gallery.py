"""Galeria de texturas con vista previa, para las pestañas que tengan.

Se extrajo de la de personajes porque props la necesita igual: son unas ciento
cincuenta lineas de miniaturas, borde de seleccion, hover y ventana de vista
previa con su dimmer. Duplicarlo era peor que compartirlo.
"""

import io
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageDraw, ImageOps, ImageTk

from ts2psp.ui.theme import (
    PREVIEW_BORDER_COLOR,
    PREVIEW_BORDER_WIDTH,
    PREVIEW_CORNER_RADIUS,
    _round_window_corners,
)


def a_imagen(pixels):
    """Lleva una textura a una imagen RGBA, venga como venga.

    Los personajes entregan el array de pixeles ya descomprimido y los props los
    bytes del PNG tal cual, porque es como los guarda su formato. Aqui se abre
    lo que sea, que para eso esta la galeria.
    """
    if isinstance(pixels, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(pixels))) as imagen:
            return imagen.convert("RGBA")
    return Image.fromarray(np.asarray(pixels, dtype=np.uint8)).convert("RGBA")


class TextureGallery(ttk.Frame):
    """Miniaturas de las texturas de un modelo, y su vista previa al pulsar.

    Las texturas se pasan enteras con `set_textures`; lo que se guarda aqui es
    solo lo necesario para pintarlas y para saber cual esta seleccionada.
    """

    def __init__(self, parent, app, on_pick=None, tile=64):
        super().__init__(parent)
        self.app = app
        self.on_pick = on_pick
        # Lado de la miniatura. En props la columna es mas ancha y las texturas
        # son pocas: a 44 px se quedan media columna en blanco.
        self.tile = tile
        self.textures = {}
        self.thumbnails = []
        self.buttons = []
        self.selected_key = ""
        self.preview_photo = None
        self.preview_window = None
        self.dimmer = None

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True)
        # El ancho solo es el que se pide: el gestor lo estira al de la columna
        # donde se monte, que en personajes es bastante mas estrecho que en
        # props. Las miniaturas se reparten con `weight`, asi que da igual.
        self.canvas = tk.Canvas(
            marco, width=182, height=196, highlightthickness=0, background="#aeb8e5"
        )
        self.scroll = ttk.Scrollbar(marco, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.grid, anchor="nw")
        self.grid.bind("<Configure>", self._grid_resized)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<MouseWheel>", self._mousewheel)

    # -- contenido ---------------------------------------------------------

    def set_textures(self, textures, selected=""):
        """Pinta las miniaturas de `textures`, un diccionario nombre -> pixeles."""
        self.hide_preview()
        self.textures = textures or {}
        self.selected_key = selected if selected in self.textures else ""
        for widget in self.grid.winfo_children():
            widget.destroy()
        self.thumbnails = []
        self.buttons = []
        filas = (len(self.textures) + self._columnas() - 1) // self._columnas()
        alto = min(max(filas, 1) * self._alto_fila(), 196)
        self.canvas.configure(height=alto)
        if filas > 4:
            self.scroll.pack(side="right", fill="y")
        else:
            self.scroll.pack_forget()
        for columna in range(self._columnas()):
            # Ancho fijo, el de la miniatura: es lo que las centra en su celda.
            self.grid.columnconfigure(
                columna, weight=0, minsize=self._alto_fila()
            )
        fondo = self.canvas.cget("background")
        for indice, (nombre, pixeles) in enumerate(sorted(self.textures.items())):
            imagen = a_imagen(pixeles)
            lado = self._lado()
            miniatura = ImageOps.fit(
                imagen, (lado, lado), method=Image.Resampling.LANCZOS
            )
            fotos = {
                "normal": self._tile(miniatura, selected=False),
                "selected": self._tile(miniatura, selected=True),
            }
            boton = tk.Button(
                self.grid,
                image=fotos["selected" if nombre == self.selected_key else "normal"],
                width=self.tile,
                height=self.tile,
                padx=0,
                pady=0,
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                background=fondo,
                activebackground=fondo,
                command=lambda key=nombre: self._pick(key),
            )
            columnas = self._columnas()
            boton.grid(
                row=indice // columnas,
                column=indice % columnas,
                padx=2,
                pady=2,
            )
            boton.image_key = nombre
            boton.tile_images = fotos
            boton.bind(
                "<Enter>",
                lambda _event, w=boton: self._hover(w, True),
            )
            boton.bind(
                "<Leave>",
                lambda _event, w=boton: self._hover(w, False),
            )
            self.buttons.append(boton)
            self.thumbnails.extend(fotos.values())
        # El grupo se centra segun cuantas sean, y eso cambia en cada carga.
        self._centrar()

    def clear(self):
        self.set_textures({})

    def _pick(self, key):
        self.selected_key = key
        self._refresh_selection()
        self.show_preview(key)
        if self.on_pick is not None:
            self.on_pick(key)

    def _lado(self):
        """Lado de la imagen dentro de la pastilla, con su marco."""
        return max(12, self.tile - 8)

    def _alto_fila(self):
        return self.tile + 4

    def _columnas(self):
        return min(3, max(1, len(self.textures)))

    def _ancho_grid(self):
        """Ancho que ocupa el grupo de miniaturas, sin repartir la columna.

        Repartidas en tres columnas de `weight`, con dos texturas se quedan
        pegadas a la izquierda y la tercera columna sale vacia y descentrada.
        Aqui cada columna mide lo que la miniatura y el grupo se centra entero.
        """
        return self._columnas() * self._alto_fila()

    def _tile(self, miniatura, selected=False):
        size = self.tile
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        radio = max(5, size // 6)
        borde = "#20df61" if selected else "#18275d"
        draw.rounded_rectangle(
            (1, 1, size - 2, size - 2), radius=radio, fill="#374456", outline=borde, width=2
        )
        mask = Image.new("L", miniatura.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, miniatura.width - 1, miniatura.height - 1),
            radius=max(3, radio - 3),
            fill=255,
        )
        # El margen va repartido a partes iguales: con uno fijo la imagen se
        # queda pegada a un lado dentro del borde, que es lo que se ve al
        # agrandar la miniatura.
        lado = miniatura.width
        inset = (size - lado) // 2
        tile.paste(miniatura, (inset, inset), mask)
        return ImageTk.PhotoImage(tile, master=self)

    def _hover(self, boton, hovering):
        selected = boton.image_key == self.selected_key
        boton.configure(
            image=boton.tile_images["selected" if selected or hovering else "normal"]
        )

    def _refresh_selection(self):
        for boton in self.buttons:
            boton.configure(
                image=boton.tile_images[
                    "selected" if boton.image_key == self.selected_key else "normal"
                ]
            )

    # -- lienzo ------------------------------------------------------------

    def _grid_resized(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _centrar(self):
        """Centra el grupo de miniaturas dentro del lienzo.

        Se usa al rellenar y al redimensionar: el lienzo se estira al ancho de su
        columna y el grupo se queda con el suyo, que depende de cuantas texturas
        haya. Si solo se recolocara al cambiar de tamano, al cargar otro modelo el
        grupo se quedaria donde estaba la lista anterior.        """
        ancho = self._ancho_grid()
        self.canvas.itemconfigure(self.window, width=ancho)
        self.canvas.coords(
            self.window, max(0, (self.canvas.winfo_width() - ancho) // 2), 0
        )

    def _canvas_resized(self, event):
        self._centrar()

    def _mousewheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), "units")

    # -- vista previa ------------------------------------------------------

    def show_preview(self, name):
        pixels = self.textures.get(name)
        if pixels is None:
            return
        self.hide_preview()
        root = self.winfo_toplevel()
        root.update_idletasks()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        width = max(1, root.winfo_width())
        height = max(1, root.winfo_height())

        self.dimmer = tk.Toplevel(root)
        self.dimmer.overrideredirect(True)
        self.dimmer.configure(background="#080910")
        self.dimmer.attributes("-alpha", 0.62)
        self.dimmer.geometry(f"{width}x{height}+{x}+{y}")
        # Cerrar al soltar, y solo si el puntero apenas se movio. Con un
        # <ButtonPress-1> cualquier arrastre que empezase sobre el dimmer lo
        # cerraba, y al redimensionar la ventana el puntero empieza justo encima
        # del dimmer, que lo cubre entero.
        self.dimmer.bind("<ButtonPress-1>", self._dimmer_press)
        self.dimmer.bind("<ButtonRelease-1>", self._dimmer_release)
        self.dimmer.lift()

        self.preview_window = tk.Toplevel(root)
        self.preview_window.overrideredirect(True)
        self.preview_window.configure(background=PREVIEW_BORDER_COLOR)
        self.preview_window.bind(
            "<Escape>", lambda _event: self.hide_preview(clear_selection=True)
        )
        preview_width = min(520, max(240, int(width * 0.5)))
        preview_height = min(480, max(200, int(height * 0.5)))
        self.preview_window.geometry(
            f"{preview_width}x{preview_height}"
            f"+{x + (width - preview_width) // 2}"
            f"+{y + (height - preview_height) // 2}"
        )
        panel = ttk.Frame(self.preview_window, padding=12)
        panel.pack(
            fill="both",
            expand=True,
            padx=PREVIEW_BORDER_WIDTH,
            pady=PREVIEW_BORDER_WIDTH,
        )
        ttk.Label(
            panel,
            text=name,
            anchor="center",
            wraplength=preview_width - 32,
            font=(root._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 8))
        image = a_imagen(pixels)
        image = ImageOps.contain(
            image,
            (preview_width - 32, preview_height - 56),
            method=Image.Resampling.LANCZOS,
        )
        # master explicito: sin el, PhotoImage se crea en el root por defecto y
        # la etiqueta, que pertenece a otro Toplevel, no lo encuentra.
        self.preview_photo = ImageTk.PhotoImage(image, master=root)
        area = ttk.Frame(panel)
        area.pack(fill="both", expand=True)
        ttk.Label(area, image=self.preview_photo).place(relx=0.5, rely=0.5, anchor="center")
        self.preview_window.lift()
        self.preview_window.focus_force()
        # Se recorta al final, cuando la ventana ya tiene su tamaño real.
        self.preview_window.update_idletasks()
        _round_window_corners(self.preview_window, PREVIEW_CORNER_RADIUS)
        # La vista previa sigue a la ventana principal si esta se redimensiona.
        root.bind("<Configure>", self._preview_moved, add="+")

    def hide_preview(self, clear_selection=False):
        for nombre in ("preview_window", "dimmer"):
            ventana = getattr(self, nombre)
            if ventana is not None:
                if ventana.winfo_exists():
                    ventana.destroy()
                setattr(self, nombre, None)
        self.preview_photo = None
        # Cerrar la vista previa a mano deja la textura deseleccionada, asi que
        # el borde verde de la miniatura se va con ella. Las llamadas internas
        # (abrir otra preview, cambiar de modelo) no deben tocar la seleccion.
        if clear_selection and self.selected_key:
            self.selected_key = ""
            self._refresh_selection()

    def _preview_moved(self, _event=None):
        """Mantiene el dimmer y la vista previa al redimensionar la ventana."""
        if self.preview_window is None:
            return
        root = self.winfo_toplevel()
        width, height = root.winfo_width(), root.winfo_height()
        if width < 2 or height < 2:
            return
        x, y = root.winfo_rootx(), root.winfo_rooty()
        self.dimmer.geometry(f"{width}x{height}+{x}+{y}")
        window = self.preview_window
        window.geometry(
            f"+{x + (width - window.winfo_width()) // 2}"
            f"+{y + (height - window.winfo_height()) // 2}"
        )

    def _dimmer_press(self, event):
        self._dimmer_origin = (event.x, event.y)

    def _dimmer_release(self, event):
        origin = getattr(self, "_dimmer_origin", None)
        self._dimmer_origin = None
        if origin is None:
            return
        # Un arrastre, como el que redimensiona la ventana, no cierra nada.
        if abs(event.x - origin[0]) > 4 or abs(event.y - origin[1]) > 4:
            return
        self.hide_preview(clear_selection=True)