"""Browse map resources, their mesh pieces, textures, and UVs."""

import csv
import io
import math
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.rendering.props import render_prop
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.surfaces import decode_surface
from ts2psp.ui.uv import UVWindow


class MapTab(ttk.Frame):
    def __init__(self, parent, app, fbx_export):
        super().__init__(parent, padding=8)
        self.app = app
        self.fbx_export = fbx_export
        with zipfile.ZipFile(app.paths["maps"]) as archive:
            self.rows = list(
                csv.DictReader(
                    io.StringIO(
                        archive.read("inventario_mapas.csv").decode("utf-8-sig")
                    )
                )
            )
        self.visible = []
        self.current = None
        self.data = b""
        self.parts = []
        self.timer = None
        self.last_frame = 0.0
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(left, text=f"Maps ({len(self.rows)})").pack(anchor="w")
        self.filter = tk.StringVar()
        entry = ttk.Entry(left, textvariable=self.filter, width=29)
        entry.pack(fill="x", pady=4)
        entry.bind("<KeyRelease>", self.refresh)
        self.map_list = tk.Listbox(left, width=30, height=24, exportselection=False)
        self.map_list.pack(fill="both", expand=True)
        self.map_list.bind("<<ListboxSelect>>", self.select_map)
        ttk.Button(left, text="Extract original map RES", command=self.export_res).pack(
            fill="x", pady=(8, 3)
        )
        ttk.Button(
            left, text="Extract PNG textures as ZIP", command=self.export_textures
        ).pack(fill="x", pady=3)
        ttk.Button(
            left, text="Export unplaced parts as GLB", command=self.export_all_parts
        ).pack(fill="x", pady=3)

        middle = ttk.Frame(self)
        middle.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(middle, text="Map 3D parts").pack(anchor="w")
        self.part_list = tk.Listbox(middle, width=29, height=24, exportselection=False)
        self.part_list.pack(fill="both", expand=True)
        self.part_list.bind("<<ListboxSelect>>", self.select_part)
        ttk.Button(middle, text="View texture and UVs", command=self.show_uv).pack(
            fill="x", pady=(8, 3)
        )
        ttk.Button(middle, text="Export part as GLB", command=self.export_part).pack(
            fill="x", pady=3
        )
        ttk.Button(
            middle, text="Export part as FBX", command=lambda: self.export_part("fbx")
        ).pack(fill="x", pady=3)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(right, minimum=(450, 500))
        self.canvas.on_resize = self.schedule
        self.camera = OrbitController(self.canvas, self.schedule, yaw=math.radians(25))
        self.show_textures = tk.BooleanVar(value=True)
        ttk.Label(right, text=CAMERA_HINT, wraplength=610).pack(anchor="w")
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
        self.status = ttk.Label(right, text="", wraplength=610)
        self.status.pack(anchor="w", fill="x")
        ttk.Label(
            right,
            text="Meshes and textures are readable. Full scene placement and TERN terrain "
            "have not yet been reconstructed.",
            wraplength=610,
        ).pack(anchor="w", pady=(5, 0))
        self.refresh()
        if self.visible:
            self.map_list.selection_set(0)
            # El mapa se carga la primera vez que se abre la pestaña, no al
            # construirla: parsearlo son varios segundos y hay quien solo quiere
            # personajes o props.

    def on_shown(self):
        """Carga el primer mapa la primera vez que se muestra la pestaña."""
        if self.visible and self.current is None:
            self.map_list.selection_set(0)
            self.select_map()

    def refresh(self, _event=None):
        term = self.filter.get().casefold()
        self.visible = [r for r in self.rows if term in r["ruta_interna"].casefold()]
        self.map_list.delete(0, "end")
        for row in self.visible:
            self.map_list.insert("end", row["ruta_interna"])

    def select_map(self, _event=None):
        choice = self.map_list.curselection()
        if not choice:
            return
        row = self.visible[choice[0]]
        if self.current and self.current["entry"] == row["entry"]:
            return
        gl.forget_all()  # los recursos del mapa anterior ya no están en pantalla
        try:
            with zipfile.ZipFile(self.app.paths["maps"]) as archive:
                self.data = archive.read(row["archivo_zip"])
            self.parts, rejected = parse_rdms(self.data, apply_scene=False)
            self.current = row
            self.part_list.delete(0, "end")
            for part in self.parts:
                label = f'{part["name"]} · {len(part["vertices"])} vertices'
                self.part_list.insert("end", label)
            self.status.configure(
                text=f'{row["ruta_interna"]} · {len(self.parts)} RDMS parts · '
                f'{row["terrenos"]} TERN sections · '
                f'{row["superficies"]} surfaces · '
                f"{len(rejected)} skipped parts"
            )
            if self.parts:
                best = max(
                    range(len(self.parts)), key=lambda i: len(self.parts[i]["vertices"])
                )
                self.part_list.selection_set(best)
                self.part_list.see(best)
                self.select_part()
        except Exception as exc:
            messagebox.showerror("Map", str(exc))

    def selected_part(self):
        choice = self.part_list.curselection()
        return self.parts[choice[0]] if choice else None

    def select_part(self, _event=None):
        part = self.selected_part()
        if part:
            self.status.configure(
                text=f'{self.current["ruta_interna"]} · {part["name"]} · '
                f'{len(part["vertices"])} vertices · '
                f'{len(part["indices"]) // 3} triangles · '
                f'{"texture" if part["texture"] else "no texture"} · '
                f'{"Vertex Color" if any(v[3] is not None for v in part["vertices"]) else "no vertex colors"}'
            )
            self.schedule()

    def schedule(self, _event=None):
        # con GPU se dibuja al instante y se limita a 60 Hz, en vez de posponer cada vez
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        if not gl.disponible():
            self.timer = self.after(50, self.render)
            return
        faltan = gl.faltan_para(self.last_frame, 16)
        if faltan > 0:
            self.timer = self.after(faltan, self.render)
        else:
            self.render()

    def render(self):
        self.timer = None
        self.last_frame = gl.ahora_ms()
        part = self.selected_part()
        if not part or not self.current:
            return
        try:
            width, height = self.canvas.view_size()
            image = render_prop(
                [part],
                width,
                height,
                self.camera.yaw,
                self.camera.zoom,
                self.show_textures.get(),
                self.camera.pitch,
                self.camera.pan,
                key=("mapa", self.current["entry"], part["name"]),
                camara=self.camera,
                show_grid=self.show_grid.get(),
            )
            self.canvas.set_image(image)
        except Exception as exc:
            self.status.configure(text=f"Preview: {exc}")

    def show_uv(self):
        part = self.selected_part()
        if part:
            UVWindow(self, [part], self.current["ruta_interna"])

    def export_part(self, fmt="glb"):
        part = self.selected_part()
        if not part:
            return
        name = f'{Path(self.current["ruta_interna"]).name}_{part["name"]}.{fmt}'
        target = filedialog.asksaveasfilename(
            defaultextension="." + fmt,
            initialfile=name,
            filetypes=[(fmt.upper(), "*." + fmt)],
        )
        if not target:
            return
        try:
            blender = self.app.resolve_blender() if fmt == "fbx" else None
            if fmt == "fbx" and not blender:
                return
            glb = self.app.cache / "map_piece.glb" if blender else target
            write_rigid_glb([part], name, glb)
            if blender:
                self.fbx_export(glb, target, blender)
            self.status.configure(text=f"Part saved: {target}")
        except Exception as exc:
            messagebox.showerror("Export part", str(exc))

    def export_res(self):
        if not self.current:
            return
        name = self.current["ruta_interna"].replace("/", "_") + ".res"
        target = filedialog.asksaveasfilename(
            defaultextension=".res",
            initialfile=name,
            filetypes=[("Recurso original", "*.res")],
        )
        if target:
            Path(target).write_bytes(self.data)
            self.status.configure(text=f"Original map saved: {target}")

    def export_textures(self):
        if not self.current:
            return
        name = self.current["ruta_interna"].replace("/", "_") + "_texturas.zip"
        target = filedialog.asksaveasfilename(
            defaultextension=".zip",
            initialfile=name,
            filetypes=[("ZIP archive", "*.zip")],
        )
        if not target:
            return
        count = 0
        try:
            with zipfile.ZipFile(
                target, "w", compression=zipfile.ZIP_DEFLATED
            ) as output:
                for i, (tag, off, size, _, _) in enumerate(sections(self.data)):
                    if tag != b"surf":
                        continue
                    png = decode_surface(self.data, off, size)
                    if png:
                        output.writestr(f"surface_{i:04d}.png", png)
                        count += 1
            self.status.configure(text=f"{count} PNG textures saved: {target}")
        except Exception as exc:
            messagebox.showerror("Extract textures", str(exc))

    def export_all_parts(self):
        if not self.current or not self.parts:
            return
        name = self.current["ruta_interna"].replace("/", "_") + "_parts.glb"
        target = filedialog.asksaveasfilename(
            defaultextension=".glb",
            initialfile=name,
            filetypes=[("GLB model", "*.glb")],
        )
        if not target:
            return
        try:
            write_rigid_glb(self.parts, self.current["ruta_interna"], target)
            self.status.configure(
                text=f"{len(self.parts)} GLB parts saved without "
                f"global placement: {target}"
            )
        except Exception as exc:
            messagebox.showerror("Export map parts", str(exc))

    def dispose(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
