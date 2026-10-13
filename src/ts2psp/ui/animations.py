"""Animation tab and its controls."""

import json
import math
import queue
import re
import threading
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import ImageDraw
from ts2psp.formats.characters import convert_character, nombre_publico, slug_publico
from ts2psp.formats.animations import attach_animations, open_glb, parse_banm
from ts2psp.exporting.gltf import export_gltf
from ts2psp.rendering.animation import (
    aplanar,
    glb_piezas,
    posed_bones,
    posed_meshes,
    validate_preview_frame,
)
from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.ui.textured_list import TexturedListbox
from ts2psp.ui.floating_button import FloatingButton
from ts2psp.ui.theme import LIST_PANEL_PATH
from ts2psp.ui.common import character_fallback, export_model_name, set_photo
from ts2psp.exporting.blender import export_path

FPS_VALORES = (20, 30, 60, 15)
PERSONAJES_POR_NOMBRE = (
    ("nightbeast", "nightbeast"),
    ("docdom", "doctordominion"),
    ("roberta", "robertarossum"),
    ("sinjin", "sinjinbalani-kine"),
    ("ritualdance", "sinjinbalani-kine"),
    ("player", "tinamansfield"),
    ("vampire", "virginyafeng_vamp"),
    # Al final a proposito: los clips de ghoul que son de fantasma o de player
    # los atrapan antes esas marcas, y solo los sueltos caen en Eadie Butler.
    ("ghoul", "eadiebutler"),
)
ZOMBIE_POR_GENERO = (
    ("female", "emmadarcy"),
    ("male", "heberdarcy"),
)
ZOMBIE_UNISEX = "zinamarlowe"
FANTASMA = "nervoussubject"
GENERO_FEMENINO = "tinamansfield"
GENERO_MASCULINO = "ashtonfelt"


class AnimationTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        with zipfile.ZipFile(app.paths["animations"]) as z:
            self.rows = json.loads(z.read("catalog.json"))
        self.by_name = {r["name"]: r for r in self.rows}
        self.visible = []
        self.loaded = None
        self.preview = None
        self.escena = None
        self.buffer = None
        self.playing = False
        self.timer = None
        self.prepare_timer = None
        self.prepare_generation = 0
        self.model_lock = threading.Lock()
        self.last_tick = 0
        self.updating_slider = False
        self.messages = queue.Queue()
        self.exporting = False
        self.disposed = False
        self.poll_timer = None
        # Panel a la izquierda con las listas; el visor 3D ocupa el resto. Se
        # empaqueta primero el panel para que reserve su ancho y el visor, con
        # expand, recoja lo que quede. Mismo ancho fijo que el visor de personajes.
        panel = ttk.Frame(self, width=250)
        panel.pack(side="left", fill="y", padx=(0, 10))
        panel.pack_propagate(False)
        ttk.Label(
            panel,
            text="Characters",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        # El personaje se elige solo segun el genero del clip, asi que la lista
        # se esconde tras un boton y solo sale cuando se quiere cambiar a mano.
        self.all_characters = list(app.characters)
        self.character = tk.StringVar(
            value="bellagoth" if "bellagoth" in app.characters else app.characters[0]
        )
        self.replace_character = tk.BooleanVar(value=False)
        self.replace_button = ttk.Button(
            panel,
            text="Replace character",
            style="Toggle.TButton",
            command=self.toggle_replace_character,
        )
        self.replace_button.pack(fill="x", pady=(8, 0))
        self.character_frame = ttk.Frame(panel)
        self.character_filter = tk.StringVar()
        char_search = ttk.Entry(
            self.character_frame,
            textvariable=self.character_filter,
            width=30,
            style="CharacterSearch.TEntry",
        )
        char_search.pack(fill="x", pady=(8, 4))
        char_search.bind("<KeyRelease>", self.filter_characters)
        char_frame = ttk.Frame(self.character_frame)
        char_frame.pack(fill="both", expand=True)
        self.char_list = TexturedListbox(
            char_frame,
            LIST_PANEL_PATH,
            width=222,
            height=300,
            font=(self.app._sims_font_family, 10, "bold"),
        )
        char_scroll = ttk.Scrollbar(
            char_frame, orient="vertical", command=self.char_list.yview
        )
        self.char_list.configure(yscrollcommand=char_scroll.set)
        char_scroll.pack(side="right", fill="y")
        self.char_list.pack(side="left", fill="both", expand=True)
        for name in self.all_characters:
            self.char_list.insert("end", nombre_publico(name))
        self.char_list.bind("<<ListboxSelect>>", self._character_selected)
        initial_character = self.character.get()
        if initial_character in self.all_characters:
            self.char_list.selection_set(self.all_characters.index(initial_character))

        # Columna de clips al extremo derecho, con los controles de
        # reproduccion debajo. El visor queda en medio.
        clips = ttk.Frame(self, width=250)
        clips.pack(side="right", fill="y", padx=(10, 0))
        clips.pack_propagate(False)
        ttk.Label(
            clips,
            text="Animations",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        # Misma textura que el buscador de personajes; la etiqueta 'Search' estorba
        # con el titulo 'Animations' justo encima.
        self.filter = tk.StringVar()
        entry = ttk.Entry(
            clips, textvariable=self.filter, width=31, style="CharacterSearch.TEntry"
        )
        entry.pack(fill="x", pady=(6, 0))
        entry.bind("<KeyRelease>", self.refresh)
        ttk.Label(clips, text="Select multiple clips with Ctrl or Shift").pack(
            anchor="w", pady=(6, 0)
        )
        list_frame = ttk.Frame(clips)
        list_frame.pack(fill="both", expand=True, pady=(3, 6))
        # La altura pedida es pequena a proposito: la columna va con
        # pack_propagate(False), asi que si la suma de lo pedido supera el hueco
        # pack reparte la falta desde el final y el slider se queda a 1 px.
        # Con expand=True, lo que sobra tras lo de arriba lo recoge esta.
        self.listbox = TexturedListbox(
            list_frame,
            LIST_PANEL_PATH,
            width=222,
            height=150,
            font=(self.app._sims_font_family, 10),
            selectmode="extended",
        )
        list_scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.schedule_prepare)
        ttk.Label(
            clips,
            text="Group",
            anchor="center",
            font=(self.app._sims_font_family, 10, "bold"),
        ).pack(fill="x", pady=(6, 0))
        self.group = tk.StringVar(value="All")
        groups = ["All", "Individual", "Social", "Scenes", "Creatures"]
        group_box = ttk.Combobox(
            clips, textvariable=self.group, values=groups, state="readonly", width=28
        )
        group_box.pack(fill="x", pady=(0, 2))
        group_box.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Separator(clips, orient="horizontal").pack(fill="x", pady=(4, 0))
        controls = ttk.Frame(clips)
        controls.pack(fill="x", pady=(8, 0))
        self.play_button = ttk.Button(controls, text="▶ Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        # Boton de FPS: pulsa y va cyclando. Comparte fila con el Play porque
        # ambos piden 120 px y 120+120 entra justo en los 250 de la columna.
        self.fps_index = 0
        self.fps_button = ttk.Button(controls, text="", command=self.ciclar_fps)
        self.fps_button.pack(side="right")
        # Loop y reloj en una segunda fila. Los botones del tema piden 120 px
        # con cualquier texto, incluso vacio, asi que en una sola fila solo
        # caben dos: con el Loop tambien ahi, el FPS se quedaba en 58 px y
        # mostraba '20 F'.
        lower = ttk.Frame(clips)
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
            clips, from_=0, to=1, variable=self.position, command=self.scrub
        )
        self.slider.pack(fill="x", pady=6)
        self._fps_button_refresh()
        # Solo para avisos y errores de carga o exportacion; el contador de
        # clips ya no se pinta aqui.
        self.status = ttk.Label(clips, text="", wraplength=230)
        self.status.pack(fill="x", pady=(4, 0))

        viewport = ttk.Frame(self)
        viewport.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(viewport, minimum=(380, 500))
        self.canvas.on_resize = lambda: self.render_frame(self.position.get())
        self.camera = OrbitController(
            self.canvas, lambda: self.render_frame(self.position.get()), yaw=math.pi
        )
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.show_grid = tk.BooleanVar(value=True)
        self.show_bones = tk.BooleanVar(value=False)
        self.export_button = self._floating_button("Export as...", self.export_as)
        self.grid_button = self._floating_button(
            "Grid", self.toggle_grid, selected=True
        )
        self.bones_button = self._floating_button("Bones", self.toggle_bones)
        self.recenter_button = self._floating_button("Reset view", self.camera.reset)
        # Capa de texto sobre el visor: mismo estilo que el visor de personajes.
        self.view_status = ttk.Label(
            self.canvas,
            text="",
            padding=(5, 3),
            wraplength=260,
            background=self.canvas.cget("background"),
            foreground="#ffffff",
        )
        self.status_toolbar_window = self.canvas.create_window(
            10, 0, anchor="sw", window=self.view_status
        )
        self.canvas.bind("<Configure>", self._position_viewport_controls, add="+")
        self.after_idle(self._position_viewport_controls)
        ttk.Label(viewport, text=CAMERA_HINT, wraplength=640).pack(anchor="w")
        ttk.Label(
            viewport,
            text="The preview shows the pose; exported GLB files retain textures and materials.",
        ).pack(anchor="w")
        self.refresh()
        initial = next(
            (
                i
                for i, r in enumerate(self.visible)
                if r["name"].endswith("a-cas-idle-breathe-f")
            ),
            0,
        )
        if self.visible:
            self.listbox.selection_set(initial)
            self.listbox.see(initial)
            self.schedule_prepare()
        self.poll_timer = self.after(100, self.poll_messages)

    def _character_selected(self, _event=None):
        """Sincroniza la lista de personajes con el StringVar."""
        selection = self.char_list.curselection()
        if not selection:
            return
        name = slug_publico(self.char_list.get(selection[0]))
        if name == self.character.get():
            return
        self.character.set(name)
        self.changed_character()

    def toggle_replace_character(self):
        """Muestra u oculta la lista de personajes."""
        self.replace_character.set(not self.replace_character.get())
        if self.replace_character.get():
            self.character_frame.pack(fill="both", expand=True, pady=(6, 0))
            self.replace_button.configure(style="Selected.Toggle.TButton")
        else:
            self.character_frame.pack_forget()
            self.replace_button.configure(style="Toggle.TButton")

    def _personaje_por_sujeto(self, nombre):
        """Modelo propio de zombis y fantasmas, o None si el clip no es de esos.

        Se consulta antes que el genero a proposito: `a-male-walk-ghost` y
        `a-male-walk-zombie` llevan prefijo de genero, pero son de fantasma y de
        zombi respectivamente, no de un sim suelto.
        """
        if "ghost" in nombre:
            return FANTASMA
        if "zombie" not in nombre:
            return None
        for marca, personaje in ZOMBIE_POR_GENERO:
            if marca in nombre:
                return personaje
        return ZOMBIE_UNISEX

    def _clip_genero(self, row):
        """'f' o 'm' si el clip marca el genero, si no None.

        De 1336 clips solo 51 acaban en -f o -m; el resto no marca genero y se
        deja el personaje que hubiera. Hay dos formas de marcarlo:

        - El nombre empieza por `a-male-` o `a-female-` (a-male-walk-alt0-normal).
          Solo al principio: en `barpose2-male-bopping` la palabra `male` es parte
          del nombre de la pose, no el genero, y de esa pose no hay version
          `female` mientras que `barpose1` tampoco lleva la palabra.
        - El ultimo token es -f, -fem o -m (a-cas-idle-breathe-f).

        Ojo con '-mon' de las criaturas y con las palabras que acaban en f o m
        sin ser genero.
        """
        base = row["name"].rsplit("/", 1)[-1].casefold()
        if base.startswith("a-female-"):
            return "f"
        if base.startswith("a-male-"):
            return "m"
        ultimo = base.rsplit("-", 1)[-1]
        if ultimo in ("f", "fem"):
            return "f"
        if ultimo == "m":
            return "m"
        return None

    def _character_for_row(self, row):
        """Personaje que pide el clip, o None si no impone ninguno.

        Prioridad: zombis y fantasmas (que llevan el genero dentro del nombre), la
        criatura o el personaje al que alude el nombre del clip (nightbeast,
        docdom, roberta) por encima de la categoria, y el genero '-f'/'-m' por
        debajo, que solo decide entre los dos por defecto.
        """
        nombre = row["name"].casefold()
        sujeto = self._personaje_por_sujeto(nombre)
        if sujeto:
            return sujeto
        for marca, personaje in PERSONAJES_POR_NOMBRE:
            if marca in nombre:
                return personaje
        categoria = row.get("category")
        if categoria in ("cowthulu_anims", "nightbeast_anims"):
            return categoria.split("_anims")[0]
        genero = self._clip_genero(row)
        if not genero:
            return None
        return GENERO_FEMENINO if genero == "f" else GENERO_MASCULINO

    def _auto_character(self):
        """Ajusta el personaje al genero del clip seleccionado.

        Devuelve True si lo cambio, para que quien llama tire la vista previa
        que habia.
        """
        rows = self.selected_rows()
        if not rows:
            return False
        # Con la lista de personajes abierta manda lo que elija el usuario: si
        # no, al elegir a mano se llamaba changed_character -> schedule_prepare
        # -> aqui, y el automatico le devolvia el suyo enseguida.
        if self.replace_character.get():
            return False
        destino = self._character_for_row(rows[0])
        if destino is None:
            return False
        if destino not in self.all_characters:
            return False
        if self.character.get() == destino:
            return False
        self.character.set(destino)
        self.char_list.selection_set(self.all_characters.index(destino))
        self.loaded = None
        self.preview = None
        self.escena = None
        self.buffer = None
        return True

    def filter_characters(self, _event=None):
        """Filtra la lista de personajes por lo escrito en el buscador.

        Solo cambia lo que se ve: el personaje cargado sigue siendo el que
        hubiera, para que escribir no tire la animacion que se esta viendo.
        """
        term = self.character_filter.get().casefold()
        self.char_list.delete(0, "end")
        for name in self.all_characters:
            if term in name.casefold() or term in nombre_publico(name).casefold():
                self.char_list.insert("end", nombre_publico(name))
        # La lista conservaria el desplazamiento anterior, que con menos
        # filas dejaria la vista a medio camino.
        self.char_list.yview("moveto", 0)

    def _floating_button(self, text, command, selected=False):
        """Botón flotante dibujado sobre el canvas del visor.

        Mismo criterio que en el visor de personajes: se dibuja como item del
        canvas y no como ttk.Button, porque el widget pinta su fondo en todo el
        rectángulo y sobre la escena 3D se vería un rectángulo negro alrededor
        de la textura redondeada.
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
        """La cámara no debe arrastrarse si el puntero está sobre un botón."""
        return any(button.hit(event.x, event.y) for button in self.floating_buttons)

    def _floating_enter(self, button, event):
        button.hover(True)

    def _floating_leave(self, button, event):
        button.hover(False)
        button.press(False)

    def _floating_press(self, button, event):
        button.press(True)

    def _floating_release(self, button, event):
        # press() devuelve si estaba pulsado, para no disparar el comando cuando
        # el puntero entra ya soltado.
        if button.press(False):
            button.command()

    def _position_viewport_controls(self, event=None):
        if event is None:
            width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        else:
            width, height = event.width, event.height
        self.export_button.place(10, 10)
        self.grid_button.place(width - 10 - self.grid_button.reqwidth, 10)
        self.bones_button.place(
            width - 10 - self.grid_button.reqwidth - self.bones_button.reqwidth - 12, 10
        )
        self.recenter_button.place(width - 10 - self.recenter_button.reqwidth, 56)
        self.canvas.coords(self.status_toolbar_window, 10, height - 10)

    def toggle_bones(self):
        enabled = not self.show_bones.get()
        self.show_bones.set(enabled)
        self.bones_button.set_selected(enabled)
        self.render_frame(self.position.get())

    def toggle_grid(self):
        enabled = not self.show_grid.get()
        self.show_grid.set(enabled)
        self.grid_button.set_selected(enabled)
        if self.escena is not None:
            self.escena.mostrar_grid = enabled
        self.render_frame(self.position.get())

    def _export_character(self, rows):
        """(esqueleto, personaje) del export, o None si no se pueden mezclar.

        Centraliza el reparto de criaturas: Cowthulu y Night Beast tienen sus
        propios anims, asi que es el clip el que decide el personaje, no el
        desplegable. Lo usan el dialogo y export_glb para no decidirlo dos veces.
        """
        creature_characters = {
            "cowthulu_anims": "cowthulu",
            "nightbeast_anims": "nightbeast",
        }
        skeletons = {creature_characters.get(row["category"], "human") for row in rows}
        if len(skeletons) > 1:
            return None
        skeleton = next(iter(skeletons))
        return skeleton, (self.character.get() if skeleton == "human" else skeleton)

    def choose_export_format(self):
        """Dialogo de formato, equivalente al del visor de personajes.

        La diferencia es que aqui lo que se exporta es un conjunto de clips
        selecionados, asi que el dialogo dice cuantos son y con que personaje,
        y avisa antes si mezclan esqueletos incompatibles.
        """
        rows = self.selected_rows()
        resolved = self._export_character(rows)
        dialog = tk.Toplevel(self)
        dialog.title("Export as...")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        selected = tk.StringVar(value="glb")
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)
        plural = "clip" if len(rows) == 1 else "clips"
        if resolved is None:
            ttk.Label(
                content,
                text=f"{len(rows)} {plural} selected",
                font=(self.winfo_toplevel()._sims_font_family, 9, "bold"),
            ).pack(anchor="w", pady=(0, 2))
            ttk.Label(
                content,
                text="These clips mix skeletons. Export human, Cowthulu "
                "and Night Beast clips separately.",
                wraplength=380,
            ).pack(anchor="w", pady=(0, 8))
        else:
            skeleton, character = resolved
            ttk.Label(
                content,
                text=f"{len(rows)} {plural} selected",
                font=(self.winfo_toplevel()._sims_font_family, 9, "bold"),
            ).pack(anchor="w", pady=(0, 2))
            ttk.Label(content, text=f"Character: {character}").pack(
                anchor="w", pady=(0, 8)
            )
        for label, value in (
            ("GLB (.glb) — single file", "glb"),
            ("glTF (.gltf) — model, binary and textures", "gltf"),
            ("FBX (.fbx) — requires Blender", "fbx"),
        ):
            ttk.Radiobutton(content, text=label, value=value, variable=selected).pack(
                anchor="w", pady=2
            )
        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(12, 0))
        choice = {"format": None}

        def accept():
            choice["format"] = selected.get()
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(
            buttons, text="Export", style="Primary.TButton", command=accept
        ).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.wait_window()
        # Un dialogo que solo avisa no debe lanzar una exportacion imposible.
        if resolved is None:
            return None
        return choice["format"]

    def export_as(self):
        """Punto de entrada del boton flotante 'Export as...'."""
        if self.exporting:
            return
        if not self.selected_rows():
            messagebox.showinfo(
                "Export animations", "Select one or more clips in the list first."
            )
            return
        fmt = self.choose_export_format()
        if not fmt:
            return
        self.export_glb(fmt)

    def refresh(self, _event=None):
        self.cancel_prepare()
        self.stop()
        key = {
            "Individual": "solo_anims",
            "Social": "social_anims",
            "Scenes": "cutscene_anims",
        }.get(self.group.get())
        term = self.filter.get().casefold()
        self.visible = []
        for row in self.rows:
            category = row["category"]
            if key and category != key:
                continue
            if self.group.get() == "Creatures" and category not in (
                "cowthulu_anims",
                "nightbeast_anims",
            ):
                continue
            if term not in row["name"].casefold():
                continue
            self.visible.append(row)
        # Solo el nombre del clip: el indice de 'entry' era un numero que delante no
        # aportaba nada y empujaba el nombre hacia el resto del ancho.
        items = [row["name"].rsplit("/", 1)[-1] for row in self.visible]
        self.listbox.delete(0, "end")
        if items:
            self.listbox.insert("end", *items)
        # El contador de clips ya no se pinta: la etiqueta se reserva para
        # avisos y errores, y refrescar no debe borrar lo que hubiera escrito.

    def cancel_prepare(self):
        self.prepare_generation += 1
        if self.prepare_timer:
            self.after_cancel(self.prepare_timer)
            self.prepare_timer = None

    def selected_rows(self):
        return [self.visible[i] for i in self.listbox.curselection()]

    def changed_character(self, _event=None):
        self.cancel_prepare()
        self.loaded = None
        self.preview = None
        self.stop()
        self.escena = None
        self.buffer = None  # la escena anterior ya no encaja
        self.schedule_prepare()

    def schedule_prepare(self, _event=None):
        if not self.selected_rows():
            self.cancel_prepare()
            self.stop()
            return
        # Un clip '-f' o '-m' trae su propio personaje, asi que se ajusta antes
        # de generar nada. _auto_character ya invalida la vista previa.
        self._auto_character()
        self.prepare_generation += 1
        generation = self.prepare_generation
        if self.prepare_timer:
            self.after_cancel(self.prepare_timer)
        self.prepare_timer = self.after(180, lambda: self.prepare(generation))

    def _read_clip(self, row):
        with zipfile.ZipFile(self.app.paths["animations"]) as z:
            data = z.read(row["file"])
        return parse_banm(data)

    def _fallback(self, character):
        name = character_fallback(self.app.character_rows, character)
        return self._read_clip(self.by_name[name])

    def _cowthulu_reference(self, row):
        clip_name = row["name"].rsplit("/", 1)[-1]
        variant = "-mon" if clip_name.endswith("-mon") else ""
        name = f"characters/bodyanims/cowthulu_anims/a-cowthulu-idle-breathe{variant}"
        reference_row = self.by_name.get(name)
        if reference_row is None:
            raise ValueError(f"Cowthulu reference pose not found: {name}")
        return self._read_clip(reference_row)

    def _static_model(self, character):
        # Regenerate older cached outfits so newly supported accessories appear.
        path = self.app.cache / ("character_" + character + "_accessories_v2.glb")
        if not path.exists():
            convert_character(self.app.paths["characters"], character, path)
        return path

    def prepare(self, generation=None):
        self.prepare_timer = None
        if generation is None:
            generation = self.prepare_generation
        if generation != self.prepare_generation:
            return
        rows = self.selected_rows()
        if not rows:
            return
        row = rows[0]
        self.stop()
        character = {
            "cowthulu_anims": "cowthulu",
            "nightbeast_anims": "nightbeast",
        }.get(row["category"], self.character.get())
        creature = row["category"] in ("cowthulu_anims", "nightbeast_anims")
        key = (character, row["entry"])
        if self.loaded == key and self.preview:
            return
        # La etiqueta del panel se reserva para avisos y errores; el progreso de
        # carga se refleja en la capa de texto sobre el visor.

        def work():
            try:
                with self.model_lock:
                    model = self._static_model(character)
                clip = self._read_clip(row)
                reference = None
                reference_pose = None
                if character == "cowthulu":
                    fallback = self._cowthulu_reference(row)
                else:
                    fallback = None if creature else self._fallback(character)
                output = self.app.cache / f"preview_animation_{generation}.glb"
                attach_animations(
                    model,
                    [clip],
                    reference,
                    output,
                    fps=20,
                    fallback=fallback,
                    reference_pose=reference_pose,
                    reference_pose_time=1.0,
                )
                doc, binary = open_glb(output)
                if not doc.get("animations"):
                    raise ValueError(
                        "This clip has no tracks compatible with this character"
                    )
                anim = doc["animations"][0]
                frame = validate_preview_frame(doc, binary, anim)
                self.messages.put(
                    ("preview", generation, key, character, clip, doc, binary, frame)
                )
            except Exception as exc:
                self.messages.put(("preview_error", generation, str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def install_preview(self, generation, key, character, clip, doc, binary, frame):
        if generation != self.prepare_generation or self.disposed:
            return
        try:
            anim = doc["animations"][0]
            self.preview = (doc, binary, anim)
            self.loaded = key
            # GL resources are created on the Tk thread; frame positions are updated
            # separately without re-uploading topology or textures.
            self.escena, self.buffer = None, None
            gl.forget(("anim", character))
            parts, texturas = glb_piezas(doc, binary, frame)
            scene_type = (
                "personaje_glb_criatura"
                if character in ("cowthulu", "nightbeast")
                else "personaje_glb"
            )
            self.escena = gl.escena(("anim", character), parts, texturas, scene_type)
            self.position.set(0)
            self.slider.configure(to=clip["duration"])
            # Los datos del clip ya van en la capa de texto sobre el visor; no
            # se repiten en el panel. self.status queda para avisos y errores.
            self.view_status.configure(
                text=f'{clip["name"].split("/")[-1]}\n'
                f'{character} · {clip["duration"]:.2f} s · '
                f'{len(clip["tracks"])} tracks'
            )
            self.render_frame(0)
            # Con Auto Play marcado, cada clip nuevo arranca solo.
            if self.auto_play.get():
                self.start_play()
        except Exception as exc:
            self._discard_preview()
            self.status.configure(text=f"Could not load: {exc}")
            self.view_status.configure(text="")

    def _discard_preview(self):
        self.preview = None
        self.loaded = None
        self.escena = None
        self.buffer = None
        self.view_status.configure(text="")
        # Sin esta llamada el deslizador se queda con el rango del clip
        # anterior, movido sobre una escena que ya no existe.
        self._reset_scrubber()

    def render_frame(self, t):
        if not self.preview:
            return
        doc, binary, anim = self.preview
        try:
            if self.escena is None:
                raise gl.SinOpenGL("no accelerated OpenGL context")
            mallas = posed_meshes(doc, binary, anim, t)
            if len(mallas) != len(
                self.escena.dibujos
            ):  # el clip no encaja con la escena
                self.escena = None
                raise gl.SinOpenGL("the scene does not match this character")
            width, height = self.canvas.view_size()
            # La escena se reutiliza entre clips, así que el estado de la
            # rejilla se reaplica en cada frame en vez de solo al crearla.
            self.escena.mostrar_grid = self.show_grid.get()
            self.escena.fijar_poses(aplanar(mallas, self.buffer), (t,))
            self.buffer = self.escena.posiciones
            imagen = self.escena.render(
                width,
                height,
                self.camera.yaw,
                self.camera.pitch,
                self.camera.zoom,
                self.camera.pan,
                self.camera,
            )
            if self.show_bones.get():
                # El esqueleto se dibuja encima de la escena ya rasterizada,
                # igual que en el visor de personajes.
                self._draw_bones(imagen, doc, binary, anim, t, width, height)
            set_photo(self.canvas, imagen)
            duration = anim["extras"]["sourceDuration"]
            self.time_label.configure(text=self._time_text(t, duration))
        except Exception as exc:
            self._discard_preview()
            self.status.configure(text=f"Preview unavailable: {exc}")
            self.stop()

    def _time_text(self, t, duration):
        """Reloj en segundos enteros: m:ss / m:ss.

        Redondea en vez de truncar para que un clip de 2.6 s no se lea como
        2 s durante casi medio segundo.
        """

        def clock(seconds):
            minutes, rest = divmod(int(round(max(0.0, float(seconds)))), 60)
            return f"{minutes}:{rest:02d}"

        return f"{clock(t)} / {clock(duration)}"

    def _reset_scrubber(self):
        """Deja el deslizador en un estado coherente cuando no hay clip."""
        self.position.set(0)
        self.slider.configure(to=1)
        self.time_label.configure(text=self._time_text(0, 0))

    def _draw_bones(self, imagen, doc, binary, anim, t, width, height):
        """Pinta el esqueleto del clip sobre la imagen ya renderizada."""
        huesos = posed_bones(doc, binary, anim, t)
        if not huesos:
            return
        overlay = ImageDraw.Draw(imagen)
        puntos = [
            self.escena.proyectar(
                h["world"][3, :3],
                width,
                height,
                self.camera.yaw,
                self.camera.pitch,
                self.camera.zoom,
                self.camera.pan,
                self.camera,
            )
            for h in huesos
        ]
        for i, hueso in enumerate(huesos):
            if hueso["parent"] >= 0:
                overlay.line(
                    (puntos[hueso["parent"]], puntos[i]), fill=(88, 235, 220), width=2
                )
        for x, y in puntos:
            overlay.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(225, 255, 250))

    def scrub(self, value):
        # El ttk.Scale ya mueve la variable al arrastrar, pero no cuando el
        # comando llega por otra via (teclado, o una llamada directa). Fijarla
        # aqui deja el pulgar, la posicion y el frame dibujado de acuerdo, que
        # si no el render va a un sitio y el cursor a otro.
        if self.updating_slider or not self.preview:
            return
        self.updating_slider = True
        self.position.set(float(value))
        self.updating_slider = False
        # Arrastrar mientras suena compite con tick(), que volveria a imponer
        # su propia posicion en el siguiente paso y haria saltar el cursor.
        if self.playing:
            self.stop()
        self.render_frame(float(value))

    def _fps_button_refresh(self):
        self.fps_button.configure(text=f"{FPS_VALORES[self.fps_index]} FPS")

    def ciclar_fps(self):
        self.fps_index = (self.fps_index + 1) % len(FPS_VALORES)
        self._fps_button_refresh()
        # Si esta sonando, el nuevo ritmo se aplica en el siguiente tick.
        self.last_tick = time.perf_counter()

    def _auto_play_toggled(self):
        """Marcar Auto Play arranca el clip ya cargado; desmarcarlo, lo para.

        Sin esto solo arrancaba al cargar un clip nuevo, y si la casilla se
        marcaba con uno en pantalla no pasaba nada: prepare() ve la misma clave
        y no reinstala.
        """
        if self.auto_play.get():
            self.start_play()
        else:
            self.stop()

    def start_play(self):
        """Arranca la reproduccion; no hace nada si ya esta sonando."""
        if not self.preview or self.playing:
            return
        self.playing = True
        self.last_tick = time.perf_counter()
        self.play_button.configure(text="❚❚ Pause")
        self.tick()

    def toggle_play(self):
        if not self.preview:
            return
        if self.playing:
            self.stop()
            return
        self.start_play()

    def stop(self):
        self.playing = False
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        self.play_button.configure(text="▶ Play")

    def tick(self):
        if not self.playing or not self.preview:
            return
        now = time.perf_counter()
        elapsed = now - self.last_tick
        self.last_tick = now
        duration = self.preview[2]["extras"]["sourceDuration"]
        t = self.position.get() + elapsed
        if t >= duration:
            if self.loop.get():
                t = t % duration
            else:
                t = duration
                self.stop()
        self.updating_slider = True
        self.position.set(t)
        self.updating_slider = False
        self.render_frame(t)
        # El paso del reloj es el del clip, no uno fijo: con 50 ms fijos la
        # reproduccion se quedaba a 20 pasos por segundo y se notaba a tirones.
        # Se pide 1000/fps y se deja al menos 1 ms para no meter un after(0).
        if self.playing:
            self.timer = self.after(
                max(1, round(1000 / FPS_VALORES[self.fps_index])), self.tick
            )

    def export_glb(self, fmt="glb"):
        rows = self.selected_rows()
        if not rows:
            return
        resolved = self._export_character(rows)
        if resolved is None:
            messagebox.showwarning(
                "Incompatible skeletons",
                "Export human, Cowthulu and Night Beast clips separately.",
            )
            return
        skeleton, character = resolved
        # Igual que en el visor de personajes: se exporta directo a una
        # subcarpeta dentro de exports/, sin pedir ruta cada vez.
        model_name = export_model_name(character)
        export_dir = self.app.export_root / f"{model_name}_export"
        export_dir.mkdir(parents=True, exist_ok=True)
        # attach_animations mete todos los clips elegidos en un unico fichero,
        # asi que el nombre de animacion solo existe si se exporta uno. Con
        # varios se queda el del personaje y son los clips los que lo distinguen
        # dentro del fichero.
        file_stem = model_name
        if len(rows) == 1:
            clip = export_model_name(rows[0]["name"].rsplit("/", 1)[-1])
            file_stem = export_model_name(f"{model_name}_{clip}")
        target = export_dir / f"{file_stem}.{fmt}"
        fbx = fmt == "fbx"
        blender = self.app.resolve_blender() if fbx else None
        if fbx and not blender:
            return
        if self.exporting:
            return
        self.exporting = True
        self.status.configure(text=f"Exporting {len(rows)} clips…")

        def work():
            try:
                with self.model_lock:
                    model = self._static_model(character)
                with zipfile.ZipFile(self.app.paths["animations"]) as z:
                    clips = [parse_banm(z.read(r["file"])) for r in rows]
                    if character == "cowthulu":
                        reference = None
                        fallback = self._cowthulu_reference(rows[0])
                        reference_pose = None
                    elif character == "nightbeast":
                        reference = None
                        fallback = None
                        reference_pose = None
                    else:
                        reference = None
                        reference_pose = None
                        ref_name = character_fallback(
                            self.app.character_rows, character
                        )
                        fallback = parse_banm(z.read(self.by_name[ref_name]["file"]))
                # glb solo se escribe directamente en el destino cuando no hay conversion
                # que hacer; fbx y gltf parten de un GLB intermedio en cache.
                directo = fmt == "glb"
                glb = target if directo else self.app.cache / "animation_export.glb"
                result = attach_animations(
                    model,
                    clips,
                    reference,
                    glb,
                    fps=30,
                    fallback=fallback,
                    reference_pose=reference_pose,
                    reference_pose_time=1.0,
                )
                if fmt == "fbx":
                    export_path(glb, target, blender)
                elif fmt == "gltf":
                    export_gltf(glb, target)
                self.messages.put(("ok", f"{len(result)} animations saved to {target}"))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def poll_messages(self):
        if self.disposed:
            return
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "preview":
                    _, generation, key, character, clip, doc, binary, frame = message
                    self.install_preview(
                        generation, key, character, clip, doc, binary, frame
                    )
                elif kind == "preview_error":
                    _, generation, text = message
                    if generation == self.prepare_generation:
                        self._discard_preview()
                        self.status.configure(
                            text=f"Invalid animation; it was not loaded: {text}"
                        )
                else:
                    _, text = message
                    self.exporting = False
                    if kind == "error":
                        messagebox.showerror("Export animations", text)
                    self.status.configure(text=text)
        except queue.Empty:
            pass
        self.poll_timer = self.after(100, self.poll_messages)

    def dispose(self):
        self.disposed = True
        self.cancel_prepare()
        self.stop()
        if self.poll_timer:
            self.after_cancel(self.poll_timer)
            self.poll_timer = None
        for button in self.floating_buttons:
            button.destroy()
        self.floating_buttons = []

    def export_res(self):
        """Extrae los .res originales; sin boton en la interfaz, se deja como
        API por si se quiere recuperar desde otro sitio."""
        rows = self.selected_rows()
        if not rows:
            return
        folder = filedialog.askdirectory(title="Folder for original BANM files")
        if not folder:
            return
        try:
            with zipfile.ZipFile(self.app.paths["animations"]) as z:
                for row in rows:
                    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", row["name"].split("/")[-1])
                    (Path(folder) / f'{row["entry"]}_{name}.res').write_bytes(
                        z.read(row["file"])
                    )
            self.status.configure(
                text=f"{len(rows)} original BANM files saved to {folder}"
            )
        except Exception as exc:
            messagebox.showerror("Extract BANM", str(exc))
