"""Browse map resources, their mesh pieces, textures, and UVs."""

import csv
import io
import json
import math
import re
import queue
import threading
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from ts2psp.ui.camera import CAMERA_HINT, OrbitController, Viewport
import ts2psp.rendering.opengl as gl
from ts2psp.rendering.props import prop_scene, render_prop, without_vertex_colors
from ts2psp.formats.props import parse_rdms, sections, write_rigid_glb
from ts2psp.formats.prop_scene import (
    daynight_rig_profiles,
    level_camera_light_points,
    level_prop_references,
    level_spawn_points,
)
from ts2psp.formats.characters import nombre_publico
from ts2psp.formats.materials import surface_textures
from ts2psp.core.names import layer_label, part_label, part_filename, texture_paths
from ts2psp.exporting.gltf import export_gltf
from ts2psp.ui.common import choose_export_format, export_model_name
from ts2psp.ui.i18n import translate
from ts2psp.ui.floating_button import FloatingButton, place_right_buttons
from ts2psp.ui.textured_list import TexturedListbox, panel_redondeado
from ts2psp.ui.texture_gallery import TextureGallery
from ts2psp.ui.theme import ANCHO_TROUGH_PROPS, LIST_PANEL_PATH
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
#
# La lista de maps y la galeria se reparten la columna a mitades, asi que estos
# dos altos son solo lo que cada una pide antes de que el reparto lo reparta:
# mientras sean iguales, da igual cuanto valgan. `uniform` los iguala al mayor y
# despues el `weight` reparte el sobrante a partes iguales, asi que basta con que
# los dos sean el mismo numero pequeno.
ALTO_LISTA_MAPS = 120
ANCHO_LISTA_MAPS = 214
ALTO_MITAD_MINIMA = 120
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


def variantes_cielo(parts):
    """Classify day/night sky domes from source names, then texture brightness."""
    cupulas = [part for part in parts if es_cupula(part)]
    variants = {}
    unresolved = []
    for part in cupulas:
        texture = part.get("texture") or ("", None)
        layers = part.get("texture_layers", ())
        descriptor = " ".join(
            str(value or "")
            for value in (
                part.get("name"),
                part.get("nombre"),
                part.get("resource_path"),
                part.get("source_texture_path"),
                texture[0] if isinstance(texture, (tuple, list)) else texture,
                *(
                    (layer.get("texture") or ("",))[0]
                    for layer in layers
                    if isinstance(layer, dict)
                ),
            )
        ).casefold()
        compact = re.sub(r"[^a-z0-9]", "", descriptor)
        if any(word in compact for word in ("night", "noche", "moon")):
            variants[part["name"]] = "night"
        elif any(word in compact for word in ("day", "dia", "sun")):
            variants[part["name"]] = "day"
        else:
            pixels = texture[1] if isinstance(texture, (tuple, list)) and len(texture) > 1 else None
            try:
                image = np.asarray(pixels, dtype=np.float32)
                if image.ndim >= 3 and image.shape[-1] >= 3:
                    rgb = image[..., :3]
                    if rgb.size and np.all(np.isfinite(rgb)):
                        unresolved.append((part["name"], float(rgb.mean())))
            except (TypeError, ValueError):
                pass
    if len(unresolved) == 2:
        dark, bright = sorted(unresolved, key=lambda row: row[1])
        if bright[1] - dark[1] > max(abs(bright[1]), 1.0) * 0.05:
            variants[dark[0]] = "night"
            variants[bright[0]] = "day"
    if len(cupulas) == 2 and len(variants) == 1:
        other = next(part["name"] for part in cupulas if part["name"] not in variants)
        variants[other] = "night" if next(iter(variants.values())) == "day" else "day"
    return variants


def nombre_del_mapa(row, language="en"):
    """El nombre que se enseña del nivel, sin su ruta interna.

    Las rutas son de desarrollo (`levels/gas_station/garage`) y no dicen nada del
    sitio: quien juega ahi lo llama Gas Station. Esta tabla es la que se enseqa, y
    lo que no esta en ella cae al nombre de la carpeta, que por lo menos no se
    repite: hay cuatro niveles que se llaman `hub`.
    """
    ruta = row["ruta_interna"]
    if language == "es" and ruta in NOMBRES_DE_MAPAS_ES:
        return NOMBRES_DE_MAPAS_ES[ruta]
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

NOMBRES_DE_MAPAS_ES = {
    "levels/deadtree/graveyard": "Cementerio",
    "levels/deadtree/hub": "Arbolseco",
    "levels/deadtree/kine": "Sociedad de la Vaca",
    "levels/deadtree/library": "Biblioteca",
    "levels/deadtree/saloon": "El Aullido Nocturno",
    "levels/division_47/barracks": "Cuartel",
    "levels/division_47/hub": "División 47",
    "levels/division_47/lab": "Laboratorio",
    "levels/division_47/secret_sublevel": "Subnivel secreto",
    "levels/gas_station/curio_shop": "Tienda de curiosidades",
    "levels/gas_station/garage": "Garaje",
    "levels/gas_station/hub": "Estación de servicio",
    "levels/interface/mainmenu": "Finca Espíritu (sin usar)",
    "levels/paradise_place/beaker": "Casa Panero",
    "levels/paradise_place/doss": "Casa Sobe",
    "levels/paradise_place/espiritu": "Finca Espíritu",
    "levels/paradise_place/hub": "Paraíso",
    "levels/paradise_place/newlow": "Casa Niubajo",
    "levels/paradise_place/rossum": "Casa Instein",
    "levels/cas": "Crear un Sim",
    "levels/the_monument": "El Monumento",
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
        self.spawn_points = []
        self.camera_light_points = []
        self.camera_points = []
        self.light_points = []
        self.daylight_profile = None
        self.daynight_rig_data = None
        self.daynight_rig_records = None
        self.daynight_mode = tk.StringVar(value="day")
        self.skybox_variants = {}
        self._daynight_update = None
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
        self.vertex_color_intensity = tk.DoubleVar(value=100.0)
        self.vertex_color_window = None
        self.vertex_color_pos = [10, 92]
        self.show_textures = tk.BooleanVar(value=True)
        self.culling = tk.BooleanVar(value=True)
        left = ttk.Frame(self)
        left.pack(side="left", fill="y", padx=(0, 8))
        self.maps_heading = ttk.Label(
            left,
            text="Maps",
            anchor="center",
            font=(self.app._sims_font_family, 12, "bold"),
        )
        self.maps_heading.pack(fill="x")
        self.filter = tk.StringVar()
        entry = ttk.Entry(
            left,
            textvariable=self.filter,
            width=ANCHO_LISTA_MAPS // 8,
            style="CharacterSearch.TEntry",
        )
        entry.pack(fill="x", pady=(5, 10))
        entry.bind("<KeyRelease>", self.refresh)

        # Las dos cajas de la columna, la lista de maps arriba y la galeria de
        # texturas abajo, con el mismo alto. El reparto no sale solo, y por eso
        # se calcula en `_repartir_la_columna`: las dos no piden lo mismo, la
        # galeria gasta ademas 101 px en su propia decoracion (la raya, el
        # rotulo y la banda con el nombre de la textura elegida), y con `pack` o
        # con `grid` a pelo se queda una de las dos mas corta y con hueco
        # debajo. `grid` las coloca en dos filas; las alturas las ponemos
        # nosotros, y la de la fila de abajo lleva el sobrante del redondeo.
        reparto = ttk.Frame(left)
        reparto.pack(fill="both", expand=True)
        reparto.rowconfigure(1, weight=1)
        reparto.bind("<Configure>", self._repartir_la_columna)
        self.reparto = reparto

        map_list_frame = ttk.Frame(reparto)
        map_list_frame.grid(row=0, column=0, sticky="nsew")
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
        # enseñan que lleva cada malla. La raya y el rotulo van en la mitad de la
        # galeria y no sueltos en la columna, para que el lienzo de las texturas
        # sea lo que llega hasta el final de abajo.
        texturas = ttk.Frame(reparto)
        texturas.grid(row=1, column=0, sticky="nsew")
        self.panel_texturas = texturas
        ttk.Separator(texturas, orient="horizontal").pack(fill="x", pady=(16, 12))
        ttk.Label(
            texturas,
            text="Textures",
            anchor="center",
            font=(self.app._sims_font_family, 11, "bold"),
        ).pack(fill="x", pady=(0, 4))
        self.gallery = TextureGallery(texturas, self.app)
        # La galeria no pide alto: lo recibe del reparto, que es lo que la deja
        # igualada con la lista y llegar al final de la columna. Lleva scroll
        # propio, asi que lo que no cabe se rueda con la rueda del raton.
        self.gallery.alto_maximo = ALTO_MITAD_MINIMA
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
            self.canvas, self.schedule, yaw=0.0, pitch=math.radians(-30), zoom=1.15
        )
        self.show_grid = tk.BooleanVar(value=True)
        self.show_spawn_points = tk.BooleanVar(value=False)
        self.show_camera_points = tk.BooleanVar(value=False)
        self.show_light_points = tk.BooleanVar(value=False)
        self.light_illumination = tk.BooleanVar(value=True)
        # Botones flotantes sobre el visor, como en la pestaña de props.
        self.floating_buttons = []
        self.camera.ignores = self._over_floating_button
        self.export_button = self._floating_button("Export as...", self.export_as)
        self.recenter_button = self._floating_button("Reset view", self.camera.reset)
        self.layers_button = self._floating_button("Layers", self.toggle_layers_panel)
        self.pointers_button = self._floating_button(
            "Pointers", self.toggle_pointers_panel
        )
        self.grid_button = self._floating_button(
            "Grid", self.toggle_grid, selected=True
        )
        self.vertex_button = self._floating_button("Lights", self.toggle_vertex_colors)
        self._build_layers_panel()
        self._build_pointers_panel()
        self._build_vertex_color_panel()
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

    def _repartir_la_columna(self, _event=None):
        """Lista de maps y galeria con el mismo alto, y la galeria hasta abajo.

        Las dos cajas miden lo mismo, no las dos mitades de la columna: la galeria
        se come por el camino la raya de separacion, el rotulo y la banda con el
        nombre de la textura elegida. Si se partiese la columna a mitades a pelo,
        el lienzo de las texturas saldria 101 px mas corto que la lista.

        Va atada al `<Configure>` de la columna, asi que se llama con cada cambio
        de tamano. Solo toca dos numeros y solo si han cambiado: si se reconfigura
        en cada llamada, el cambio dispara otro `<Configure>` y la ventana se
        queda repintando sola.
        """
        if not hasattr(self, "reparto") or not hasattr(self, "gallery"):
            return
        alto = self.reparto.winfo_height()
        if alto < 4 * ALTO_MITAD_MINIMA:
            return
        # El cromo son dos cosas: lo que gasta la galeria en su banda con el
        # nombre de la textura elegida, y lo que gasta su marco en la raya de
        # separacion y el rotulo. Las dos se miden con lo que piden, que no
        # depende de como haya quedado el reparto anterior.
        cromo = self.gallery.cromo_vertical() + (
            self.panel_texturas.winfo_reqheight() - self.gallery.winfo_reqheight()
        )
        mitad = int((alto - cromo) / 2)
        if mitad < ALTO_MITAD_MINIMA:
            return
        if self.map_list.winfo_reqheight() != mitad:
            self.map_list.configure(height=mitad)
        if self.gallery.canvas.winfo_reqheight() != mitad:
            self.gallery.fijar_alto(mitad)

    def on_shown(self):
        """Carga el primer mapa la primera vez que se muestra la pestaña."""
        if self.visible and self.current is None:
            self.map_list.selection_set(0)
            self.select_map()

    def refresh(self, _event=None):
        selected_entry = (
            self.current["entry"] if self.current else None
        )
        term = self.filter.get().casefold()
        self.visible = [r for r in self.rows if term in r["ruta_interna"].casefold()]
        self.map_list.delete(0, "end")
        for row in self.visible:
            self.map_list.insert("end", nombre_del_mapa(row, self.app.language))
        if selected_entry:
            index = next(
                (i for i, row in enumerate(self.visible) if row["entry"] == selected_entry),
                None,
            )
            if index is not None:
                self.map_list.marcar(index)

    def set_language(self, _language):
        self.maps_heading.configure(text="Mapas" if _language == "es" else "Maps")
        top = self.winfo_toplevel()
        for button in getattr(self, "floating_buttons", ()):
            label = translate(button.text, _language)
            button.set_text(
                label, top._sims_floating_text_width(label) + 2 * 14
            )
        if hasattr(self, "_vertex_color_title"):
            self.vertex_color_bar.itemconfigure(
                self._vertex_color_title,
                text=translate("Lights", _language),
            )
        if hasattr(self, "_pointers_title"):
            self.pointers_bar.itemconfigure(
                self._pointers_title, text=translate("Pointers", _language)
            )
            self.pointers_spawn_label.configure(
                text=translate("Spawn points", _language)
            )
            self.pointers_lights_label.configure(
                text=translate("Lights", _language)
            )
            self.pointers_cameras_label.configure(
                text=translate("Cameras", _language)
            )
            self._pintar_pointers_panel()
        if hasattr(self, "_light_illumination_label"):
            self._light_illumination_label.configure(
                text=translate("Light illumination", _language)
            )
        if hasattr(self, "_rotulo"):
            self.capa_barra.itemconfigure(
                self._rotulo, text=translate("Layers", _language)
            )
        self.after_idle(self._position_viewport_controls)
        self.refresh()

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
        if any(button.hit(event.x, event.y) for button in self.floating_buttons):
            return True
        return self._over_vertex_color_panel(event.x, event.y) or self._over_pointers_panel(
            event.x, event.y
        )

    def _over_vertex_color_panel(self, x, y):
        if not getattr(self, "vertex_color_window", None):
            return False
        if self.canvas.itemcget(self.vertex_color_window, "state") != "normal":
            return False
        bounds = self.canvas.bbox(self.vertex_color_background)
        return bool(bounds and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])

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
        y = place_right_buttons(
            width,
            (
                self.grid_button,
                self.pointers_button,
                self.vertex_button,
                self.layers_button,
            ),
        )
        self.recenter_button.place(width - 10 - self.recenter_button.reqwidth, y)
        if getattr(self, "vertex_color_window", None) and self.canvas.itemcget(
            self.vertex_color_window, "state"
        ) == "normal":
            self._colocar_vertex_color_panel()
        if getattr(self, "pointers_window", None) and self.canvas.itemcget(
            self.pointers_window, "state"
        ) == "normal":
            self._colocar_pointers_panel()
        self._colocar_panel_capas()

    def toggle_grid(self):
        self.show_grid.set(not self.show_grid.get())
        self.grid_button.set_selected(self.show_grid.get())
        self.schedule()

    def toggle_spawn_points(self):
        self._set_spawn_points(not self.show_spawn_points.get())

    def toggle_camera_points(self):
        self._set_camera_points(not self.show_camera_points.get())

    def _set_camera_points(self, enabled):
        self.show_camera_points.set(bool(enabled))
        self._update_pointers_buttons()
        self.schedule()

    def toggle_light_points(self):
        self._set_light_points(not self.show_light_points.get())

    def _set_spawn_points(self, enabled):
        self.show_spawn_points.set(bool(enabled))
        self._update_pointers_buttons()
        self.schedule()

    def _set_light_points(self, enabled):
        self.show_light_points.set(bool(enabled))
        self._update_pointers_buttons()
        self.schedule()

    def _update_pointers_buttons(self):
        if not hasattr(self, "pointers_spawn_on"):
            return
        spawn = self.show_spawn_points.get()
        lights = self.show_light_points.get()
        cameras = self.show_camera_points.get()
        self.pointers_spawn_on.configure(
            style="Selected.Toggle.TButton" if spawn else "Toggle.TButton"
        )
        self.pointers_spawn_off.configure(
            style="Toggle.TButton" if spawn else "Selected.Toggle.TButton"
        )
        self.pointers_lights_on.configure(
            style="Selected.Toggle.TButton" if lights else "Toggle.TButton"
        )
        self.pointers_lights_off.configure(
            style="Toggle.TButton" if lights else "Selected.Toggle.TButton"
        )
        self.pointers_cameras_on.configure(
            style="Selected.Toggle.TButton" if cameras else "Toggle.TButton"
        )
        self.pointers_cameras_off.configure(
            style="Toggle.TButton" if cameras else "Selected.Toggle.TButton"
        )

    def toggle_pointers_panel(self, force_close=False):
        abierto = self.canvas.itemcget(self.pointers_window, "state") == "normal"
        mostrar = not abierto and not force_close
        estado = "normal" if mostrar else "hidden"
        self.canvas.itemconfigure(self.pointers_window, state=estado)
        self.canvas.itemconfigure(self.pointers_background, state=estado)
        self.pointers_button.set_selected(mostrar)
        if mostrar:
            bounds = self.canvas.bbox(self.pointers_background)
            ancho = bounds[2] - bounds[0] if bounds else ANCHO_TROUGH_PROPS
            self.pointers_pos = [
                max(10, self.canvas.winfo_width() - ancho - 10),
                92,
            ]
            self._colocar_pointers_panel()
            self.canvas.tag_raise(self.pointers_background)
            self.canvas.tag_raise(self.pointers_window)

    def _build_pointers_panel(self):
        """Create the floating controls for spawn-point and light markers."""
        self.pointers_background = self.canvas.create_image(0, 0, anchor="nw")
        self.pointers_panel = ttk.Frame(self.canvas)
        self.pointers_window = self.canvas.create_window(
            0, 0, anchor="nw", window=self.pointers_panel
        )
        self.pointers_pos = [10, 92]
        self._pointers_drag_origin = None
        self.pointers_bar = tk.Canvas(
            self.pointers_panel,
            width=ANCHO_TROUGH_PROPS,
            height=self.ALTO_BARRA,
            highlightthickness=0,
            borderwidth=0,
            background=self.app._sims_theme_colors["background"],
        )
        self.pointers_bar.pack(fill="x", padx=MARGEN_CAPAS, pady=(MARGEN_CAPAS, 0))
        self.pointers_bar.bind("<Configure>", self._pintar_pointers_bar)
        self.pointers_bar.bind("<ButtonPress-1>", self._pointers_panel_press)
        self.pointers_bar.bind("<B1-Motion>", self._pointers_panel_drag)
        self._pointers_title = self.pointers_bar.create_text(
            0,
            self.ALTO_BARRA / 2,
            text="Pointers",
            anchor="center",
            font=(self.app._sims_font_family, 9, "bold"),
            fill=self.app._sims_theme_colors["foreground"],
        )
        self._pointers_close = self.pointers_bar.create_line(
            0, 0, 0, 0, width=2, fill=self.app._sims_theme_colors["foreground"]
        )
        self._pointers_close2 = self.pointers_bar.create_line(
            0, 0, 0, 0, width=2, fill=self.app._sims_theme_colors["foreground"]
        )
        body = ttk.Frame(self.pointers_panel)
        body.pack(fill="x", padx=MARGEN_CAPAS, pady=(6, MARGEN_CAPAS))
        self.pointers_spawn_label = ttk.Label(body, text="Spawn points")
        self.pointers_spawn_label.pack(anchor="w")
        spawn_row = ttk.Frame(body)
        spawn_row.pack(fill="x", pady=(2, 6))
        self.pointers_spawn_on = ttk.Button(
            spawn_row, text="ON", command=lambda: self._set_spawn_points(True)
        )
        self.pointers_spawn_on.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.pointers_spawn_off = ttk.Button(
            spawn_row, text="OFF", command=lambda: self._set_spawn_points(False)
        )
        self.pointers_spawn_off.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.pointers_lights_label = ttk.Label(body, text="Lights")
        self.pointers_lights_label.pack(anchor="w")
        lights_row = ttk.Frame(body)
        lights_row.pack(fill="x", pady=(2, 6))
        self.pointers_lights_on = ttk.Button(
            lights_row, text="ON", command=lambda: self._set_light_points(True)
        )
        self.pointers_lights_on.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.pointers_lights_off = ttk.Button(
            lights_row, text="OFF", command=lambda: self._set_light_points(False)
        )
        self.pointers_lights_off.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.pointers_cameras_label = ttk.Label(body, text="Cameras")
        self.pointers_cameras_label.pack(anchor="w")
        cameras_row = ttk.Frame(body)
        cameras_row.pack(fill="x", pady=(2, 0))
        self.pointers_cameras_on = ttk.Button(
            cameras_row, text="ON", command=lambda: self._set_camera_points(True)
        )
        self.pointers_cameras_on.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.pointers_cameras_off = ttk.Button(
            cameras_row, text="OFF", command=lambda: self._set_camera_points(False)
        )
        self.pointers_cameras_off.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._update_pointers_buttons()
        self.pointers_panel.update_idletasks()
        self._pintar_pointers_panel()
        self.canvas.itemconfigure(self.pointers_background, state="hidden")
        self.canvas.itemconfigure(self.pointers_window, state="hidden")

    def _pintar_pointers_bar(self, _event=None):
        ancho = max(ANCHO_TROUGH_PROPS, self.pointers_bar.winfo_width())
        centro_y = self.ALTO_BARRA / 2
        self.pointers_bar.coords(self._pointers_title, ancho / 2, centro_y)
        x = ancho - self.HOLGURA_BARRA - self.LADO_BOTON / 2
        brazo = 4
        self.pointers_bar.coords(
            self._pointers_close,
            x - brazo, centro_y - brazo, x + brazo, centro_y + brazo,
        )
        self.pointers_bar.coords(
            self._pointers_close2,
            x - brazo, centro_y + brazo, x + brazo, centro_y - brazo,
        )

    def _pintar_pointers_panel(self):
        # Preserve visibility when redrawing after language/theme changes.
        # Otherwise a hidden panel leaves its background image at the origin.
        state = self.canvas.itemcget(self.pointers_window, "state")
        self.pointers_panel.update_idletasks()
        ancho = self.pointers_panel.winfo_reqwidth() + 2 * MARGEN_CAPAS
        alto = self.pointers_panel.winfo_reqheight() + 2 * MARGEN_CAPAS
        self._pointers_photo = ImageTk.PhotoImage(
            panel_redondeado(self._capas_fuente, ancho, alto), master=self.canvas
        )
        self.canvas.itemconfigure(
            self.pointers_background, image=self._pointers_photo, state=state
        )
        self._pintar_pointers_bar()

    def _colocar_pointers_panel(self):
        if not getattr(self, "pointers_window", None):
            return
        bounds = self.canvas.bbox(self.pointers_background)
        ancho = bounds[2] - bounds[0] if bounds else ANCHO_TROUGH_PROPS
        alto = bounds[3] - bounds[1] if bounds else 140
        x, y = self.pointers_pos
        x = max(10, min(x, max(10, self.canvas.winfo_width() - ancho - 10)))
        y = max(10, min(y, max(10, self.canvas.winfo_height() - alto - 10)))
        self.pointers_pos = [x, y]
        self.canvas.coords(self.pointers_background, x, y)
        self.canvas.coords(
            self.pointers_window, x + MARGEN_CAPAS, y + MARGEN_CAPAS
        )

    def _over_pointers_panel(self, x, y):
        if not getattr(self, "pointers_window", None):
            return False
        if self.canvas.itemcget(self.pointers_window, "state") != "normal":
            return False
        bounds = self.canvas.bbox(self.pointers_background)
        return bool(bounds and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])

    def _pointers_panel_press(self, event):
        ancho = self.pointers_bar.winfo_width()
        if event.x >= ancho - self.HOLGURA_BARRA - self.LADO_BOTON:
            self.toggle_pointers_panel(force_close=True)
            return
        self._pointers_drag_origin = (event.x_root, event.y_root)

    def _pointers_panel_drag(self, event):
        origen = self._pointers_drag_origin
        if origen is None:
            return
        self.pointers_pos[0] += event.x_root - origen[0]
        self.pointers_pos[1] += event.y_root - origen[1]
        self._pointers_drag_origin = (event.x_root, event.y_root)
        self._colocar_pointers_panel()

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

        self.layer_filter = tk.StringVar()
        self.layer_indices = []
        self.layer_search = ttk.Entry(
            self.layers_cuerpo,
            textvariable=self.layer_filter,
            width=ANCHO_LISTA_CAPAS // 8,
            style="CharacterSearch.TEntry",
        )
        self.layer_search.pack(fill="x", pady=(6, 6))
        self.layer_filter.trace_add("write", self._filter_layers)

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

    def _build_vertex_color_panel(self):
        """Build the draggable, rounded canvas panel used by Layers."""
        self.vertex_color_background = self.canvas.create_image(0, 0, anchor="nw")
        self.vertex_color_panel = ttk.Frame(self.canvas)
        self.vertex_color_window = self.canvas.create_window(
            0, 0, anchor="nw", window=self.vertex_color_panel
        )
        self.vertex_color_bar = tk.Canvas(
            self.vertex_color_panel,
            width=ANCHO_TROUGH_PROPS,
            height=self.ALTO_BARRA,
            highlightthickness=0,
            borderwidth=0,
            background=self.app._sims_theme_colors["background"],
        )
        self.vertex_color_bar.pack(fill="x", padx=MARGEN_CAPAS, pady=(MARGEN_CAPAS, 0))
        self.vertex_color_bar.bind("<Configure>", self._pintar_vertex_color_bar)
        self.vertex_color_bar.bind("<ButtonPress-1>", self._vertex_color_panel_press)
        self.vertex_color_bar.bind("<B1-Motion>", self._vertex_color_panel_drag)
        self._vertex_color_title = self.vertex_color_bar.create_text(
            0,
            self.ALTO_BARRA / 2,
            text="Lights",
            anchor="center",
            font=(self.app._sims_font_family, 9, "bold"),
            fill=self.app._sims_theme_colors["foreground"],
        )
        self._vertex_color_close = self.vertex_color_bar.create_line(
            0, 0, 0, 0, width=2, fill=self.app._sims_theme_colors["foreground"]
        )
        self._vertex_color_close2 = self.vertex_color_bar.create_line(
            0, 0, 0, 0, width=2, fill=self.app._sims_theme_colors["foreground"]
        )
        self.vertex_color_body = ttk.Frame(self.vertex_color_panel)
        self.vertex_color_body.pack(fill="x", padx=MARGEN_CAPAS, pady=(6, MARGEN_CAPAS))
        self._vertex_color_label = ttk.Label(
            self.vertex_color_body, text="Vertex color"
        )
        self._vertex_color_label.pack(anchor="w")
        vertex_color_states = ttk.Frame(self.vertex_color_body)
        vertex_color_states.pack(fill="x", pady=(2, 0))
        self.vertex_color_on = ttk.Button(
            vertex_color_states,
            text="ON",
            command=lambda: self._set_vertex_colors(True),
        )
        self.vertex_color_on.pack(
            side="left", fill="x", expand=True, padx=(0, 4)
        )
        self.vertex_color_off = ttk.Button(
            vertex_color_states,
            text="OFF",
            command=lambda: self._set_vertex_colors(False),
        )
        self.vertex_color_off.pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )
        self._light_illumination_label = ttk.Label(
            self.vertex_color_body, text="Light illumination"
        )
        self._light_illumination_label.pack(anchor="w")
        light_states = ttk.Frame(self.vertex_color_body)
        light_states.pack(fill="x", pady=(2, 0))
        self.light_illumination_on = ttk.Button(
            light_states,
            text="ON",
            command=lambda: self._set_light_illumination(True),
        )
        self.light_illumination_on.pack(
            side="left", fill="x", expand=True, padx=(0, 4)
        )
        self.light_illumination_off = ttk.Button(
            light_states,
            text="OFF",
            command=lambda: self._set_light_illumination(False),
        )
        self.light_illumination_off.pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )
        self._daynight_label = ttk.Label(self.vertex_color_body, text="Day/night")
        self._daynight_label.pack(anchor="w", pady=(6, 0))
        daynight_states = ttk.Frame(self.vertex_color_body)
        daynight_states.pack(fill="x", pady=(2, 0))
        self.day_button = ttk.Button(
            daynight_states,
            text="Day",
            command=lambda: self._set_daynight_mode("day"),
        )
        self.day_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.night_button = ttk.Button(
            daynight_states,
            text="Night",
            command=lambda: self._set_daynight_mode("night"),
        )
        self.night_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self._update_vertex_color_buttons()
        self._update_daynight_buttons()
        self._update_light_illumination_buttons()
        self._pintar_vertex_color_panel()
        self.canvas.itemconfigure(self.vertex_color_background, state="hidden")
        self.canvas.itemconfigure(self.vertex_color_window, state="hidden")

    def _pintar_vertex_color_bar(self, _event=None):
        ancho = max(ANCHO_TROUGH_PROPS, self.vertex_color_bar.winfo_width())
        self.vertex_color_bar.coords(self._vertex_color_title, ancho / 2, self.ALTO_BARRA / 2)
        x = ancho - self.HOLGURA_BARRA - self.LADO_BOTON / 2
        brazo = 4
        self.vertex_color_bar.coords(
            self._vertex_color_close,
            x - brazo,
            self.ALTO_BARRA / 2 - brazo,
            x + brazo,
            self.ALTO_BARRA / 2 + brazo,
        )
        self.vertex_color_bar.coords(
            self._vertex_color_close2,
            x - brazo,
            self.ALTO_BARRA / 2 + brazo,
            x + brazo,
            self.ALTO_BARRA / 2 - brazo,
        )

    def _pintar_vertex_color_panel(self):
        self.vertex_color_panel.update_idletasks()
        ancho = self.vertex_color_panel.winfo_reqwidth() + 2 * MARGEN_CAPAS
        alto = self.vertex_color_panel.winfo_reqheight() + 2 * MARGEN_CAPAS
        self._vertex_color_photo = ImageTk.PhotoImage(
            panel_redondeado(self._capas_fuente, ancho, alto), master=self.canvas
        )
        self.canvas.itemconfigure(
            self.vertex_color_background,
            image=self._vertex_color_photo,
            state="normal",
        )

    def _colocar_vertex_color_panel(self):
        if not getattr(self, "vertex_color_window", None):
            return
        x, y = self.vertex_color_pos
        bounds = self.canvas.bbox(self.vertex_color_background)
        ancho = bounds[2] - bounds[0] if bounds else ANCHO_TROUGH_PROPS
        alto = bounds[3] - bounds[1] if bounds else 120
        x = max(10, min(x, max(10, self.canvas.winfo_width() - ancho - 10)))
        y = max(10, min(y, max(10, self.canvas.winfo_height() - alto - 10)))
        self.vertex_color_pos = [x, y]
        self.canvas.coords(self.vertex_color_background, x, y)
        self.canvas.coords(
            self.vertex_color_window, x + MARGEN_CAPAS, y + MARGEN_CAPAS
        )

    def _vertex_color_panel_press(self, event):
        ancho = self.vertex_color_bar.winfo_width()
        zona_cerrar = ancho - self.HOLGURA_BARRA - self.LADO_BOTON
        if event.x >= zona_cerrar:
            self.toggle_vertex_colors(force_close=True)
            return
        self._vertex_color_drag_origin = (event.x_root, event.y_root)

    def _vertex_color_panel_drag(self, event):
        origen = getattr(self, "_vertex_color_drag_origin", None)
        if origen is None:
            return
        self.vertex_color_pos[0] += event.x_root - origen[0]
        self.vertex_color_pos[1] += event.y_root - origen[1]
        self._vertex_color_drag_origin = (event.x_root, event.y_root)
        self._colocar_vertex_color_panel()

    def toggle_vertex_colors(self, force_close=False):
        """Show or hide the live controls panel, without toggling the effect."""
        abierto = self.canvas.itemcget(self.vertex_color_window, "state") == "normal"
        mostrar = not abierto and not force_close
        estado = "normal" if mostrar else "hidden"
        self.canvas.itemconfigure(self.vertex_color_background, state=estado)
        self.canvas.itemconfigure(self.vertex_color_window, state=estado)
        if getattr(self, "vertex_button", None):
            self.vertex_button.set_selected(mostrar)
        if mostrar:
            panel_bounds = self.canvas.bbox(self.vertex_color_background)
            _, _, button_x1, button_y1 = self.vertex_button.bbox()
            panel_width = (
                panel_bounds[2] - panel_bounds[0]
                if panel_bounds
                else ANCHO_TROUGH_PROPS
            )
            # Open directly below the Lights button, right-edge aligned.
            self.vertex_color_pos = [
                button_x1 - panel_width,
                button_y1 + 8,
            ]
            self._colocar_vertex_color_panel()
            self.canvas.tag_raise(self.vertex_color_background)
            self.canvas.tag_raise(self.vertex_color_window)

    def _close_vertex_color_window(self):
        if getattr(self, "vertex_color_window", None):
            self.toggle_vertex_colors(force_close=True)

    def _set_vertex_colors(self, enabled):
        self.vertex_colors.set(bool(enabled))
        self._update_vertex_color_buttons()
        self.schedule()

    def _update_vertex_color_buttons(self):
        if not hasattr(self, "vertex_color_on"):
            return
        enabled = self.vertex_colors.get()
        self.vertex_color_on.configure(
            style="Selected.Toggle.TButton" if enabled else "Toggle.TButton"
        )
        self.vertex_color_off.configure(
            style="Toggle.TButton" if enabled else "Selected.Toggle.TButton"
        )

    def _set_light_illumination(self, enabled):
        self.light_illumination.set(bool(enabled))
        self._update_light_illumination_buttons()
        if self.objeto is not None:
            self.objeto.fijar_iluminacion_mapa(self.light_illumination.get())
        self.schedule()

    def _update_light_illumination_buttons(self):
        if not hasattr(self, "light_illumination_on"):
            return
        enabled = self.light_illumination.get()
        self.light_illumination_on.configure(
            style="Selected.Toggle.TButton" if enabled else "Toggle.TButton"
        )
        self.light_illumination_off.configure(
            style="Toggle.TButton" if enabled else "Selected.Toggle.TButton"
        )

    def _set_daynight_mode(self, mode):
        """Switch the map's authored DNL curves and matching sky dome."""
        mode = "night" if str(mode).casefold() == "night" else "day"
        self.daynight_mode.set(mode)
        self._update_daynight_buttons()
        if self.daynight_rig_data is not None and self.daynight_rig_records is not None:
            hour = 0.0 if mode == "night" else 12.0
            self.daylight_profile = daynight_rig_profiles(
                self.daynight_rig_data,
                self.daynight_rig_records,
                hour,
            )
            if self.objeto is not None:
                self.objeto.fijar_luz_direccional_mapa(self.daylight_profile)
        # Mode changes can swap the dome meshes, so rebuild the cached scene.
        self.objeto_key = None
        self.schedule()

    def _update_daynight_buttons(self):
        if not hasattr(self, "day_button"):
            return
        is_day = self.daynight_mode.get() == "day"
        self.day_button.configure(
            style="Selected.Toggle.TButton" if is_day else "Toggle.TButton"
        )
        self.night_button.configure(
            style="Toggle.TButton" if is_day else "Selected.Toggle.TButton"
        )

    def _filter_layers(self, *_args):
        """Search changes list rows only; visibility and mesh IDs stay intact."""
        self.part_list.delete(0, "end")
        self.part_list.refrescar_filas(self._filas_de_capas())

    def _filas_de_capas(self):
        term = self.layer_filter.get().strip().casefold()
        self.layer_indices = [
            i
            for i, part in enumerate(self.parts)
            if term in layer_label(part).casefold()
        ]
        return [
            f"[{' ' if self.parts[i]['name'] in self.ocultas else 'x'}] "
            f"{layer_label(self.parts[i])}"
            for i in self.layer_indices
        ]

    def _alternar_capa(self, index):
        """Enciende o apaga la pieza de la fila que se ha pulsado."""
        if not 0 <= index < len(self.layer_indices):
            return
        nombre = self.parts[self.layer_indices[index]]["name"]
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
            text=translate(
                f"{resumen} · {len(self.parts) - len(self.ocultas)} "
                f"of {len(self.parts)} pieces shown",
                self.app.language,
            )
        )

    def export_as(self):
        """Export the full map with the format picker shared with Characters."""
        if not self.current:
            return
        fmt = choose_export_format(
            self, nombre_del_mapa(self.current, self.app.language)
        )
        if fmt:
            self.export_all_parts(fmt)

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
                "spawn_points": level_spawn_points(self.data, sections(self.data)),
            }
            Path(target).write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            messagebox.showerror(
                translate("Export prop placements", self.app.language), str(exc)
            )

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
            (
                _, data, parts, rejected, light_profile, rig_data, rig_records,
                spawn_points,
            ) = self._cache
            self.daylight_profile = light_profile
            self.daynight_rig_data = rig_data
            self.daynight_rig_records = rig_records
            self._aplicar_mapa(row, data, parts, rejected, spawn_points)
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
        self.status.configure(
            text=translate(f'{row["ruta_interna"]}: loading...', self.app.language)
        )
        path = self.app.paths["maps"]
        archivo = row["archivo_zip"]
        # Keep the daytime directional rig active; night-only DNL profiles
        # should not contribute to map illumination in this preview.
        daynight_hour = 0.0 if self.daynight_mode.get() == "night" else 12.0

        def work():
            try:
                with zipfile.ZipFile(path) as archive:
                    data = archive.read(archivo)
                    light_profile = None
                    rig_data = None
                    rig_records = None
                    references = level_prop_references(data, sections(data))
                    spawn_points = level_spawn_points(data, sections(data))
                    rig = next(
                        (
                            item["resource"]
                            for item in references
                            if item["resource"].startswith("objects/light_rigs/fx_dnl_")
                        ),
                        None,
                    )
                    if rig:
                        try:
                            rig_data = archive.read(f"lighting/{rig}.res")
                        except KeyError:
                            # Older extracted map archives predate bundled
                            # lighting rigs; importing the game data again adds
                            # the referenced source assets.
                            pass
                        else:
                            rig_records = sections(rig_data)
                            light_profile = daynight_rig_profiles(
                                rig_data, rig_records, daynight_hour
                            )
                parts, rejected = parse_rdms(
                    data,
                    apply_scene=True,
                    vertex_colors=True,
                    # La ruta hace falta para las excepciones que van por nivel,
                    # como las piezas a las que hay que dar la vuelta a las caras.
                    ruta=row["ruta_interna"],
                )
                cola.put(
                    (
                        generacion, row, data, parts, rejected,
                        light_profile, rig_data, rig_records, spawn_points, None,
                    )
                )
            except Exception as exc:
                # Aqui no se toca la interfaz ni se avisa de nada: solo se deja
                # el resultado y que lo recoja el hilo de la interfaz.
                cola.put(
                    (generacion, row, None, None, None, None, None, None, None, exc)
                )

        threading.Thread(target=work, daemon=True).start()
        self._carga_poll = self.after(50, self._poll_carga)

    def _poll_carga(self):
        """Recoge el mapa en cuanto el hilo lo deja."""
        self._carga_poll = None
        if self.disposed or self._carga_cola is None:
            return
        try:
            (
                generacion, row, data, parts, rejected, light_profile,
                rig_data, rig_records, spawn_points, error,
            ) = (
                self._carga_cola.get_nowait()
            )
        except queue.Empty:
            self._carga_poll = self.after(50, self._poll_carga)
            return
        self._carga_cola = None
        self.map_list.configure(cursor="")
        if error is not None:
            self._carga_entrada = None
            messagebox.showerror(translate("Map", self.app.language), str(error))
            return
        if generacion != self._carga_generacion:
            return  # mientras cargaba se eligio otro mapa: este ya no vale
        self._cache = (
            row["entry"], data, parts, rejected, light_profile,
            rig_data, rig_records, spawn_points,
        )
        self.daylight_profile = light_profile
        self.daynight_rig_data = rig_data
        self.daynight_rig_records = rig_records
        self._aplicar_mapa(row, data, parts, rejected, spawn_points)

    def _aplicar_mapa(self, row, data, parts, rejected, spawn_points):
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
        self.skybox_variants = variantes_cielo(parts)
        self.spawn_points = spawn_points
        self.camera_light_points = level_camera_light_points(
            data,
            sections(data),
            self.daynight_rig_data,
            self.daynight_rig_records,
        )
        self.camera_points = [
            point for point in self.camera_light_points if point["kind"] == "camera"
        ]
        self.light_points = [
            point for point in self.camera_light_points if point["kind"] == "light"
        ]
        self.vertex_colors.set(True)
        self._update_vertex_color_buttons()
        self.current = row
        # La camara vuelve al origen y al encuadre de partida: el foco es la caja
        # de lo que se dibuja, asi que sin resetarla cada nivel se abriria
        # descentrado respecto al anterior y con la inclinacion que se hubiera
        # dejado antes.
        self.camera.reset()
        # Las piezas apagadas son de este mapa: al cambiar de nivel se olvida.
        self.ocultas = set()
        self.layer_filter.set("")
        self._filter_layers()
        # La galeria de la izquierda enseña lo que lleva el nivel: cada pieza
        # guarda su textura como (nombre, pixeles), y sin pixeles no hay miniatura.
        self.gallery.set_textures(
            {
                texture[0]: texture[1]
                for part in self.parts
                for texture in (
                    [part.get("texture")]
                    + [layer.get("texture") for layer in part.get("texture_layers", [])]
                )
                if texture and texture[1]
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
        return (
            self.parts[self.layer_indices[choice[0]]]
            if choice and choice[0] < len(self.layer_indices)
            else None
        )

    def select_part(self, _event=None):
        part = self.selected_part()
        if part:
            self.status.configure(
                text=f'{self.current["ruta_interna"]} · {part_label(part)} · '
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
        mode = self.daynight_mode.get()
        partes = [
            p
            for p in self.parts
            if p["name"] not in self.ocultas
            and (
                not es_cupula(p)
                or self.skybox_variants.get(p["name"]) in (None, mode)
            )
        ]
        if self.show_sky.get():
            return partes
        return [p for p in partes if not es_cupula(p)]

    def piezas_de_la_escena(self):
        """Las piezas que se suben a la GPU: el nivel entero, con el cielo si esta.

        Van todas, tambien las apagadas. Apagar una no quita su malla de la
        escena, solo deja de pintarse: asi la caja del encuadre no cambia y la
        camara no se reajusta cada vez que se enciende o apaga una pieza.
        """
        mode = self.daynight_mode.get()
        return [
            p
            for p in self.parts
            if not es_cupula(p)
            or (
                self.show_sky.get()
                and self.skybox_variants.get(p["name"]) in (None, mode)
            )
        ]

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
        mode = self.daynight_mode.get()
        return tuple(
            p["name"]
            for p in self.parts
            if es_cupula(p)
            and self.skybox_variants.get(p["name"]) in (None, mode)
        )

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
                image = render_prop(
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
                self.canvas.set_image(image)
                return
            culling = self.culling.get()
            # La escena se monta una vez por nivel y no se toca al apagar nada:
            # el unico motivo para rehacerla es cambiar de nivel o de ajustes que
            # cambian lo que se dibuja de verdad.
            key = (
                self.current["entry"],
                self.show_textures.get(),
                self.show_sky.get(),
                self.daynight_mode.get(),
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
                    light_profile=self.daylight_profile,
                    ruta_escena=self.current["ruta_interna"],
                )
                self.objeto.fijar_luces_locales_mapa(self.light_points)
                self.objeto.fijar_iluminacion_mapa(
                    self.light_illumination.get()
                )
                self.objeto_key = key
            self.objeto.fijar_vertex_colors(
                self.vertex_colors.get(), self.vertex_color_intensity.get()
            )
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
            if self.show_spawn_points.get():
                self._draw_map_markers(image, width, height, self.spawn_points)
            if self.show_camera_points.get():
                self._draw_map_markers(image, width, height, self.camera_points)
            if self.show_light_points.get():
                self._draw_map_markers(image, width, height, self.light_points)
            self.canvas.set_image(image)
        except Exception as exc:
            self.status.configure(
                text=translate(f"Preview: {exc}", self.app.language)
            )

    def _draw_map_markers(self, image, width, height, points):
        """Overlay authored scene transforms with their local XYZ axes."""
        if self.objeto is None or not points:
            return
        draw = ImageDraw.Draw(image)
        axis_colors = ((245, 88, 88), (108, 226, 129), (91, 169, 255))
        marker_colors = {
            "camera": (94, 210, 255),
            "spawn": (255, 222, 72),
            "hidden": (255, 125, 214),
        }
        for point in points:
            kind = point.get("kind", "spawn")
            position = np.asarray(point["position"], dtype=np.float64)
            x, y = self.objeto.proyectar(
                position,
                width,
                height,
                self.camera.yaw,
                self.camera.pitch,
                self.camera.zoom,
                self.camera.pan,
                self.camera,
            )
            if not (-12 <= x < width + 12 and -12 <= y < height + 12):
                continue
            for axis, color in enumerate(axis_colors):
                direction = np.asarray(point["matrix"], dtype=np.float64)[:3, axis]
                length = np.linalg.norm(direction)
                if length < 1e-8:
                    continue
                endpoint = position + direction / length * 0.75
                end_x, end_y = self.objeto.proyectar(
                    endpoint,
                    width,
                    height,
                    self.camera.yaw,
                    self.camera.pitch,
                    self.camera.zoom,
                    self.camera.pan,
                    self.camera,
                )
                draw.line((x, y, end_x, end_y), fill=color, width=2)
            radius = 4
            marker_color = marker_colors.get(kind, marker_colors["spawn"])
            label = point["name"]
            if kind == "light":
                profile = (self.daylight_profile or {}).get(point["name"])
                light_color = profile.get("color") if profile else point.get("color")
                intensity = (
                    profile.get("intensity")
                    if profile
                    else point.get("intensity", 0.0)
                )
                if light_color:
                    marker_color = tuple(
                        int(np.clip(channel, 0.0, 1.0) * 255)
                        for channel in light_color[:3]
                    )
                if str(point.get("light_type", "")).casefold() == "directional":
                    direction = np.asarray(
                        point.get("direction", ()), dtype=np.float64
                    )
                    direction_length = float(np.linalg.norm(direction))
                    if direction.shape == (3,) and direction_length > 1e-8:
                        # The stored directional axis points toward the source;
                        # show the parallel light-ray direction toward surfaces.
                        ray_direction = -direction / direction_length
                        ray_length = max(
                            2.0,
                            min(float(self.objeto.radio) * 0.15, 18.0),
                        )
                        reference = np.array((0.0, 0.0, 1.0))
                        if abs(float(np.dot(ray_direction, reference))) > 0.95:
                            reference = np.array((1.0, 0.0, 0.0))
                        side = np.cross(ray_direction, reference)
                        side /= np.linalg.norm(side)
                        vertical = np.cross(ray_direction, side)
                        cone_radius = min(ray_length * 0.14, 2.5)
                        base = position + ray_direction * ray_length
                        cone_ring = []
                        for step in range(17):
                            angle = 2.0 * math.pi * step / 16
                            ring_point = base + cone_radius * (
                                math.cos(angle) * side + math.sin(angle) * vertical
                            )
                            cone_ring.append(
                                self.objeto.proyectar(
                                    ring_point,
                                    width,
                                    height,
                                    self.camera.yaw,
                                    self.camera.pitch,
                                    self.camera.zoom,
                                    self.camera.pan,
                                    self.camera,
                                )
                            )
                        draw.line(cone_ring, fill=marker_color, width=2)
                        # A tapered wireframe cone makes the ray direction clearer
                        # without obscuring the map surface beneath it.
                        draw.line((x, y, *cone_ring[0]), fill=marker_color, width=2)
                        draw.line((x, y, *cone_ring[8]), fill=marker_color, width=2)
                if str(point.get("light_type", "")).casefold() == "point":
                    reach = gl.alcance_luz_local_mapa(point)
                    if reach > 0:
                        reach_color = tuple(
                            int(channel * 0.55 + 255 * 0.45)
                            for channel in marker_color
                        )
                        for first_axis, second_axis in ((0, 1), (0, 2), (1, 2)):
                            ring = []
                            for step in range(33):
                                angle = 2.0 * math.pi * step / 32
                                sample = position.copy()
                                sample[first_axis] += math.cos(angle) * reach
                                sample[second_axis] += math.sin(angle) * reach
                                ring.append(
                                    self.objeto.proyectar(
                                        sample,
                                        width,
                                        height,
                                        self.camera.yaw,
                                        self.camera.pitch,
                                        self.camera.zoom,
                                        self.camera.pan,
                                        self.camera,
                                    )
                                )
                            draw.line(ring, fill=reach_color, width=1)
                radius = min(9, 4 + int(np.log10(1.0 + max(0.0, intensity))))
                type_label = point.get("light_type", "light").title()
                if point.get("light_type_inferred"):
                    type_label += " (inferred)"
                label = (
                    f"{type_label}: {point['name']} "
                    f"I={intensity:g}"
                )
            elif kind == "camera":
                label = f"Camera: {label}"
            elif kind == "hidden":
                label = f"{translate('Hidden', self.app.language)}: {label}"
            elif point.get("character_slug"):
                label = nombre_publico(
                    point["character_slug"], self.app.language
                )
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=marker_color,
                outline=(30, 25, 12),
                width=1,
            )
            draw.text(
                (x + 7, y - 8),
                label,
                fill=(255, 245, 192),
                stroke_width=2,
                stroke_fill=(20, 20, 20),
            )

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
        name = f'{Path(self.current["ruta_interna"]).name}_{part_filename(part)}.{fmt}'
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
            write_rigid_glb(without_vertex_colors([part]), name, glb)
            if blender:
                self.fbx_export(glb, target, blender)
            self.status.configure(
                text=translate(f"Part saved: {target}", self.app.language)
            )
        except Exception as exc:
            messagebox.showerror(translate("Export part", self.app.language), str(exc))

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
            self.status.configure(
                text=translate(f"Original map saved: {target}", self.app.language)
            )

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
                textures = {
                    texture[0]: texture[1]
                    for _, texture, _ in surface_textures(
                        self.data, sections(self.data)
                    )
                    if texture
                }
                for key, path in texture_paths(textures).items():
                    output.writestr(path, textures[key])
                    count += 1
            self.status.configure(
                text=translate(f"{count} PNG textures saved: {target}", self.app.language)
            )
        except Exception as exc:
            messagebox.showerror(
                translate("Extract textures", self.app.language), str(exc)
            )

    def export_all_parts(self, fmt="glb"):
        if not self.current or not self.parts:
            return
        try:
            fbx = fmt == "fbx"
            blender = self.app.resolve_blender() if fbx else None
            if fbx and not blender:
                return
            name = export_model_name(self.current["ruta_interna"].replace("/", "_"))
            export_dir = self.app.export_root / f"{name}_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            target = export_dir / f"{name}.{fmt}"
            glb = target if fmt == "glb" else self.app.cache / "map_export.glb"
            write_rigid_glb(
                without_vertex_colors(self.parts), self.current["ruta_interna"], glb
            )
            if fbx:
                self.fbx_export(glb, target, blender)
            elif fmt == "gltf":
                export_gltf(glb, target)
            self.status.configure(
                text=translate(
                    f"{len(self.parts)} map parts saved: {target}", self.app.language
                )
            )
        except Exception as exc:
            messagebox.showerror(
                translate("Export map parts", self.app.language), str(exc)
            )

    def _export_parts(self, parts):
        if not self.vertex_colors.get():
            return without_vertex_colors(parts)
        intensidad = max(0.0, min(200.0, self.vertex_color_intensity.get())) / 100.0
        has_blended_parts = any(part.get("texture_blend") for part in parts)

        def malla_con_vertex_color_oscuro(part):
            return gl.malla_necesita_brillo_vertex_color(part)

        def malla_tiene_vertex_color(part):
            return not part.get("texture_blend") and any(
                vertex[3] is not None for vertex in part["vertices"]
            )

        if (
            abs(intensidad - 1.0) < 1e-6
            and not has_blended_parts
            and not any(malla_con_vertex_color_oscuro(part) for part in parts)
        ):
            return parts
        resultado = []
        for part in parts:
            afecta_malla_entera = malla_tiene_vertex_color(part)
            admite_lift = gl.malla_admite_lift_vertex_color(part)
            color_material = tuple(
                float(channel) / 255.0
                for channel in (part.get("color") or (255, 255, 255, 255))
            )
            if len(color_material) == 3:
                color_material += (1.0,)
            normales = np.asarray(
                [
                    vertex[1] if vertex[1] is not None else (0.0, 0.0, 0.0)
                    for vertex in part["vertices"]
                ],
                dtype=np.float32,
            )
            factores_luz = gl.factores_luz_direccional_mapa(
                normales, self.daylight_profile
            )
            vertices = []
            for index, (position, normal, uv, color) in enumerate(part["vertices"]):
                if color is None and not afecta_malla_entera:
                    vertices.append((position, normal, uv, color))
                    continue
                tiene_color_original = color is not None
                color = color_material if color is None else tuple(color)
                if len(color) == 3:
                    color += (1.0,)
                if part.get("texture_blend"):
                    vertices.append((position, normal, uv, (1.0, 1.0, 1.0, 1.0)))
                    continue
                rgb = np.asarray(color[:3], dtype=np.float32) * factores_luz[index]
                rgb = gl.levantar_vertex_colors_oscuros(
                    rgb[None, :],
                    np.array([tiene_color_original and admite_lift], dtype=bool),
                )[0]
                rgb = np.clip(rgb * intensidad, 0.0, 1.0)
                vertices.append(
                    (position, normal, uv, tuple(float(c) for c in rgb) + (color[3],))
                )
            resultado.append({**part, "vertices": vertices})
        return resultado

    def dispose(self):
        self.disposed = True
        self._close_vertex_color_window()
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        # El poll que recoge el hilo tambien se cancela: si no sigue vivo, Tk
        # avisa con un "application has been destroyed" al cerrar el visor.
        if self._carga_poll:
            self.after_cancel(self._carga_poll)
            self._carga_poll = None
        if self._daynight_update:
            self.after_cancel(self._daynight_update)
            self._daynight_update = None
        self._carga_cola = None
        self._cache = None
        self.objeto = None
        self.objeto_key = None
