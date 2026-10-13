"""Character tab and its controls."""

import math
import numpy as np
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageOps, ImageTk
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
from ts2psp.ui.floating_button import FloatingButton
from ts2psp.ui.theme import (
    LIST_PANEL_PATH,
    PREVIEW_BORDER_COLOR,
    PREVIEW_BORDER_WIDTH,
    PREVIEW_CORNER_RADIUS,
    _round_window_corners,
)
from ts2psp.ui.common import export_model_name, set_photo
from ts2psp.exporting.blender import export_path


class CharacterTab(ttk.Frame):
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
        self.texture_thumbnails = []
        self.texture_gallery_buttons = []
        self.selected_texture_key = ""
        self.texture_preview_photo = None
        self.texture_preview_window = None
        self.texture_dimmer = None
        left = ttk.Frame(self, width=250)
        left.pack(side="left", fill="both", padx=(0, 8))
        left.pack_propagate(False)
        ttk.Label(
            left,
            text="Characters",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        self.filter = tk.StringVar()
        self.search_entry = ttk.Entry(
            left, textvariable=self.filter, width=30, style="CharacterSearch.TEntry"
        )
        self.search_entry.pack(fill="x", pady=5)
        self.search_entry.bind("<KeyRelease>", self.refresh)
        character_list_frame = ttk.Frame(left)
        character_list_frame.pack(fill="both", expand=True)
        self.listbox = TexturedListbox(
            character_list_frame,
            LIST_PANEL_PATH,
            width=222,
            height=560,
            font=(self.app._sims_font_family, 10, "bold"),
        )
        character_scroll = ttk.Scrollbar(
            character_list_frame, orient="vertical", command=self.listbox.yview
        )
        self.listbox.configure(yscrollcommand=character_scroll.set)
        character_scroll.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.select)
        shapes = ttk.Frame(self, width=190, padding=(8, 0, 0, 0))
        shapes.pack(side="right", fill="y")
        shapes.pack_propagate(False)
        ttk.Label(
            shapes,
            text="Shape Keys",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        shape_list_frame = ttk.Frame(shapes)
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
            shapes,
            text="Strength",
            anchor="center",
            font=(self.app._sims_font_family, 10, "bold"),
        ).pack(fill="x")
        self.shape_weight = tk.DoubleVar(value=0)
        self.shape_slider = ttk.Scale(
            shapes,
            from_=-1,
            to=1,
            variable=self.shape_weight,
            command=self.on_shape_slider,
            style="Shape.Horizontal.TScale",
            length=180,
        )
        self.shape_slider.pack(fill="x", pady=(3, 6))
        self.shape_slider.state(["disabled"])
        presets = ttk.Frame(shapes)
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
        ttk.Separator(shapes, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(
            shapes,
            text="Textures",
            anchor="center",
            font=(self.app._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 4))
        gallery = ttk.Frame(shapes)
        gallery.pack(fill="x", expand=False)
        self.gallery_canvas = tk.Canvas(
            gallery, width=204, height=196, highlightthickness=0, background="#aeb8e5"
        )
        self.gallery_scroll = ttk.Scrollbar(
            gallery, orient="vertical", command=self.gallery_canvas.yview
        )
        self.gallery_canvas.configure(yscrollcommand=self.gallery_scroll.set)
        self.gallery_canvas.pack(side="left", fill="both", expand=True)
        self.gallery_grid = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window(
            (0, 0), window=self.gallery_grid, anchor="nw"
        )
        self.gallery_grid.bind("<Configure>", self._gallery_grid_resized)
        self.gallery_canvas.bind("<Configure>", self._gallery_canvas_resized)
        self.gallery_canvas.bind("<MouseWheel>", self._gallery_mousewheel)
        center = ttk.Frame(self)
        center.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(center, minimum=(500, 550))
        self.canvas.on_resize = self.schedule
        self.camera = OrbitController(self.canvas, self.schedule, yaw=math.pi)
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button("Export as...", self.export)
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
        self.refresh()
        if self.visible:
            self.select_name(
                "bellagoth" if "bellagoth" in self.visible else self.visible[0]
            )

    def _gallery_grid_resized(self, _event=None):
        self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all"))

    def _gallery_canvas_resized(self, event):
        self.gallery_canvas.itemconfigure(self.gallery_window, width=event.width)

    def _gallery_mousewheel(self, event):
        self.gallery_canvas.yview_scroll(-int(event.delta / 120), "units")

    def _floating_button(self, text, command, selected=False):
        """Crea un botón flotante dibujado sobre el canvas del visor.

        Se usa un item del canvas y no un ttk.Button porque el widget pinta su
        fondo en todo el rectángulo y sobre la escena 3D se vería un rectángulo
        negro alrededor de la textura redondeada. `selected` decide el resalte:
        con el botón verde visible se aclara su base, si no, el borde azul.
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
        self.schedule()

    def toggle_grid(self):
        enabled = not self.show_grid.get()
        self.show_grid.set(enabled)
        self.grid_button.set_selected(enabled)
        self.schedule()

    def _populate_texture_gallery(self):
        for widget in self.gallery_grid.winfo_children():
            widget.destroy()
        self.texture_thumbnails = []
        self.texture_gallery_buttons = []
        rows = (len(self.textures) + 2) // 3
        height = min(max(rows, 1) * 48, 196)
        self.gallery_canvas.configure(height=height)
        if rows > 4:
            self.gallery_scroll.pack(side="right", fill="y")
        else:
            self.gallery_scroll.pack_forget()
        for column in range(3):
            self.gallery_grid.columnconfigure(column, weight=1)
        for index, (name, pixels) in enumerate(sorted(self.textures.items())):
            image = Image.fromarray(np.asarray(pixels, dtype=np.uint8)).convert("RGBA")
            thumbnail = ImageOps.fit(image, (36, 36), method=Image.Resampling.LANCZOS)
            photos = {
                "normal": self._texture_tile_image(thumbnail, selected=False),
                "selected": self._texture_tile_image(thumbnail, selected=True),
            }
            colors = getattr(self.winfo_toplevel(), "_sims_theme_colors", {})
            gallery_background = self.gallery_canvas.cget("background")
            button = tk.Button(
                self.gallery_grid,
                image=photos[
                    "selected" if name == self.selected_texture_key else "normal"
                ],
                width=44,
                height=44,
                padx=0,
                pady=0,
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
                background=gallery_background,
                activebackground=gallery_background,
                command=lambda key=name: self._show_texture_preview(key),
            )
            button.grid(row=index // 3, column=index % 3, padx=2, pady=2)
            button.image_key = name
            button.tile_images = photos
            button.bind(
                "<Enter>",
                lambda _event, w=button: self._set_texture_tile_hover(w, True),
            )
            button.bind(
                "<Leave>",
                lambda _event, w=button: self._set_texture_tile_hover(w, False),
            )
            self.texture_gallery_buttons.append(button)
            self.texture_thumbnails.extend(photos.values())

    def _texture_tile_image(self, thumbnail, selected=False):
        size = 44
        tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(tile)
        border = "#20df61" if selected else "#18275d"
        draw.rounded_rectangle(
            (1, 1, size - 2, size - 2),
            radius=7,
            fill="#374456",
            outline=border,
            width=2,
        )
        mask = Image.new("L", thumbnail.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, thumbnail.width - 1, thumbnail.height - 1), radius=4, fill=255
        )
        tile.paste(thumbnail, (4, 4), mask)
        return ImageTk.PhotoImage(tile, master=self)

    def _set_texture_tile_hover(self, button, hovering):
        selected = button.image_key == self.selected_texture_key
        button.configure(
            image=button.tile_images["selected" if selected or hovering else "normal"]
        )

    def _refresh_texture_tile_selection(self):
        for button in self.texture_gallery_buttons:
            button.configure(
                image=button.tile_images[
                    (
                        "selected"
                        if button.image_key == self.selected_texture_key
                        else "normal"
                    )
                ]
            )

    def _show_texture_preview(self, name):
        pixels = self.textures.get(name)
        if pixels is None:
            return
        self.selected_texture_key = name
        self._refresh_texture_tile_selection()
        self._hide_texture_preview()
        root = self.winfo_toplevel()
        root.update_idletasks()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        width = max(1, root.winfo_width())
        height = max(1, root.winfo_height())

        self.texture_dimmer = tk.Toplevel(root)
        self.texture_dimmer.overrideredirect(True)
        self.texture_dimmer.configure(background="#080910")
        self.texture_dimmer.attributes("-alpha", 0.62)
        self.texture_dimmer.geometry(f"{width}x{height}+{x}+{y}")
        # Cerrar al soltar, y solo si el puntero apenas se movió. Con un
        # <ButtonPress-1> cualquier arrastre que empezasse sobre el dimmer lo
        # cerraba, y al redimensionar la ventana el puntero empieza justo
        # encima del dimmer, que la cubre entera.
        self.texture_dimmer.bind("<ButtonPress-1>", self._dimmer_press)
        self.texture_dimmer.bind("<ButtonRelease-1>", self._dimmer_release)
        self.texture_dimmer.lift()

        self.texture_preview_window = tk.Toplevel(root)
        self.texture_preview_window.overrideredirect(True)
        self.texture_preview_window.configure(background=PREVIEW_BORDER_COLOR)
        self.texture_preview_window.bind(
            "<Escape>", lambda _event: self._hide_texture_preview(clear_selection=True)
        )
        preview_width = min(520, max(240, int(width * 0.5)))
        preview_height = min(480, max(200, int(height * 0.5)))
        preview_x = x + (width - preview_width) // 2
        preview_y = y + (height - preview_height) // 2
        self.texture_preview_window.geometry(
            f"{preview_width}x{preview_height}+{preview_x}+{preview_y}"
        )
        panel = ttk.Frame(self.texture_preview_window, padding=12)
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
            font=(self.winfo_toplevel()._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 8))
        image = Image.fromarray(np.asarray(pixels, dtype=np.uint8)).convert("RGBA")
        image = ImageOps.contain(
            image,
            (preview_width - 32, preview_height - 56),
            method=Image.Resampling.LANCZOS,
        )
        # master explicito: sin el, PhotoImage se crea en el root por defecto y
        # la etiqueta, que pertenece a otro Toplevel, no lo encuentra.
        self.texture_preview_photo = ImageTk.PhotoImage(image, master=root)
        image_area = ttk.Frame(panel)
        image_area.pack(fill="both", expand=True)
        ttk.Label(image_area, image=self.texture_preview_photo).place(
            relx=0.5, rely=0.5, anchor="center"
        )
        self.texture_preview_window.lift()
        self.texture_preview_window.focus_force()
        # Se recorta al final, cuando la ventana ya tiene su tamaño real.
        self.texture_preview_window.update_idletasks()
        _round_window_corners(self.texture_preview_window, PREVIEW_CORNER_RADIUS)
        # La vista previa sigue a la ventana principal si esta se redimensiona.
        root.bind("<Configure>", self._preview_moved, add="+")

    def _preview_moved(self, _event=None):
        """Mantiene el dimmer y la vista previa al redimensionar la ventana."""
        if self.texture_preview_window is None:
            return
        root = self.winfo_toplevel()
        width, height = root.winfo_width(), root.winfo_height()
        if width < 2 or height < 2:
            return
        x, y = root.winfo_rootx(), root.winfo_rooty()
        self.texture_dimmer.geometry(f"{width}x{height}+{x}+{y}")
        window = self.texture_preview_window
        window.geometry(
            f"+{x+(width-window.winfo_width())//2}"
            f"+{y+(height-window.winfo_height())//2}"
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
        self._hide_texture_preview(clear_selection=True)

    def _hide_texture_preview(self, clear_selection=False):
        if self.texture_preview_window is not None:
            if self.texture_preview_window.winfo_exists():
                self.texture_preview_window.destroy()
            self.texture_preview_window = None
        if self.texture_dimmer is not None:
            if self.texture_dimmer.winfo_exists():
                self.texture_dimmer.destroy()
            self.texture_dimmer = None
        self.texture_preview_photo = None
        # Cerrar la vista previa a mano deja la textura deseleccionada, así que
        # el borde verde de la miniatura se va con ella. Las llamadas internas
        # (abrir otra preview, cambiar de personaje) no deben tocar la selección.
        if clear_selection and self.selected_texture_key:
            self.selected_texture_key = ""
            self._refresh_texture_tile_selection()

    def refresh(self, _event=None):
        term = self.filter.get().casefold()
        # Se busca por nombre visible y por slug, para que teclear `mib1` o
        # `men in black` saquen lo mismo. `self.visible` sigue con los slugs:
        # la lista pinta el nombre, pero de aqui abajo se trabaja con el slug.
        self.visible = [
            n
            for n in self.names
            if term in n.casefold() or term in nombre_publico(n).casefold()
        ]
        self.listbox.delete(0, "end")
        for n in self.visible:
            self.listbox.insert("end", nombre_publico(n))

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
        if name == self.current:
            return
        try:
            paths, self.parts, self.bones, self.textures = load_character(
                self.app.paths["characters"], name
            )
            self.current = name
            self._hide_texture_preview()
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
                text=f"{name}\n{len(self.bones)} bones · "
                f"{vertices} vertices{detail}"
            )
            self.schedule()
        except Exception as exc:
            messagebox.showerror("Character", str(exc))

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
            self.status.configure(text=f"Preview: {exc}")

    def choose_export_format(self):
        dialog = tk.Toplevel(self)
        dialog.title("Export as...")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        selected = tk.StringVar(value="glb")
        content = ttk.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text=f"Format for {self.current}:").pack(
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
        return choice["format"]

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
            self.status.configure(text=f'Saved: {target}\n{info["bones"]} bones')
        except Exception as exc:
            messagebox.showerror("Export character", str(exc))

    def dispose(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        self._hide_texture_preview()
