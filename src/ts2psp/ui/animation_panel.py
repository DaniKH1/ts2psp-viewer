"""Panel de animaciones: lista de clips y reproduccion.

Se extrae aparte de la pestaña para que el mismo panel se pueda montar en mas de
un sitio. Ahora mismo vive en dos: su propia pestaña y, dentro de la de
personajes, debajo del boton que conmuta a 'Animation View'. Duplicar la logica
de reproduccion habria sido copiar 800 lineas y que las dos se desincronizasen.
"""

import json
import math
import queue
import re
import shutil
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
import ts2psp.rendering.opengl as gl
from ts2psp.ui.textured_list import TexturedListbox
from ts2psp.ui.theme import LIST_PANEL_PATH
from ts2psp.ui.common import (
    FPS_VALORES,
    character_fallback,
    export_model_name,
    set_photo,
)
from ts2psp.ui.i18n import localize_widget_tree, translate
from ts2psp.exporting.blender import export_path

PERSONAJES_POR_NOMBRE = (
    ("nightbeast", "nightbeast"),
    ("docdom", "doctordominion"),
    ("roberta", "robertarossum"),
    ("sinjin", "sinjinbalani-kine"),
    ("ritualdance", "sinjinbalani-kine"),
    ("player", "tinamansfield"),
    ("vampire", "virginiyafeng_vamp"),
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


class AnimationStage:
    """El lienzo donde un `AnimationPanel` pinta los fotogramas del clip.

    El panel no sabe en que pestaña esta: solo necesita el tamano del lienzo, la
    camara, que le pinten la imagen y donde escribir el pie de estado. Eso permite
    que la misma clase de panel funcione en la pestaña de animaciones y en la de
    personajes sin saber cual es.
    """

    def __init__(self, canvas, camera, show_grid, show_bones, status_label):
        self.canvas = canvas
        self.camera = camera
        self.show_grid = show_grid
        self.show_bones = show_bones
        self.status_label = status_label

    def view_size(self):
        return self.canvas.view_size()

    def camera_args(self):
        """Lo que pide `escena.render`: giro, inclinacion, zoom, desplazamiento."""
        return (
            self.camera.yaw,
            self.camera.pitch,
            self.camera.zoom,
            self.camera.pan,
            self.camera,
        )

    def present(self, imagen):
        set_photo(self.canvas, imagen)

    def overlay(self, imagen, escena, doc, binary, anim, t, width, height):
        """Pinta el esqueleto del clip sobre la imagen ya rasterizada."""
        if not self.show_bones.get():
            return
        huesos = posed_bones(doc, binary, anim, t)
        if not huesos:
            return
        dibujo = ImageDraw.Draw(imagen)
        puntos = [
            escena.proyectar(h["world"][3, :3], width, height, *self.camera_args())
            for h in huesos
        ]
        for i, hueso in enumerate(huesos):
            if hueso["parent"] >= 0:
                dibujo.line(
                    (puntos[hueso["parent"]], puntos[i]), fill=(88, 235, 220), width=2
                )
        for x, y in puntos:
            dibujo.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(225, 255, 250))

    def set_view_status(self, text):
        self.status_label.configure(text=translate(text, self.status_label.winfo_toplevel().language))


class AnimationPanel(ttk.Frame):
    """Lista de clips, controles de reproduccion y exportacion de animaciones.

    No pinta nada por su cuenta: delega en `self.visor`, que es un
    `AnimationStage` que le pasa quien lo monta. Asi el mismo panel sirve en
    varias pestañas y solo cambia el lienzo de destino.
    """

    def __init__(self, parent, app, titulo="Animations", con_personaje=False):
        super().__init__(parent)
        self.app = app
        with zipfile.ZipFile(app.paths["animations"]) as z:
            self.rows = json.loads(z.read("catalog.json"))
        self.by_name = {r["name"]: r for r in self.rows}
        self.visible = []
        self.loaded = None
        self.preview = None
        self.preview_character = None
        self.preview_clip = None
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
        # Lo inyecta el anfitrion justo despues de crear el panel. Hasta entonces
        # no se pinta nada, que es lo que hace falta porque el lienzo todavia no
        # existe cuando se construye.
        self.visor = None
        # Callback del anfitrion para que se entere del personaje que ha elegido
        # el clip. La lista de personajes vive en la pestaña, no aqui.
        self.on_character = None
        # False cuando el panel esta montado pero oculto (la vista de animaciones
        # de la pestaña de personajes). En ese caso no escribe el pie de estado
        # sobre el lienzo, que lo esta usando otro visor.
        self.visible_on_screen = True
        self.all_characters = list(app.characters)
        self.character = tk.StringVar(
            value="bellagoth" if "bellagoth" in app.characters else app.characters[0]
        )
        if con_personaje:
            self.replace_character = tk.BooleanVar(value=False)
            self.replace_button = ttk.Button(
                self,
                text="Replace character",
                style="Toggle.TButton",
                command=self.toggle_replace_character,
            )
            self.replace_button.pack(fill="x", pady=(8, 0))
            self.character_frame = ttk.Frame(self)
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
                self.char_list.insert("end", nombre_publico(name, self.app.language))
            self.char_list.bind("<<ListboxSelect>>", self._character_selected)
            inicial = self.character.get()
            if inicial in self.all_characters:
                self.char_list.selection_set(self.all_characters.index(inicial))

        ttk.Label(
            self,
            text=titulo,
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        # Misma textura que el buscador de personajes; la etiqueta 'Search' estorba
        # con el titulo justo encima.
        self.filter = tk.StringVar()
        entry = ttk.Entry(
            self, textvariable=self.filter, width=31, style="CharacterSearch.TEntry"
        )
        entry.pack(fill="x", pady=(6, 14))
        entry.bind("<KeyRelease>", self.refresh)
        ttk.Label(self, text="Select multiple clips with Ctrl or Shift").pack(
            anchor="w", pady=(6, 0)
        )
        list_frame = ttk.Frame(self)
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
            # Negrita, que es lo que mas ayuda a leerla, pero a 10 y no a 11: los nombres
            # de clip son largos ('a-cas-idle-outgoing-m-start') y a 11 se salian
            # de la columna mas de la cuenta.
            font=(self.app._sims_font_family, 10, "bold"),
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
            self,
            text="Group",
            anchor="center",
            font=(self.app._sims_font_family, 10, "bold"),
        ).pack(fill="x", pady=(6, 0))
        self.group = tk.StringVar(value="All")
        groups = ["All", "Individual", "Social", "Scenes", "Creatures"]
        group_box = ttk.Combobox(
            self, textvariable=self.group, values=groups, state="readonly", width=28
        )
        group_box.pack(fill="x", pady=(0, 2))
        group_box.bind("<<ComboboxSelected>>", self.refresh)
        ttk.Separator(self, orient="horizontal").pack(fill="x", pady=(4, 0))
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 0))
        self.play_button = ttk.Button(controls, text="▶ Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        # Boton de FPS: pulsa y va cyclando. Comparte fila con el Play. Los botones del
        # tema piden 120 px por defecto (ttk los crea con 20 caracteres de ancho,
        # mida lo que mida el texto), asi que en una columna de 222 el FPS se
        # queda en 102 y sigue mostrando '20 FPS' entero.
        self.fps_index = 0
        self.fps_button = ttk.Button(controls, text="", command=self.ciclar_fps)
        self.fps_button.pack(side="right")
        # Loop y reloj en una segunda fila. Con el Loop tambien en la de arriba, en una
        # sola fila el FPS se quedaba en 58 px y mostraba '20 F'.
        lower = ttk.Frame(self)
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
            self,
            from_=0,
            to=1,
            variable=self.position,
            command=self.scrub,
            style="Clips.Horizontal.TScale",
        )
        self.slider.pack(fill="x", pady=6)
        self._fps_button_refresh()
        # Solo para avisos y errores de carga o exportacion; el contador de
        # clips ya no se pinta aqui.
        self.status = ttk.Label(self, text="", wraplength=230)
        self.status.pack(fill="x", pady=(4, 0))
        self.refresh()

    def start(self):
        """Engancha el visor y selecciona el primer clip.

        Va aparte del constructor porque el anfitrion crea el lienzo despues del
        panel: sin esto, `prepare()` se pondria a pintar antes de tener donde.
        """
        self.poll_timer = self.after(100, self.poll_messages)
        if not self.visible:
            return
        inicial = next(
            (
                i
                for i, r in enumerate(self.visible)
                if r["name"].endswith("a-cas-idle-breathe-f")
            ),
            0,
        )
        self.listbox.selection_set(inicial)
        self.listbox.see(inicial)
        self.schedule_prepare()

    def redibujar(self):
        """Repinta el fotograma actual; lo llama el anfitrion al cambiar la vista."""
        self.render_frame(self.position.get())

    def set_character(self, name, avisar=True):
        """Fija el personaje del clip y avisa al anfitrion si ha cambiado."""
        if name == self.character.get():
            return
        self.character.set(name)
        if avisar and self.on_character is not None:
            self.on_character(name)

    def _character_selected(self, _event=None):
        """Sincroniza la lista de personajes con el StringVar."""
        selection = self.char_list.curselection()
        if not selection:
            return
        self.set_character(slug_publico(self.char_list.get(selection[0])))

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
        if getattr(self, "replace_character", None) is not None and (
            self.replace_character.get()
        ):
            return False
        destino = self._character_for_row(rows[0])
        if destino is None:
            return False
        if destino not in self.all_characters:
            return False
        if self.character.get() == destino:
            return False
        self.set_character(destino)
        self.loaded = None
        self._olvidar_preview()
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
            display_name = nombre_publico(name, self.app.language)
            if term in name.casefold() or term in display_name.casefold():
                self.char_list.insert("end", display_name)
        # La lista conservaria el desplazamiento anterior, que con menos
        # filas dejaria la vista a medio camino.
        self.char_list.yview("moveto", 0)

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
        seleccionados, asi que el dialogo dice cuantos son y con que personaje,
        y avisa antes si mezclan esqueletos incompatibles.
        """
        rows = self.selected_rows()
        resolved = self._export_character(rows)
        dialog = tk.Toplevel(self)
        dialog.title(translate("Export as...", self.app.language))
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
        ttk.Separator(content, orient="horizontal").pack(fill="x", pady=(10, 6))
        incluir = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            content,
            text="Export the selected animation",
            variable=incluir,
        ).pack(anchor="w")
        ttk.Label(
            content,
            text="Unmarked, only the character model is exported.",
            wraplength=380,
        ).pack(anchor="w", pady=(2, 0))
        buttons = ttk.Frame(content)
        buttons.pack(fill="x", pady=(12, 0))
        choice = {"format": None, "incluir": True}

        def accept():
            choice["format"] = selected.get()
            choice["incluir"] = incluir.get()
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(
            buttons, text="Export", style="Primary.TButton", command=accept
        ).pack(side="right", padx=(0, 8))
        localize_widget_tree(dialog, self.app.language)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.wait_window()
        # Un dialogo que solo avisa no debe lanzar una exportacion imposible.
        if resolved is None:
            return None
        return choice

    def export_as(self):
        """Punto de entrada del boton flotante 'Export as...'."""
        if self.exporting:
            return
        if not self.selected_rows():
            messagebox.showinfo(
                translate("Export animations", self.app.language),
                translate("Select one or more clips in the list first.", self.app.language),
            )
            return
        choice = self.choose_export_format()
        if not choice:
            return
        self.export_glb(choice["format"], incluir=choice["incluir"])

    def refresh(self, _event=None):
        self.cancel_prepare()
        self.stop()
        key = {
            "Individual": "solo_anims",
            "Social": "social_anims",
            "Scenes": "cutscene_anims",
        }.get(translate(self.group.get(), "en"))
        term = self.filter.get().casefold()
        self.visible = []
        for row in self.rows:
            category = row["category"]
            if key and category != key:
                continue
            if translate(self.group.get(), "en") == "Creatures" and category not in (
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
        self._olvidar_preview()
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
        # Rebuild older models that lack the fixed joint orientations required
        # by BANM rotations. Bind offsets and accessories are retained as well.
        path = self.app.cache / ("character_" + character + "_texture_names_v5.glb")
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
            # Aparte, para poder reescribir los datos de la esquina al conmutar
            # la vista. `self.preview` es la escena, no lo que se cuenta.
            self.preview_character = character
            self.preview_clip = clip
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
            self.refresh_view_status()
            self.render_frame(0)
            # Con Auto Play marcado, cada clip nuevo arranca solo.
            if self.auto_play.get():
                self.start_play()
        except Exception as exc:
            self._discard_preview()
            self.status.configure(
                text=translate(f"Could not load: {exc}", self.app.language)
            )

    def _olvidar_preview(self):
        """Se suelta el clip cargado, con los datos que iba a poner en el visor."""
        self.preview = None
        self.preview_character = None
        self.preview_clip = None

    def refresh_view_status(self):
        """Escribe en la capa de texto del visor los datos del clip que se ve.

        Se vuelve a llamar al conmutar la vista: si el clip ya estaba cargado no
        se reinstala y entonces no pasaria por aqui, con lo que la esquina se
        queda con los datos del personaje de la otra vista.
        """
        if self.visor is None or not self.visible_on_screen:
            return
        clip = self.preview_clip
        if self.preview is None or clip is None:
            self.visor.set_view_status("")
            return
        self.visor.set_view_status(
            f'{clip["name"].split("/")[-1]}\n'
            f'{self.preview_character} · {clip["duration"]:.2f} s · '
            f'{len(clip["tracks"])} tracks'
        )

    def _discard_preview(self):
        self._olvidar_preview()
        self.loaded = None
        self.escena = None
        self.buffer = None
        self.refresh_view_status()
        # Sin esta llamada el deslizador se queda con el rango del clip
        # anterior, movido sobre una escena que ya no existe.
        self._reset_scrubber()

    def render_frame(self, t):
        if not self.preview or self.visor is None:
            return
        if not self.visible_on_screen:
            # El clip se carga en segundo plano aunque el panel este tapado; el
            # fotograma se dibuja cuando se muestra, no mientras esta oculto.
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
            width, height = self.visor.view_size()
            # La escena se reutiliza entre clips, así que el estado de la
            # rejilla se reaplica en cada frame en vez de solo al crearla.
            self.escena.mostrar_grid = self.visor.show_grid.get()
            self.escena.fijar_poses(aplanar(mallas, self.buffer), (t,))
            self.buffer = self.escena.posiciones
            imagen = self.escena.render(width, height, *self.visor.camera_args())
            self.visor.overlay(imagen, self.escena, doc, binary, anim, t, width, height)
            self.visor.present(imagen)
            duration = anim["extras"]["sourceDuration"]
            # El cursor se mueve con el fotograma que se acaba de dibujar. Si solo
            # se actualizara el reloj, el pulgar se quedaria donde estuviera y
            # marcaria un instante distinto del que se ve. El guardia evita que
            # el ttk.Scale reentree por su propio comando.
            if not self.updating_slider:
                self.updating_slider = True
                self.position.set(t)
                self.updating_slider = False
            self.time_label.configure(text=self._time_text(t, duration))
        except Exception as exc:
            self._discard_preview()
            self.status.configure(
                text=translate(f"Preview unavailable: {exc}", self.app.language)
            )
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
        self.play_button.configure(text=translate("❚❚ Pause", self.app.language))
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
        self.play_button.configure(text=translate("▶ Play", self.app.language))

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

    def export_glb(self, fmt="glb", incluir=True):
        """Exporta los clips elegidos, o solo el modelo si `incluir` es False."""
        rows = self.selected_rows()
        if not rows:
            return
        resolved = self._export_character(rows)
        if resolved is None:
            messagebox.showwarning(
                translate("Incompatible skeletons", self.app.language),
                translate(
                    "Export human, Cowthulu and Night Beast clips separately.",
                    self.app.language,
                ),
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
        if not incluir:
            file_stem = export_model_name(f"{model_name}_model")
        elif len(rows) == 1:
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
        self.status.configure(
            text=translate(
                (
                f"Exporting {model_name}…"
                if not incluir
                else f"Exporting {len(rows)} clips…"
                ),
                self.app.language,
            )
        )

        def work():
            try:
                with self.model_lock:
                    model = self._static_model(character)
                if not incluir:
                    # Solo el modelo: el GLB del personaje tal cual, sin clips.
                    # Se copia entero en vez de reconstruirlo, porque
                    # convert_character ya lo deja con texturas y materiales.
                    destino = (
                        target
                        if fmt == "glb"
                        else (self.app.cache / "model_only_export.glb")
                    )
                    shutil.copyfile(model, destino)
                    if fmt == "fbx":
                        export_path(destino, target, blender)
                    elif fmt == "gltf":
                        export_gltf(destino, target)
                    self.messages.put(("ok", f"model saved to {target}"))
                    return
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
                            text=translate(
                                f"Invalid animation; it was not loaded: {text}",
                                self.app.language,
                            )
                        )
                else:
                    _, text = message
                    self.exporting = False
                    if kind == "error":
                        messagebox.showerror(
                            translate("Export animations", self.app.language),
                            translate(text, self.app.language),
                        )
                    self.status.configure(text=translate(text, self.app.language))
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
            messagebox.showerror(
                translate("Extract BANM", self.app.language), str(exc)
            )
