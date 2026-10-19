"""Prop tab and its controls."""

import csv
import io
import math
import queue
import threading
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ts2psp.rendering.props import (
    bones_over,
    load_parts,
    posed_positions,
    prop_scene,
    render_prop,
)
from ts2psp.formats.props import ALFA_ILEGIBLE, convert_prop, prop_joints
from ts2psp.formats.prop_assets import PropAssets
from ts2psp.formats.prop_skin import SkinnedPropAnimator
from ts2psp.formats.prop_anim import (
    PropAnimator,
    iter_prop_animations,
    prop_animations,
    prop_clip,
)
from ts2psp.ui.camera import OrbitController, Viewport
from ts2psp.ui.floating_button import FloatingButton, place_right_buttons
from ts2psp.ui.textured_list import TexturedListbox
from ts2psp.ui.texture_gallery import TextureGallery
from ts2psp.ui.theme import LIST_PANEL_PATH
import ts2psp.rendering.opengl as gl
from ts2psp.ui.uv import UVWindow
from ts2psp.ui.common import (
    FPS_VALORES,
    choose_export_format,
    export_model_name,
    set_photo,
)
from ts2psp.ui.i18n import translate
from ts2psp.exporting.blender import export_path
from ts2psp.exporting.gltf import export_gltf

# Alto de las dos listas, en pixeles y el mismo para las dos. La lista es un
# `tk.Canvas`, asi que su `height` va en pixeles y no en filas. No se estiran a
# llenar la columna: se quedan en un alto fijo, mas corto que el que tenian.
ALTO_LISTA_PROPS = 360
ALTO_LISTA_ANIMS = 360


class PropTab(ttk.Frame):
    # Valores por defecto. La clase se puede montar sin pasar por __init__ (los
    # tests la levantan a mano) y `dispose` no debe encontrarse con un
    # atributo que todavia no existe.
    _anim_rows = None
    _anim_todas = ()
    _anim_queue = None
    _anim_poll = None
    floating_buttons = ()
    fps_index = 0

    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.assets = PropAssets(app.paths["props"])
        with zipfile.ZipFile(app.paths["props"]) as z:
            self.rows = list(
                csv.DictReader(
                    io.StringIO(z.read("inventario_props.csv").decode("utf-8-sig"))
                )
            )
        self.visible = []
        self.current = None
        self.parts = []
        self.timer = None
        self.last_frame = 0.0
        self.rendering = False
        self.redraw_requested = False
        self.disposed = False
        self.clip = None
        self.animator = None
        self.has_export_animation = False
        self.data = None
        self.bone_src = ()
        self.playing = False
        self.playhead = 0.0
        self.clock_ms = 0.0
        self.moving_slider = False
        self.objeto = None
        self.objeto_key = None
        self.bones = ()
        self.textures = {}
        self.show_textures = tk.BooleanVar(value=True)
        self.show_grid = tk.BooleanVar(value=True)
        self.show_bones = tk.BooleanVar(value=False)
        self.show_helpers = tk.BooleanVar(value=False)
        self.show_vertex_colors = tk.BooleanVar(value=True)
        grupo = app._sims_font_family

        # Columna de la izquierda: los modelos, su desplegable de grupos y la
        # galeria de texturas. Lo de abajo se empaqueta desde abajo: la lista
        # pide cientos de filas, y si se deja que se la quede se lleva el hueco
        # entero y el desplegable y la galeria se quedan fuera de la pantalla.
        left = ttk.Frame(self, width=250)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        ttk.Label(
            left,
            text="Props",
            anchor="center",
            font=(grupo, 12, "bold"),
        ).pack(fill="x")
        # El buscador lleva la misma textura que el de personajes.
        self.filter = tk.StringVar()
        self.search_entry = ttk.Entry(
            left, textvariable=self.filter, width=30, style="CharacterSearch.TEntry"
        )
        self.search_entry.pack(side="top", fill="x", pady=(6, 12))
        self.search_entry.bind("<KeyRelease>", self.refresh)
        # Desplegable de carpetas: la categoria sale de la ruta interna del prop.
        self.group = tk.StringVar(value="All")
        self.group_box = ttk.Combobox(
            left, textvariable=self.group, state="readonly", width=28
        )
        self.gallery = TextureGallery(left, app)
        # Abajo del todo y en este orden: `side="bottom"` apila hacia arriba, asi
        # que lo primero que se empaqueta es lo que mas abajo queda. Debajo de la
        # lista va el desplegable, despues un separador y despues la galeria, que
        # ademas se estira para que no quede un hueco entre medias.
        self.gallery.pack(side="bottom", fill="both", expand=True)
        ttk.Label(
            left, text="Textures", anchor="center", font=(grupo, 11, "bold")
        ).pack(side="bottom", fill="x", pady=(10, 4))
        ttk.Separator(left, orient="horizontal").pack(
            side="bottom", fill="x", pady=(8, 10)
        )
        self.group_box.pack(side="bottom", fill="x", pady=(0, 4))
        ttk.Label(left, text="Group", anchor="center", font=(grupo, 10, "bold")).pack(
            side="bottom", fill="x", pady=(10, 0)
        )
        self.group_box.bind("<<ComboboxSelected>>", self.refresh)
        self.listbox = self._lista_texturizada(left, ALTO_LISTA_PROPS, self.select)

        # Columna de la derecha: las animaciones del prop, su desplegable de
        # grupos y los controles de reproduccion. Mismo reparto que en la
        # izquierda: los controles van desde abajo y la lista toma lo que queda.
        right = ttk.Frame(self, width=250)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)
        self.anim_label = ttk.Label(
            right, text="Animations", anchor="center", font=(grupo, 12, "bold")
        )
        self.anim_label.pack(side="top", fill="x")
        # Mismo buscador y misma textura que el de los props.
        self.anim_filter = tk.StringVar()
        self.anim_search_entry = ttk.Entry(
            right,
            textvariable=self.anim_filter,
            width=30,
            style="CharacterSearch.TEntry",
        )
        self.anim_search_entry.pack(side="top", fill="x", pady=(6, 12))
        self.anim_search_entry.bind("<KeyRelease>", self.refresh_animations)
        self.anim_group = tk.StringVar(value="All")
        self.anim_group_box = ttk.Combobox(
            right, textvariable=self.anim_group, state="readonly", width=28
        )
        # De arriba abajo, como la izquierda: la lista tiene alto fijo y lo que
        # sobra de la columna se queda al final, en vez de abrir un hueco en
        # medio. Las estadisticas del prop ya no van aqui, se pintan sobre el
        # visor.
        # 'extended' para poder elegir varias con Ctrl o Shift y exportar un
        # modelo por cada animacion elegida.
        self.anim_list = self._lista_texturizada(
            right, ALTO_LISTA_ANIMS, self.select_animation, selectmode="extended"
        )
        ttk.Label(right, text="Group", anchor="center", font=(grupo, 10, "bold")).pack(
            fill="x", pady=(10, 0)
        )
        self.anim_group_box.pack(fill="x", pady=(0, 4))
        self.anim_group_box.bind("<<ComboboxSelected>>", self.refresh_animations)
        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(10, 6))
        # La misma tabla que en la vista de animaciones de personajes: Play y el
        # boton de FPS en una fila, Loop / Auto Play con el reloj en otra, y el
        # deslizador debajo.
        controls = ttk.Frame(right)
        controls.pack(fill="x")
        self.play_button = ttk.Button(controls, text="▶ Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        # Boton de FPS: pulsa y va cyclando. Comparte fila con el Play.
        self.fps_index = 0
        self.fps_button = ttk.Button(controls, text="", command=self.ciclar_fps)
        self.fps_button.pack(side="right")
        # Loop, Auto Play y reloj en una segunda fila: en una sola, el FPS se
        # queda en 58 px y muestra '20 F'.
        lower = ttk.Frame(right)
        lower.pack(fill="x", pady=(3, 0))
        self.loop = tk.BooleanVar(value=True)
        ttk.Checkbutton(lower, text="Loop", variable=self.loop).pack(side="left")
        self.auto_play = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            lower,
            text="Auto Play",
            variable=self.auto_play,
            command=self._auto_play_toggled,
        ).pack(side="left", padx=(12, 0))
        self.time_label = ttk.Label(lower, text="0:00 / 0:00", anchor="e")
        self.time_label.pack(side="right")
        self.position = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(
            right,
            from_=0,
            to=1,
            variable=self.position,
            command=self.scrub,
            style="Props.Horizontal.TScale",
        )
        self.slider.pack(fill="x", pady=6)
        ttk.Checkbutton(
            right,
            text="Vertex colors",
            variable=self.show_vertex_colors,
            command=self.schedule,
        ).pack(anchor="w", pady=4)
        self._fps_button_refresh()

        # El visor en el centro, con los botones flotantes encima.
        center = ttk.Frame(self)
        center.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(center, minimum=(500, 550))
        self.canvas.on_resize = self.schedule
        # Media vuelta: los props vienen mirando hacia un lado y el visor enseña la
        # cara de detrás, que es la que se ve. Sigue siendo un visor 3D, esto solo
        # es de donde parte.
        self.camera = OrbitController(self.canvas, self.schedule, yaw=math.pi)
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button(
            "Export as...", lambda: self.export()
        )
        self.recenter_button = self._floating_button("Reset view", self.camera.reset)
        self.bones_button = self._floating_button("Bones", self.toggle_bones)
        self.grid_button = self._floating_button(
            "Grid", self.toggle_grid, selected=True
        )
        # Capa de texto sobre el visor, en la esquina inferior izquierda, como en
        # la pestaña de personajes. Aqui van las estadisticas del prop.
        self.status = ttk.Label(
            self.canvas,
            text="",
            padding=(5, 3),
            wraplength=330,
            background=self.canvas.cget("background"),
            foreground="#ffffff",
        )
        self.status_toolbar_window = self.canvas.create_window(
            10, 0, anchor="sw", window=self.status
        )
        self.canvas.bind("<Configure>", self._position_viewport_controls, add="+")
        self.after_idle(self._position_viewport_controls)

        self.group_box.configure(
            values=tuple(
                translate(value, self.app.language) for value in self._grupos(self.rows)
            )
        )
        self.refresh()
        if self.visible:
            initial = next(
                (
                    i
                    for i, row in enumerate(self.visible)
                    if row["ruta_interna"] == "objects/chairs/fx_chair_dining_medieval"
                ),
                0,
            )
            self.listbox.selection_set(initial)
            self.listbox.see(initial)
            self.select()

    # -- piezas de la interfaz ------------------------------------------------

    def _lista_texturizada(self, parent, alto, al_seleccionar, selectmode="browse"):
        """Lista con el panel de fondo del tema y su barra de desplazamiento.

        `alto` va en pixeles: la lista es un `tk.Canvas`, y en un canvas el
        `height` son pixeles y no filas. Va anclada arriba y sin estirar, que lo
        de abajo es lo que no puede quedarse sin hueco.
        """
        marco = ttk.Frame(parent)
        marco.pack(side="top", fill="both", pady=(3, 0))
        lista = TexturedListbox(
            marco,
            LIST_PANEL_PATH,
            width=222,
            height=alto,
            font=(self.app._sims_font_family, 10, "bold"),
            selectmode=selectmode,
        )
        scroll = ttk.Scrollbar(marco, orient="vertical", command=lista.yview)
        lista.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        lista.pack(side="left", fill="both", expand=True)
        lista.bind("<<ListboxSelect>>", al_seleccionar)
        return lista

    def _floating_button(self, text, command, selected=False):
        """Boton flotante dibujado sobre el canvas del visor.

        Se usa un item del canvas y no un `ttk.Button` porque el widget pinta su
        fondo en todo el rectangulo y sobre la escena 3D se veria un rectangulo
        negro alrededor de la textura redondeada.
        """
        top = self.winfo_toplevel()
        width = top._sims_floating_text_width(text) + 2 * 14
        button = FloatingButton(
            self.canvas,
            top._sims_floating_button_factory,
            text,
            command,
            width,
            32,
            font=top._sims_floating_font,
        )
        button.set_selected(selected)
        self.floating_buttons.append(button)
        button.install(
            lambda event, b=button: self._floating_enter(b, event),
            lambda event, b=button: self._floating_leave(b, event),
            lambda event, b=button: self._floating_press(b, event),
            lambda event, b=button: self._floating_release(b, event),
        )
        return button

    def _over_floating_button(self, event):
        """La camara no debe arrastrarse si el puntero esta sobre un boton."""
        return any(button.hit(event.x, event.y) for button in self.floating_buttons)

    def set_language(self, language):
        top = self.winfo_toplevel()
        for button in self.floating_buttons:
            label = translate(button.text, language)
            button.set_text(label, top._sims_floating_text_width(label) + 2 * 14)
        self.group_box.configure(
            values=tuple(translate(value, language) for value in self._grupos(self.rows))
        )
        if self._anim_todas:
            self.anim_group_box.configure(
                values=tuple(
                    translate(value, language)
                    for value in self._grupos(self._anim_todas)
                )
            )
        self.after_idle(self._position_viewport_controls)
        self.refresh()

    def _floating_enter(self, button, event):
        button.hover(True)

    def _floating_leave(self, button, event):
        button.hover(False)
        button.press(False)

    def _floating_press(self, button, event):
        button.press(True)

    def _floating_release(self, button, event):
        if button.press(False):
            button.command()

    def _position_viewport_controls(self, event=None):
        if event is None:
            width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        else:
            width, height = event.width, event.height
        self.export_button.place(10, 10)
        y = place_right_buttons(width, (self.grid_button, self.bones_button))
        self.recenter_button.place(width - 10 - self.recenter_button.reqwidth, y)
        self.canvas.coords(self.status_toolbar_window, 10, height - 10)

    def toggle_bones(self):
        enabled = not self.show_bones.get()
        self.show_bones.set(enabled)
        self.bones_button.set_selected(enabled)
        self.schedule()

    def toggle_grid(self):
        enabled = not self.show_grid.get()
        self.show_grid.set(enabled)
        self.grid_button.set_selected(enabled)
        self.schedule()

    @staticmethod
    def _categoria(row):
        """La carpeta de un prop: `objects/chairs/silla` deja `chairs`."""
        partes = [p for p in row["ruta_interna"].split("/") if p]
        if len(partes) >= 2:
            return partes[-2]
        return partes[-1] if partes else "All"

    @staticmethod
    def _nombre(row):
        """El nombre de un prop: `objects/chairs/fx_silla` deja `fx_silla`.

        Solo el ultimo tramo. El numero de entrada no dice nada y la ruta entera
        no cabe en la lista.
        """
        return row.get("animation_name", row["ruta_interna"]).rpartition("/")[2]

    def _grupos(self, rows):
        """Categorias de una lista de filas, con 'All' delante y en orden."""
        vistos = []
        for row in rows:
            categoria = self._categoria(row)
            if categoria not in vistos:
                vistos.append(categoria)
        return ["All"] + sorted(vistos)

    @property
    def anim_rows(self):
        """Los clips de props, calculados la primera vez que se piden.

        Recorrer los 767 .res buscando animaciones cuesta varios segundos, y la
        mayoria de las veces no se mira la lista. Antes se hacia al construir la
        pestaña y eran casi 9 s de los 10 del arranque.
        """
        if self._anim_rows is None:
            if self._anim_queue is not None:
                # El escaneo ya esta en marcha; la lista se pintara sola.
                return []
            self._anim_rows = self._fill_anim_list(
                prop_animations(self.app.paths["props"], self.rows)
            )
        return self._anim_rows

    def _fill_anim_list(self, rows):
        """Pone los clips en la lista. Solo desde el hilo de la interfaz."""
        self._anim_todas = rows
        self.anim_group_box.configure(
            values=tuple(
                translate(value, self.app.language) for value in self._grupos(rows)
            )
        )
        self._pintar_anims()
        return rows

    def refresh_animations(self, _event=None):
        """Buscador o desplegable de las animaciones: se repinta la lista."""
        self._pintar_anims()

    def _pintar_anims(self):
        """Aplica el filtro de texto y el grupo a la lista de animaciones.

        Separa pintar de rellenar porque el buscador se puede tocar en cualquier
        momento, tambien antes de que el escaneo termine, y entonces lo que hay
        que repintar es lo ya escaneado.
        """
        term = self.anim_filter.get().casefold()
        grupo = translate(self.anim_group.get(), "en")
        self.anim_list.delete(0, "end")
        self.anim_visible = [
            r
            for r in self._anim_todas
            if term in self._nombre(r).casefold()
            and (grupo == "All" or self._categoria(r) == grupo)
        ]
        for row in self.anim_visible:
            self.anim_list.insert("end", self._nombre(row))

    def on_shown(self):
        """La primera vez que se abre la pestaña, rellena la lista de animaciones.

        El escaneo va en un hilo aparte. Recorrer los 767 .res son unos dos
        segundos y, hecho aqui, la ventana se quedaba congelada y sin responder
        a nada hasta terminar.

        Medido al abrir la pestaña, la pausa mas larga de la interfaz era de
        2,1 s bloqueando, 0,06 s en hilo y 0,5 s repartiendo el trabajo en
        tandas con `after`. Se queda el hilo. Las tandas parecian la solucion
        limpia, pero al vaciarse las llamadas pendientes de una sola vez se
        juntan todas y hacen una pausa aun mayor que el reparto en origen.
        """
        if self._anim_rows is not None or self._anim_queue is not None:
            return
        cola = queue.Queue()
        self._anim_queue = cola
        paths, rows = self.app.paths["props"], self.rows

        def work():
            try:
                filas = []
                for i, fila in enumerate(iter_prop_animations(paths, rows)):
                    filas.append(fila)
                    if not i % 32:
                        # Cede el GIL de vez en cuando. Son dos segundos de CPU
                        # seguidos y, sin esto, el render de las demas pestanas
                        # se va de 6 a 16 ms mientras dura el escaneo.
                        time.sleep(0.001)
                cola.put(filas)
            except Exception:
                # Aqui no se puede tocar la interfaz, ni avisar: solo dejar el
                # resultado y que lo recoja el hilo de la interfaz.
                cola.put([])

        threading.Thread(target=work, daemon=True).start()
        self._anim_poll = self.after(100, self._poll_anim_rows)

    def _poll_anim_rows(self):
        """Recoge el escaneo en cuanto termine."""
        self._anim_poll = None
        if self.disposed or self._anim_queue is None:
            return
        try:
            filas = self._anim_queue.get_nowait()
        except queue.Empty:
            self._anim_poll = self.after(100, self._poll_anim_rows)
            return
        self._anim_queue = None
        self._anim_rows = self._fill_anim_list(filas)

    def refresh(self, _event=None):
        term = self.filter.get().casefold()
        grupo = translate(self.group.get(), "en")
        self.visible = [
            r
            for r in self.rows
            if term in r["ruta_interna"].casefold()
            and (grupo == "All" or self._categoria(r) == grupo)
        ]
        self.listbox.delete(0, "end")
        for r in self.visible:
            self.listbox.insert("end", self._nombre(r))

    def select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.load(self.visible[sel[0]])

    def select_animation(self, _event=None):
        """Al elegir un clip de la lista de abajo se abre su prop arriba."""
        sel = self.anim_list.curselection()
        if not sel:
            return
        row = self.anim_visible[sel[0]]
        for i, candidate in enumerate(self.visible):
            if candidate["archivo_zip"] == row["archivo_zip"]:
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(i)
                self.listbox.see(i)
                break
        self.load(row)

    def load(self, row):
        try:
            with zipfile.ZipFile(self.app.paths["props"]) as z:
                data = z.read(row["archivo_zip"])
            self.stop()
            self.data = data
            self.parts = load_parts(
                data,
                include_helpers=self.show_helpers.get(),
                opaco=row["ruta_interna"] in ALFA_ILEGIBLE,
                ruta=row["ruta_interna"],
                assets=self.assets,
            )
            self.bone_src, _ = prop_joints(
                data, include_helpers=self.show_helpers.get()
            )
            self.bones = bones_over(self.parts, self.bone_src)
            self.clip = prop_clip(data)
            self.animator = PropAnimator(data, self.clip)
            clips = (
                self.assets.clips(row["ruta_interna"]) if b"bmsh" in data[:128] else []
            )
            if clips:
                reference = next(
                    (c for c in clips if c["name"].endswith("idle-breathe")), clips[0]
                )
                selected = next(
                    (c for c in clips if c["name"] == row.get("animation_name")),
                    reference,
                )
                self.animator = SkinnedPropAnimator(data, selected, reference)
                self.clip = self.animator.clip
                self.bones = self.bone_src
            self.has_export_animation = bool(
                self.clip and self.animator.bindings
            )
            self.current = row
            for textured in (False, True):
                gl.forget(("prop", row["entry"], textured))
            self.objeto = None
            self.objeto_key = None
            # La galeria enseña lo que se pinta: cada parte lleva su textura en
            # `texture` como (nombre, pixeles), y sin pixeles no se ve nada.
            self.textures = {
                texture[0]: texture[1]
                for part in self.parts
                for texture in (
                    [part.get("texture")]
                    + [layer.get("texture") for layer in part.get("texture_layers", [])]
                )
                if texture and texture[1]
            }
            self.gallery.set_textures(self.textures)
            self.playhead = 0.0
            self.moving_slider = True
            self.position.set(0)
            self.moving_slider = False
            self.slider.configure(to=self.clip["duration"] if self.clip else 1)
            supported = bool(self.parts and self.clip and self.animator.bindings)
            self.play_button.configure(state="normal" if supported else "disabled")
            self.slider.configure(state="normal" if supported else "disabled")
            self.time_label.configure(
                text=self.time_text(0, self.clip["duration"] if self.clip else 0)
            )
            vertices = sum(len(p["vertices"]) for p in self.parts)
            self.status.configure(
                text=translate(self.status_text(row, vertices), self.app.language)
            )
            # Un recurso que solo trae plantillas de efecto no tiene nada que
            # dibujar: hay que borrar lo que hubiera, que si no se queda el
            # modelo anterior pegado en pantalla.
            if not self.parts:
                self.canvas.clear()
            # Con 'Auto Play' marcado, abrir un prop que tenga clip ya lo pone en
            # marcha: es lo que se quiere al irlos viendo seguidos.
            if supported and self.auto_play.get():
                self.start_play()
            self.schedule()
        except Exception as exc:
            messagebox.showerror(translate("Object", self.app.language), str(exc))

    def status_text(self, row, vertices):
        """Lo que se lee en la esquina del visor, como en la pestaña de personajes.

        Dos lineas: el nombre y lo que se ha cargado. Si el prop trae clip, una
        tercera con su duracion y sus pistas, que es la linea que lleva la vista
        de animaciones de los personajes. El clip de un prop no tiene nombre
        propio en el formato: el del prop es el nombre, asi que no se repite.
        """
        lineas = [
            self._nombre(row),
            f"{len(self.bones)} bones · {vertices} vertices",
        ]
        if self.clip and self.animator.bindings:
            pistas = len(self.clip["channels"])
            lineas.append(f"{self.clip['duration']:.2f} s · {pistas} tracks")
        warnings = sorted(
            {p["texture_warning"] for p in self.parts if p.get("texture_warning")}
        )
        lineas.extend(warnings)
        if not self.assets.manifest.get("version"):
            lineas.append(
                "Reimport the ISO (File > Select ISO) to load object bitmaps and skeletal clips."
            )
        if row["ruta_interna"].endswith(("/fx_lamp_kine", "/fx_lamp_swinging")):
            lineas.append(
                "Black shading is stored in vertex colors. Toggle Vertex colors to inspect the texture."
            )
        return "\n".join(lineas)

    def time_text(self, t, duration):
        def clock(seconds):
            minutes, rest = divmod(int(round(max(0.0, float(seconds)))), 60)
            return f"{minutes}:{rest:02d}"

        return f"{clock(t)} / {clock(duration)}"

    def stop(self):
        self.playing = False
        self.play_button.configure(text=translate("▶ Play", self.app.language))

    def start_play(self):
        """Arranca la reproduccion; no hace nada si ya esta sonando."""
        if not self.clip or not self.animator.bindings or self.playing:
            return
        if self.clip["duration"] <= 0:
            return
        self.playing = True
        if self.playhead >= self.clip["duration"]:
            self.playhead = 0.0
        self.clock_ms = gl.ahora_ms()
        self.play_button.configure(text=translate("❚❚ Pause", self.app.language))
        self.schedule()

    def toggle_play(self):
        if not self.clip or not self.animator.bindings or self.clip["duration"] <= 0:
            return
        if self.playing:
            self.stop()
            return
        self.start_play()

    def _auto_play_toggled(self):
        """Marcar Auto Play arranca el clip ya cargado; desmarcarlo, lo para.

        Sin esto solo arrancaba al abrir un prop nuevo, y marcar la casilla con
        uno en pantalla no hacia nada.
        """
        if self.auto_play.get():
            self.start_play()
        elif self.playing:
            self.stop()

    def _fps_button_refresh(self):
        self.fps_button.configure(text=f"{FPS_VALORES[self.fps_index]} FPS")

    def ciclar_fps(self):
        self.fps_index = (self.fps_index + 1) % len(FPS_VALORES)
        self._fps_button_refresh()

    def scrub(self, value):
        if self.moving_slider or not self.clip:
            return
        self.playhead = float(value)
        if self.playing:
            self.clock_ms = gl.ahora_ms()
        self.time_label.configure(
            text=self.time_text(self.playhead, self.clip["duration"])
        )
        self.schedule()

    def posed_parts(self):
        """Las piezas en el instante actual, con las curvas aplicadas."""
        show_rest = (
            not self.playing
            and self.playhead == 0
            and getattr(self.animator, "rest_overrides", ())
        )
        if not self.clip or show_rest:
            self.bones = (
                self.bone_src
                if isinstance(self.animator, SkinnedPropAnimator)
                else bones_over(self.parts, self.bone_src)
            )
            return self.parts
        partes, huesos = self.animator.pose(self.parts, self.playhead, self.bone_src)
        posed_bones = tuple(
            {**bone, "position": huesos.get(bone["index"], bone["position"])}
            for bone in self.bone_src
        )
        self.bones = (
            posed_bones
            if isinstance(self.animator, SkinnedPropAnimator)
            else bones_over(partes, posed_bones)
        )
        return partes

    def tick(self):
        """Avanza el reloj de reproduccion. Devuelve True si sigue en marcha."""
        if not self.clip or not self.playing:
            return False
        ahora = gl.ahora_ms()
        # El ritmo elegido manda: hasta que no ha pasado el tiempo de un
        # fotograma no se toca el reloj, que es lo que hace que 20 FPS se vea a
        # tirones y 60 no.
        if ahora - self.clock_ms < 1000 / FPS_VALORES[self.fps_index]:
            return True
        self.playhead += max(0.0, (ahora - self.clock_ms) / 1000.0)
        self.clock_ms = ahora
        if self.playhead < self.clip["duration"]:
            self.moving_slider = True
            self.position.set(self.playhead)
            self.moving_slider = False
            self.time_label.configure(
                text=self.time_text(self.playhead, self.clip["duration"])
            )
            return True
        if self.loop.get() and self.clip["duration"] > 0:
            self.playhead = self.playhead % self.clip["duration"]
            self.moving_slider = True
            self.position.set(self.playhead)
            self.moving_slider = False
            self.time_label.configure(
                text=self.time_text(self.playhead, self.clip["duration"])
            )
            return True
        self.playhead = self.clip["duration"]
        self.stop()
        return False

    def schedule(self, _event=None):
        if self.disposed:
            return
        if self.rendering:
            self.redraw_requested = True
            return
        # Coalesce input events; a pending frame already reads the latest state.
        if self.timer:
            return
        delay = gl.faltan_para(self.last_frame, 16) if gl.disponible() else 60
        # A slow frame must still return to Tk before starting the next one.
        self.timer = self.after(max(1, delay), self.render)

    def render(self):
        self.timer = None
        if self.disposed:
            return
        if self.rendering:
            self.redraw_requested = True
            return
        self.rendering = True
        self.redraw_requested = False
        self.last_frame = gl.ahora_ms()
        try:
            if not self.parts:
                self.stop()
                return
            self.tick()
            partes = self.posed_parts()
            w, h = self.canvas.view_size()
            # La escena se reutiliza entre fotogramas y solo se le cambian los
            # vertices: si se pidiera por clave, la cache devolveria la de
            # reposo y el prop no se moveria.
            key = (self.current["entry"], self.show_textures.get())
            if self.objeto_key != key:
                self.objeto = prop_scene(self.parts, key[0], key[1])
                self.objeto_key = key
            self.objeto.fijar_vertex_colors(self.show_vertex_colors.get())
            if self.clip:
                self.objeto.fijar_poses(posed_positions(partes))
            image = render_prop(
                partes,
                w,
                h,
                self.camera.yaw,
                self.camera.zoom,
                self.show_textures.get(),
                self.camera.pitch,
                self.camera.pan,
                key=("prop", self.current["entry"] if self.current else ""),
                camara=self.camera,
                bones=self.bones,
                show_bones=self.show_bones.get(),
                objeto=self.objeto,
            )
            set_photo(self.canvas, image)
        except Exception as exc:
            self.stop()
            self.status.configure(
                text=translate(f"Preview: {exc}", self.app.language)
            )
        finally:
            self.rendering = False
            if self.playing or self.redraw_requested:
                self.schedule()

    def export(self, fmt=None):
        """Exporta un modelo por cada animacion elegida, o el prop que hay cargado.

        Con una sola animacion elegida se.exporta como siempre, con su dialogo de
        archivo. Con varias se pide una carpeta y se escribe un modelo por
        animacion, cada uno con el nombre de su prop.
        """
        elegidas = self._animaciones_elegidas()
        if not self.current:
            return
        incluir_animacion = self.has_export_animation
        if fmt is None:
            nombre = (
                f"{len(elegidas)} prop animations"
                if len(elegidas) > 1
                else self._nombre(self.current)
            )
            choice = choose_export_format(
                self,
                nombre,
                animation_available=self.has_export_animation,
            )
            if self.has_export_animation:
                fmt, incluir_animacion = choice
            else:
                fmt = choice
            if not fmt:
                return
        if len(elegidas) > 1 and incluir_animacion:
            self._exportar_varios(elegidas, fmt, incluir_animacion)
            return
        try:
            fbx = fmt == "fbx"
            blender = self.app.resolve_blender() if fbx else None
            if fbx and not blender:
                return
            model_name = export_model_name(self._nombre(self.current))
            export_dir = self.app.export_root / f"{model_name}_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            target = export_dir / f"{model_name}.{fmt}"
            with zipfile.ZipFile(self.app.paths["props"]) as z:
                data = z.read(self.current["archivo_zip"])
            glb = target if fmt == "glb" else self.app.cache / "prop_export.glb"
            info = convert_prop(
                data,
                self.current["ruta_interna"],
                glb,
                include_helpers=self.show_helpers.get(),
                assets=self.assets,
                animation=self.current.get("animation_name"),
                include_animation=incluir_animacion,
            )
            if fbx:
                export_path(glb, target, blender)
            elif fmt == "gltf":
                export_gltf(glb, target)
            self.status.configure(
                text=translate(
                    f'Saved: {target}\n{info["parts"]} parts · '
                    f'{info["color_parts"]} with vertex colors',
                    self.app.language,
                )
            )
        except Exception as exc:
            messagebox.showerror(
                translate("Export object", self.app.language), str(exc)
            )

    def _animaciones_elegidas(self):
        """Las filas de la lista de animaciones que estan marcadas."""
        return [self.anim_visible[i] for i in self.anim_list.curselection()]

    def _exportar_varios(self, rows, fmt, include_animation=True):
        """Export each selected prop animation to its own Character-style folder."""
        try:
            fbx = fmt == "fbx"
            blender = self.app.resolve_blender() if fbx else None
            if fbx and not blender:
                return
            lineas = []
            with zipfile.ZipFile(self.app.paths["props"]) as z:
                for row in rows:
                    data = z.read(row["archivo_zip"])
                    nombre = export_model_name(self._nombre(row))
                    destino = self.app.export_root / f"{nombre}_export"
                    destino.mkdir(parents=True, exist_ok=True)
                    target = destino / f"{nombre}.{fmt}"
                    glb = target if fmt == "glb" else self.app.cache / "prop_export.glb"
                    info = convert_prop(
                        data,
                        row["ruta_interna"],
                        glb,
                        include_helpers=self.show_helpers.get(),
                        assets=self.assets,
                        animation=row.get("animation_name"),
                        include_animation=include_animation,
                    )
                    if fbx:
                        export_path(glb, target, blender)
                    elif fmt == "gltf":
                        export_gltf(glb, target)
                    lineas.append(
                        f"{nombre}: {info['parts']} parts · "
                        f"{info['color_parts']} with vertex colors"
                    )
        except Exception as exc:
            messagebox.showerror(
                translate("Export objects", self.app.language), str(exc)
            )
            return
        self.status.configure(
            text=translate(
                f"{len(rows)} models exported\n" + "\n".join(lineas),
                self.app.language,
            )
        )

    def show_uv(self):
        if self.parts:
            UVWindow(self, self.parts, self.current["ruta_interna"])

    def dispose(self):
        self.disposed = True
        self.stop()
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        if self._anim_poll:
            self.after_cancel(self._anim_poll)
            self._anim_poll = None
        for button in self.floating_buttons:
            button.destroy()
        self.floating_buttons = []
