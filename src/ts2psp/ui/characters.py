"""Character tab and its controls."""

import math
import tkinter as tk
from tkinter import messagebox, ttk
from ts2psp.formats.characters import convert_character, nombre_publico
from ts2psp.rendering.characters import (
    draw_character,
    load_character,
    shape_key_options,
)
from ts2psp.exporting.gltf import export_gltf
from ts2psp.ui.camera import OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.ui.textured_list import TexturedListbox
from ts2psp.ui.animation_panel import AnimationPanel, AnimationStage
from ts2psp.ui.floating_button import FloatingButton, place_right_buttons
from ts2psp.ui.texture_gallery import TextureGallery
from ts2psp.ui.theme import ANCHO_TROUGH_CLIPS, ANCHO_TROUGH_SHAPES, LIST_PANEL_PATH
from ts2psp.ui.common import choose_export_format, export_model_name, set_photo
from ts2psp.ui.i18n import translate
from ts2psp.exporting.blender import export_path

# La columna de la derecha no mide lo mismo en las dos vistas: el recorrido del
# pulgar de los Shape Keys es mas estrecho que el de los clips. Cada deslizador
# lleva un trough hecho del ancho justo, asi que el de la columna sale de ahi, con
# el margen izquierdo que lleva la propia columna.
ANCHO_SHAPES = ANCHO_TROUGH_SHAPES + 8
ANCHO_CLIPS = ANCHO_TROUGH_CLIPS + 8

# Alto de la lista de personajes, en pixeles (la lista es un `tk.Canvas`). El
# mismo que la de props: las dos columnas se leen igual de un vistazo.
ALTO_LISTA = 360


def ancho_del_boton(top, text):
    """El ancho de la pastilla de un botón flotante, con el texto que lleva.

    No es un método de la clase a proposito: `_floating_button` se ata sola a
    mano en las comprobaciones, sin el resto de la clase alrededor.
    """
    return top._sims_floating_text_width(text) + 2 * 14


class CharacterTab(ttk.Frame):
    """La pestaña de personajes, con sus dos vistas y el panel de clips.

    Comparten el mismo lienzo: en Character View dibuja el personaje estatico y
    en Animation View el clip que se haya elegido. Lo que decide cual de los dos
    esta delante son `animation_view` y, dentro de la vista de animaciones, el
    boton 'Replace character'.
    """

    def __init__(self, parent, app):
        super().__init__(parent, padding=6)
        self.app = app
        self.names = app.characters
        self.visible = []
        self.current = ""
        self.parts = []
        self.bones = []
        self.textures = {}
        self.timer = None
        self.last_frame = 0.0
        # Que de las dos columnas esta delante. 'Replace character' solo tiene
        # sentido en la vista de animaciones, que es donde el clip elige el
        # personaje por su cuenta.
        self.animation_view = tk.BooleanVar(value=False)
        self.replace_character = tk.BooleanVar(value=False)

        # -- columna izquierda: el boton de vista y la lista de personajes ----
        left = ttk.Frame(self, width=250)
        left.pack(side="left", fill="both", padx=(0, 8))
        left.pack_propagate(False)
        # La etiqueta del boton dice a donde se va, no donde se esta.
        self.view_button = ttk.Button(
            left,
            text="Animation View",
            style="ViewToggle.TButton",
            command=self.toggle_view,
        )
        self.view_button.pack(fill="x", pady=(0, 4))
        # Este no se empaqueta aqui: `_repack_left` decide, y tiene que quedar
        # por encima de la lista, que es donde se lee a quien sustituye.
        self.replace_button = ttk.Button(
            left,
            text="Replace character",
            style="ViewToggle.TButton",
            command=self.toggle_replace_character,
        )
        self.characters_frame = ttk.Frame(left)
        self.characters_heading = ttk.Label(
            self.characters_frame,
            text="Characters",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        )
        self.characters_heading.pack(fill="x")
        self.filter = tk.StringVar()
        self.search_entry = ttk.Entry(
            self.characters_frame,
            textvariable=self.filter,
            width=30,
            style="CharacterSearch.TEntry",
        )
        self.search_entry.pack(fill="x", pady=(5, 14))
        self.search_entry.bind("<KeyRelease>", self.refresh)
        # Las texturas van en su propio bloque, y no dentro de `characters_frame`:
        # son de quien esta cargado, y en la vista de animaciones quien esta
        # cargado es el clip. Se abren y se cierran con la vista.
        self.textures_frame = ttk.Frame(left)
        ttk.Separator(self.textures_frame, orient="horizontal").pack(
            fill="x", pady=(16, 12)
        )
        ttk.Label(
            self.textures_frame,
            text="Textures",
            anchor="center",
            font=(self.app._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 4))
        self.gallery = TextureGallery(self.textures_frame, app)
        self.gallery.pack(fill="x", expand=False)
        character_list_frame = ttk.Frame(self.characters_frame)
        self.character_list_frame = character_list_frame
        character_list_frame.pack(fill="both", pady=(3, 0))
        self.listbox = TexturedListbox(
            character_list_frame,
            LIST_PANEL_PATH,
            width=222,
            height=ALTO_LISTA,
            font=(self.app._sims_font_family, 10, "bold"),
        )
        character_scroll = ttk.Scrollbar(
            character_list_frame, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=character_scroll.set)
        character_scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.select)

        # -- columna derecha: Shape Keys y texturas, o los clips --------------
        shapes = ttk.Frame(self, width=ANCHO_SHAPES, padding=(8, 0, 0, 0))
        shapes.pack(side="right", fill="y")
        shapes.pack_propagate(False)
        self.shapes_column = shapes
        # Las dos vistas de la columna no se apilan: se empaqueta una y la otra
        # se quita. Con las dos puestas y expand=True, pack reparte segun lo
        # pedido por cada una y la segunda se queda aplastada a un pixel.
        self.shapes_view = ttk.Frame(shapes)
        self.shapes_view.pack(fill="both", expand=True)
        ttk.Label(
            self.shapes_view,
            text="Shape Keys",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        shape_list_frame = ttk.Frame(self.shapes_view)
        shape_list_frame.pack(fill="x", expand=False, pady=(5, 8))
        self.shape_list = TexturedListbox(
            shape_list_frame,
            LIST_PANEL_PATH,
            width=162,
            height=510,
            font=(self.app._sims_font_family, 10, "bold"),
        )
        shape_scroll = ttk.Scrollbar(
            shape_list_frame, orient="vertical", command=self.shape_list.yview
        )
        self.shape_list.configure(yscrollcommand=shape_scroll.set)
        shape_scroll.pack(side="right", fill="y")
        self.shape_list.pack(side="left", fill="both", expand=True)
        self.shape_list.bind("<<ListboxSelect>>", self.select_shape)
        ttk.Label(
            self.shapes_view,
            text="Strength",
            anchor="center",
            font=(self.app._sims_font_family, 10, "bold"),
        ).pack(fill="x")
        self.shape_weight = tk.DoubleVar(value=0)
        # Sin `length`: el trough del tema ya esta hecho del ancho justo, y lo
        # que tiene que medir el widget es lo que mide el.
        self.shape_slider = ttk.Scale(
            self.shapes_view,
            from_=-1,
            to=1,
            variable=self.shape_weight,
            command=self.on_shape_slider,
            style="Shape.Horizontal.TScale",
        )
        self.shape_slider.pack(fill="x", pady=(3, 6))
        self.shape_slider.state(["disabled"])
        presets = ttk.Frame(self.shapes_view)
        presets.pack(fill="x", pady=(0, 6))
        self.shape_preset_buttons = []
        self.shape_preset_values = (-1, 0, 1)
        for column in range(3):
            presets.columnconfigure(column, weight=1, uniform="shape_presets")
        for value in self.shape_preset_values:
            button = ttk.Button(
                presets,
                image=self.app._sims_shape_preset_images[value]["disabled"],
                command=lambda weight=value: self.set_shape_weight(weight),
                style="ShapePreset.TButton",
            )
            button.grid(row=0, column=value + 1, padx=3)
            button.state(["disabled"])
            button.preset_value = value
            button.preset_hover = False
            button.bind(
                "<Enter>",
                lambda _event, item=button: self._set_shape_preset_hover(item, True),
            )
            button.bind(
                "<Leave>",
                lambda _event, item=button: self._set_shape_preset_hover(item, False),
            )
            self.shape_preset_buttons.append(button)
        # El panel de animaciones, movido tal cual de la pestaña de animaciones.
        # Vive en la misma columna que los Shape Keys pero NO se empaqueta aqui:
        # se empaqueta al conmutar la vista.
        self.animation_frame = ttk.Frame(shapes)
        self.animations = AnimationPanel(self.animation_frame, app)
        self.animations.pack(fill="both", expand=True)

        # -- el visor, en el centro, con los botones flotantes encima ---------
        center = ttk.Frame(self)
        center.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(center, minimum=(500, 550))
        self.canvas.on_resize = self.schedule
        self.camera = OrbitController(self.canvas, self.schedule, yaw=math.pi)
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button(
            "Export as...", self.export_segun_vista
        )
        self.shape = tk.StringVar(value="")
        self.shape_id = None
        self.show_bones = tk.BooleanVar(value=False)
        self.show_grid = tk.BooleanVar(value=True)
        self.recenter_button = self._floating_button("Reset view", self.camera.reset)
        self.bones_button = self._floating_button("Bones", self.toggle_bones)
        self.grid_button = self._floating_button(
            "Grid", self.toggle_grid, selected=True
        )
        self.status = ttk.Label(
            self.canvas,
            text="",
            padding=(5, 3),
            wraplength=230,
            background=self.canvas.cget("background"),
            foreground="#ffffff",
        )
        self.status_toolbar_window = self.canvas.create_window(
            10, 0, anchor="sw", window=self.status
        )
        self.canvas.bind("<Configure>", self._position_viewport_controls, add="+")
        self.after_idle(self._position_viewport_controls)

        # El panel se engancha al lienzo de aqui y avisa de los personajes que
        # eligen las reglas de cada clip. Va al final porque hasta ahora no
        # existia el lienzo donde tiene que pintar.
        self.animations.visor = AnimationStage(
            self.canvas, self.camera, self.show_grid, self.show_bones, self.status
        )
        self.animations.on_character = self._animation_character_changed
        # La misma bandera que la del boton: con ella marcada, `_auto_character`
        # deja de imponer el genero del clip y manda lo que elija el usuario.
        self.animations.replace_character = self.replace_character
        # De momento esta oculto, asi que no pisa el pie de estado del visor.
        self.animations.visible_on_screen = False

        self._repack_left()
        self.animations.start()
        self.refresh()
        if self.visible:
            self.select_name(
                "bellagoth" if "bellagoth" in self.visible else self.visible[0]
            )
            # Lo que viene por defecto no es lo primero de la lista, y `select_name`
            # arrastra hasta el. Arriba del todo es donde se distingue de lo que
            # elige el usuario, que si se conserva.
            self.listbox.al_principio()

    # -- las dos vistas ----------------------------------------------------

    def toggle_view(self):
        """Conmuta entre la lista de personajes y la de clips.

        Cambia tambien el ancho de la columna de la derecha, el boton de
        exportar y el boton 'Replace character', que solo aparece en la vista de
        animaciones.
        """
        self.animation_view.set(not self.animation_view.get())
        animacion = self.animation_view.get()
        self.view_button.configure(
            text=translate(
                "Character View" if animacion else "Animation View",
                self.app.language,
            )
        )
        self.shapes_column.configure(width=ANCHO_CLIPS if animacion else ANCHO_SHAPES)
        if animacion:
            self.shapes_view.pack_forget()
            self.animation_frame.pack(fill="both", expand=True)
        else:
            self.animation_frame.pack_forget()
            self.shapes_view.pack(fill="both", expand=True)
        self.animations.visible_on_screen = animacion
        # Los datos de la esquina son de quien esta en pantalla: al conmutar hay
        # que reescribirlos, que si no se quedan con los de la otra vista.
        if animacion:
            self.animations.refresh_view_status()
        else:
            self.restaurar_estado_del_personaje()
        self.export_button.set_text(
            "Export clip as..." if animacion else "Export as...",
            ancho_del_boton(
                self.winfo_toplevel(),
                "Export clip as..." if animacion else "Export as...",
            ),
        )
        self._repack_left()
        # Es la misma lista en las dos vistas y en cada una se ve un personaje
        # distinto, asi que al conmutar hay que senalar el que toca: el del clip
        # si la vista es la de animaciones, el del visor si no.
        self._resaltar_en_la_lista(
            self.animations.character.get() if animacion else self.current
        )
        # Quien repinta el lienzo es el de la vista que ha quedado delante.
        if animacion:
            self.animations.redibujar()
        else:
            self.schedule()

    def toggle_replace_character(self):
        """Abre o cierra la lista de personajes en la vista de animaciones.

        En la vista de personajes la lista esta siempre puesta: es la propia
        vista. Aqui el boton es el que la saca, y mientras este marcado el clip
        deja de imponer su personaje.
        """
        self.replace_character.set(not self.replace_character.get())
        marcado = self.replace_character.get()
        # Desactivado: verde al mantener y normal al soltar, como cualquier
        # boton. Activado: verde fijo, sin resalte al pasar el raton, y algo mas
        # claro solo mientras se mantiene. Los dos son estilos del tema con el
        # mismo relleno y la misma fuente, asi que no se mueve nada al cambiar.
        self.replace_button.configure(
            style="SelectedHold.TButton" if marcado else "ViewToggle.TButton"
        )
        self._repack_left()
        if marcado:
            # La lista es la misma que la de la vista de personajes, asi que al
            # abrirla sale marcado el personaje que esta viendo el clip.
            self._resaltar_en_la_lista(self.animations.character.get())

    def _repack_left(self):
        """Vuelve a empaquetar la columna izquierda, y en orden.

        `pack` respeta el orden en que se empaquetaron los hermanos, y eso se
        conserva entre un `pack_forget` y un `pack` posteriores: un boton que se
        oculta y reaparece se queda al final, debajo de la lista. Repackando todo
        cada vez, cada pieza vuelve a su sitio.

        Ademas decide lo que depende de la vista: en la de animaciones no hay
        galeria (las texturas son del clip, no de quien se mira) y la lista, sin
        nada debajo, aprovecha la columna entera.
        """
        animacion = self.animation_view.get()
        lista_visible = not animacion or self.replace_character.get()
        for widget in (
            self.view_button,
            self.replace_button,
            self.characters_frame,
            self.textures_frame,
        ):
            widget.pack_forget()
        self.view_button.pack(fill="x", pady=(0, 4))
        if animacion:
            self.replace_button.pack(fill="x", pady=(0, 4))
        # Con `side="top"` manda el orden de empaquetado, asi que la lista se
        # empaqueta antes que el bloque de texturas para que este quede debajo.
        self._al_mostrar_la_lista(lista_visible, animacion)
        if not animacion:
            # Inmediatamente debajo de la lista, sin estirarse: si el bloque
            # creciera, el separador se iria al final de la columna y entre la
            # lista y las texturas quedaria un hueco enorme.
            self.textures_frame.pack(side="top", fill="x")

    def _al_mostrar_la_lista(self, mostrar=None, animacion=None):
        """Abre o cierra la lista de personajes segun la vista y el boton.

        Cada vez que se abre, arriba del todo: es la misma lista en las dos
        vistas y en cada una se ve un personaje distinto, y desplazada el
        resalte puede quedar fuera de la vista.
        """
        if mostrar is None:
            mostrar = not self.animation_view.get() or self.replace_character.get()
        if animacion is None:
            animacion = self.animation_view.get()
        if not mostrar:
            self.characters_frame.pack_forget()
            return
        # En la vista de personajes el marco no se estira: si creciera, el hueco
        # le caeria dentro y las texturas se separarian de la lista. Lo que sobra
        # se queda al final de la columna.
        self.characters_frame.pack(fill="both", expand=animacion)
        self.character_list_frame.pack_forget()
        self.character_list_frame.pack(fill="both", expand=animacion, pady=(3, 0))
        self.listbox.al_principio()

    def _resaltar_en_la_lista(self, name):
        """Marca un personaje en la lista sin recargarlo ni arrastrarla.

        No se usa `selection_set()`: ademas de recargar el modelo, el gestor
        mueve la lista hasta el elemento, que deshace el volver al principio.
        """
        if not name or name not in self.visible:
            return
        self.listbox.marcar(self.visible.index(name))

    def _animation_character_changed(self, name):
        """Las reglas del clip han cambiado de personaje.

        Con 'Replace character' marcado manda el usuario y la lista ya senala lo
        suyo. En la vista de personajes el clip no toca la seleccion: es la que
        recuerda a quien se estaba viendo, y pisarla hacia que al volver de la
        vista de animaciones apareciera otro personaje.
        """
        if (
            self.animation_view.get()
            and self.replace_character.get()
            and self.characters_frame.winfo_manager() == "pack"
        ):
            self._resaltar_en_la_lista(name)

    def _llevar_el_personaje_al_clip(self, name):
        """Con 'Replace character' marcado, lo elegido en la lista pasa al clip.

        El visor de personajes no se toca. Avisar al panel tiene que valer
        tambien cuando el personaje es el que ya esta cargado, porque `select`
        sale antes de llegar: si no, hay que pulsarlo dos veces.
        """
        if not (
            self.animation_view.get()
            and self.replace_character.get()
            and self.characters_frame.winfo_manager() == "pack"
        ):
            return
        self._resaltar_en_la_lista(name)
        if self.animations.character.get() != name:
            self.animations.set_character(name)
            self.animations.changed_character()

    # -- botones flotantes -------------------------------------------------

    def _floating_button(self, text, command, selected=False):
        """Crea un botón flotante dibujado sobre el canvas del visor.

        Se usa un item del canvas y no un ttk.Button porque el widget pinta su
        fondo en todo el rectángulo y sobre la escena 3D se vería un rectángulo
        negro alrededor de la textura redondeada. `selected` decide el resalte:
        con el botón verde visible se aclara su base, si no, el borde azul.
        """
        top = self.winfo_toplevel()
        button = FloatingButton(
            self.canvas,
            top._sims_floating_button_factory,
            text,
            command,
            ancho_del_boton(top, text),
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

    # -- galeria de texturas ------------------------------------------------

    @property
    def selected_texture_key(self):
        """La textura con el borde verde. La lleva la galeria."""
        return self.gallery.selected_key

    @selected_texture_key.setter
    def selected_texture_key(self, value):
        self.gallery.selected_key = value

    def _populate_texture_gallery(self):
        names = {
            path: part["source_texture_path"]
            for path, part in self.parts
            if part.get("source_texture_path")
        }
        self.gallery.set_textures(self.textures, self.selected_texture_key, names=names)

    def _hide_texture_preview(self, clear_selection=False):
        self.gallery.hide_preview(clear_selection=clear_selection)

    # -- lista y seleccion -------------------------------------------------

    def refresh(self, _event=None):
        term = self.filter.get().casefold()
        # Se busca por nombre visible y por slug, para que teclear `mib1` o
        # `men in black` saquen lo mismo. `self.visible` sigue con los slugs:
        # la lista pinta el nombre, pero de aqui abajo se trabaja con el slug.
        self.visible = [
            n
            for n in self.names
            if term in n.casefold()
            or term in nombre_publico(n, self.app.language).casefold()
        ]
        self.listbox.delete(0, "end")
        for n in self.visible:
            self.listbox.insert("end", nombre_publico(n, self.app.language))
        if self.current in self.visible:
            self.listbox.marcar(self.visible.index(self.current))

    def set_language(self, _language):
        self.characters_heading.configure(
            text="Personajes" if _language == "es" else "Characters"
        )
        self.view_button.configure(
            text=(
                ("Vista de personaje" if self.animation_view.get() else "Vista de animación")
                if _language == "es"
                else ("Character View" if self.animation_view.get() else "Animation View")
            )
        )
        if hasattr(self, "export_button"):
            anim = self.animation_view.get()
            label = (
                ("Exportar clip..." if anim else "Exportar...")
                if _language == "es"
                else ("Export clip as..." if anim else "Export as...")
            )
            self.export_button.set_text(label, ancho_del_boton(self.winfo_toplevel(), label))
        top = self.winfo_toplevel()
        for button in getattr(self, "floating_buttons", ()):
            label = translate(button.text, _language)
            button.set_text(label, ancho_del_boton(top, label))
        self.after_idle(self._position_viewport_controls)
        self.refresh()

    def select_name(self, name):
        if name in self.visible:
            idx = self.visible.index(name)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
            self.select()

    def select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.visible[sel[0]]
        # En la vista de animaciones con 'Replace character' marcado, lo que se
        # elige en esta lista es el personaje del clip y nada mas. El visor de
        # personajes no se toca, que esa vista tiene que seguir recordando el
        # suyo cuando se vuelva a ella.
        if self.animation_view.get() and self.replace_character.get():
            self._llevar_el_personaje_al_clip(name)
            return
        if name == self.current:
            return
        try:
            paths, self.parts, self.bones, self.textures = load_character(
                self.app.paths["characters"], name
            )
            self.current = name
            self.selected_texture_key = ""
            self._populate_texture_gallery()
            gl.forget(("personaje", name))
            face_morphs = any(
                part.get("shape_scope", path).casefold()
                in ("face/afface", "face/amface")
                for path, part in self.parts
            )
            self.shape_options = [
                option
                for option in shape_key_options(
                    self.parts, preserve_morph_ids=face_morphs
                )
                if not option[0].startswith("Face archetype: ")
            ]
            self.shape_list.delete(0, "end")
            for shape_name, _ in self.shape_options:
                self.shape_list.insert(
                    "end", shape_name.removeprefix("Face modifier: ")
                )
            self.shape.set("")
            self.shape_weight.set(0)
            self.shape_id = None
            self._set_shape_controls_enabled(False)
            vertices = sum(len(part["vertices"]) for _, part in self.parts)
            face = next(
                (
                    path.rsplit("/", 1)[-1]
                    for path in paths
                    if path.startswith("face/cas/")
                ),
                "",
            )
            detail = f"\nFace: {face}" if face else ""
            self.status.configure(
                text=translate(
                    f"{name}\n{len(self.bones)} bones · "
                    f"{vertices} vertices{detail}",
                    self.app.language,
                )
            )
            self.schedule()
        except Exception as exc:
            messagebox.showerror(translate("Character", self.app.language), str(exc))

    def restaurar_estado_del_personaje(self):
        """Vuelve a poner los datos del personaje en la esquina del visor.

        En la vista de animaciones los escribe el panel con los del clip, y al
        volver hay que deshacerlo: si no, la esquina sigue con el nombre del
        clip y el de otro personaje.
        """
        if self.current and self.parts:
            vertices = sum(len(part["vertices"]) for _, part in self.parts)
            self.status.configure(
                text=translate(
                    f"{self.current}\n{len(self.bones)} bones · {vertices} vertices",
                    self.app.language,
                )
            )

    def select_shape(self, _event=None):
        selection = self.shape_list.curselection()
        if selection:
            index = selection[0]
            self.shape.set(self.shape_options[index][0])
            self.shape_id = self.shape_options[index][1]
        else:
            self.shape.set("")
            self.shape_id = None
        self._set_shape_controls_enabled(bool(self.shape.get()))
        self._update_shape_preset_styles()
        self.schedule()

    def _set_shape_controls_enabled(self, enabled):
        state = "!disabled" if enabled else "disabled"
        for button in self.shape_preset_buttons:
            button.state([state])
        self.shape_slider.state([state])
        self._update_shape_preset_styles()

    def _update_shape_preset_styles(self):
        enabled = bool(self.shape.get())
        weight = self.shape_weight.get()
        for button in self.shape_preset_buttons:
            selected = enabled and abs(weight - button.preset_value) < 0.001
            if not enabled:
                appearance = "disabled"
            elif selected:
                appearance = "selected_hover" if button.preset_hover else "selected"
            elif button.preset_hover:
                appearance = "hover"
            else:
                appearance = "normal"
            button.configure(
                image=self.app._sims_shape_preset_images[button.preset_value][
                    appearance
                ]
            )

    def _set_shape_preset_hover(self, button, hovered):
        button.preset_hover = hovered
        self._update_shape_preset_styles()

    def set_shape_weight(self, weight):
        if not self.shape.get():
            return
        self.shape_weight.set(weight)
        self._update_shape_preset_styles()
        self.schedule()

    def on_shape_slider(self, _value=None):
        self._update_shape_preset_styles()
        self.schedule()

    # -- dibujo ------------------------------------------------------------

    def schedule(self, _event=None):
        # Con GPU el frame dura ~2 ms: se dibuja de inmediato y solo se recorta a 60 Hz.
        # Reprogramar el temporizador en cada movimiento haría que al girar deprisa
        # no llegara a dispararse nunca, y la cámara iría a trompicones.
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        if not gl.disponible():
            self.timer = self.after(40, self.render)
            return
        faltan = gl.faltan_para(self.last_frame, 16)
        if faltan > 0:
            self.timer = self.after(faltan, self.render)
        else:
            self.render()

    def render(self):
        self.timer = None
        self.last_frame = gl.ahora_ms()
        if self.animation_view.get():
            # El lienzo lo lleva el panel de clips, pero la camara y el
            # redimensionado entran por aqui. Si este metodo se limitase a no
            # dibujar, arrastrar dejaria el visor congelado sin que se note por
            # que.
            self.animations.redibujar()
            return
        if not self.parts:
            return
        try:
            w, h = self.canvas.view_size()
            image = draw_character(
                self.parts,
                self.bones,
                w,
                h,
                self.camera.yaw,
                self.camera.pitch,
                self.camera.zoom,
                self.show_bones.get(),
                self.shape.get(),
                self.shape_weight.get(),
                self.textures,
                self.camera.pan,
                key=("personaje", self.current),
                camara=self.camera,
                show_grid=self.show_grid.get(),
                shape_id=self.shape_id,
            )
            set_photo(self.canvas, image)
        except Exception as exc:
            self.status.configure(
                text=translate(f"Preview: {exc}", self.app.language)
            )

    # -- exportacion -------------------------------------------------------

    def export_segun_vista(self):
        """El boton flotante exporta el clip o el personaje, segun la vista."""
        if self.animation_view.get():
            self.animations.export_as()
        else:
            self.export()

    def choose_export_format(self):
        return choose_export_format(self, self.current)

    def export(self):
        if not self.current:
            return
        fmt = self.choose_export_format()
        if not fmt:
            return
        try:
            fbx = fmt == "fbx"
            blender = self.app.resolve_blender() if fbx else None
            if fbx and not blender:
                return
            model_name = export_model_name(self.current)
            export_dir = self.app.export_root / f"{model_name}_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            target = export_dir / f"{model_name}.{fmt}"
            glb = target if fmt == "glb" else self.app.cache / "character_export.glb"
            info = convert_character(self.app.paths["characters"], self.current, glb)
            if fbx:
                export_path(glb, target, blender)
            elif fmt == "gltf":
                export_gltf(glb, target)
            self.status.configure(
                text=translate(f'Saved: {target}\n{info["bones"]} bones', self.app.language)
            )
        except Exception as exc:
            messagebox.showerror(
                translate("Export character", self.app.language), str(exc)
            )

    def dispose(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        self.animations.dispose()
        self._hide_texture_preview()
