"""Prop tab and its controls."""

import csv
import io
import math
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
from ts2psp.formats.props import convert_prop, prop_bones, prop_joints
from ts2psp.formats.prop_anim import pose, prop_animations, prop_clip, reparte_canales
from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.ui.uv import UVWindow
from ts2psp.ui.common import set_photo
from ts2psp.exporting.blender import export_path


class PropTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
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
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(0, 10))
        ttk.Label(left, text=f"Objects and props ({len(self.rows)})").pack(anchor="w")
        self.filter = tk.StringVar()
        entry = ttk.Entry(left, textvariable=self.filter, width=36)
        entry.pack(fill="x", pady=5)
        entry.bind("<KeyRelease>", self.refresh)
        self.listbox = tk.Listbox(left, width=44, height=34, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.select)
        ttk.Button(left, text="Export object as GLB", command=self.export).pack(
            fill="x", pady=7
        )
        ttk.Button(
            left, text="Export object as FBX", command=lambda: self.export("fbx")
        ).pack(fill="x", pady=(0, 7))
        ttk.Button(left, text="View texture maps and UVs", command=self.show_uv).pack(
            fill="x", pady=(0, 7)
        )
        # Provisional: la animacion va dentro del propio prop, asi que la lista
        # son los props que llevan curvas. Se lee el catalogo entero al abrir,
        # que tarda unas tres décimas.
        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=(4, 6))
        self.anim_rows = prop_animations(app.paths["props"], self.rows)
        ttk.Label(left, text=f"Prop animations ({len(self.anim_rows)})").pack(anchor="w")
        self.anim_list = tk.Listbox(left, width=44, height=8, exportselection=False)
        self.anim_list.pack(fill="both", expand=False, pady=(4, 0))
        self.anim_list.bind("<<ListboxSelect>>", self.select_animation)
        for row in self.anim_rows:
            self.anim_list.insert("end", f'{row["entry"]}  {row["ruta_interna"]}')
        self.clip = None
        self.data = None
        self.reparto = {}
        self.bone_src = ()
        self.playing = False
        self.playhead = 0.0
        self.clock_ms = 0.0
        self.moving_slider = False
        self.objeto = None
        self.objeto_key = None
        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(7, 0))
        self.play_button = ttk.Button(controls, text="▶ Play", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.loop = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Loop", variable=self.loop).pack(
            side="left", padx=(10, 0)
        )
        self.time_label = ttk.Label(controls, text="0:00 / 0:00", anchor="e")
        self.time_label.pack(side="right")
        self.position = tk.DoubleVar(value=0)
        self.slider = ttk.Scale(left, from_=0, to=1, variable=self.position, command=self.scrub)
        self.slider.pack(fill="x", pady=6)
        self.status = ttk.Label(left, text="", wraplength=300)
        self.status.pack(fill="x")
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(right, minimum=(500, 550))
        self.canvas.on_resize = self.schedule
        self.camera = OrbitController(self.canvas, self.schedule, yaw=math.radians(20))
        self.show_textures = tk.BooleanVar(value=True)
        ttk.Label(right, text=CAMERA_HINT, wraplength=640).pack(anchor="w")
        ttk.Button(right, text="Reset camera", command=self.camera.reset).pack(
            anchor="w", pady=4
        )
        ttk.Checkbutton(
            right,
            text="Show textures / hide to inspect vertex colors",
            variable=self.show_textures,
            command=self.schedule,
        ).pack(anchor="w")
        self.show_grid = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right, text="Show grid", variable=self.show_grid, command=self.schedule
        ).pack(anchor="w")
        self.show_bones = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            right,
            text="Show bones",
            variable=self.show_bones,
            command=self.schedule,
        ).pack(anchor="w")
        self.bones = ()
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

    def refresh(self, _event=None):
        term = self.filter.get().casefold()
        self.visible = [r for r in self.rows if term in r["ruta_interna"].casefold()]
        self.listbox.delete(0, "end")
        for r in self.visible:
            self.listbox.insert("end", f'{r["entry"]}  {r["ruta_interna"]}')

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
        row = self.anim_rows[sel[0]]
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
            self.parts = load_parts(data)
            self.bone_src, _ = prop_joints(data)
            self.bones = bones_over(self.parts, self.bone_src)
            self.clip = prop_clip(data)
            self.reparto = reparte_canales(data, self.clip) if self.clip else {}
            self.current = row
            gl.forget(("prop", row["entry"]))
            self.objeto = None
            self.objeto_key = None
            self.playhead = 0.0
            self.moving_slider = True
            self.position.set(0)
            self.moving_slider = False
            self.slider.configure(to=self.clip["duration"] if self.clip else 1)
            self.time_label.configure(
                text=self.time_text(0, self.clip["duration"] if self.clip else 0)
            )
            vertices = sum(len(p["vertices"]) for p in self.parts)
            self.status.configure(
                text=f'{row["ruta_interna"]}\n{len(self.parts)} parts · {vertices} vertices'
                + self.bones_text(data)
                + self.clip_text()
            )
            self.schedule()
        except Exception as exc:
            messagebox.showerror("Object", str(exc))

    def clip_text(self):
        if not self.clip:
            return ""
        claves = sum(len(canal["times"]) for canal in self.clip["channels"])
        return (
            f'\n{len(self.clip["channels"])} channels · {claves} keys'
            f' · {self.clip["duration"]:.2f} s'
        )

    def time_text(self, t, duration):
        def clock(seconds):
            minutes, rest = divmod(int(round(max(0.0, float(seconds)))), 60)
            return f"{minutes}:{rest:02d}"

        return f"{clock(t)} / {clock(duration)}"

    def stop(self):
        self.playing = False
        self.play_button.configure(text="▶ Play")

    def toggle_play(self):
        if not self.clip:
            return
        self.playing = not self.playing
        self.play_button.configure(text="❚❚ Pause" if self.playing else "▶ Play")
        if self.playing:
            self.clock_ms = gl.ahora_ms()
            self.schedule()

    def scrub(self, value):
        if self.moving_slider or not self.clip:
            return
        self.playhead = float(value)
        self.time_label.configure(
            text=self.time_text(self.playhead, self.clip["duration"])
        )
        self.schedule()

    def posed_parts(self):
        """Las piezas en el instante actual, con las curvas aplicadas."""
        if not self.clip:
            return self.parts
        partes, huesos = pose(
            self.parts, self.data, self.clip, self.playhead, self.reparto
        )
        for bone in self.bone_src:
            if bone["index"] in huesos:
                bone["position"] = huesos[bone["index"]]
        self.bones = bones_over(self.parts, self.bone_src)
        return partes

    def tick(self):
        """Avanza el reloj de reproduccion. Devuelve True si sigue en marcha."""
        if not self.clip or not self.playing:
            return False
        ahora = gl.ahora_ms()
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

    def bones_text(self, data):
        """Lineas con los huesos del prop, o '' si no se detectan.

        El recuento y los nombres van por separado porque no siempre vienen los
        dos: hay props con el grafo de nodos completo pero sin un solo nombre
        guardado (los de Roberta, entre otros).
        """
        count, names = prop_bones(data)
        if count is None:
            return ""
        if not names:
            return f"\n{count} bones (none named in the resource)"
        # Los nombres de nodo siguen el convenio de Sims: el de la pieza va
        # primero, luego los transforms de raiz, luego agarres y superficies.
        # Se agrupan porque en una lista plana no se ve cuales son puntos de
        # enganche, que es lo que hace que un prop se anime. Cada nombre cae en
        # un solo grupo: el primero que le encaja.
        categorias = [
            ("root", lambda n: n.startswith(("__root_", "__base_"))),
            ("grip", lambda n: "grip" in n.casefold()),
            ("slot", lambda n: n.startswith("__slot_")),
            ("piece", lambda n: n.startswith("__accessory")),
            ("surface", lambda n: n.endswith("_surface__")),
            ("other", lambda n: True),
        ]
        grupos = {etiqueta: [] for etiqueta, _ in categorias}
        for name in names:
            for etiqueta, coincide in categorias:
                if coincide(name):
                    grupos[etiqueta].append(name)
                    break
        texto = f"\n{count} bones"
        for etiqueta, _ in categorias:
            grupo = grupos[etiqueta]
            if not grupo:
                continue
            shown = ", ".join(grupo[:4])
            rest = f" +{len(grupo) - 4} more" if len(grupo) > 4 else ""
            texto += f"\n  {etiqueta}: {shown}{rest}"
        return texto

    def schedule(self, _event=None):
        # igual que en Characters: con GPU se dibuja al instante y se limita a 60 Hz
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        if not gl.disponible():
            self.timer = self.after(60, self.render)
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
            partes = self.posed_parts()
            w, h = self.canvas.view_size()
            # La escena se reutiliza entre fotogramas y solo se le cambian los
            # vertices: si se pidiera por clave, la cache devolveria la de
            # reposo y el prop no se moveria.
            key = (self.current["entry"], self.show_textures.get())
            if self.objeto_key != key:
                self.objeto = prop_scene(self.parts, key[0], key[1])
                self.objeto_key = key
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
            self.status.configure(text=f"Preview: {exc}")
        # Mientras suene el clip hay que seguir pidiendo fotogramas; parado, el
        # refresco lo dispara el raton o el deslizador.
        if self.tick():
            self.schedule()

    def export(self, fmt="glb"):
        if not self.current:
            return
        name = Path(self.current["ruta_interna"]).name + "." + fmt
        target = filedialog.asksaveasfilename(
            defaultextension="." + fmt,
            initialfile=name,
            filetypes=[(fmt.upper() + " model", "*." + fmt)],
        )
        if not target:
            return
        try:
            fbx = fmt == "fbx"
            blender = self.app.resolve_blender() if fbx else None
            if fbx and not blender:
                return
            with zipfile.ZipFile(self.app.paths["props"]) as z:
                data = z.read(self.current["archivo_zip"])
            glb = self.app.cache / "prop_export.glb" if fbx else target
            info = convert_prop(data, self.current["ruta_interna"], glb)
            if fbx:
                export_path(glb, target, blender)
            self.status.configure(
                text=f'Saved: {target}\n{info["parts"]} parts · '
                f'{info["color_parts"]} with vertex colors'
            )
        except Exception as exc:
            messagebox.showerror("Export object", str(exc))

    def show_uv(self):
        if self.parts:
            UVWindow(self, self.parts, self.current["ruta_interna"])

    def dispose(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
