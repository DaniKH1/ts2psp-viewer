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

from PIL import Image, ImageTk

from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.rendering.props import prop_scene, render_prop
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.surfaces import decode_surface
from ts2psp.ui.floating_button import FloatingButton
from ts2psp.ui.textured_list import TexturedListbox, panel_redondeado
from ts2psp.ui.texture_gallery import TextureGallery
from ts2psp.ui.theme import LIST_PANEL_PATH
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

# Alturas de las listas del panel de capas, en pixeles. Son canvas, no filas.
# La de mapas es bajita a proposito: debajo va la galeria de texturas, que con
# 21 niveles competian por el alto de la columna.
ALTO_LISTA_MAPS = 356
ALTO_LISTA_CAPAS = 300
ANCHO_LISTA_MAPS = 250
ANCHO_LISTA_CAPAS = 244
# Holgura entre el borde redondeado del panel y lo que va dentro, en pixeles.
MARGEN_CAPAS = 9


def es_sin_colocar(part):
    """Si la pieza llega al recurso sin sitio: su cabecera rdms la deja en el centro.

    Esto ya no aparta nada del dibujo. Se queda como detector porque el filtro
    escondia muebles que si estan colocados, y mientras se estuvo aplicando no
    se podia saber cuales eran: lo que no se ve, no se comprueba.
    """
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


def firma_de_piezas(ocultas):
    """La parte de la clave de escena que depende de que piezas estan apagadas.

    Van los NOMBRES y no la cuenta. Con la cuenta, apagar una pieza y luego otra
    deja el mismo numero que al principio: la escena se creia la misma, no se
    remitaba a construir y al volver a encender la primera seguia sin verse.
    """
    return tuple(sorted(ocultas))


def nombre_del_mapa(row):
    """El nombre que se enseña del nivel, sin su ruta interna.

    Las rutas son de desarrollo (`levels/gas_station/garage`) y no dicen nada del
    sitio: quien juega ahi lo llama Gas Station. Esta tabla es la que se enseqa, y
    lo que no esta en ella cae al nombre de la carpeta, que por lo menos no se
    repite: hay cuatro niveles que se llaman `hub`.
    """
    ruta = row["ruta_interna"]
    conocido = NOMBRES_DE_MAPAS.get(ruta)
    if conocido:
        return conocido
    partes = ruta.split("/")
    return partes[-2] + "/" + partes[-1] if len(partes) > 2 else ruta


NOMBRES_DE_MAPAS = {
    "levels/deadtree/graveyard": "Graveyard",
    "levels/deadtree/hub": "Deadtree",
    "levels/deadtree/kine": "Kine",
    "levels/deadtree/library": "Library",
    "levels/deadtree/saloon": "Saloon",
    "levels/division_47/barracks": "Barracks",
    "levels/division_47/hub": "Division 47",
    "levels/division_47/lab": "Laboratory",
    "levels/division_47/secret_sublevel": "Secret Sublevel",
    "levels/gas_station/curio_shop": "Curio Shop",
    "levels/gas_station/garage": "Garage",
    "levels/gas_station/hub": "Gas Station",
    "levels/interface/mainmenu": "Espiritu Estate (Unused)",
    "levels/paradise_place/beaker": "Beaker House",
    "levels/paradise_place/doss": "Doss House",
    "levels/paradise_place/espiritu": "Espiritu Estate",
    "levels/paradise_place/hub": "Paradise Place",
    "levels/paradise_place/newlow": "Newlow House",
    "levels/paradise_place/rossum": "Rossum House",
    "levels/cas": "Create a Sim",
    "levels/the_monument": "The Monument",
}


class ListaCapas(TexturedListbox):
    """Lista de capas del panel flotante.

    Cada fila es una pieza del nivel y lo que hace al pulsarla es encenderla o
    apagarla. Se aparta de la lista normal en dos cosas: no desplaza la lista al
    elegir (si no, tocar una pieza del final salta el panel entero) y avisa a
    quien la ha creado en vez de limitarse a marcar la fila.
    """

    def __init__(self, parent, texture_path, al_tocar, **kwargs):
        super().__init__(parent, texture_path, **kwargs)
        self._al_tocar = al_tocar

    def _choose(self, index, extend=False, toggle=False):
        if not 0 <= index < len(self._items):
            return "break"
        self._selected = index
        self._selected_set = {index}
        self._redraw()
        self._al_tocar(index)
        self.event_generate("<<ListboxSelect>>")
        return "break"

    def refrescar_filas(self, textos):
        """Cambia el texto de las filas sin perder cual esta marcada."""
        self._items[:] = [str(texto) for texto in textos]
        self._redraw()


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
    _carga_color = False
    _resumen = ""
    ocultas = frozenset()

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
        # Nombres de las piezas apagadas desde el panel de capas. Se guardan por
        # nombre y no por indice porque al recargar el mapa la lista se rehace y
        # los indices habrian cambiado de lugar.
        self.ocultas = set()
        # Estas cuatro ya no tienen casilla: la cupula y el mobiliario sin
        # colocar se quedan fuera a proposito, las texturas se ven siempre y el
        # recorte de caras traseras va activo. Los colores de vertice siguen
        # teniendo su boton flotante, que es la unica opcion que cambia el
        # parseo y obliga a recargar el nivel.
        self.show_sky = tk.BooleanVar(value=False)
        self.show_unplaced = tk.BooleanVar(value=False)
        self.vertex_colors = tk.BooleanVar(value=False)
        self.show_textures = tk.BooleanVar(value=True)
        self.culling = tk.BooleanVar(value=True)
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(
            left,
            text=f"Maps ({len(self.rows)})",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        ).pack(fill="x")
        self.filter = tk.StringVar()
        entry = ttk.Entry(
            left,
            textvariable=self.filter,
            width=29,
            style="CharacterSearch.TEntry",
        )
        entry.pack(fill="x", pady=(5, 10))
        entry.bind("<KeyRelease>", self.refresh)
        map_list_frame = ttk.Frame(left)
        map_list_frame.pack(fill="both", expand=True)
        self.map_list = TexturedListbox(
            map_list_frame,
            LIST_PANEL_PATH,
            width=ANCHO_LISTA_MAPS,
            height=ALTO_LISTA_MAPS,
            font=(self.app._sims_font_family, 10, "bold"),
        )
        map_scroll = ttk.Scrollbar(
            map_list_frame, orient="vertical", command=self.map_list.yview
        )
        self.map_list.configure(yscrollcommand=map_scroll.set)
        map_scroll.pack(side="right", fill="y")
        self.map_list.pack(side="left", fill="both", expand=True)
        self.map_list.bind("<<ListboxSelect>>", self.select_map)
        # Debajo de la lista, las texturas del nivel que este cargado. Se separa
        # con una raya porque es otra cosa: la lista elige donde mirar y estas
        # enseñan que lleva cada malla.
        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=(16, 12))
        ttk.Label(
            left,
            text="Textures",
            anchor="center",
            font=(self.app._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 4))
        self.gallery = TextureGallery(left, self.app)
        self.gallery.pack(fill="x", expand=False)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(right, minimum=(450, 500))
        self.canvas.on_resize = self.schedule
        # Un nivel se mira desde arriba y con la rotacion alineada a los ejes: los
        # muros del juego caen en coordenadas exactas, y con la camara en 25
        # grados de guiñada todo el solar va torcido respecto a la rejilla.
        self.camera = OrbitController(
            self.canvas, self.schedule, yaw=0.0, pitch=math.radians(30)
        )
        self.show_grid = tk.BooleanVar(value=True)
        # Botones flotantes sobre el visor, como en la pestaña de props.
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button("Export as...", self.export_as)
        self.recenter_button = self._floating_button(
            "Reset view", self.camera.reset
        )
        self.vertex_button = self._floating_button(
            "Vertex Colors", self.toggle_vertex_colors
        )
        self.layers_button = self._floating_button("Layers", self.toggle_layers_panel)
        self.grid_button = self._floating_button(
            "Grid", self.toggle_grid, selected=True
        )
        self._build_layers_panel()
        self.canvas.bind("<Configure>", self._position_viewport_controls, add="+")
        self.after_idle(self._position_viewport_controls)

        # La barra de estado se queda sin sitio en la ventana: sigue existiendo
        # como etiqueta sin empaquetar porque el resto de la pestana sigue
        # hablandole al cargar un nivel y al exportar.
        self.status = ttk.Label(text="")
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
            self.map_list.insert("end", nombre_del_mapa(row))

    # -- botones flotantes y panel de capas ----------------------------------

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
            width = self.canvas.winfo_width()
        else:
            width = event.width
        self.export_button.place(10, 10)
        # A la derecha, de fuera hacia dentro: Grid, Layers y Vertex Colors.
        x = width - 10
        for boton in (self.grid_button, self.layers_button, self.vertex_button):
            x -= boton.reqwidth
            boton.place(x, 10)
            x -= 12
        # Reset view en la derecha, en la fila de debajo y pegado al mismo borde
        # que Grid, que es donde se busca cuando se ha descolocado la camara.
        self.recenter_button.place(width - 10 - self.recenter_button.reqwidth, 50)
        self._colocar_panel_capas()

    def toggle_grid(self):
        self.show_grid.set(not self.show_grid.get())
        self.grid_button.set_selected(self.show_grid.get())
        self.schedule()

    def _build_layers_panel(self):
        """El desplegable de capas: se pinta encima del visor y se puede mover.

        El fondo redondeado es un item del canvas, no un widget: un marco con
        fondo plano haria un rectangulo con las esquinas vivas sobre la escena,
        y la textura del panel es transparente justo en las esquinas.

        El panel no lleva palomitas. Las opciones que afectan a todo el nivel
        (cupula, mobiliario sin colocar, texturas y colores de vertice) estan
        como botones flotantes y como casillas al pie del visor; aqui solo la
        lista de piezas y los dos botones que las encienden o apagan todas.
        """
        self._capas_fuente = Image.open(LIST_PANEL_PATH).convert("RGBA")
        self._capas_foto = None
        self.layers_fondo = self.canvas.create_image(0, 0, anchor="nw")
        self.layers_panel = ttk.Frame(self.canvas)
        self.layers_window = self.canvas.create_window(
            0, 0, anchor="nw", window=self.layers_panel
        )

        cabecera = ttk.Frame(self.layers_panel)
        cabecera.pack(fill="x")
        self._titulo_capas = ttk.Label(
            cabecera,
            text="Layers",
            font=(self.app._sims_font_family, 10, "bold"),
        )
        self._titulo_capas.pack(side="left")
        # Los dos signs de la derecha son la equis que cierra y el guion que
        # pliega la lista. Antes decia "drag" y habia que acertar para arrastrar
        # la cabecera; asi se ven las dos cosas que se pueden hacer.
        cerrar = ttk.Label(cabecera, text="\u2715", foreground="#182658")
        cerrar.pack(side="right", padx=(12, 2))
        plegar = ttk.Label(cabecera, text="\u2212", foreground="#182658")
        plegar.pack(side="right")
        self._signo_cerrar = cerrar
        self._signo_plegar = plegar
        cerrar.bind("<Button-1>", lambda event: self.toggle_layers_panel())
        plegar.bind("<Button-1>", lambda event: self.toggle_minimizar_capas())
        # El titulo y los dos signs no arrastran; el resto de la cabecera si.
        cabecera.bind("<ButtonPress-1>", self._capa_press)
        cabecera.bind("<B1-Motion>", self._arrastrar_capas)
        self._titulo_capas.bind("<ButtonPress-1>", self._capa_press)
        self._titulo_capas.bind("<B1-Motion>", self._arrastrar_capas)
        self.layers_pos = [10, 10]
        self._capa_origen = (0, 0)
        self._capas_minimizado = False

        # Todo lo de debajo de la cabecera se pliega junto: la lista, los dos
        # botones de encender y apagar y el texto de ayuda.
        self.layers_cuerpo = ttk.Frame(self.layers_panel)
        self.layers_cuerpo.pack(fill="both", expand=True)

        botones = ttk.Frame(self.layers_cuerpo)
        botones.pack(fill="x", pady=(6, 0))
        ttk.Button(
            botones, text="Hide everything", command=lambda: self._todas(False)
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            botones, text="Unhide everything", command=lambda: self._todas(True)
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Separator(self.layers_cuerpo).pack(fill="x", pady=6)
        ttk.Label(
            self.layers_cuerpo,
            text="Click a piece to show or hide it",
            wraplength=ANCHO_LISTA_CAPAS - 16,
        ).pack(anchor="w")

        marco = ttk.Frame(self.layers_cuerpo)
        marco.pack(fill="both", expand=True)
        self.part_list = ListaCapas(
            marco,
            LIST_PANEL_PATH,
            self._alternar_capa,
            width=ANCHO_LISTA_CAPAS,
            height=ALTO_LISTA_CAPAS,
            font=(self.app._sims_font_family, 9),
        )
        scroll = ttk.Scrollbar(marco, orient="vertical", command=self.part_list.yview)
        self.part_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.part_list.pack(side="left", fill="both", expand=True)
        self.part_list.bind("<<ListboxSelect>>", self.select_part)
        self._pintar_panel_capas()
        self.canvas.itemconfigure(self.layers_fondo, state="hidden")
        self.canvas.itemconfigure(self.layers_window, state="hidden")

    def _pintar_panel_capas(self):
        """Da al panel el alto de su contenido y pinta el fondo redondeado.

        Se vuelve a pintar al plegar y al desplegar, porque el alto del panel lo
        marca su contenido y el fondo redondeado va a medida.
        """
        self.layers_panel.update_idletasks()
        ancho = self.layers_panel.winfo_reqwidth() + 2 * MARGEN_CAPAS
        alto = self.layers_panel.winfo_reqheight() + 2 * MARGEN_CAPAS
        self._capas_foto = ImageTk.PhotoImage(
            panel_redondeado(self._capas_fuente, ancho, alto), master=self.canvas
        )
        self.canvas.itemconfigure(
            self.layers_fondo, image=self._capas_foto, state="normal"
        )

    def toggle_minimizar_capas(self):
        """Pliega la lista de piezas dejando solo la cabecera del panel."""
        self._capas_minimizado = not self._capas_minimizado
        if self._capas_minimizado:
            self.layers_cuerpo.pack_forget()
            for hijo in self._signos_capas():
                hijo.configure(text="\u25b8")
        else:
            self.layers_cuerpo.pack(fill="both", expand=True)
            for hijo in self._signos_capas():
                hijo.configure(text="\u2212")
        self.layers_panel.update_idletasks()
        self._pintar_panel_capas()
        self._colocar_panel_capas()

    def _signos_capas(self):
        """Los dos etiquetas de la cabecera: la equis y el guion del pliegue."""
        return self._signo_plegar, self._signo_cerrar

    def _todas(self, mostrar):
        """Enciende o apaga todas las piezas de golpe."""
        self.ocultas = set() if mostrar else {p["name"] for p in self.parts}
        self.part_list.refrescar_filas(self._filas_de_capas())
        self._avisar_capas()
        self.schedule()

    def _colocar_panel_capas(self):
        if not hasattr(self, "layers_window"):
            return
        x, y = self.layers_pos
        ancho = self.canvas.bbox(self.layers_fondo)
        alto = (ancho[3] - ancho[1]) if ancho else 360
        maximo_y = max(10, self.canvas.winfo_height() - alto - 10)
        y = max(10, min(y, maximo_y))
        self.canvas.coords(self.layers_fondo, x, y)
        self.canvas.coords(self.layers_window, x + MARGEN_CAPAS, y + MARGEN_CAPAS)

    def _capa_press(self, event):
        """Marca donde se ha pulsado la cabecera, para medir el arrastre desde ahi."""
        self._capa_origen = (event.x_root, event.y_root)

    def _arrastrar_capas(self, event):
        x, y = self.layers_pos
        self.layers_pos = [
            x + event.x_root - self._capa_origen[0],
            y + event.y_root - self._capa_origen[1],
        ]
        self._capa_origen = (event.x_root, event.y_root)
        self._colocar_panel_capas()

    def toggle_layers_panel(self):
        abierto = self.canvas.itemcget(self.layers_window, "state") == "normal"
        estado = "hidden" if abierto else "normal"
        self.canvas.itemconfigure(self.layers_window, state=estado)
        self.canvas.itemconfigure(self.layers_fondo, state=estado)
        self.layers_button.set_selected(not abierto)
        if not abierto:
            # Se abre pegado al borde derecho y por debajo de las dos filas de
            # botones: si empieza a la altura de la primera, tapa la de Reset
            # view, que esta justo debajo de Grid.
            ancho = self.canvas.bbox(self.layers_fondo)
            total = (ancho[2] - ancho[0]) if ancho else ANCHO_LISTA_CAPAS
            self.layers_pos = [
                max(10, self.canvas.winfo_width() - total - 10),
                92,
            ]
            self._colocar_panel_capas()

    def toggle_vertex_colors(self):
        """El boton flotante de los colores de vertice: recargan el nivel entero."""
        self.vertex_colors.set(not self.vertex_colors.get())
        self.vertex_button.set_selected(self.vertex_colors.get())
        self.reload()

    def _filas_de_capas(self):
        filas = []
        for part in self.parts:
            marca = " " if part["name"] in self.ocultas else "x"
            filas.append(f"[{marca}] {part['name']} · {len(part['vertices'])} verts")
        return filas

    def _alternar_capa(self, index):
        """Enciende o apaga la pieza de la fila que se ha pulsado."""
        if not 0 <= index < len(self.parts):
            return
        nombre = self.parts[index]["name"]
        if nombre in self.ocultas:
            self.ocultas.discard(nombre)
        else:
            self.ocultas.add(nombre)
        self.part_list.refrescar_filas(self._filas_de_capas())
        self._avisar_capas()
        self.schedule()

    def _avisar_capas(self):
        """El resumen del mapa mas cuantas piezas hay encendidas."""
        if not hasattr(self, "part_list"):
            return
        resumen = getattr(self, "_resumen", "")
        if not resumen:
            return
        self.status.configure(
            text=f"{resumen} · {len(self.parts) - len(self.ocultas)} "
            f"of {len(self.parts)} pieces shown"
        )

    def export_as(self):
        """El boton flotante 'Export as...' ofrece los cuatro formatos del mapa."""
        if not self.current:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Export as...")
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        elegido = tk.StringVar(value="glb")
        contenido = ttk.Frame(dialog, padding=16)
        contenido.pack(fill="both", expand=True)
        ttk.Label(
            contenido, text=f"Export {self.current['ruta_interna']} as:"
        ).pack(anchor="w", pady=(0, 8))
        for etiqueta, valor in (
            ("Every 3D piece as GLB (.glb)", "glb"),
            ("The selected piece as GLB (.glb)", "parte_glb"),
            ("The selected piece as FBX (.fbx) — needs Blender", "parte_fbx"),
            ("The original map file (.res)", "res"),
            ("All the textures as a ZIP", "texturas"),
        ):
            ttk.Radiobutton(
                contenido, text=etiqueta, value=valor, variable=elegido
            ).pack(anchor="w", pady=2)
        botones = ttk.Frame(contenido)
        botones.pack(fill="x", pady=(12, 0))
        decision = {"valor": None}

        def aceptar():
            decision["valor"] = elegido.get()
            dialog.destroy()

        ttk.Button(botones, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(
            botones, text="Export", style="Primary.TButton", command=aceptar
        ).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.wait_window()
        destino = decision["valor"]
        if destino == "glb":
            self.export_all_parts()
        elif destino == "parte_glb":
            self.export_part()
        elif destino == "parte_fbx":
            self.export_part("fbx")
        elif destino == "res":
            self.export_res()
        elif destino == "texturas":
            self.export_textures()

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
        if self._cache is not None and self._cache[0] == self._clave_de_cache():
            _, data, parts, rejected = self._cache
            self._aplicar_mapa(row, data, parts, rejected)
            return
        self._empezar_carga(row)

    def _clave_de_cache(self):
        """La cache del ultimo mapa, con los ajustes que cambian el parseo."""
        if self.current is None:
            return None
        return (self.current["entry"], self.vertex_colors.get())

    def reload(self, _event=None):
        """Vuelve a parsear el mapa actual, para cuando cambia una opcion."""
        if self.current is None:
            return
        self._cache = None
        self._carga_entrada = None
        self._empezar_carga(self.current)

    def _empezar_carga(self, row):
        """Lanza el hilo que lee y parsea el mapa.

        Con `apply_scene=True`: el grafo de nodos coloca las mallas del nivel. En
        el menu principal 68 de los 105 muros llegan con su propia traslacion (una
        de ellas 43,1 en x) mientras el terreno se queda quieto, y sin aplicarla la
        casa se dibuja encima de la carretera. En el cementerio los nodos valen la
        identidad y no cambia nada, asi que se aplica siempre.
        """
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
        con_color = self._carga_color = self.vertex_colors.get()

        def work():
            try:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(archivo)
                parts, rejected = parse_rdms(
                    data,
                    apply_scene=True,
                    vertex_colors=con_color,
                    # La ruta hace falta para las excepciones que van por nivel,
                    # como las piezas a las que hay que dar la vuelta a las caras.
                    ruta=row["ruta_interna"],
                )
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
        self._cache = (row["entry"], self._carga_color, data, parts, rejected)
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
        self.data = data
        self.parts = parts
        self.current = row
        # La camara vuelve al origen y al encuadre de partida: el foco es la caja
        # de lo que se dibuja, asi que sin resetarla cada nivel se abriria
        # descentrado respecto al anterior y con la inclinacion que se hubiera
        # dejado antes.
        self.camera.reset()
        # Las piezas apagadas son de este mapa: al cambiar de nivel se olvida.
        self.ocultas = set()
        self.part_list.delete(0, "end")
        self.part_list.refrescar_filas(self._filas_de_capas())
        # La galeria de la izquierda enseña lo que lleva el nivel: cada pieza
        # guarda su textura como (nombre, pixeles), y sin pixeles no hay miniatura.
        self.gallery.set_textures(
            {
                part["texture"][0]: part["texture"][1]
                for part in self.parts
                if part.get("texture") and part["texture"][1]
            }
        )
        sin_colocar = sum(1 for p in self.parts if es_sin_colocar(p))
        self._resumen = (
            f'{row["ruta_interna"]} · {len(self.parts)} RDMS parts · '
            f'{row["terrenos"]} TERN sections · '
            f'{row["superficies"]} surfaces · '
            f"{len(rejected)} skipped parts"
        )
        if sin_colocar:
            self._resumen += (
                f" · {sin_colocar} pieces with no place in the file, drawn where "
                f"the resource leaves them"
            )
        self._avisar_capas()
        # No se elige ninguna pieza: el visor pinta el mapa entero, asi que
        # escolher la mas grande solo tapaba el resumen con el nombre de una
        # pieza. Elige la que quieras en el panel de capas cuando la busques.

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
        """Las piezas que se dibujan: todo lo que trae el recurso, menos lo apagado.

        Antes se apartaba ademas el mobiliario que el nivel coloca al cargar, el
        que llega con el origen a cero y se ve amontonado en el centro. Eso
        quitaba piezas que si estaban colocadas y, mientras estuvo puesto, no se
        podia distinguir una de otra. Ahora se dibuja todo lo que hay en el
        archivo y es el panel de capas el que apaga lo que sobre, una a una o de
        golpe.

        La cupula si se sigue dejando fuera del encuadre: son dos esferas de 528
        de lado que envuelven el solar, y con ellas dentro la escena se abarca
        desde tan lejos que el nivel entero queda en un punto de dos pixeles.
        """
        partes = [p for p in self.parts if p["name"] not in self.ocultas]
        if self.show_sky.get():
            return partes
        return [p for p in partes if not es_cupula(p)]

    def render(self):
        self.timer = None
        self.last_frame = gl.ahora_ms()
        if not self.current:
            return
        parts = self.piezas_visibles()
        try:
            width, height = self.canvas.view_size()
            if not parts:
                # No queda ninguna pieza encendida, pero la rejilla se sigue
                # dibujando: si se saliera sin mas, el fotograma anterior se
                # quedaria pegado y el visor pareceria congelado en vez de vacio.
                if self.objeto is None:
                    self.canvas.clear()
                    return
                self.objeto.sin_mallas = True
                self.canvas.set_image(
                    render_prop(
                        [],
                        width,
                        height,
                        self.camera.yaw,
                        self.camera.zoom,
                        self.show_textures.get(),
                        self.camera.pitch,
                        self.camera.pan,
                        key=("mapa", "solo-rejilla"),
                        camara=self.camera,
                        objeto=self.objeto,
                        show_grid=self.show_grid.get(),
                        culling=True,
                    )
                )
                return
            culling = self.culling.get()
            # La escena se vuelve a montar cuando cambia el CONJUNTO de piezas,
            # y por eso en la clave va quais son las apagadas y no cuantas hay.
            key = (
                self.current["entry"],
                self.show_textures.get(),
                self.show_sky.get(),
                self.show_unplaced.get(),
                culling,
                firma_de_piezas(self.ocultas),
            )
            if self.objeto_key != key:
                # La escena se reutiliza entre fotogramas: montar las 387 piezas
                # y subir 80 texturas son unos 270 ms, y si se hiciera en cada
                # redibujado el visor no responderia al arrastrar la camara.
                self.objeto = prop_scene(parts, ("mapa",) + key, culling=culling)
                self.objeto_key = key
            self.objeto.sin_mallas = False
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
                culling=culling,
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
