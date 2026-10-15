"""Browse map resources, their mesh pieces, textures, and UVs."""

import csv
import io
import json
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
from ts2psp.rendering.props import prop_scene, render_prop, without_vertex_colors
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.prop_scene import level_prop_references
from ts2psp.formats.surfaces import decode_surface
from ts2psp.ui.floating_button import FloatingButton
from ts2psp.ui.textured_list import TexturedListbox, panel_redondeado
from ts2psp.ui.texture_gallery import TextureGallery
from ts2psp.ui.theme import LIST_PANEL_PATH
from ts2psp.ui.uv import UVWindow

# La cupula del cielo de los niveles.
#
# En 16 de los 21 mapas del catalogo son siempre la misma malla: dos piezas
# identicas de 392 vertices y 527.8 de lado, las dos mayores de muy lejos (en el
# cementerio la siguiente pieza mide 100). Los cinco mapas sin cielo son 123700,
# 123784, 123808, 123820 y 123868. Se detectan por esa firma y no por su nombre
# porque los numeros de pieza cambian de un mapa a otro.
#
# Se dibujan, pero no cuentan para el encuadre: sin ellas la escena se abarca
# desde tan lejos, para que quepa una esfera de 528, que el solar entero queda en
# un punto de dos pixeles. Para eso la escena lleva `sin_framing`, que deja sus
# vertices fuera de la caja sin dejar de pintarlos. Sus 672 triangulos miran
# todos hacia dentro, asi que con el recorte de caras traseras puesto se ven
# desde el solar sin tocar nada mas.
#
# El visor tiene un boton para volver a apagarlas.
CUPULA_VERTICES = 392
CUPULA_LADO = 527.8
CUPULA_TOLERANCIA = 0.05

# Las piezas que se dibujan sin recortar caras traseras.
#
# El recorte esta siempre puesto en los mapas, que es lo que hace que los muros
# se vean macizos y no por los dos lados. Estas piezas de aqui son de dos caras
# y con el recorte puesto salen con agujeros: son las dos de 0,92 de lado de la
# entrada del solar de Deadtree, unas de 28 y 40 vertices con su volumen bien
# orientado (0,27 y 1,51), o sea que no estan del reves sino que son huecas por
# un lado y con el recorte se ve el hueco.
#
# No se les da la vuelta con `CARAS_AL_REVES`: esa funcion invierte el sentido de
# los triangulos, y aqui ya apuntan hacia fuera. Lo que hace falta es dibujarlas
# sin recortar, que para eso tiene la escena el conjunto `sin_recorte`.
SIN_RECORTAR = {
    "levels/deadtree/hub": ("pieza_292", "pieza_293"),
}

# Legacy compression-origin diagnostic. NODE references, rather than RDMS
# origins, determine whether a prop has an authored placement.
SIN_COLOCAR = 1.0

# Tamano de la columna de la izquierda. La lista va al ancho de la galeria de
# texturas que tiene debajo, para que no se queden dos medidas distintas en la
# misma columna. Las alturas van en pixeles porque las listas son canvas.
ALTO_LISTA_MAPS = 178
ANCHO_LISTA_MAPS = 214
ALTO_LISTA_CAPAS = 300
ANCHO_LISTA_CAPAS = 178
# Holgura entre el borde redondeado del panel y lo que va dentro, en pixeles.
MARGEN_CAPAS = 9


def es_sin_colocar(part):
    """Legacy origin diagnostic, never a visibility or placement filter."""
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
    lado = max(max(p[k] for p in puntos) - min(p[k] for p in puntos) for k in range(3))
    return abs(lado - CUPULA_LADO) <= CUPULA_LADO * CUPULA_TOLERANCIA


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
        # teniendo su boton flotante; se conservan en memoria incluso apagados.
        self.show_sky = tk.BooleanVar(value=True)
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
            width=ANCHO_LISTA_MAPS // 8,
            style="CharacterSearch.TEntry",
        )
        entry.pack(fill="x", pady=(5, 10))
        entry.bind("<KeyRelease>", self.refresh)
        map_list_frame = ttk.Frame(left)
        # Sin `expand`: si lo tuviera, el marco se estiraria a todo lo que quedase
        # de la columna y la lista ocuparia el alto entero del visor, que es lo
        # que hacia que `ALTO_LISTA_MAPS` no sirviera de nada. Lo que se estira es
        # la galeria de debajo, que es la que puede aprovechar el hueco.
        map_list_frame.pack(fill="both")
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
        # La galeria es la que se estira con el hueco que deja la lista. Lleva
        # scroll propio, asi que quepa mas o menos no importa: si no cabe entera,
        # se rueda con la rueda del raton.
        self.gallery.pack(fill="both", expand=True)

        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        self.canvas = Viewport(right, minimum=(450, 500))
        self.canvas.on_resize = self.schedule
        # Un nivel se mira desde arriba y con la rotacion alineada a los ejes: los
        # muros del juego caen en coordenadas exactas, y con la camara en 25
        # grados de guiñada todo el solar va torcido respecto a la rejilla.
        # De frente y algo por encima: la guiñada a cero, para que los muros del juego,
        # que caen en coordenadas exactas, salgan rectos y no torsionados contra
        # la rejilla. Ni cenital, que aplana el solar y quita la sensacion de
        # altura, ni horizontal, que lo deja en una franja.
        #
        # El signo del pitch va al reves que en un visor normal: aqui negativo es
        # mirar hacia abajo. Con -30 la camara queda por encima del solar y lo
        # abarca entero, que es como se lee un mapa; en positivo se va por debajo
        # del terreno y el suelo no se ve, al mirarse su cara de menos.
        self.camera = OrbitController(
            self.canvas, self.schedule, yaw=0.0, pitch=math.radians(-30)
        )
        self.show_grid = tk.BooleanVar(value=True)
        # Botones flotantes sobre el visor, como en la pestaña de props.
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button("Export as...", self.export_as)
        self.recenter_button = self._floating_button("Reset view", self.camera.reset)
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

        self.layers_pos = [10, 10]
        self._capa_origen = (0, 0)
        self._capa_gesto = None
        self._capa_movido = False
        self._capas_minimizado = False
        self._build_titlebar()

        # Todo lo de debajo de la barra se pliega junto: la lista, los dos
        # botones de encender y apagar y el texto de ayuda.
        self.layers_cuerpo = ttk.Frame(self.layers_panel)
        self.layers_cuerpo.pack(fill="both", expand=True)

        botones = ttk.Frame(self.layers_cuerpo)
        # Los dos botones van centrados y en fila. El ancho hay que ponerlo a mano
        # porque el boton del tema es una imagen con un ancho minimo de 120 px:
        # sin esto, dos botones en fila piden 245 y ensanchan la ventana mas que
        # la lista, que es la que marca su ancho. Ocho caracteres es lo que hace
        # falta para que quepa "Show All" sin recortarse.
        botones.pack(fill="x", pady=(6, 0))
        ttk.Button(
            botones, text="Hide All", width=8, command=lambda: self._todas(False)
        ).pack(side="left", expand=True, padx=(0, 5))
        ttk.Button(
            botones, text="Show All", width=8, command=lambda: self._todas(True)
        ).pack(side="left", expand=True)
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

    # Alto de la franja de arriba del panel. Lo que se ve en ella son la flecha,
    # la equis y el rotulo; el fondo es el del panel, no una barra propia.
    ALTO_BARRA = 24
    # La esquina de la derecha cierra. La zona es de este ancho, un poco mas que
    # el de la equis, para no tener que apuntar a la punta de la cruz.
    HOLGURA_BARRA = 3
    LADO_BOTON = 17

    def _build_titlebar(self):
        """La franja de arriba de la ventana de capas.

        Antes era una barra de titulo de verdad: fondo con degradado, el rotulo
        «Layers» en medio, la flecha que plegaba en la esquina y la equis que
        cerraba en la otra. Encima del boton flotante «Layers» del visor, ese
        rectangulo mas claro flotando sobre el panel era lo unico que molestaba:
        parecian dos barras, una encima de otra.

        Del fondo no queda nada. La franja es del mismo color que el panel, de
        modo que la flecha, la equis y el rotulo salen directamente sobre el marco
        y no se ve nada detras. Los gestos se conservan enteros, que no tienen
        otro sitio donde vivir: un clic en cualquier parte pliega y despliega la
        ventana, un clic en la esquina de la derecha la cierra, y arrastrando se
        mueve. Todo eso mira donde ha caido el raton, en `_capa_press`, asi que
        los signos no son botones.
        """
        self.capa_barra = tk.Canvas(
            self.layers_panel,
            # El ancho hay que ponerlo a mano: un canvas de Tk pide 378 px de
            # ancho si no se le dice otro, y como la franja se empaqueta con
            # fill="x" esa peticion es la que marca el ancho de toda la ventana.
            width=ANCHO_LISTA_CAPAS,
            height=self.ALTO_BARRA,
            highlightthickness=0,
            borderwidth=0,
            # El color del panel, no el de la barra: la franja no se distingue del
            # marco. Tk no tiene lienzos transparentes, asi que el truco es que no
            # pinte ningun color que el de al lado.
            background=self.app._sims_theme_colors["background"],
        )
        # Con el margen del panel dentro: la franja ocupa el ancho del marco, no
        # el de la lista, y asi se agarra la ventana por los bordes tambien.
        self.capa_barra.pack(fill="x", padx=MARGEN_CAPAS, pady=(MARGEN_CAPAS, 0))
        self.capa_barra.bind("<Configure>", self._pintar_barra)
        self.capa_barra.bind("<ButtonPress-1>", self._capa_press)
        self.capa_barra.bind("<B1-Motion>", self._arrastrar_capas)
        self.capa_barra.bind("<ButtonRelease-1>", self._capa_soltar)
        self._flecha = self.capa_barra.create_polygon(
            0, 0, 0, 0, 0, 0, fill="", outline=""
        )
        self._equis = self.capa_barra.create_line(0, 0, 0, 0, width=2)
        self._equis2 = self.capa_barra.create_line(0, 0, 0, 0, width=2)
        self._rotulo = self.capa_barra.create_text(
            0,
            0,
            text="Layers",
            anchor="center",
            font=(self.app._sims_font_family, 9, "bold"),
        )
        self._capas_minimizado = False
        self.after_idle(self._pintar_barra)

    def _pintar_barra(self, event=None):
        """Coloca la flecha, la equis y el rotulo sobre el marco del panel.

        Solo eso: no se pinta ningun fondo. La franja es del mismo color que el
        panel, y los tres signos van en el color del texto del tema.
        """
        texto = self.app._sims_theme_colors["foreground"]
        ancho = max(120, self.capa_barra.winfo_width())
        alto = self.ALTO_BARRA
        self.capa_barra.itemconfigure(self._flecha, fill=texto, outline="")
        self.capa_barra.itemconfigure(self._equis, fill=texto)
        self.capa_barra.itemconfigure(self._equis2, fill=texto)
        self.capa_barra.itemconfigure(self._rotulo, fill=texto)
        self.capa_barra.coords(self._rotulo, ancho / 2, alto / 2)

        # La flecha: abajo apuntando cuando esta desplegada, a la derecha cuando
        # esta plegada. Es la misma flecha de los arboles de carpeta.
        centro_x = self.HOLGURA_BARRA + self.LADO_BOTON / 2
        centro_y = alto / 2
        if self._capas_minimizado:
            puntos = [
                centro_x - 3,
                centro_y - 5,
                centro_x - 3,
                centro_y + 5,
                centro_x + 5,
                centro_y,
            ]
        else:
            puntos = [
                centro_x - 5,
                centro_y - 3,
                centro_x + 5,
                centro_y - 3,
                centro_x,
                centro_y + 5,
            ]
        self.capa_barra.coords(self._flecha, *puntos)

        x = ancho - self.HOLGURA_BARRA - self.LADO_BOTON / 2
        brazo = 4
        self.capa_barra.coords(
            self._equis,
            x - brazo,
            centro_y - brazo,
            x + brazo,
            centro_y + brazo,
        )
        self.capa_barra.coords(
            self._equis2,
            x - brazo,
            centro_y + brazo,
            x + brazo,
            centro_y - brazo,
        )

    def _zona_de_la_barra(self, x):
        """Donde se ha pulsado en la franja: la esquina que cierra, o el resto.

        La esquina de la equis reserva un ancho de zona, no el del signo, para no
        tener que apuntar a la punta de la cruz. El resto de la franja pliega.
        """
        ancho = max(120, self.capa_barra.winfo_width())
        if x >= ancho - self.HOLGURA_BARRA - self.LADO_BOTON:
            return "equis"
        return "titulo"

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
        """Pliega la lista dejando solo la franja de arriba, y la vuelve a abrir."""
        self._capas_minimizado = not self._capas_minimizado
        if self._capas_minimizado:
            self.layers_cuerpo.pack_forget()
        else:
            self.layers_cuerpo.pack(fill="both", expand=True)
        self.layers_panel.update_idletasks()
        # La flecha gira al cambiar el estado, y el fondo redondeado se vuelve a
        # medir porque el alto del panel lo marca su contenido.
        self._pintar_barra()
        self._pintar_panel_capas()
        self._colocar_panel_capas()

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
        """Pulsar la barra: la equis cierra y el resto empieza un arrastre.

        Pulsar el resto no pliega todavia: hasta que no se suelta no se sabe si
        el raton estaba para plegar o arrastrando para mover. Lo decide
        `_capa_soltar`, segun `_capa_gesto`.
        """
        if self._zona_de_la_barra(event.x) == "equis":
            # El gesto ya esta gastado en cerrar. Sin esto, al soltar se plegaria
            # tambien y la ventana se abriria la vez siguiente ya plegada.
            self._capa_gesto = "cerrar"
            return self.toggle_layers_panel()
        return self._capa_origen_de(event)

    def _capa_soltar(self, event):
        """Al soltar: si el raton no se ha movido, el gesto fue un clic.

        Un clic en cualquier parte de la barra pliega la ventana, no solo en la
        flecha: es lo que se espera de una barra de titulo. Y como al soltar se
        mira cuanto se ha movido, el arrastre para mover la ventana sigue siendo
        el otro gesto.
        """
        if self._capa_gesto == "arrastrar" and not self._capa_movido:
            self.toggle_minimizar_capas()

    def _capa_origen_de(self, event):
        """Marca donde se ha pulsado la barra, para medir el arrastre desde ahi."""
        self._capa_origen = (event.x_root, event.y_root)
        self._capa_gesto = "arrastrar"
        self._capa_movido = False

    def _arrastrar_capas(self, event):
        x, y = self.layers_pos
        desplazamiento = max(
            abs(event.x_root - self._capa_origen[0]),
            abs(event.y_root - self._capa_origen[1]),
        )
        # A partir de un par de pixeles se da por arrastrado. Es lo que separa el
        # gesto de mover la ventana del gesto de pulsar la barra para plegarla: la
        # mano tiembla al pulsar y si no, cualquier clic moveria el panel.
        if desplazamiento > 3:
            self._capa_movido = True
        if not self._capa_movido:
            return
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
        """Change cached shading; keep geometry, textures, layers and camera."""
        self.vertex_colors.set(not self.vertex_colors.get())
        self.vertex_button.set_selected(self.vertex_colors.get())
        self.schedule()

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
        ttk.Label(contenido, text=f"Export {self.current['ruta_interna']} as:").pack(
            anchor="w", pady=(0, 8)
        )
        for etiqueta, valor in (
            ("Every 3D piece as GLB (.glb)", "glb"),
            ("The selected piece as GLB (.glb)", "parte_glb"),
            ("The selected piece as FBX (.fbx) — needs Blender", "parte_fbx"),
            ("The original map file (.res)", "res"),
            ("All the textures as a ZIP", "texturas"),
            ("Prop placement references (.json)", "placements"),
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
        elif destino == "placements":
            self.export_placements()

    def export_placements(self):
        if not self.current or self.data is None:
            return
        target = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=self.current["ruta_interna"].rsplit("/", 1)[-1] + "_props.json",
        )
        if not target:
            return
        try:
            report = {
                "map": self.current["ruta_interna"],
                "coordinates": "PSP: X/Y ground, Z up; column-vector matrices, row-major JSON",
                "note": "Authored references; game scripts may select alternate states or move objects.",
                "references": level_prop_references(self.data, sections(self.data)),
            }
            Path(target).write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            messagebox.showerror("Export prop placements", str(exc))

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

        def work():
            try:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(archivo)
                parts, rejected = parse_rdms(
                    data,
                    apply_scene=True,
                    vertex_colors=True,
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
            generacion, row, data, parts, rejected, error = (
                self._carga_cola.get_nowait()
            )
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
        instances = {
            p.get("placement_node") for p in self.parts if p.get("placement_node")
        }
        self._resumen = (
            f'{row["ruta_interna"]} · {len(self.parts)} RDMS parts · '
            f'{row["terrenos"]} TERN sections · '
            f'{row["superficies"]} surfaces · '
            f"{len(rejected)} skipped parts"
        )
        if instances:
            self._resumen += f" · {len(instances)} placed prop instances"
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
                + (
                    f' · {part["resource_path"]} · {part["placement_node"]}'
                    if part.get("resource_path")
                    else ""
                )
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

        La cupula va aparte: son dos esferas de 528 de lado que envuelven el
        solar, y no entran en el encuadre de la camara. Se dibujan si el boton
        del cielo esta encendido, pero la escena las deja fuera de la caja, que
        si no se abarca el nivel desde tan lejos que queda en un punto.
        """
        partes = [p for p in self.parts if p["name"] not in self.ocultas]
        if self.show_sky.get():
            return partes
        return [p for p in partes if not es_cupula(p)]

    def piezas_de_la_escena(self):
        """Las piezas que se suben a la GPU: el nivel entero, con el cielo si esta.

        Van todas, tambien las apagadas. Apagar una no quita su malla de la
        escena, solo deja de pintarse: asi la caja del encuadre no cambia y la
        camara no se reajusta cada vez que se enciende o apaga una pieza.
        """
        if self.show_sky.get():
            return list(self.parts)
        return [p for p in self.parts if not es_cupula(p)]

    def nombres_sin_recorte(self):
        """Las piezas del nivel que hay que dibujar sin recortar caras traseras.

        Salen de la lista a mano `SIN_RECORTAR`, que va por nivel porque los
        numeros de pieza cambian de un mapa a otro. Los cinco niveles que no
        estan en la tabla no devuelven ninguna.
        """
        return SIN_RECORTAR.get(self.current["ruta_interna"], ())

    def nombres_del_cielo(self):
        """Los nombres de las piezas de cielo del nivel, si las tiene.

        Se le pasan a la escena como `sin_framing`: se dibujan, pero sus vertices
        no cuentan para la caja. Los 16 niveles que traen cielo tienen dos
        esferas iguales de 392 vertices y 528 de lado; en los otros 5 no hay
        ninguna y esta lista sale vacia.
        """
        if not self.show_sky.get():
            return ()
        return tuple(p["name"] for p in self.parts if es_cupula(p))

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
            # La escena se monta una vez por nivel y no se toca al apagar nada:
            # el unico motivo para rehacerla es cambiar de nivel o de ajustes que
            # cambian lo que se dibuja de verdad.
            key = (
                self.current["entry"],
                self.show_textures.get(),
                self.show_sky.get(),
                culling,
            )
            if self.objeto_key != key:
                # La escena se reutiliza entre fotogramas: montar las 387 piezas
                # y subir 80 texturas son unos 270 ms, y si se hiciera en cada
                # redibujado el visor no responderia al arrastrar la camara.
                self.objeto = prop_scene(
                    self.piezas_de_la_escena(),
                    ("mapa",) + key,
                    culling=culling,
                    sin_framing=self.nombres_del_cielo(),
                )
                self.objeto_key = key
            self.objeto.fijar_vertex_colors(self.vertex_colors.get())
            self.objeto.sin_mallas = False
            # Las apagadas se marcan por nombre y la escena las busca una vez.
            self.objeto.ocultas_mallas = frozenset(
                indice
                for indice, nombre in enumerate(self.objeto.nombres)
                if nombre in self.ocultas
            )
            # Lo mismo con las de dos caras: se dibujan sin recortar, pero sin
            # mezclarse, que es distinto de como se dibuja el pelo.
            sin_recorte = self.nombres_sin_recorte()
            self.objeto.sin_recorte = frozenset(
                indice
                for indice, nombre in enumerate(self.objeto.nombres)
                if nombre in sin_recorte
            )
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
            write_rigid_glb(self._export_parts([part]), name, glb)
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
            write_rigid_glb(
                self._export_parts(self.parts), self.current["ruta_interna"], target
            )
            self.status.configure(
                text=f"{len(self.parts)} GLB parts saved without "
                f"global placement: {target}"
            )
        except Exception as exc:
            messagebox.showerror("Export map parts", str(exc))

    def _export_parts(self, parts):
        return parts if self.vertex_colors.get() else without_vertex_colors(parts)

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
