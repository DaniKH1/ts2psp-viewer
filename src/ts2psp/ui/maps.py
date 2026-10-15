"""Browse map resources, their mesh pieces, textures, and UVs."""

import csv
import io
import math
import queue
import threading
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.rendering.props import prop_scene, render_prop
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.surfaces import decode_surface
from ts2psp.ui.uv import UVWindow

# La cupula del cielo de los niveles.
#
# En los 15 mapas del catalogo que la traen son siempre la misma malla: dos
# piezas identicas de 392 vertices y 527.8 de lado, las dos mayores de muy lejos
# (en el cementerio la siguiente pieza mide 100). Se detectan por esa firma y no
# por su nombre porque los numeros de pieza cambian de un mapa a otro, y solo se
# quitan del encuadre: sin ellas la escena se abarca desde tan lejos, para que
# quepa una esfera de 528, que el solar entero queda en un punto de dos pixeles.
#
# El visor tiene un boton para volver a enseñarlas.
CUPULA_VERTICES = 392
CUPULA_LADO = 527.8
CUPULA_TOLERANCIA = 0.05

# Las piezas que el nivel coloca al cargar y no al leer.
#
# El juego pone el mobiliario del solar (camas, neveras, butacas, alfombras)
# con un Lua de nivel, no con coordenadas en el .res: la cabecera rdms de esas
# piezas trae el origen a cero y se dibujan todas amontonadas en el centro del
# solar. Se comprueba pieza a pieza en los 21 niveles: en el cuartel son 154 de
# 304 con el origen a menos de 1, y el menu principal solo 3 de 413, que es
# justo el unico nivel que se veia bien.
#
# No se borran: el boton del visor las vuelve a ensenar.
SIN_COLOCAR = 1.0


def es_sin_colocar(part):
    """Si la pieza llega al recurso sin sitio: su cabecera rdms la deja en el centro."""
    origen = part.get("origin")
    if origen is None:
        return False
    return max(abs(valor) for valor in origen) < SIN_COLOCAR


def es_cupula(part):
    """Si una pieza es la cupula del cielo del nivel."""
    vertices = part["vertices"]
    if len(vertices) != CUPULA_VERTICES:
        return False
    puntos = [v[0] for v in vertices]
    lado = max(
        max(p[k] for p in puntos) - min(p[k] for p in puntos) for k in range(3)
    )
    return abs(lado - CUPULA_LADO) <= CUPULA_LADO * CUPULA_TOLERANCIA


class MapTab(ttk.Frame):
    # Valores por defecto. La clase se puede montar sin pasar por __init__ (los
    # tests la levantan a mano) y `dispose` no debe encontrarse con un atributo
    # que todavia no existe.
    disposed = False
    _carga_cola = None
    _carga_poll = None
    _carga_generacion = 0
    _carga_entrada = None
    _cache = None
    objeto = None
    objeto_key = None

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
        self.show_sky = tk.BooleanVar(value=False)
        self.show_unplaced = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            middle,
            text="Show sky dome / hide to frame the lot",
            variable=self.show_sky,
            command=self.schedule,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(
            middle,
            text="Show unplaced furniture / hidden, the level positions it on load",
            variable=self.show_unplaced,
            command=self.schedule,
        ).pack(anchor="w")
        ttk.Button(middle, text="Frame part", command=self.frame_part).pack(
            fill="x", pady=(8, 3)
        )
        ttk.Button(middle, text="View texture and UVs", command=self.show_uv).pack(
            fill="x", pady=3
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
        # Un nivel se mira desde arriba. Con la inclinacion de 0 que le va bien a
        # un prop, el mapa se aplasta en una franja en el horizonte y se desperdicia
        # media pantalla; a 30 grados se ve la planta del solar sin deformarlo.
        self.camera = OrbitController(
            self.canvas, self.schedule, yaw=math.radians(25), pitch=math.radians(30)
        )
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
            text="Every RDMS piece of the level is drawn in its own place. The pieces "
            "the level places when it loads (furniture, lamps, fences) have no "
            "position in the file and are hidden; the checkbox brings them back. The "
            "TERN terrain mesh is not shown yet.",
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
        """Elige un mapa. Parsearlo va en un hilo salvo que ya este cacheado.

        Leer el recurso y sacar las piezas son entre 0,8 y 2,4 s de CPU segun el
        mapa, y hacerlo aqui dejaba la ventana sin responder a nada hasta
        terminar: al abrir la pestaña se congela. El hilo deja el resultado en
        una cola y el hilo de la interfaz lo pinta cuando llega, asi que se
        puede seguir tocando la lista mientras carga.

        El ultimo mapa se guarda ya parseado, para dar y quitar la pestaña no
        cueste otra vez los dos segundos.
        """
        choice = self.map_list.curselection()
        if not choice:
            return
        row = self.visible[choice[0]]
        if self.current and self.current["entry"] == row["entry"]:
            return
        if self._carga_entrada == row["entry"]:
            return  # este mapa ya esta cargandose
        if self._cache is not None and self._cache[0] == row["entry"]:
            _, data, parts, rejected = self._cache
            self._aplicar_mapa(row, data, parts, rejected)
            return
        self._empezar_carga(row)

    def _empezar_carga(self, row):
        """Lanza el hilo que lee y parsea el mapa."""
        self._carga_generacion += 1
        generacion = self._carga_generacion
        cola = queue.Queue()
        self._carga_cola = cola
        self._carga_entrada = row["entry"]
        self.map_list.configure(cursor="watch")
        self.part_list.delete(0, "end")
        self.status.configure(text=f'{row["ruta_interna"]}: loading...')
        path = self.app.paths["maps"]
        archivo = row["archivo_zip"]

        def work():
            try:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(archivo)
                parts, rejected = parse_rdms(data, apply_scene=False)
                cola.put((generacion, row, data, parts, rejected, None))
            except Exception as exc:
                # Aqui no se toca la interfaz ni se avisa de nada: solo se deja
                # el resultado y que lo recoja el hilo de la interfaz.
                cola.put((generacion, row, None, None, None, exc))

        threading.Thread(target=work, daemon=True).start()
        self._carga_poll = self.after(50, self._poll_carga)

    def _poll_carga(self):
        """Recoge el mapa en cuanto el hilo lo deja."""
        self._carga_poll = None
        if self.disposed or self._carga_cola is None:
            return
        try:
            generacion, row, data, parts, rejected, error = self._carga_cola.get_nowait()
        except queue.Empty:
            self._carga_poll = self.after(50, self._poll_carga)
            return
        self._carga_cola = None
        self.map_list.configure(cursor="")
        if error is not None:
            self._carga_entrada = None
            messagebox.showerror("Map", str(error))
            return
        if generacion != self._carga_generacion:
            return  # mientras cargaba se eligio otro mapa: este ya no vale
        self._cache = (row["entry"], data, parts, rejected)
        self._aplicar_mapa(row, data, parts, rejected)

    def _aplicar_mapa(self, row, data, parts, rejected):
        """Pinta las piezas de un mapa ya parseado y dibuja la mas grande."""
        self._carga_entrada = None
        # Los recursos del mapa anterior ya no estan en pantalla. Se sueltan
        # aqui y no al pedir el mapa, para que el anterior siga puesto mientras
        # carga el nuevo en vez de dejar el visor en negro.
        gl.forget_all()
        # `forget_all` acaba de soltar los recursos de la escena anterior, asi
        # que la referencia se tira con ellos: si se reutilizara, se dibujaria
        # contra buffers ya liberados.
        self.objeto = None
        self.objeto_key = None
        self.camera.focus_offset = (0.0, 0.0, 0.0)
        self.data = data
        self.parts = parts
        self.current = row
        self.part_list.delete(0, "end")
        for part in self.parts:
            self.part_list.insert(
                "end", f'{part["name"]} · {len(part["vertices"])} vertices'
            )
        self.status.configure(
            text=f'{row["ruta_interna"]} · {len(self.parts)} RDMS parts · '
            f'{row["terrenos"]} TERN sections · '
            f'{row["superficies"]} surfaces · '
            f"{len(rejected)} skipped parts"
        )
        sin_colocar = sum(1 for p in self.parts if es_sin_colocar(p))
        if sin_colocar:
            self.status.configure(
                text=f'{row["ruta_interna"]} · {len(self.parts)} RDMS parts · '
                f'{row["terrenos"]} TERN sections · '
                f'{row["superficies"]} surfaces · '
                f"{len(rejected)} skipped parts · "
                f"{sin_colocar} pieces hidden: the level places them when it "
                f"loads and their position is not in the file"
            )
        # No se elige ninguna pieza: el visor pinta el mapa entero, asi que
        # escolher la mas grande solo tapaba el resumen con el nombre de una
        # pieza. Elige la que quieras de la lista cuando la busques.

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

    def piezas_visibles(self):
        """Las piezas que se dibujan: el mapa entero, sin fondo ni mobiliario suelto.

        Se dejan fuera dos cosas, y solo del encuadre: la cupula, que son dos
        esferas de 528 de lado que envuelven todo el solar, y las piezas que el
        nivel coloca al cargar y cuyo sitio no esta en el recurso. Sin la cupula
        la escena se abarca desde tan lejos, para que quepa una esfera de 528,
        que el solar entero queda en un punto de dos pixeles. Sin las otras, el
        mobiliario aparece amontonado en el centro.

        Los dos grupos se pueden volver a ensenar desde el visor.
        """
        partes = [
            p
            for p in self.parts
            if self.show_unplaced.get() or not es_sin_colocar(p)
        ]
        if self.show_sky.get():
            return partes
        return [p for p in partes if not es_cupula(p)]

    def render(self):
        self.timer = None
        self.last_frame = gl.ahora_ms()
        parts = self.piezas_visibles()
        if not parts or not self.current:
            return
        try:
            width, height = self.canvas.view_size()
            key = (
                self.current["entry"],
                self.show_textures.get(),
                self.show_sky.get(),
            )
            if self.objeto_key != key:
                # La escena se reutiliza entre fotogramas: montar las 387 piezas
                # y subir 80 texturas son unos 270 ms, y si se hiciera en cada
                # redibujado el visor no responderia al arrastrar la camara.
                self.objeto = prop_scene(parts, ("mapa",) + key)
                self.objeto_key = key
            image = render_prop(
                parts,
                width,
                height,
                self.camera.yaw,
                self.camera.zoom,
                self.show_textures.get(),
                self.camera.pitch,
                self.camera.pan,
                key=("mapa",) + key,
                camara=self.camera,
                objeto=self.objeto,
                show_grid=self.show_grid.get(),
            )
            self.canvas.set_image(image)
        except Exception as exc:
            self.status.configure(text=f"Preview: {exc}")

    def frame_part(self):
        """Centra la camara en la pieza elegida, sin dejar de dibujar el mapa.

        El foco de la camara es un desplazamiento sobre el pivote de la escena,
        que es el centro de la caja de todo lo dibujado. Por eso hay que restar
        ese pivote: si se pusiera el centro de la pieza tal cual, la camara se
        moveria el doble de lo justo.
        """
        part = self.selected_part()
        if not part or self.objeto is None or self.objeto.pivote is None:
            return
        puntos = [v[0] for v in part["vertices"]]
        centro = tuple(
            (min(p[k] for p in puntos) + max(p[k] for p in puntos)) / 2.0
            for k in range(3)
        )
        self.camera.focus_offset = tuple(
            float(centro[k]) - float(self.objeto.pivote[k]) for k in range(3)
        )
        self.schedule()

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
        self.disposed = True
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        # El poll que recoge el hilo tambien se cancela: si no sigue vivo, Tk
        # avisa con un "application has been destroyed" al cerrar el visor.
        if self._carga_poll:
            self.after_cancel(self._carga_poll)
            self._carga_poll = None
        self._carga_cola = None
        self._cache = None
        self.objeto = None
        self.objeto_key = None
