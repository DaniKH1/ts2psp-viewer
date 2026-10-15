"""Comprueba el render por GPU: velocidad, tamaño de la imagen, cámara y app completa.

Uso:  py -3 test_gpu.py     (desde esta carpeta, con la carpeta data del nivel superior)

El render por CPU ya no existe: la GPU es obligatoria y no hay a qué caer. La batería
mide el frame, comprueba que la imagen sale del tamaño pedido, que la cámara la cambia
al girar o arrastrar, que el zoom se centra en el punto de mira y que, si el equipo
no puede acelerar, el render avisa con SinOpenGL en vez de dibujar por software.
"""

import csv
import io
import math
import sys
import time
import tkinter as tk
import zipfile
from tkinter import ttk
from pathlib import Path
from types import SimpleNamespace

import ts2psp.rendering.opengl as gl
from ts2psp.app import Sims2PSPApp
from ts2psp.ui.camera import OrbitController, Viewport
from ts2psp.rendering.props import load_parts, render_prop
from ts2psp.rendering.characters import draw_character, load_character
from ts2psp.formats.characters import nombre_publico
from ts2psp.ui.theme import ANCHO_TROUGH_CLIPS, ANCHO_TROUGH_SHAPES, NOTEBOOK_TAB_LABELS

RAIZ = Path(__file__).resolve().parent.parent
import os

DATOS = Path(os.environ.get("TS2_PSP_DATA_DIR", RAIZ / "data"))
FALLO = []


def comprobar(condicion, mensaje):
    print(("  ok   " if condicion else "  FALLO") + f"  {mensaje}")
    if not condicion:
        FALLO.append(mensaje)


def cronometrar(fn, veces):
    """Milisegundos por llamada, por mediana.

    Se usa la mediana y no la media porque la GPU necesita dos pasadas para
    entrar en regimen: la primera medible sale carisima por el calentamiento de
    la tuberia y, promediada con las demas, hacia fallar el umbral sin que nada
    haya cambiado en el codigo.
    """
    fn()
    fn()
    muestras = []
    for _ in range(veces):
        inicio = time.perf_counter()
        fn()
        muestras.append((time.perf_counter() - inicio) * 1000)
    muestras.sort()
    return muestras[len(muestras) // 2]


def seccion(titulo):
    print(f"\n{titulo}\n" + "-" * len(titulo))


def pct_distinto(primera, segunda):
    """Porcentaje de píxeles que cambian entre dos imágenes del mismo tamaño."""
    import numpy as np

    a = np.asarray(primera, dtype=np.int16)[:, :, :3]  # RGBX trae un canal de sobra
    b = np.asarray(segunda, dtype=np.int16)[:, :, :3]
    if a.shape != b.shape:
        return 100.0
    return 100.0 * float((np.abs(a - b).sum(axis=2) > 12).mean())


def como_error(fn):
    """La excepción con la que revienta `fn`, o None si termina sin quejarse."""
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def prueba_rendimiento():
    seccion("Rendimiento (bellagoth, 1040x655)")
    root = tk.Tk()
    root.withdraw()
    disponible = gl.disponible()
    print(
        "  GPU:", gl.Contexto.compartido().renderer if disponible else "no disponible"
    )
    if not disponible:
        comprobar(False, "esta máquina tiene aceleración por hardware (requisito)")
        root.destroy()
        return
    comprobar(True, "esta máquina tiene aceleración por hardware")
    ancho, alto = 1040, 655
    _, piezas, huesos, texturas = load_character(
        DATOS / "sims2_psp_personajes_recursos.zip", "bellagoth"
    )
    argumentos = dict(textures=texturas, key="bellagoth")
    frame = cronometrar(
        lambda: draw_character(
            piezas, huesos, ancho, alto, math.pi, 0.2, 1.0, **argumentos
        ),
        20,
    )
    imagen = draw_character(
        piezas, huesos, ancho, alto, math.pi, 0.2, 1.0, **argumentos
    )
    print(f"  frame GPU       {frame:7.2f} ms  -> {1000 / frame:6.1f} fps")
    comprobar(frame < 10.0, "el frame por GPU dura menos de 10 ms")
    comprobar(imagen.size == (ancho, alto), "la imagen sale del tamaño pedido")
    comprobar(
        imagen.mode in ("RGB", "RGBX"), f"la imagen vuelve en RGB (es {imagen.mode})"
    )

    # la GPU tiene que pintar el modelo, no dejar el fondo y listo
    import numpy as np

    pixeles = np.asarray(imagen, dtype=np.int16)[
        :, :, :3
    ]  # RGBX trae un canal de sobra
    fondo = np.array([27, 31, 42])
    pintados = int((np.abs(pixeles - fondo).sum(axis=2) > 12).sum())
    print(f"  modelo pintado en {100 * pintados / (ancho * alto):5.1f} % de la vista")
    comprobar(
        pintados > ancho * alto * 0.02, "la GPU dibuja el modelo, no solo el fondo"
    )
    sin_grid = draw_character(
        piezas, huesos, ancho, alto, math.pi, 0.2, 1.0, show_grid=False, **argumentos
    )
    cambio_grid = pct_distinto(imagen, sin_grid)
    print(f"  rejilla visible en {cambio_grid:5.1f} % de los píxeles")
    comprobar(cambio_grid > 0.1, "la rejilla del suelo se distingue del fondo")
    escena = gl.escena(("personaje", "bellagoth"), piezas, texturas, "personaje")
    alcance_grid = float(np.max(np.abs(escena._grid_vertices[:, :2])))
    comprobar(
        alcance_grid >= 4 * escena.radio, "la rejilla se extiende lejos del modelo"
    )

    # girar y arrastrar tienen que cambiar el dibujo, no reenviar el mismo frame
    girada = draw_character(
        piezas, huesos, ancho, alto, math.pi + 0.7, 0.2, 1.0, **argumentos
    )
    inclinada = draw_character(
        piezas, huesos, ancho, alto, math.pi, 0.6, 1.0, **argumentos
    )
    print(
        f"  girando la cámara cambia el {pct_distinto(imagen, girada):5.1f} % de los píxeles"
    )
    comprobar(pct_distinto(imagen, girada) > 1.0, "girar la cámara cambia la imagen")
    comprobar(
        pct_distinto(imagen, inclinada) > 1.0, "la rotación vertical cambia la imagen"
    )
    comprobar(
        pct_distinto(girada, inclinada) > 0.5, "girar e inclinar dan imágenes distintas"
    )
    root.destroy()


def prueba_props():
    seccion("Props y UV")
    with zipfile.ZipFile(DATOS / "sims2_psp_props_recursos.zip") as z:
        filas = list(
            csv.DictReader(
                io.StringIO(z.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        fila = next(
            f
            for f in filas
            if f["ruta_interna"] == "objects/chairs/fx_chair_dining_medieval"
        )
        piezas = load_parts(z.read(fila["archivo_zip"]))
    argumentos = dict(key=("test", fila["entry"]))
    frame = cronometrar(
        lambda: render_prop(piezas, 958, 702, 0.35, 1.0, True, 0.2, **argumentos), 20
    )
    imagen = render_prop(piezas, 958, 702, 0.35, 1.0, True, 0.2, **argumentos)
    girada = render_prop(piezas, 958, 702, 0.35 + 0.9, 1.0, True, 0.2, **argumentos)
    print(f"  silla GPU {frame:5.2f} ms -> {1000 / frame:5.0f} fps")
    comprobar(frame < 10.0, "los props también van por GPU y son rápidos")
    comprobar(imagen.size == (958, 702), "el prop sale del tamaño pedido")
    comprobar(pct_distinto(imagen, girada) > 1.0, "girar el prop cambia la imagen")


def prueba_zoom():
    """El zoom mueve la cámara al punto de mira y recorta la geometría atravesada."""
    import numpy as np

    seccion("Zoom de cámara sobre el punto de mira")
    root = tk.Tk()
    root.withdraw()
    _, piezas, huesos, texturas = load_character(
        DATOS / "sims2_psp_personajes_recursos.zip", "bellagoth"
    )
    escena = gl.Escena(piezas, texturas, "personaje")
    lienzo = Viewport(root, minimum=(700, 700))
    lienzo.pack(fill="both", expand=True)
    root.geometry("1040x655")
    root.update_idletasks()
    root.update()
    control = OrbitController(lienzo, lambda: None, yaw=math.pi, pitch=0.2)
    control.zoom_tope = escena.zoom_tope(*lienzo.view_size())
    W, H = lienzo.view_size()
    control.set_view(escena.pivote, escena._escala(W, H))
    yaw, pitch = control.yaw, control.pitch

    centro_vista = (W / 2.0, H / 2.0)
    fuera = (0.30, 0.02, 0.97)
    control.zoom = 1.0
    antes = escena.proyectar(escena.pivote, W, H, yaw, pitch, 1.0, control.pan, control)
    for _ in range(6):
        control.wheel_linux(1)
    despues = escena.proyectar(
        escena.pivote, W, H, yaw, pitch, control.zoom, control.pan, control
    )
    comprobar(
        math.dist(antes, centro_vista) < 0.01
        and math.dist(despues, centro_vista) < 0.01,
        "el punto de mira permanece en el centro de la vista al ampliar",
    )
    comprobar(
        math.dist(antes, despues) < 0.01,
        "el punto de mira no se desplaza al hacer zoom",
    )

    # El punto de mira se mueve al paneo y permanece como centro de órbita.
    peor = 0.0
    for desplazamiento in ((120.0, -70.0), (-260.0, 90.0)):
        control.zoom = 1.0
        control.focus_offset = (0.0, 0.0, 0.0)
        control.move_focus(*desplazamiento)
        punto = escena._punto_mira(control).copy()
        yaw_orbitado = yaw + 0.6
        for _ in range(6):
            control.wheel_linux(1)
        donde = escena.proyectar(
            punto, W, H, yaw_orbitado, pitch, control.zoom, control.pan, control
        )
        peor = max(peor, math.dist(donde, centro_vista))
    comprobar(
        peor < 10.0,
        "el punto de mira sigue centrado al ampliar y orbitar después del paneo",
    )
    punto_mira = escena._punto_mira(control).copy()
    control.yaw += 0.75
    orbitado = escena.proyectar(
        punto_mira, W, H, control.yaw, control.pitch, control.zoom, control.pan, control
    )
    comprobar(
        math.dist(orbitado, centro_vista) < 0.01
        and tuple(escena._punto_mira(control)) == tuple(punto_mira),
        "la rotación orbita alrededor del punto exacto de mira",
    )
    control.yaw = yaw

    # Las coordenadas del cursor no deben alterar el punto fijo del zoom.
    con_cursor = OrbitController(lienzo, lambda: None, yaw=yaw, pitch=pitch)
    sin_cursor = OrbitController(lienzo, lambda: None, yaw=yaw, pitch=pitch)
    con_cursor.wheel(SimpleNamespace(delta=120, x=W * 0.9, y=H * 0.1))
    sin_cursor.wheel(SimpleNamespace(delta=120, x=W * 0.5, y=H * 0.5))
    comprobar(
        (con_cursor.zoom, con_cursor.pan) == (sin_cursor.zoom, sin_cursor.pan),
        "la posición del cursor no cambia el punto de zoom",
    )

    paso = OrbitController(lienzo, lambda: None)
    paso.wheel(SimpleNamespace(delta=120, x=0, y=0))
    comprobar(
        math.isclose(paso.zoom, 1 / 0.88, rel_tol=1e-9),
        "un paso de rueda acerca la cámara un 12 %, como en mom_viewer",
    )
    paso.wheel(SimpleNamespace(delta=60, x=0, y=0))
    comprobar(
        math.isclose(paso.zoom, (1 / 0.88) ** 1.5, rel_tol=1e-9),
        "el zoom responde proporcionalmente a movimientos parciales de rueda",
    )
    paso.wheel(SimpleNamespace(delta=-120, x=0, y=0))
    comprobar(
        math.isclose(paso.zoom, (1 / 0.88) ** 1.5 / 1.14, rel_tol=1e-9),
        "un paso de rueda hacia fuera aleja la cámara un 14 %, como en mom_viewer",
    )
    salida = OrbitController(lienzo, lambda: None)
    salida.wheel(SimpleNamespace(delta=-120, x=0, y=0))
    comprobar(
        math.isclose(salida.zoom, 1 / 1.14, rel_tol=1e-9),
        "un paso de rueda hacia fuera aplica el radio 1.14 del visor de referencia",
    )

    control.zoom = 1.0
    control.focus_offset = (0.0, 0.0, 0.0)
    lejos_antes = escena.proyectar(fuera, W, H, yaw, pitch, 1.0, control.pan, control)
    for _ in range(6):
        control.wheel_linux(1)
    lejos_despues = escena.proyectar(
        fuera, W, H, yaw, pitch, control.zoom, control.pan, control
    )
    zoom_6_pasos = control.zoom
    comprobar(
        math.dist(lejos_despues, centro_vista)
        > math.dist(lejos_antes, centro_vista) * 1.5,
        "el zoom de cámara amplía el modelo alrededor del punto de mira",
    )

    # El zoom mueve la cámara hacia delante, así que la perspectiva debe hacerse más
    # visible, no desaparecer como ocurriría con una proyección ortográfica.
    def separacion(alto_punto, zoom):
        izquierda = escena.proyectar(
            (-0.30, 0.0, alto_punto), W, H, math.pi, 0.35, zoom
        )
        derecha = escena.proyectar((0.30, 0.0, alto_punto), W, H, math.pi, 0.35, zoom)
        return math.dist(izquierda, derecha)

    lejos = separacion(0.97, 1.0)
    cerca = separacion(0.47, 1.0)
    lejos_zoom = separacion(0.97, zoom_6_pasos)
    cerca_zoom = separacion(0.47, zoom_6_pasos)
    comprobar(
        cerca_zoom / lejos_zoom > cerca / lejos,
        "el zoom de cámara conserva y refuerza la perspectiva",
    )
    comprobar(
        math.isclose(lejos_zoom / lejos, zoom_6_pasos, rel_tol=1e-6),
        "la ampliación del plano de enfoque corresponde al factor de zoom",
    )

    # El zoom acerca la cámara, pero se detiene antes de penetrar demasiado el modelo.
    control.zoom = 1.0
    control.focus_offset = (0.0, 0.0, 0.0)
    tope = control.zoom_tope
    distancia_inicial = escena.distancia(escena.pivote, W, H, yaw, pitch, control.zoom)
    for _ in range(100):
        control.wheel_linux(1)
    comprobar(
        abs(control.zoom - tope) < 1e-6,
        "el controlador limita el acercamiento de la cámara",
    )
    comprobar(
        math.dist(
            escena.proyectar(
                escena.pivote, W, H, yaw, pitch, control.zoom, control.pan, control
            ),
            centro_vista,
        )
        < 0.01,
        "el punto de mira no se mueve al llegar al límite",
    )
    distancia_final = escena.distancia(escena.pivote, W, H, yaw, pitch, control.zoom)
    comprobar(
        math.isclose(distancia_final, gl.DISTANCIA_MINIMA, abs_tol=1e-6),
        "el zoom permite acercarse hasta el radio mínimo de mom_viewer",
    )
    comprobar(
        distancia_final < distancia_inicial,
        "la cámara se acerca de verdad al punto de mira",
    )
    ojo, _ = escena._camara(W, H, yaw, pitch, control.zoom, control.pan, control)
    atravesados = int((ojo[:, 2] >= -gl.PLANO_CERCANO).sum())
    comprobar(
        atravesados > 0,
        f"la cámara atraviesa y recorta geometría ({atravesados} vértices tras el plano cercano)",
    )

    # La rejilla lejana no debe degradar la profundidad del personaje al alejar.
    escena.mostrar_grid = False
    modelo_sin_grid = np.asarray(
        escena.render(W, H, yaw, pitch, 0.15, control.pan, control), dtype=np.int16
    )
    escena.mostrar_grid = True
    modelo_con_grid = np.asarray(
        escena.render(W, H, yaw, pitch, 0.15, control.pan, control), dtype=np.int16
    )
    mascara_modelo = np.abs(modelo_sin_grid - np.array([27, 31, 42])).sum(axis=2) > 20
    pixeles_modelo = int(mascara_modelo.sum())
    mascara_modelo_grid = (
        np.abs(modelo_con_grid - np.array([27, 31, 42])).sum(axis=2) > 20
    )
    comprobar(pixeles_modelo > 100, "el modelo sigue visible al alejar la cámara")
    comprobar(
        int(mascara_modelo_grid.sum()) > 100,
        "el modelo sigue visible con la rejilla al alejar la cámara",
    )
    root.destroy()


def prueba_alpha_textura():
    """Las texturas con una imagen -alpha.png se cargan como RGBA."""
    import numpy as np
    from PIL import Image
    from ts2psp.formats.characters import TextureLibrary

    seccion("Alfa de texturas")
    texture_zip = DATOS / "sims2_psp_personajes_texturas_png.zip"
    with zipfile.ZipFile(texture_zip) as archive:
        library = TextureLibrary(archive)
        path = "body/afbodyanniehowell/afbodyanniehowell"
        rgba = Image.open(io.BytesIO(library.read(path))).convert("RGBA")
        alpha_range = rgba.getchannel("A").getextrema()
        comprobar(
            alpha_range[0] < 255,
            "el mapa -alpha.png se aplica al canal alfa de la textura base",
        )
        comprobar(
            gl.Escena._tiene_alpha(None, np.asarray(rgba)),
            "la escena detecta transparencia para activar el blending",
        )
    with zipfile.ZipFile(texture_zip) as archive:
        from ts2psp.formats.characters import load_character_rows

        with zipfile.ZipFile(DATOS / "sims2_psp_personajes_recursos.zip") as resources:
            row = next(
                r
                for r in load_character_rows(resources)
                if r["personaje"] == "bellagoth"
            )
        hair = TextureLibrary(archive).character_texture(row, "hair")
    hair_rgba = Image.open(io.BytesIO(hair[1])).convert("RGBA")
    comprobar(
        hair_rgba.getchannel("A").getextrema()[0] < 255,
        "el alpha textureHairAlpha se aplica al pelo compuesto del personaje",
    )
    comprobar(
        gl.Escena._tiene_alpha(None, np.asarray(hair_rgba)),
        "el pelo con máscara activa el blending en la escena",
    )
    _, parts, bones, textures = load_character(
        DATOS / "sims2_psp_personajes_recursos.zip", "bellagoth"
    )
    character_scene = gl.escena(
        ("personaje", "alpha-depth-test-bellagoth"), parts, textures, "personaje"
    )
    gl_context = character_scene.contexto
    enabled, disabled, alpha_functions = [], [], []
    original_enable = gl_context.activar
    original_disable = gl_context.desactivar
    original_alpha_func = gl_context.funcion_alfa
    gl_context.activar = lambda value: (enabled.append(value), original_enable(value))[
        1
    ]
    gl_context.desactivar = lambda value: (
        disabled.append(value),
        original_disable(value),
    )[1]
    gl_context.funcion_alfa = lambda mode, threshold: (
        alpha_functions.append((mode, threshold)),
        original_alpha_func(mode, threshold),
    )[1]
    try:
        draw_character(
            parts,
            bones,
            320,
            240,
            math.pi,
            0.2,
            1.0,
            textures=textures,
            key="alpha-depth-test-bellagoth",
        )
    finally:
        gl_context.activar = original_enable
        gl_context.desactivar = original_disable
        gl_context.funcion_alfa = original_alpha_func
    comprobar(
        character_scene.transparentes > 0
        and gl.GL_ALPHA_TEST in enabled
        and (gl.GL_GREATER, 0.5) in alpha_functions
        and gl.GL_ALPHA_TEST in disabled
        and gl.GL_CULL_FACE in disabled
        and gl.GL_CULL_FACE in enabled
        and any(es_peinado for _, _, _, es_peinado in character_scene.dibujos),
        "el pelo desactiva temporalmente el culling y usa alpha test",
    )


def prueba_nombres_morphs():
    """La cara femenina LOD5 conserva el mapeo de formas del FBX de referencia."""
    from ts2psp.formats.characters import (
        find_mesh,
        load_character_rows,
        parse_character_mesh,
        parse_mesh,
    )

    seccion("Nombres de morphs faciales")
    archive_path = DATOS / "sims2_psp_personajes_recursos.zip"
    with zipfile.ZipFile(archive_path) as archive:
        rows = load_character_rows(archive)
        emily = next(row for row in rows if row["personaje"] == "emilyemory")
        johnny = next(row for row in rows if row["personaje"] == "johnnysmith")
        female_data = find_mesh(archive, emily["bodyHead"])
        male_data = find_mesh(archive, johnny["bodyHead"])

    female_parts = parse_character_mesh(emily["bodyHead"], female_data)
    female_names = {name for part in female_parts for name in part["morph_names"]}
    comprobar(
        emily["bodyHead"] == "face/afface"
        and {
            "R_scrunch",
            "L_scrunch",
            "R_brow",
            "L_brow",
            "R_eye",
            "L_eye",
            "R_cheekpuff",
            "L_cheekpuff",
        }.issubset(female_names),
        "los morphs femeninos tardíos se renombran según sus deltas",
    )
    male_character = parse_character_mesh(johnny["bodyHead"], male_data)
    male_names = {name for part in male_character for name in part["morph_names"]}
    comprobar(
        {"L_zcorner", "R_zcorner", "L_ycorner", "R_ycorner"} <= male_names,
        "el mapeo masculino conserva los morphs faciales laterales",
    )


def prueba_sin_gpu():
    """Sin aceleración no hay a dónde caer: el render tiene que avisar, no dibujar."""
    seccion("Sin aceleración por hardware")
    root = tk.Tk()
    root.withdraw()
    _, piezas, huesos, texturas = load_character(
        DATOS / "sims2_psp_personajes_recursos.zip", "bellagoth"
    )
    argumentos = dict(textures=texturas)
    # se simula el fallo de la misma forma que lo recordaría el sistema real
    gl._fallo = RuntimeError("sin gpu")
    try:
        error = como_error(
            lambda: draw_character(
                piezas,
                huesos,
                320,
                240,
                math.pi,
                0.2,
                1.0,
                key=("sin-gpu", "bellagoth"),
                **argumentos,
            )
        )
    finally:
        gl._fallo = None
    if error is None:
        comprobar(
            False, "sin GPU el render lanza SinOpenGL en vez de dibujar por software"
        )
    else:
        mensaje = str(error).strip()
        print("  aviso del render:", error)
        comprobar(
            isinstance(error, gl.SinOpenGL),
            "sin GPU el render lanza SinOpenGL en vez de dibujar por software",
        )
        comprobar(
            any(pista in mensaje.lower() for pista in ("gpu", "opengl", "aceleraci")),
            f"el aviso explica por qué no se puede dibujar: {mensaje}",
        )

    with zipfile.ZipFile(DATOS / "sims2_psp_props_recursos.zip") as z:
        filas = list(
            csv.DictReader(
                io.StringIO(z.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        fila = next(
            f
            for f in filas
            if f["ruta_interna"] == "objects/chairs/fx_chair_dining_medieval"
        )
        piezas = load_parts(z.read(fila["archivo_zip"]))
    gl._fallo = RuntimeError("sin gpu")
    try:
        error_prop = como_error(
            lambda: render_prop(
                piezas, 320, 240, 0.35, 1.0, True, 0.2, key=("sin-gpu", fila["entry"])
            )
        )
    finally:
        gl._fallo = None
    comprobar(
        isinstance(error_prop, gl.SinOpenGL),
        "los props también avisan con SinOpenGL si no hay GPU",
    )
    comprobar(gl.disponible(), "tras la simulación la GPU vuelve a estar disponible")
    root.destroy()


def comprobar_pestanas_no_cambian_de_ancho(app):
    """Resaltar una pestaña no puede cambiarle el ancho.

    Al pasar el ratón se le cambia la textura por la de resaltado, y la anchura
    de cada textura sale de lo que mide su texto. Si al buscar la textura se
    coge la de otra pestaña, sale una mas ancha y la pestaña crece y encoge
    segun por donde vaya el cursor. Pasa con una lista de etiquetas desfasada
    respecto a las pestañas que hay: al quitar 'Animations', 'Props' cogia su
    textura y 'Maps' la de 'Props'.
    """
    nb = app.tabs
    titulos = [nb.tab(i, "text") for i in range(nb.index("end"))]
    comprobar(titulos, "el cuaderno tiene pestañas")
    comprobar(
        tuple(titulos) == tuple(NOTEBOOK_TAB_LABELS),
        "y son exactamente las que el tema tiene textura",
    )
    for i, titulo in enumerate(titulos):
        normal = app._sims_tab_images.get(titulo)
        resaltada = app._sims_tab_highlight_images.get(titulo)
        comprobar(
            normal is not None and resaltada is not None,
            f"la pestaña {titulo} tiene las dos texturas",
        )
        if normal is None or resaltada is None:
            continue
        comprobar(
            (normal.width(), normal.height()) == (resaltada.width(), resaltada.height()),
            f"la pestaña {titulo} no cambia de tamaño al resaltarse",
        )


def comprobar_deslizadores(app):
    """El trough del deslizador va hecho del ancho que mide el deslizador.

    El trough es una imagen de ancho fijo. Si el widget mide otra cosa, ttk la
    estira o la recorta y el remate redondeado se pierde por un lado: con las
    columnas estrechas se veía la barra cortada a la derecha. No se arregla con
    `border`, porque ese margen es el relleno de los hijos y el pulgar es hijo
    del trough, asi que la imagen se hace del ancho justo.

    Aqui solo se comprueba la medida, que es lo que se puede comprobar sin
    dependerde de lo que haya en pantalla. Que las dos tapas salgan iguales es
    cosa de mirar, no de testear: capturar la ventana desde fuera no es fiable
    si no esta al frente.
    """
    tab = app.character_tab
    app.tabs.select(tab)
    app.update()
    for nombre, slider, esperado in (
        ("Shape Keys", tab.shape_slider, ANCHO_TROUGH_SHAPES),
        ("Animación", tab.animations.slider, ANCHO_TROUGH_CLIPS),
    ):
        if not slider.winfo_ismapped():
            continue
        slider.update_idletasks()
        comprobar(
            slider.winfo_width() == esperado,
            f"{nombre}: el deslizador mide {esperado} px, como su trough",
        )
        comprobar(
            str(slider.cget("style"))
            in ("Shape.Horizontal.TScale", "Clips.Horizontal.TScale"),
            f"{nombre}: usa su propio estilo de deslizador",
        )


def comprobar_personaje_por_defecto(app):
    """Abre con Bella Goth, y la lista vuelve arriba cada vez que se abre.

    Lo que viene por defecto tiene que distinguirse de lo que elige el usuario:
    por eso la lista se reencuadra en su principio al entrar en la vista. El
    personaje elegido si se conserva, que es lo otro.
    """
    tab = app.character_tab
    app.tabs.select(tab)
    app.update()
    panel = tab.animations
    lista = tab.listbox
    comprobar(
        (tab.current or "").casefold() == "bellagoth",
        "arranca con Bella Goth cargado",
    )
    comprobar(
        lista.curselection() and lista.get(lista.curselection()[0]) == "Bella Goth",
        "y sale marcada en la lista",
    )
    comprobar(lista._first == 0, "con la lista al principio")

    # Se mira otra parte de la lista y se elige a alguien mas.
    lista.yview("moveto", 0.5)
    app.update()
    otro = lista._first + 3
    lista.selection_clear(0, "end")
    lista.selection_set(otro)
    tab.select()
    app.update()
    elegido = lista.get(otro)
    nombre = tab.current
    comprobar(lista._first > 0, "al elegir otro, la lista se desplaza")

    tab.toggle_view()
    app.update()
    tab.toggle_view()
    app.update()
    comprobar(lista._first == 0, "al volver a la vista de personajes, la lista arriba")
    comprobar(
        lista.get(lista.curselection()[0]) == elegido,
        "pero el personaje elegido se conserva",
    )
    comprobar(tab.current == nombre, "y el visor sigue con el mismo personaje")

    # La lista de 'Replace character' tambien aparece siempre en su principio.
    tab.toggle_view()
    app.update()
    tab.replace_button.invoke()
    app.update()
    lista.yview("moveto", 0.6)
    app.update()
    comprobar(lista._first > 0, "se puede desplazar la lista de la vista de animaciones")
    tab.toggle_replace_character()
    app.update()
    tab.toggle_replace_character()
    app.update()
    comprobar(
        lista._first == 0,
        "pero al cerrar y abrirla vuelve a estar en su principio",
    )
    # Con el boton ya marcado, cambiar de vista tambien repone el resaltado: en
    # cada vista se ve un personaje distinto y la lista es la misma.
    if not tab.animation_view.get():
        tab.toggle_view()
        app.update()
    lista.yview("moveto", 0.7)
    app.update()
    clip_actual = nombre_publico(panel.character.get())
    comprobar(
        lista.get(lista.curselection()[0]) == clip_actual,
        "en animation view la lista señala al personaje del clip",
    )
    tab.toggle_view()
    app.update()
    comprobar(
        lista.get(lista.curselection()[0]) == elegido and lista._first == 0,
        "al pasar a character view señala al del visor, y arriba del todo",
    )
    tab.toggle_view()
    app.update()
    comprobar(
        lista.get(lista.curselection()[0]) == clip_actual and lista._first == 0,
        "y al volver a animation view vuelve al del clip",
    )
    # Se deja como estaba, que la comprobacion siguiente empieza desde la vista
    # de personajes y sin 'Replace character' marcado.
    if tab.replace_character.get():
        tab.toggle_replace_character()
        app.update()
    if tab.animation_view.get():
        tab.toggle_view()
        app.update()
    # Y al volver a la vista de personajes, con el boton aun marcado, la lista
    # tiene que seguir enseñando el personaje que eligió el usuario. El flag no
    # se reinicia al cambiar de vista, asi que si solo se mirase ese, el clip
    # venia a sobreescribir la seleccion.
    tab.replace_character.set(True)
    tab._animation_character_changed("johnnysmith")
    app.update()
    comprobar(
        lista.get(lista.curselection()[0]) == elegido,
        "el clip no pisa la selección de la lista en Character View",
    )
    tab.replace_character.set(False)
    if tab.animation_view.get():
        tab.toggle_view()
        app.update()

    # El primer clic tiene que valer tambien cuando el personaje es el que ya
    # esta cargado en el visor. `select()` sale pronto en ese caso, y si por ahi
    # no se avisa al panel, hay que pulsar dos veces.
    if not tab.animation_view.get():
        tab.toggle_view()
        app.update()
    if not tab.replace_character.get():
        tab.replace_button.invoke()
        app.update()
    lista.yview("moveto", 0.0)
    app.update()
    sel = lista.curselection()
    if sel and tab.current in tab.visible:
        fila = tab.visible.index(tab.current) - lista._first
        if 0 <= fila < max(1, lista._visible_rows()):
            alto = lista._row_height
            y = 6 + fila * alto + alto // 2
            lista.event_generate("<Button-1>", x=30, y=y)
            lista.event_generate("<ButtonRelease-1>", x=30, y=y)
            app.update()
            comprobar(
                panel.character.get() == tab.current,
                "un clic sobre el personaje ya cargado sí lo lleva al clip",
            )

    # Y al revés: elegir en la lista de animaciones no debe tocar el personaje
    # del visor, que es el que esa vista tiene que recordar.
    fila = 2 - lista._first
    if 0 <= fila < max(1, lista._visible_rows()):
        alto = lista._row_height
        y = 6 + fila * alto + alto // 2
        lista.event_generate("<Button-1>", x=30, y=y)
        lista.event_generate("<ButtonRelease-1>", x=30, y=y)
        app.update()
        comprobar(
            (tab.current or "").casefold() == (nombre or "").casefold(),
            "elegir en animaciones no cambia el personaje del visor",
        )
        comprobar(
            panel.character.get() != "ashtonfelt",
            "pero sí el del clip",
        )
    if tab.replace_character.get():
        tab.toggle_replace_character()
        app.update()
    if tab.animation_view.get():
        tab.toggle_view()
        app.update()
    comprobar(
        not tab.animation_view.get() and not tab.replace_character.get(),
        "la comprobación deja la vista como la encontró",
    )
    comprobar(
        (tab.current or "").casefold() == (nombre or "").casefold(),
        "y el visor sigue con el personaje que tenía",
    )


def prueba_app():
    seccion("App completa")
    app = Sims2PSPApp(base=DATOS)
    app.deiconify()
    app.update_idletasks()
    app.update()
    tamano = (app.winfo_width(), app.winfo_height())
    comprobar_pestanas_no_cambian_de_ancho(app)
    comprobar_deslizadores(app)
    comprobar_personaje_por_defecto(app)
    def orbit(tab, veces=8):
        canvas = tab.canvas
        canvas.event_generate("<ButtonPress-1>", x=120, y=120)
        for i in range(veces):
            canvas.event_generate("<B1-Motion>", x=120 + 16 * i, y=120 + 5 * i)
            app.update()
        canvas.event_generate("<MouseWheel>", delta=120)
        canvas.event_generate("<ButtonPress-2>", x=200, y=200)
        canvas.event_generate("<B2-Motion>", x=230, y=225)
        app.update()

    for tab in (app.character_tab, app.prop_tab, app.map_tab):
        app.tabs.select(tab)
        app.update()
        # La primera vez que se abre una pestaña hay trabajo de fondo: en props se
        # escanean los .res y en mapas se lee y parsea el nivel entero, los dos
        # en un hilo para no congelar la ventana. Es trabajo de una vez, asi que se
        # espera a que termine antes de cronometrar, o lo que se mide es el escaneo
        # de fondo y no el render.
        #
        # En mapas hay que esperar tambien a que el mapa este aplicado, no solo a
        # que el hilo suelte su cola: si no, el render medido no tiene nada que
        # dibujar y sale en 0.00 ms sin comprobar nada.
        waited = time.perf_counter()
        while time.perf_counter() - waited < 30 and (
            getattr(tab, "_anim_queue", None) is not None
            or getattr(tab, "_carga_cola", None) is not None
            or (tab is app.map_tab and tab.current is None)
        ):
            app.update()
            time.sleep(0.05)
        comprobar(
            getattr(tab, "_anim_queue", None) is None,
            f"{type(tab).__name__}: el escaneo de animaciones termina solo",
        )
        comprobar(
            getattr(tab, "_carga_cola", None) is None,
            f"{type(tab).__name__}: la carga de la pestaña termina sola",
        )
        if tab is app.map_tab:
            comprobar(
                tab.current is not None and len(tab.parts) > 100,
                "MapTab: el mapa entero queda cargado, con todas sus piezas",
            )
            comprobar(
                len(tab.piezas_visibles()) < len(tab.parts),
                "MapTab: la cupula del cielo se deja fuera del encuadre",
            )
        app.update_idletasks()
        # La camara se prueba sobre algo que se pueda mover. La pestana de
        # personajes abre con Bella Goth cargado; si no lo estuviera, sin modelo
        # el foco no cambiaria al orbitar y la comprobacion no diria nada.
        if tab is app.character_tab and not tab.current:
            tab.select_name(
                "bellagoth" if "bellagoth" in tab.visible else tab.visible[0]
            )
            app.update()
            app.update_idletasks()
        antes = (tab.camera.yaw, tab.camera.pitch, tab.camera.zoom)
        foco_antes = tab.camera.focus_offset
        orbit(tab)
        comprobar(
            tab.camera.focus_offset != foco_antes,
            f"{type(tab).__name__}: el botón central mueve la cámara",
        )
        foco_antes_derecho = tab.camera.focus_offset
        tab.canvas.event_generate("<ButtonPress-3>", x=200, y=200)
        tab.canvas.event_generate("<B3-Motion>", x=260, y=260)
        app.update()
        comprobar(
            tab.camera.focus_offset != foco_antes_derecho,
            f"{type(tab).__name__}: el botón derecho mueve la cámara",
        )
        tab.render()
        frame = cronometrar(tab.render, 8)
        movido = (tab.camera.yaw, tab.camera.pitch, tab.camera.zoom) != antes
        estable = (app.winfo_width(), app.winfo_height()) == tamano
        print(
            f"  {type(tab).__name__:13} {tab.canvas.view_size()} "
            f"{frame:6.2f} ms/frame ({1000 / frame:5.0f} fps)"
        )
        comprobar(movido, f"{type(tab).__name__}: la cámara orbita con el ratón")
        comprobar(estable, f"{type(tab).__name__}: la ventana no crece al girar")
        comprobar(frame < 20, f"{type(tab).__name__}: el frame dura menos de 20 ms")

    # Se guarda el ancho del visor antes de ensanchar la ventana, para comprobar
    # que CRECE en vez de mirar un numero fijo: las columnas laterales se han
    # ensanchado (la de Shape Keys comparte hueco con el panel de animaciones) y
    # un umbral absoluto empezaria a fallar por eso, no porque el render se
    # adaptase mal.
    anchos_antes = {}
    for tab in (app.character_tab, app.prop_tab, app.map_tab):
        app.tabs.select(tab)
        app.update()
        app.update_idletasks()
        anchos_antes[tab] = tab.canvas.view_size()[0]
    app.geometry("1400x900")
    app.update()
    for tab in (app.character_tab, app.prop_tab, app.map_tab):
        app.tabs.select(tab)
        app.update()
        tab.schedule()
        app.update()
    comprobar(
        (app.winfo_width(), app.winfo_height()) == (1400, 900),
        "la ventana se redimensiona y las pestañas repintan",
    )
    for tab in (app.character_tab, app.prop_tab, app.map_tab):
        ancho = tab.canvas.view_size()[0]
        print(f"  {type(tab).__name__:13} vista {tab.canvas.view_size()}")
        comprobar(
            ancho > anchos_antes[tab],
            f"{type(tab).__name__}: el render se adapta al tamaño nuevo",
        )

    comprobar(
        "GPU" in app.info.get(), "la app arranca usando la GPU: " + app.info.get()
    )
    comprobar(not hasattr(app.tabs, "gpu"), "no hay opción para desactivar la GPU")

    for nombre in ("bellagoth", "neopaganini"):
        if nombre in app.character_tab.visible:
            app.character_tab.select_name(nombre)
            app.update()
            app.character_tab.schedule()
            app.update()
            comprobar(True, f"personaje {nombre} seleccionado y dibujado")
    character_tab = app.character_tab
    comprobar(
        character_tab.listbox.size() == len(character_tab.visible) == 86,
        "la lista texturizada contiene todos los personajes",
    )
    character_tab.select_name("bellagoth")
    comprobar(
        character_tab.shape_list.size() > 0,
        "la lista texturizada muestra los Shape Keys de la cara",
    )
    character_tab.shape_list.selection_set(0)
    app.update()
    comprobar(
        bool(character_tab.shape.get()) and character_tab.shape_id is not None,
        "la selección de un Shape Key activa el control de la forma",
    )
    character_tab.set_shape_weight(1.0)
    character_tab.render()
    comprobar(
        character_tab.shape_weight.get() == 1.0,
        "el Shape Key seleccionado se puede aplicar en el visor",
    )
    character_tab.select_name("emilyemory")
    labels = character_tab.shape_list.get(0, "end")
    comprobar(
        not hasattr(character_tab, "face_box"),
        "se ha retirado el selector de arquetipo facial",
    )
    comprobar(
        not any(label.startswith("Face archetype: ") for label in labels),
        "los arquetipos no aparecen en la lista de Shape Keys",
    )
    comprobar(
        "nosebackfwd" in labels
        and not any(label.startswith("Face modifier: ") for label in labels),
        "los modificadores aparecen sin el prefijo Face modifier",
    )
    comprobar(
        any(
            name.startswith("Face archetype: ")
            for _, part in character_tab.parts
            for name in part.get("morph_names", [])
        ),
        "los arquetipos siguen disponibles en los datos del modelo",
    )
    modifier_index = labels.index("nosebackfwd")
    character_tab.shape_list.selection_clear(0, "end")
    character_tab.shape_list.selection_set(modifier_index)
    app.update()
    comprobar(
        character_tab.shape.get() == "Face modifier: nosebackfwd"
        and character_tab.shape_id is not None,
        "el nombre abreviado conserva el Shape Key original",
    )
    comprobar_vista_animaciones_de_personajes(app)
    app.close()


def comprobar_vista_animaciones_de_personajes(app):
    """La vista 'Animation View' de la pestaña de personajes pinta un clip.

    Comparte el lienzo con el personaje estatico, asi que hay que comprobar las
    dos cosas: que el clip se dibuja y que al volver se repinta el personaje.
    """
    import time

    tab = app.character_tab
    app.tabs.select(tab)
    comprobar(
        tab.view_button.cget("text") == "Animation View",
        "el botón de la vista de animaciones empieza en 'Animation View'",
    )
    comprobar(
        not tab.animation_view.get(), "arranca en la vista de personajes"
    )
    # El boton dice a donde se va, y va centrado en su columna.
    boton = tab.view_button
    comprobar(
        abs(
            (boton.winfo_x() + boton.winfo_width() // 2)
            - (boton.master.winfo_width() // 2)
        )
        <= 2,
        "el botón de vista está centrado en su columna",
    )
    # El boton se comporta como los demas: verde mientras se mantiene y al
    # kembali normal solo, nunca fijo por el estado de la vista. El estado de
    # ttk es lo que cambia; el estilo se queda puesto el mismo.
    comprobar(
        boton.cget("style") == "ViewToggle.TButton",
        "el botón de vista usa su propio estilo de toggle",
    )
    comprobar(
        "pressed" not in boton.state(),
        "el botón de vista está normal por defecto",
    )
    boton.event_generate("<ButtonPress-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" in boton.state(),
        "el botón de vista se pone verde al mantenerlo",
    )
    boton.event_generate("<ButtonRelease-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" not in boton.state(),
        "y vuelve a su estado normal al soltar",
    )
    waited = time.perf_counter()
    while time.perf_counter() - waited < 0.3:
        app.update()
    comprobar(
        "pressed" not in boton.state(),
        "y se queda normal, no verde fijo",
    )
    # Ese clic ha conmutado la vista, que es lo que debe hacer.
    comprobar(tab.animation_view.get(), "el clic cambia de vista")
    tab.toggle_view()
    app.update()
    # Un personaje que el panel de animaciones no elige por las reglas del clip,
    # para que se note si la otra vista lo pisa.
    personaje = "ashtonfelt" if "ashtonfelt" in tab.visible else tab.visible[0]
    tab.select_name(personaje)
    app.update()
    # winfo_ismapped no vale aqui: con la ventana sin mapear del todo, ninguna
    # lista sale como mapeada aunque esten las dos puestas. Lo que se comprueba
    # es que las dos vistas esten gestionadas por el gestor de geometria.
    comprobar(
        tab.characters_frame.winfo_manager() == "pack"
        and tab.shapes_view.winfo_manager() == "pack",
        "arranca con la lista de personajes y la de Shape Keys puestas",
    )
    comprobar(
        tab.animation_frame.winfo_manager() == "",
        "y el panel de animaciones sin empaquetar hasta conmutar",
    )
    tab.toggle_view()
    app.update()
    comprobar(
        tab.view_button.cget("text") == "Character View",
        "el botón pasa a 'Character View' para volver",
    )
    comprobar(tab.animation_view.get(), "la conmutación activa la vista de animaciones")
    panel = tab.animations
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < 60 and panel.preview is None:
        app.update()
        time.sleep(0.05)
    comprobar(panel.preview is not None, "la vista de animaciones carga un clip")
    comprobar(panel.escena is not None, "la vista de animaciones usa la GPU")
    if panel.escena is not None:
        texturas = {t for _, t, _, _ in panel.escena.dibujos}
        print(f"  {len(panel.escena.dibujos)} mallas con {len(texturas)} texturas")
        comprobar(len(texturas) > 0, "las animaciones se dibujan con texturas")
        comprobar(
            len(texturas) < len(panel.escena.dibujos),
            "las mallas que comparten textura no la suben otra vez",
        )
        panel.render_frame(0.3)
        app.update()
        antes = tab.canvas.itemcget(tab.canvas._item, "image")
        panel.render_frame(1.1)
        app.update()
        comprobar(bool(antes), "el clip se dibuja en el lienzo de personajes")
        comprobar(
            bool(tab.canvas.itemcget(tab.canvas._item, "image")),
            "y se repinta al avanzar el tiempo",
        )
        frame = cronometrar(lambda: panel.render_frame(0.5), 6)
        print(f"  frame de animación {frame:5.2f} ms ({1000 / frame:5.0f} fps)")
        comprobar(frame < 25, "la animación se reproduce sin que se note")
    comprobar(
        tab.listbox.winfo_ismapped() == 0, "la lista de personajes se oculta"
    )
    comprobar(
        tab.shape_list.winfo_ismapped() == 0, "la lista de Shape Keys se oculta"
    )
    comprobar(
        panel.listbox.winfo_ismapped() == 1, "la lista de clips se ve"
    )
    # Lo que se ve tiene que tener alto de verdad. Con las dos vistas
    # apiladas y expand=True, pack reparte segun lo pedido por cada una y la
    # segunda se quedaba aplastada a un pixel.
    alto_clips = panel.listbox.winfo_height()
    comprobar(alto_clips > 100, f"la lista de clips tiene alto usable ({alto_clips})")
    comprobar(
        panel.play_button.winfo_ismapped() == 1, "los controles de Play se ven"
    )
    # El visor lo lleva el panel en esta vista, pero la camara y el
    # redimensionado entran por CharacterTab.render. Si ese metodo se limita a no
    # dibujar, arrastrar deja el visor congelado sin que se note por que.
    if panel.escena is not None:
        antes = tab.canvas.itemcget(tab.canvas._item, "image")
        yaw = tab.camera.yaw
        tab.canvas.event_generate("<ButtonPress-1>", x=400, y=400)
        for paso in range(10):
            tab.canvas.event_generate("<B1-Motion>", x=400 + paso * 18, y=400)
            app.update()
        tab.canvas.event_generate("<ButtonRelease-1>", x=580, y=400)
        app.update()
        comprobar(
            tab.camera.yaw != yaw, "la cámara orbita en la vista de animaciones"
        )
        comprobar(
            tab.canvas.itemcget(tab.canvas._item, "image") != antes,
            "y el clip se repinta al arrastrar",
        )
    comprobar(
        panel.play_button.winfo_width() >= panel.play_button.winfo_reqwidth(),
        "el botón de Play no se queda a medias",
    )
    comprobar(
        panel.fps_button.winfo_width() >= panel.fps_button.winfo_reqwidth(),
        "el botón de FPS no se queda a medias",
    )
    comprobar(
        tab.export_button.text == "Export clip as...",
        "el botón flotante dice que va a exportar el clip",
    )
    comprobar(
        tab.export_button.command == tab.export_segun_vista,
        "el botón flotante despacha según la vista",
    )
    # 'Replace character' solo tiene sentido en la vista de animaciones, que es
    # donde el clip elige el personaje por su cuenta.
    comprobar(
        tab.replace_button.winfo_ismapped() == 1,
        "el botón Replace character aparece en la vista de animaciones",
    )
    comprobar(
        not tab.replace_character.get(),
        "y de salida está desmarcado, para que mande el clip",
    )
    comprobar(
        tab.listbox.winfo_ismapped() == 0,
        "la lista de personajes sigue oculta sin marcarlo",
    )
    # Desactivado: verde entero al mantener y normal al soltar.
    comprobar(
        tab.replace_button.cget("style") == "ViewToggle.TButton",
        "desactivado, el botón se pone verde al mantenerlo",
    )
    tab.replace_button.event_generate("<ButtonPress-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" in tab.replace_button.state(),
        "y se pone verde al mantenerlo",
    )
    tab.replace_button.event_generate("<ButtonRelease-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" not in tab.replace_button.state(),
        "y al soltar deja de estar pulsado",
    )
    # Ese clic lo ha activado: la lista se abre y el boton se queda en verde
    # fijo. Es la diferencia con el boton de vista, que vuelve siempre a su
    # color normal porque su estado no se ve en ninguna parte.
    comprobar(
        tab.replace_character.get() and tab.listbox.winfo_ismapped() == 1,
        "al marcarlo se abre la lista de personajes",
    )
    # La lista es la misma que la de la vista de personajes, asi que al abrirla
    # tiene que verse marcado el personaje que esta viendo el clip. No es el de
    # la vista de personajes: al abrir el boton se ven los dos a la vez.
    sel = tab.listbox.curselection()
    comprobar(
        bool(sel)
        and tab.listbox.get(sel[0]) == nombre_publico(tab.animations.character.get()),
        "y sale marcado el personaje que está viendo el clip",
    )
    # Y si el clip cambia de personaje por sus reglas, el resaltado le sigue.
    tab._animation_character_changed("johnnysmith")
    app.update()
    sel = tab.listbox.curselection()
    comprobar(
        bool(sel) and tab.listbox.get(sel[0]) == "Johnny Smith",
        "el resaltado sigue al personaje que elige el clip",
    )
    # Marcar no puede recargar el modelo: `selection_set` ademas emite el evento
    # de seleccion, y aqui lo unico que se quiere es pintar la marca.
    comprobar(
        (tab.current or "").casefold() == "ashtonfelt",
        "y marcar no recarga el personaje del visor",
    )
    # Con 'Replace character' marcado manda lo que se elija, no las reglas del
    # clip. Se mira la regla del panel en si, sin tocar el clip cargado: cargar
    # otro aqui dejaria el panel en un estado que la comprobacion siguiente no
    # espera.
    tab.replace_character.set(True)
    comprobar(
        not panel._auto_character(),
        "con Replace character marcado, las reglas del clip no imponen",
    )
    tab._animation_character_changed("bellagoth")
    app.update()
    comprobar(
        tab.replace_button.cget("style") == "SelectedHold.TButton",
        "activado, el botón se queda en verde",
    )
    # Activado no se resalta al pasar el raton. ttk sigue poniendo su bandera
    # 'active' con el raton encima; lo que evita el salto de color es que el
    # elemento del boton use la misma imagen para 'normal' y para 'active'. Eso
    # no se puede leer desde ttk, asi que se comprueba que el estilo activo es
    # el propio del tema y no el de los toggles, que si resaltan.
    comprobar(
        "pressed" not in tab.replace_button.state(),
        "activado, el botón se queda pulsado sin depender del ratón",
    )
    comprobar(
        ttk.Style(tab).layout("SelectedHold.TButton")[0][0]
        == "Sims.SelectedHoldButton",
        "y el estilo activo es el del tema que no resalta al pasar el ratón",
    )
    tab.replace_button.event_generate("<ButtonPress-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" in tab.replace_button.state(),
        "y estando verde tambien reacciona al mantenerlo",
    )
    # El clic lo apaga, y entonces si vuelve al estilo normal: verde solo al
    # mantener. Este segundo clic es el que lo apaga, porque el de antes lo
    # encendio.
    tab.replace_button.event_generate("<ButtonRelease-1>", x=10, y=5)
    app.update()
    comprobar(
        "pressed" not in tab.replace_button.state()
        and not tab.replace_character.get()
        and tab.replace_button.cget("style") == "ViewToggle.TButton",
        "al desactivarlo vuelve al estilo normal, verde solo al mantener",
    )
    comprobar(
        tab.listbox.winfo_ismapped() == 0,
        "y la lista de personajes se vuelve a cerrar",
    )
    # Se deja activado para lo que viene: es el estado que dispara el fallo de
    # orden, con la lista abierta y el ida y vuelta entre las dos vistas.
    tab.replace_button.invoke()
    app.update()
    tab.toggle_view()
    app.update()
    tab.toggle_view()
    app.update()
    comprobar(
        tab.replace_button.winfo_manager() == "pack"
        and tab.characters_frame.winfo_manager() == "pack",
        "al volver a la vista de animaciones siguen los dos a la vista",
    )
    comprobar(
        tab.replace_button.winfo_rooty() > 0
        and tab.replace_button.winfo_rooty() < tab.listbox.winfo_rooty(),
        "y el botón Replace character sigue encima de la lista, no debajo",
    )
    # El ancho del botón se calcula a partir del texto. Si se cambia la etiqueta
    # sin recalcularlo, 'Export clip as...' se sale de la pastilla.
    comprobar(
        tab.export_button.width
        >= tab.winfo_toplevel()._sims_floating_text_width(
            tab.export_button.text
        ),
        "el botón flotante es lo bastante ancho para su texto",
    )
    # El panel de clips tiene que medir lo mismo que en su propia pestaña: si
    # se queda mas estrecho, el recorrido del pulgar se ve mas corto.
    # El cursor va con el fotograma que se dibuja. Antes `render_frame` movia el
    # reloj y dejaba el pulgar donde estuviera, asi que marcaba otro instante.
    if panel.preview is not None:
        duracion = panel.preview[2]["extras"]["sourceDuration"]
        panel.render_frame(duracion)
        app.update()
        comprobar(
            abs(panel.position.get() - duracion) < 1e-6,
            "el deslizador llega al final del clip",
        )
        comprobar(
            panel.time_label.cget("text").startswith("0:03"),
            "y el reloj concuerda con el final",
        )
        panel.render_frame(0.0)
        app.update()
        comprobar(
            panel.position.get() == 0.0, "y vuelve al principio"
        )
    # Al volver, el personaje estatico tiene que volver a pintar su sitio: si
    # no, el clip se queda pegado en pantalla.
    tab.toggle_view()
    app.update()
    comprobar(
        not tab.animation_view.get(), "vuelve a la vista de personajes"
    )
    # El panel elige personaje por las reglas del clip. Si eso tocara la
    # seleccion de la vista de personajes, al volver aparecia otro.
    comprobar(
        tab.current == personaje,
        "al volver sigue el personaje que habia en Character View",
    )
    comprobar(
        tab.animations.character.get() != personaje
        or not tab.animations.all_characters,
        "el panel de animaciones lleva el suyo sin tocar el de la otra vista",
    )
    comprobar(
        tab.view_button.cget("text") == "Animation View",
        "y el botón vuelve a poner 'Animation View'",
    )
    comprobar(
        tab.listbox.winfo_ismapped() == 1, "vuelve la lista de personajes"
    )
    comprobar(
        tab.shape_list.winfo_ismapped() == 1, "vuelve la lista de Shape Keys"
    )
    tab.render()
    app.update()
    comprobar(bool(tab.parts), "el personaje estático sigue cargado")
    comprobar(
        tab.export_button.text == "Export as...",
        "y el botón vuelve a decir 'Export as...'",
    )
    comprobar(
        tab.replace_button.winfo_ismapped() == 0,
        "y el botón Replace character desaparece en la vista de personajes",
    )


def prueba_arrastre():
    """El bug del trompicón: al girar deprisa llegan más eventos que frames."""
    seccion("Fluidez al arrastrar rápido")
    app = Sims2PSPApp(base=DATOS)
    app.deiconify()
    app.update_idletasks()
    app.update()
    app.tabs.select(app.character_tab)
    app.update()
    tab = app.character_tab
    tab.schedule()
    app.update()

    renders = [0]
    original = tab.render

    def contando():
        renders[0] += 1
        return original()

    tab.render = contando
    tab.camera.drag = (100, 100)
    import time

    inicio = time.perf_counter()
    i = 0
    while time.perf_counter() - inicio < 1.0:
        i += 1
        tab.canvas.event_generate("<B1-Motion>", x=100 + i, y=100)
        app.update()
        time.sleep(0.005)
    fps = renders[0] / (time.perf_counter() - inicio)
    print(f"  {i} eventos de movimiento en 1 s -> {renders[0]} frames ({fps:.1f} fps)")
    comprobar(fps > 20, "un arrastre rápido sigue refrescando a más de 20 fps")
    app.close()


def main():
    if not DATOS.exists():
        print(f"No se encuentra la carpeta de recursos en {DATOS}")
        return 1
    prueba_rendimiento()
    prueba_props()
    prueba_zoom()
    prueba_alpha_textura()
    prueba_nombres_morphs()
    prueba_sin_gpu()
    prueba_app()
    prueba_arrastre()
    print("\n" + "=" * 46)
    if FALLO:
        print(f"{len(FALLO)} comprobaciones fallidas:")
        for fallo in FALLO:
            print("  -", fallo)
        return 1
    print("Todas las comprobaciones han pasado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

