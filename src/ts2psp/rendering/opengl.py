"""Renderizado 3D por GPU con OpenGL sobre ctypes. Es la única vía que hay.

La app original rasterizaba cada triángulo a mano con numpy, unos 98 ms por frame en un
personaje y con la cámara a trompicones. Aquí la misma escena se dibuja en un
framebuffer fuera de pantalla y se lee a memoria: ~1,6 ms por frame.

No hace falta PyOpenGL: se habla directamente con opengl32.dll, que viene con Windows.
No hay render por software: si la máquina no da contexto OpenGL (máquinas virtuales,
escritorio remoto, drivers antiguos) salta SinOpenGL y cada pestaña lo enseña en su
barra de estado en vez de dibujar un cuadro más lento.

Uso desde la app:

    escena = ts2psp.rendering.opengl.escena(clave, piezas, texturas, tipo='personaje')
    imagen = escena.render(ancho, alto, yaw, pitch, zoom, pan, camara)
"""

import ctypes
import math
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np
from PIL import Image

from ts2psp.ui.camera import (
    DISTANCIA_MINIMA,
    FOV,
    PLANO_CERCANO,
    ZOOM_MAXIMO,
    escala_profundidad,
)

# --- constantes de OpenGL ----------------------------------------------------
# Brighter preview for authored vertex colors. The gamma exponent is below one
# to lift darker values; a neutral luminance addition preserves the color cast.
UMBRAL_LUMINANCIA_VERTEX_COLOR_OSCURO = 0.80
GAMMA_VERTEX_COLOR_OSCURO = 0.15
PISO_LUMINANCIA_VERTEX_COLOR_OSCURO = 0.16
GANANCIA_BRILLO_VERTEX_COLOR = 1.08


def levantar_vertex_colors_oscuros(colores, mascara_vertex_color):
    """Lift low vertex-color luminance neutrally, without changing exposure."""
    colores = np.asarray(colores, dtype=np.float32)
    salida = colores.copy()
    mascara = np.asarray(mascara_vertex_color, dtype=bool)
    if colores.ndim != 2 or colores.shape[1] < 3 or mascara.shape != (len(colores),):
        return salida
    rgb = np.clip(colores[:, :3], 0.0, 1.0)
    luminancia = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    peso = np.clip(
        (UMBRAL_LUMINANCIA_VERTEX_COLOR_OSCURO - luminancia)
        / UMBRAL_LUMINANCIA_VERTEX_COLOR_OSCURO,
        0.0,
        1.0,
    )
    exponentes = 1.0 - (1.0 - GAMMA_VERTEX_COLOR_OSCURO) * peso
    luminancia_gamma = np.power(luminancia, exponentes)
    luminancia_gamma = np.maximum(
        luminancia_gamma, PISO_LUMINANCIA_VERTEX_COLOR_OSCURO
    )
    luminancia_objetivo = luminancia + peso * (luminancia_gamma - luminancia)
    aporte_neutro = np.maximum(luminancia_objetivo - luminancia, 0.0)
    corregidos = np.clip(rgb + aporte_neutro[:, None], 0.0, 1.0)
    corregidos[mascara] *= GANANCIA_BRILLO_VERTEX_COLOR
    salida[mascara, :3] = np.clip(corregidos[mascara], 0.0, 1.0)
    return salida


def malla_admite_lift_vertex_color(part):
    """Only lift meshes whose vertex RGB is color, not a terrain blend mask."""
    return not part.get("texture_blend")


def malla_necesita_brillo_vertex_color(part):
    """Return whether a mesh has dark authored colors eligible for the lift."""
    if not malla_admite_lift_vertex_color(part):
        return False
    colors = [vertex[3][:3] for vertex in part.get("vertices", ()) if vertex[3] is not None]
    if not colors:
        return False
    values = np.asarray(colors, dtype=np.float32)
    luminance = values @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return bool(np.any(luminance < UMBRAL_LUMINANCIA_VERTEX_COLOR_OSCURO))


def factores_luz_direccional_mapa(normales, perfil):
    """Evaluate the viewer's diffuse model against the source DNL lights.

    The resources provide the light transforms and animated RGBA curves, but
    not the game's GPU combiner equation. The viewer retains its existing
    diffuse/ambient model and averages the authored light terms.
    """
    normales = np.asarray(normales, dtype=np.float32)
    factores = np.ones((len(normales), 3), dtype=np.float32)
    if not isinstance(perfil, dict):
        return factores
    lights = (
        [value for value in perfil.values() if isinstance(value, dict) and "direction" in value]
        if "direction" not in perfil
        else [perfil]
    )
    if not lights:
        return factores
    longitudes = np.linalg.norm(normales, axis=1)
    validas = longitudes > 1e-8
    normales_unitarias = np.zeros_like(normales)
    normales_unitarias[validas] = normales[validas] / longitudes[validas, None]
    contribuciones = []
    for light in lights:
        direccion = np.asarray(light.get("direction", ()), dtype=np.float32)
        color = np.asarray(light.get("color", (1.0, 1.0, 1.0)), dtype=np.float32)
        intensidad = float(light.get("intensity", 0.0))
        longitud = float(np.linalg.norm(direccion))
        max_color = float(np.max(color)) if color.size == 3 else 0.0
        if (
            not np.isfinite(longitud)
            or longitud <= 1e-8
            or not np.isfinite(intensidad)
            or not np.all(np.isfinite(color))
            or max_color <= 1e-8
            or intensidad <= 1e-8
        ):
            continue
        direccion /= longitud
        intensidad = float(np.clip(intensidad, 0.0, 1.0))
        # Keep the source RGB magnitude as well as its hue. Normalizing by the
        # brightest channel preserves hue but discards the authored light power.
        tinte = np.clip(color, 0.0, 1.0)
        lambert = np.zeros(len(normales), dtype=np.float32)
        lambert[validas] = np.maximum(normales_unitarias[validas] @ direccion, 0.0)
        contribuciones.append(
            (1.0 - intensidad)
            + intensidad * lambert[:, None] * tinte[None, :]
        )
    if contribuciones:
        factores[:] = np.mean(contribuciones, axis=0)
    return factores


def radio_luz_local_mapa(light):
    """Return the inverse-square falloff radius for a map point light."""
    try:
        intensity = float(light.get("intensity", 0.0))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    if not math.isfinite(intensity) or intensity <= 0:
        return 0.0
    light_type = str(light.get("light_type", "unknown")).casefold()
    radius_scale = 0.01 if light_type == "point" else 0.04
    return max(math.sqrt(intensity) * radius_scale, 1.0)


def alcance_luz_local_mapa(light):
    """Practical visible reach: twice the radius leaves 20% attenuation."""
    return radio_luz_local_mapa(light) * 2.0


def factores_luces_locales_mapa(posiciones, normales, luces):
    """Estimate diffuse gains from authored local lights for map surfaces.

    Point/spot intensity in the source files is stored in large engine-specific
    units. Convert it to a bounded preview radius and gain; this keeps vertex
    colors as the material base while allowing nearby surfaces to receive light.
    """
    posiciones = np.asarray(posiciones, dtype=np.float32)
    normales = np.asarray(normales, dtype=np.float32)
    factores = np.ones((len(posiciones), 3), dtype=np.float32)
    if (
        posiciones.shape != normales.shape
        or posiciones.ndim != 2
        or posiciones.shape[1] != 3
    ):
        return factores

    normal_lengths = np.linalg.norm(normales, axis=1)
    valid = normal_lengths > 1e-8
    unit_normals = np.zeros_like(normales)
    unit_normals[valid] = normales[valid] / normal_lengths[valid, None]
    additions = np.zeros_like(factores)
    for light in luces or ():
        if not isinstance(light, dict):
            continue
        light_type = str(light.get("light_type", "unknown")).casefold()
        if light_type not in {"ambient", "directional", "point", "spot", "unknown"}:
            continue
        name = str(light.get("name", "")).casefold()
        # DayNightLight is already evaluated from the selected animated DNL profile.
        if name.startswith("daynightlight"):
            continue
        color = np.asarray(light.get("color", (1.0, 1.0, 1.0)), dtype=np.float32)
        if color.shape != (3,) or not np.all(np.isfinite(color)):
            continue
        color = np.clip(color, 0.0, 1.0)
        if not np.any(color > 1e-6):
            continue
        try:
            intensity = float(light.get("intensity", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(intensity) or intensity <= 0:
            continue
        matrix = np.asarray(light.get("matrix", np.eye(4)), dtype=np.float32)
        if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
            continue

        if light_type == "ambient":
            strength = intensity if intensity <= 1.0 else intensity / (intensity + 5000.0)
            additions += np.clip(strength * 0.35, 0.0, 0.35) * color[None, :]
            continue

        if light_type == "directional":
            # Directionals use only their world-space axis. Their translation
            # is deliberately ignored. The source shape stores RGB and
            # intensity, but no radius or gain: directional illumination has
            # no distance falloff, and the authored intensity is used directly.
            direction = np.asarray(
                light.get("direction", matrix[:3, 2]), dtype=np.float32
            )
            if direction.shape != (3,) or not np.all(np.isfinite(direction)):
                continue
            direction_length = float(np.linalg.norm(direction))
            if direction_length <= 1e-8:
                continue
            direction /= direction_length
            lambert = np.zeros(len(posiciones), dtype=np.float32)
            lambert[valid] = np.maximum(unit_normals[valid] @ direction, 0.0)
            additions += max(intensity, 0.0) * lambert[:, None] * color[None, :]
            continue

        light_position = matrix[:3, 3]
        to_light = light_position[None, :] - posiciones
        distances = np.linalg.norm(to_light, axis=1)
        directions = np.zeros_like(to_light)
        nonzero = distances > 1e-8
        directions[nonzero] = to_light[nonzero] / distances[nonzero, None]
        lambert = np.zeros(len(posiciones), dtype=np.float32)
        lambert[valid] = np.maximum(
            np.sum(unit_normals[valid] * directions[valid], axis=1), 0.0
        )
        radius = radio_luz_local_mapa(light)
        source_strength = intensity / (intensity + 5000.0)
        attenuation = 1.0 / (1.0 + np.square(distances / radius))
        is_point_light = light_type == "point"
        gain = 2.0 if is_point_light else 1.5
        gain_limit = 1.8 if is_point_light else 1.4
        point_gain = source_strength * attenuation * lambert * gain
        additions += (
            np.minimum(point_gain[:, None], gain_limit)
            * color[None, :]
        )

    # Authored RGB contributes positive diffuse light; it must not darken a
    # surface merely because a source channel is below mid-gray.
    factores += np.clip(additions, 0.0, 1.4)
    return factores
GL_TEXTURE_2D = 0x0DE1
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_UNSIGNED_INT = 0x1405
GL_FLOAT = 0x1406
GL_TRIANGLES = 0x0004
GL_DEPTH_TEST = 0x0B71
GL_ALPHA_TEST = 0x0BC0
GL_BLEND = 0x0BE2
GL_BLEND_SRC_ALPHA = 0x0302
GL_BLEND_DST_ONE_MINUS_SRC_ALPHA = 0x0303
GL_GREATER = 0x0204
GL_CULL_FACE = 0x0B44
GL_BACK = 0x0405
GL_NEAREST = 0x2600
GL_LINEAR = 0x2601
GL_LINEAR_MIPMAP_LINEAR = 0x2703
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S = 0x2802
GL_TEXTURE_WRAP_T = 0x2803
GL_CLAMP_TO_EDGE = 0x812F
GL_REPEAT = 0x2901
GL_TEXTURE_MAX_ANISOTROPY = 0x84FE
GL_MAX_TEXTURE_MAX_ANISOTROPY = 0x84FF
GL_FRAMEBUFFER = 0x8D40
GL_RENDERBUFFER = 0x8D41
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_DEPTH_ATTACHMENT = 0x8D00
GL_DEPTH_COMPONENT24 = 0x81A6
GL_FRAMEBUFFER_COMPLETE = 0x8CD5
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_PROJECTION = 0x1701
GL_MODELVIEW = 0x1700
GL_VERTEX_ARRAY = 0x8074
GL_COLOR_ARRAY = 0x8076
GL_TEXTURE_COORD_ARRAY = 0x8078
GL_RENDERER = 0x1F01
GL_DEPTH_CLEAR = 0x0B73
GL_LESS = 0x0201
GL_LINES = 0x0001
GL_POLYGON_OFFSET_LINE = 0x8507
GRID_CELDA_RADIO = 0.5
GRID_MAX_CELDAS = 80


class SinOpenGL(RuntimeError):
    """El sistema no permite crear un contexto utilizable."""


# --- enlaces con la biblioteca de Windows ------------------------------------
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gl = ctypes.WinDLL("opengl32", use_last_error=True)
_gl.glGetString.restype = ctypes.c_char_p
_gl.glGetString.argtypes = [ctypes.c_uint]
_gl.wglGetProcAddress.restype = ctypes.c_void_p
_gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]
_gl.wglCreateContext.restype = ctypes.c_void_p
_gl.wglCreateContext.argtypes = [wintypes.HDC]
_gl.wglMakeCurrent.argtypes = [wintypes.HDC, ctypes.c_void_p]
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_gdi32.ChoosePixelFormat.restype = ctypes.c_int
_gdi32.ChoosePixelFormat.argtypes = [wintypes.HDC, ctypes.c_void_p]
_gdi32.SetPixelFormat.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_void_p]


class _FORMATO(ctypes.Structure):
    _campos = (
        ("nSize", wintypes.WORD),
        ("nVersion", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("iPixelType", wintypes.BYTE),
        ("cColorBits", wintypes.BYTE),
        ("cRedBits", wintypes.BYTE),
        ("cRedShift", wintypes.BYTE),
        ("cGreenBits", wintypes.BYTE),
        ("cGreenShift", wintypes.BYTE),
        ("cBlueBits", wintypes.BYTE),
        ("cBlueShift", wintypes.BYTE),
        ("cAlphaBits", wintypes.BYTE),
        ("cAlphaShift", wintypes.BYTE),
        ("cAccumBits", wintypes.BYTE),
        ("cAccumRedBits", wintypes.BYTE),
        ("cAccumGreenBits", wintypes.BYTE),
        ("cAccumBlueBits", wintypes.BYTE),
        ("cAccumAlphaBits", wintypes.BYTE),
        ("cDepthBits", wintypes.BYTE),
        ("cStencilBits", wintypes.BYTE),
        ("cAuxBuffers", wintypes.BYTE),
        ("iLayerType", wintypes.BYTE),
        ("bReserved", wintypes.BYTE),
        ("dwLayerMask", wintypes.DWORD),
        ("dwVisibleMask", wintypes.DWORD),
        ("dwDamageMask", wintypes.DWORD),
    )
    _fields_ = _campos


def _fn(nombre, retorno, *argumentos):
    """OpenGL 1.1 se exporta en opengl32.dll; el resto se pide por wglGetProcAddress."""
    try:
        funcion = getattr(_gl, nombre)
    except AttributeError:
        direccion = _gl.wglGetProcAddress(nombre.encode())
        if not direccion:
            raise SinOpenGL(f"{nombre} is unavailable in this driver")
        funcion = ctypes.CFUNCTYPE(retorno, *argumentos)(direccion)
    funcion.restype = retorno
    funcion.argtypes = list(argumentos)
    return funcion


class Contexto:
    """Contexto OpenGL compartido, creado sobre una ventana oculta de Tk."""

    _instancia = None

    def __init__(self):
        root = tk._default_root
        if root is None:
            raise SinOpenGL("the application needs an active Tk window")
        self.ventana = tk.Toplevel(root)
        self.ventana.withdraw()
        marco = tk.Frame(self.ventana, width=64, height=64)
        marco.update_idletasks()
        hdc = _user32.GetDC(wintypes.HWND(marco.winfo_id()))
        formato = _FORMATO()
        formato.nSize, formato.nVersion = ctypes.sizeof(_FORMATO), 1
        formato.dwFlags = (
            0x4 | 0x20 | 0x1
        )  # doble buffer | soporta OpenGL | dibuja en ventana
        formato.cColorBits, formato.cDepthBits = 32, 24
        elegido = _gdi32.ChoosePixelFormat(hdc, ctypes.byref(formato))
        if not elegido or not _gdi32.SetPixelFormat(
            hdc, elegido, ctypes.byref(formato)
        ):
            raise SinOpenGL("could not configure the pixel format")
        contexto = _gl.wglCreateContext(hdc)
        if not contexto or not _gl.wglMakeCurrent(hdc, contexto):
            raise SinOpenGL("no se pudo crear el contexto OpenGL")
        self.hdc, self.contexto = hdc, contexto
        self.renderer = (_gl.glGetString(GL_RENDERER) or b"").decode(errors="replace")
        if any(
            marca in self.renderer for marca in ("GDI Generic", "Software", "llvmpipe")
        ):
            raise SinOpenGL(f"hardware acceleration unavailable ({self.renderer})")
        self.funciona()

    @classmethod
    def compartido(cls):
        if cls._instancia is None:
            cls._instancia = Contexto()
        return cls._instancia

    @classmethod
    def disponible(cls):
        try:
            return cls.compartido() is not None
        except Exception:
            return False

    def funciona(self):
        """Enlaza las funciones de OpenGL 2.0 o superiores (requiere contexto activo)."""
        c, i, p, f = ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_float
        d = ctypes.c_double
        u32 = ctypes.POINTER(ctypes.c_uint)
        self.gen_framebuffer = _fn("glGenFramebuffers", None, c, u32)
        self.bind_framebuffer = _fn("glBindFramebuffer", None, i, i)
        self.gen_renderbuffer = _fn("glGenRenderbuffers", None, c, u32)
        self.bind_renderbuffer = _fn("glBindRenderbuffer", None, i, i)
        self.almacenar = _fn("glRenderbufferStorage", None, i, i, c, c)
        self.adjuntar = _fn("glFramebufferRenderbuffer", None, i, i, i, i)
        self.comprobar = _fn("glCheckFramebufferStatus", i, i)
        self.gen_textura = _fn("glGenTextures", None, c, u32)
        self.bind_textura = _fn("glBindTexture", None, i, i)
        self.subir_textura = _fn("glTexImage2D", None, i, c, c, c, c, c, i, i, p)
        self.parametro = _fn("glTexParameteri", None, i, i, c)
        self.parametro_real = _fn("glTexParameterf", None, i, i, f)
        # mipmaps y filtrado anisotrópico son opcionales: si faltan, se usa lineal simple
        self.mipmaps = False
        self.anisotropia = 0.0
        try:
            self.generar_mipmap = _fn("glGenerateMipmap", None, i)
            valor = f()
            _fn("glGetFloatv", None, i, ctypes.POINTER(f))(
                GL_MAX_TEXTURE_MAX_ANISOTROPY, ctypes.pointer(valor)
            )
            self.anisotropia = float(
                valor.value
            )  # a los tipos de ctypes no se les puede pedir float()
            if self.anisotropia < 1:
                self.anisotropia = 0.0
            self.mipmaps = self.anisotropia > 0
        except Exception:  # controlador sin la extensión: nos quedamos con lineal
            self.mipmaps = False
            self.anisotropia = 0.0
        self.viewport = _fn("glViewport", None, c, c, c, c)
        self.limpiar_color = _fn("glClearColor", None, f, f, f, f)
        self.limpiar = _fn("glClear", None, i)
        self.activar = _fn("glEnable", None, i)
        self.desactivar = _fn("glDisable", None, i)
        self.mezcla = _fn("glBlendFunc", None, i, i)
        self.funcion_alfa = _fn("glAlphaFunc", None, i, f)
        self.cara_cull = _fn("glCullFace", None, i)
        self.activar_array = _fn("glEnableClientState", None, i)
        self.desactivar_array = _fn("glDisableClientState", None, i)
        self.matrices = _fn("glMatrixMode", None, i)
        self.identidad = _fn("glLoadIdentity", None)
        self.perspectiva = _fn("glFrustum", None, d, d, d, d, d, d)
        self.punteros = _fn("glVertexPointer", None, c, i, c, p)
        self.punteros_uv = _fn("glTexCoordPointer", None, c, i, c, p)
        self.punteros_color = _fn("glColorPointer", None, c, i, c, p)
        self.dibujar = _fn("glDrawElements", None, i, c, i, p)
        self.fin = _fn("glFinish", None)
        self.leer = _fn("glReadPixels", None, c, c, c, c, i, i, p)
        self.ancho_linea = _fn("glLineWidth", None, f)
        self.mascara_profundidad = _fn("glDepthMask", None, c)
        self.funcion_profundidad = _fn("glDepthFunc", None, i)
        self.desplazamiento_poligono = _fn("glPolygonOffset", None, f, f)


class Escena:
    """Geometría de un modelo subida a la GPU; mientras solo gires la cámara no se toca.

    Las piezas usan el mismo formato que los visores de CPU: una lista de dicts o de
    tuplas con 'vertices' (posicion, nada, uv, color) e 'indices'.
    """

    # Valores por defecto, aqui y no solo en `__init__`, porque hay pruebas que
    # montan una escena llamando a `_cargar` directamente sobre un objeto vacio.
    nombres = ()
    tramos_vertices = ()
    sin_framing = frozenset()
    ocultas_mallas = frozenset()
    sin_recorte = frozenset()
    sin_mallas = False

    def __init__(
        self,
        piezas,
        texturas=None,
        tipo="personaje",
        fondo=(27, 31, 42),
        sin_framing=(),
        ruta_escena="",
    ):
        self.contexto = Contexto.compartido()
        self.fondo = fondo
        self.ancho = self.alto = 0
        self.lectura = None
        self.filtros = ctypes.c_uint()  # id del framebuffer fuera de pantalla
        self.posiciones = None
        self.UV = None
        self.colores = None
        self.dibujos = []
        self.alpha_modes = []
        self.transparentes = 0
        self.tipo = tipo
        self.ruta_escena = str(ruta_escena or "")
        self.mostrar_grid = True
        # Nombres de las piezas que se dibujan pero no cuentan para el encuadre.
        # Sirve para el cielo de los mapas: es una esfera de 528 de lado que
        # envuelve el solar, y si entrara en la caja de la escena la camara se
        # separaria tanto que el nivel entero quedaria en un punto.
        self.sin_framing = frozenset(sin_framing)
        # Dibuja solo la rejilla: lo usa el visor de mapas cuando no queda ninguna
        # pieza encendida, para que la rejilla siga donde esta y el fotograma no
        # se quede pegado del anterior.
        self.sin_mallas = False
        # Indices de `dibujos` que no se pintan. Las mallas siguen subidas y la
        # caja de la escena no cambia, con lo que la camara no se reajusta al
        # apagar o encender una pieza.
        self.ocultas_mallas = frozenset()
        # Indices de `dibujos` que se pintan sin recortar caras traseras, pero sin
        # que eso las vuelva transparentes. Es distinto del pelo, que ademas se
        # mezcla: aqui son piezas opacas de dos caras.
        self.sin_recorte = frozenset()
        # Props use two-sided materials, just as their exported GLB does.
        self.sin_culling = tipo == "prop"
        self._grid_vertices = None
        self._grid_colores = None
        self._grid_indices = None
        self._grid_para_vista = None
        self._cargar(piezas, texturas or {})

    # -- subida ---------------------------------------------------------------
    def _cargar(self, piezas, texturas):
        posiciones, uvs, colores, normales, indices, tramos = [], [], [], [], [], []
        colores_base, modos_base = [], []
        vertices_con_color = []
        vertices_vertex_color_original = []
        vertices_texture_blend = []
        blend_coords = []
        uv_panning_ranges = []
        self.texture_layers = []
        self.alpha_modes = []
        # El nombre de la pieza de cada dibujo, en el mismo orden. Lo usa el
        # visor de mapas para apagar y encender piezas sin rehacer la escena.
        self.nombres = []
        # El tramo de vertices de cada dibujo, para poder dejar fuera de la caja
        # de la escena las piezas que no cuentan para el encuadre.
        self.tramos_vertices = []
        ids_textura = {}
        alpha_textura = {}
        escala_pos, normaliza_uv = (
            (0.001, True)
            if self.tipo in ("personaje", "personaje_criatura")
            else (1.0, False)
        )
        # glTexImage2D coloca la fila 0 abajo. El v de los personajes ya viene invertido en
        # los UV, así que subir la textura tal cual reproduce exactamente el muestreo
        # del rasterizador de CPU (comprobado: diferencia media 0 frente a la CPU)
        voltear = False
        partes_normalizadas = list(self._normalizar(piezas))
        intensidad_referencia_terreno = None
        if (
            self.tipo in {"mapa", "prop"}
            and self.ruta_escena.replace("\\", "/").strip("/").lower()
            == "levels/paradise_place/espiritu"
        ):
            colores_terreno_referencia = []
            for _, candidata in partes_normalizadas:
                if (
                    candidata.get("name") in {"pieza_377", "pieza_382"}
                    or not candidata.get("texture_blend")
                ):
                    continue
                rutas_capas = {
                    str((layer.get("texture") or ("",))[0])
                    .replace("\\", "/")
                    .lower()
                    for layer in candidata.get("texture_layers", ())
                    if isinstance(layer, dict)
                }
                if not (
                    any(
                        path.endswith("textures/terrain/txt_terrain_desert_tan.tif")
                        for path in rutas_capas
                    )
                    and any(
                        path.endswith("textures/terrain/txt_terrain_greengrass_.tif")
                        for path in rutas_capas
                    )
                ):
                    continue
                colores_terreno_referencia.extend(
                    tuple(vertex[3][:3])
                    for vertex in candidata.get("vertices", ())
                    if vertex[3] is not None
                )
            if colores_terreno_referencia:
                intensidad_referencia_terreno = float(
                    np.asarray(colores_terreno_referencia, dtype=np.float32).mean()
                )

        for ruta, parte in partes_normalizadas:
            if parte.get("oculta"):
                # parte sustituida por otra (p. ej. la cabeza por defecto cuando
                # la ficha declara un arquetipo facial): no se sube a la GPU
                continue
            inicio_vertices, inicio_indices = len(posiciones), len(indices)
            ruta = self._clave_textura(ruta, parte)
            textura = texturas.get(ruta)
            texture_state = (ruta, bool(parte.get("repeat_texture")))
            if textura is not None and texture_state not in ids_textura:
                ids_textura[texture_state] = self._subir_textura(
                    textura, voltear, bool(parte.get("repeat_texture"))
                )
                alpha_textura[texture_state] = self._tiene_alpha(textura)
            color = parte.get("color")
            # Keep this inspection override scoped to the green grass meshes
            # from Espiritu Estate. Their source RGB is normally treated as
            # neutral by the terrain blend shader, but the viewer should expose
            # the authored values when inspecting Vertex Color.
            layer_paths = {
                str((layer.get("texture") or ("",))[0]).replace("\\", "/").lower()
                for layer in parte.get("texture_layers", ())
                if isinstance(layer, dict)
            }
            es_terreno_espiritu = (
                self.tipo in {"mapa", "prop"}
                and self.ruta_escena.replace("\\", "/").strip("/").lower()
                == "levels/paradise_place/espiritu"
                and any("/textures/terrain/" in path for path in layer_paths)
            )
            es_terreno_verde_espiritu = (
                es_terreno_espiritu
                and str(parte.get("name", "")) in {"pieza_377", "pieza_382"}
                and any(
                    path.endswith("textures/terrain/txt_terrain_greengrass_.tif")
                    for path in layer_paths
                )
            )
            descriptor_light_ray = " ".join(
                str(value or "")
                for value in (
                    parte.get("name"),
                    parte.get("nombre"),
                    parte.get("source_texture_path"),
                    parte.get("resource_path"),
                    parte.get("ruta"),
                    (parte.get("texture") or ("",))[0],
                    *(layer_paths or ()),
                )
            ).casefold()
            es_light_ray_garage = (
                self.tipo == "mapa"
                and self.ruta_escena.replace("\\", "/").strip("/").casefold()
                == "levels/gas_station/garage"
                and "lightray"
                in descriptor_light_ray.replace("_", "").replace("-", "").replace(" ", "")
            )
            malla_tiene_vertex_color = (
                (not parte.get("texture_blend") or es_terreno_espiritu)
                and not es_light_ray_garage
                and any(vertice[3] is not None for vertice in parte["vertices"])
            )
            second_texture = 0
            if parte.get("texture_blend") and textura is not None:
                second = parte["texture_layers"][1]["texture"]
                state = (second[0], bool(parte.get("repeat_texture")))
                if state not in ids_textura:
                    ids_textura[state] = self._subir_textura(second, voltear, state[1])
                second_texture = ids_textura[state]
            self.texture_layers.append(second_texture)
            weights = parte.get("blend_weights", [])
            secondary_uvs = parte.get("secondary_uvs", [])
            tiene_uv = all(v[2] is not None for v in parte["vertices"])
            vertex_alpha = False
            for vertex_index, vertice in enumerate(parte["vertices"]):
                uv2 = (
                    secondary_uvs[vertex_index]
                    if secondary_uvs
                    else (vertice[2] or (0, 0))
                )
                blend_coords.append((*uv2, weights[vertex_index] if weights else 1.0))
                posiciones.append(tuple(v * escala_pos for v in vertice[0]))
                normales.append(vertice[1])
                # Intensity applies to the whole mesh if it has Vertex Color;
                # vertices without an authored color use the material fallback
                # but must not be left behind in a partially colored mesh.
                vertices_con_color.append(malla_tiene_vertex_color)
                vertices_texture_blend.append(bool(parte.get("texture_blend")))
                vertices_vertex_color_original.append(
                    vertice[3] is not None
                    and malla_admite_lift_vertex_color(parte)
                    and not es_light_ray_garage
                )
                # los personajes traen UV crudos en 1/4096: se normalizan con el v
                # invertido, igual que los usa el rasterizador de CPU. En los props
                # el v ya viene normalizado y sin invertir
                if tiene_uv and normaliza_uv:
                    uvs.append(
                        (
                            (vertice[2][0] + 32768) / 4096.0,
                            1.0 - (vertice[2][1] + 32768) / 4096.0,
                        )
                    )
                elif tiene_uv:
                    uvs.append(vertice[2])
                else:
                    uvs.append((0.0, 0.0))
                base_color = (
                    color
                    if color is not None
                    else (
                        (255, 255, 255, 255)
                        if textura is not None
                        else self._tono(parte.get("nombre", ruta)) + (255,)
                    )
                )
                colores_base.append(base_color)
                if not normaliza_uv and vertice[3] is not None:
                    rgba = tuple(vertice[3])
                    rgba = rgba + (1.0,) if len(rgba) == 3 else rgba
                    if es_light_ray_garage:
                        # The Garage light ray uses its material/texture color;
                        # preserve vertex alpha if it carries coverage data.
                        rgba = (1.0, 1.0, 1.0, rgba[3])
                    if es_terreno_verde_espiritu and intensidad_referencia_terreno:
                        # Keep the source green hue, scaling its strength to the
                        # mean authored RGB level of adjacent terrain meshes
                        # that use the same diffuse layers. Preserve alpha.
                        rgb = np.asarray(rgba[:3], dtype=np.float32)
                        maximo = float(rgb.max())
                        if maximo > 1e-8:
                            rgb *= intensidad_referencia_terreno / maximo
                        rgb = np.clip(rgb, 0.0, 1.0)
                        rgba = tuple(float(channel) for channel in rgb) + (rgba[3],)
                    if parte.get("texture_blend") and not es_terreno_espiritu:
                        # Terrain RGB is unused by the game: its alpha selects
                        # coverage between the two diffuse layers. Tinting the
                        # shader with that often-black RGB turns the whole road
                        # black, so blended terrain contributes neutral white.
                        rgba = (1.0, 1.0, 1.0, 1.0)
                        # Keep the authored GSHD diffuse factor (often 0.5);
                        # neutralize only the vertex RGB mask.
                    base = color if color is not None else (255, 255, 255, 255)
                    colores.append(tuple(rgba[j] * base[j] for j in range(4)))
                    vertex_alpha |= rgba[3] < 0.999
                elif color is not None:
                    colores.append(color)
                elif textura is not None:
                    colores.append((255, 255, 255, 255))
                else:
                    colores.append(self._tono(parte.get("nombre", ruta)) + (255,))
            indices.extend(inicio_vertices + i for i in parte["indices"])
            alfa = None
            if color is not None and len(color) > 3:
                alfa = color[3] < 255
            es_peinado = self._es_peinado(ruta, parte)
            tramos.append(
                (
                    inicio_indices,
                    len(parte["indices"]),
                    ids_textura.get(texture_state, 0),
                    bool(alfa)
                    or vertex_alpha
                    or alpha_textura.get(texture_state, False)
                    or es_peinado,
                    es_peinado,
                )
            )
            # El nombre de la pieza va pegado al tramo porque los dos tienen que
            # contar lo mismo: las piezas ocultas no se suben y tampoco van aqui.
            nombre = str(parte.get("name") or parte.get("nombre") or ruta)
            self.nombres.append(nombre)
            self.tramos_vertices.append((inicio_vertices, len(parte["vertices"])))
            pan_speed = float(parte.get("preview_uv_pan_speed", 0.0) or 0.0)
            if math.isfinite(pan_speed) and pan_speed:
                uv_panning_ranges.append(
                    (inicio_vertices, len(parte["vertices"]), pan_speed)
                )
            transparent = tramos[-1][3]
            modo = parte.get("alpha_mode")
            if self.tipo != "prop":
                # en los personajes solo se mezcla si el material lo pide; el
                # resto de las piezas con alfa van a recorte
                modo = (
                    "BLEND"
                    if modo == "BLEND"
                    else ("MASK" if transparent else "OPAQUE")
                )
            elif not modo and transparent:
                # los props respetan el grupo que les asigno el material. Sin
                # material resuelto se cae en mezcla, que es lo que se hacia
                # antes con cualquier textura que tuviera un pixel de alfa bajo
                modo = "BLEND"
            self.alpha_modes.append(modo or "OPAQUE")
            modos_base.append(
                parte.get("alpha_mode_without_vertex_colors", modo) or "OPAQUE"
            )
        if not posiciones or not indices:
            raise SinOpenGL("the scene has no geometry")
        self.posiciones = np.ascontiguousarray(posiciones, dtype=np.float32)
        self.normales = np.zeros((len(normales), 3), dtype=np.float32)
        for index, normal in enumerate(normales):
            if normal is not None:
                self.normales[index] = normal
        self.tiene_normales = np.fromiter(
            (normal is not None for normal in normales), dtype=bool, count=len(normales)
        )
        # Some map meshes have no authored vertex normals. Derive smooth normals
        # from their triangles so authored lights can illuminate them too.
        missing_normals = ~self.tiene_normales
        if np.any(missing_normals):
            triangles = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
            triangle_positions = self.posiciones[triangles]
            face_normals = np.cross(
                triangle_positions[:, 1] - triangle_positions[:, 0],
                triangle_positions[:, 2] - triangle_positions[:, 0],
            )
            derived_normals = np.zeros_like(self.normales)
            for corner in range(3):
                np.add.at(derived_normals, triangles[:, corner], face_normals)
            lengths = np.linalg.norm(derived_normals, axis=1)
            derivable = missing_normals & (lengths > 1e-8)
            self.normales[derivable] = (
                derived_normals[derivable] / lengths[derivable, None]
            )
        self.UV = np.ascontiguousarray(uvs, dtype=np.float32)
        self._uv_base = self.UV.copy()
        self._uv_panning_ranges = tuple(uv_panning_ranges)
        self.colores = np.ascontiguousarray(colores, dtype=np.float32) / 255.0
        self._colores_vertex_sin_luz = self.colores.copy()
        self._colores_vertex = self._colores_vertex_sin_luz
        self._vertices_con_color = np.asarray(vertices_con_color, dtype=bool)
        self._vertices_vertex_color_original = np.asarray(
            vertices_vertex_color_original, dtype=bool
        )
        self._vertices_texture_blend = np.asarray(vertices_texture_blend, dtype=bool)
        self._vertex_colors_enabled = True
        self._perfil_luz_mapa = None
        self._iluminacion_mapa_enabled = True
        self._luces_locales_mapa = ()
        self._factores_luces_locales_mapa = np.ones((len(self.posiciones), 3), dtype=np.float32)
        self._factores_iluminacion_mapa = np.ones((len(self.posiciones), 3), dtype=np.float32)
        self._blend_coords = np.ascontiguousarray(blend_coords, dtype=np.float32)
        self._colores_base = (
            np.ascontiguousarray(colores_base, dtype=np.float32) / 255.0
        )
        self._modos_vertex = self.alpha_modes
        self._modos_base = modos_base
        indices = np.ascontiguousarray(indices, dtype=np.uint32)
        self.dibujos = [
            (indices[inicio : inicio + n], textura, alfa, es_peinado)
            for inicio, n, textura, alfa, es_peinado in tramos
        ]
        self.transparentes = sum(1 for _, _, alfa, _ in self.dibujos if alfa)
        self.rango = self._rango()
        self.pivote = (
            np.array([0.0, 0.0, 0.97])
            if self.tipo.startswith("personaje") and "criatura" not in self.tipo
            else (self.rango[0][0] + self.rango[0][1]) / 2.0
        )
        # distancia del pivote al punto más lejano: la cámara no baja de aquí.
        # Solo sobre las piezas que cuentan para el encuadre: si el cielo entra,
        # la cámara se aleja hasta que el solar es un punto.
        encuadre = self._posiciones_del_encuadre()
        self.radio = float(np.abs(encuadre - self.pivote).max())
        self.pose = ("", 0.0)

    def _normalizar(self, piezas):
        """Acepta tanto [dict] (props) como [(ruta, dict)] (personajes)."""
        for pieza in piezas:
            if isinstance(pieza, tuple):
                yield pieza
            else:
                yield (pieza.get("textura", ""), pieza)

    def _clave_textura(self, ruta, parte):
        """La clave con la que la app guarda la textura de esta pieza."""
        if ruta:
            return ruta
        textura = parte.get("texture")
        if isinstance(textura, tuple):
            return textura[0]
        return id(parte)

    def _tono(self, nombre):
        if str(nombre).startswith("hair/"):
            return (77, 56, 55)
        if str(nombre).startswith("face/"):
            return (198, 142, 115)
        if str(nombre).startswith("top/"):
            return (98, 109, 167)
        if str(nombre).startswith("bottom/"):
            return (74, 83, 119)
        return (112, 105, 151)

    def _es_peinado(self, ruta, parte):
        """True si la pieza es pelo: se dibuja sin culling y con mezcla.

        No basta con mirar `ruta`. En el visor de personajes `ruta` es el path
        del personaje ('hair/afhair...') y sirve, pero en el de animaciones las
        piezas vienen del GLB y `ruta` es la clave de la textura, mientras que
        el path del pelo va en parte['nombre']; sin mirar tambien ahi, el pelo
        salia recortado por backface culling y opaco.
        """
        for valor in (ruta, parte.get("nombre"), parte.get("name")):
            if str(valor or "").replace("\\", "/").casefold().startswith("hair/"):
                return True
        return bool(parte.get("double_sided"))

    def _tiene_alpha(self, textura):
        if isinstance(textura, tuple):
            import io
            from PIL import Image as _Image

            imagen = _Image.open(io.BytesIO(textura[1])).convert("RGBA")
            return imagen.getchannel("A").getextrema()[0] < 255
        if isinstance(textura, np.ndarray):
            if textura.ndim < 3 or textura.shape[2] < 4:
                return False
            return bool(np.any(textura[:, :, 3] < 255))
        imagen = Image.fromarray(np.uint8(textura)).convert("RGBA")
        return imagen.getchannel("A").getextrema()[0] < 255

    def _subir_textura(self, textura, voltear=False, repeat=False):
        """Sube una textura RGBA. `voltear` invierte el eje v de los UV."""
        gl = self.contexto
        nombre = ctypes.c_uint()
        gl.gen_textura(1, ctypes.byref(nombre))
        gl.bind_textura(GL_TEXTURE_2D, nombre.value)
        if isinstance(textura, tuple):  # (nombre, bytes png)
            import io
            from PIL import Image as _Image

            textura = np.asarray(_Image.open(io.BytesIO(textura[1])).convert("RGBA"))
        elif not isinstance(textura, np.ndarray):
            textura = np.asarray(Image.fromarray(np.uint8(textura)).convert("RGBA"))
        if voltear:
            textura = textura[::-1]
        alto, ancho = textura.shape[:2]
        gl.subir_textura(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            ancho,
            alto,
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            np.ascontiguousarray(textura, dtype=np.uint8).ctypes.data,
        )
        # filtrado lineal en vez del próximo píxel: al girar se ven las texturas
        # mezcladas y no escalonadas. Con mipmaps, las lejanas no parpadean
        if gl.mipmaps:
            gl.generar_mipmap(GL_TEXTURE_2D)
        minimo = GL_LINEAR_MIPMAP_LINEAR if gl.mipmaps else GL_LINEAR
        wrap = GL_REPEAT if repeat else GL_CLAMP_TO_EDGE
        for parametro, valor in (
            (GL_TEXTURE_MIN_FILTER, minimo),
            (GL_TEXTURE_MAG_FILTER, GL_LINEAR),
            (GL_TEXTURE_WRAP_S, wrap),
            (GL_TEXTURE_WRAP_T, wrap),
        ):
            gl.parametro(GL_TEXTURE_2D, parametro, valor)
        if gl.anisotropia > 1:
            # el suelo y los objetos largos se ven nítidos a ángulo rasante
            gl.parametro_real(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY, gl.anisotropia)
        return nombre.value

    def zoom_tope(self, ancho, alto):
        """Límite del zoom según la distancia mínima real de cámara."""
        if FOV <= 0:
            return ZOOM_MAXIMO
        escala = self._escala(ancho, alto)
        focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
        return min(ZOOM_MAXIMO, focal / (escala * DISTANCIA_MINIMA))

    def _escala(self, ancho, alto):
        """Píxeles por unidad en el plano del pivote, con el zoom 1."""
        # 'personaje' y 'personaje_glb' comparten el encuadre de la camara
        if self.tipo.startswith("personaje") and "criatura" not in self.tipo:
            return min(ancho / 2.35, alto / 2.45)
        if self.radio <= 0 or FOV <= 0 or alto <= 0:
            return min(ancho, alto) * 0.78
        # Encajar solo con el tamano del modelo no basta: la camara esta a
        # `distancia` del pivote, y el punto de la esfera envolvente mas cercano
        # esta a `distancia - radio`, que la perspectiva agranda. Con una silla
        # de un metro, lo de delante se salia por arriba de la pantalla.
        #
        # Buscamos la escala maxima con la que esa esfera-template entre. Con
        # `s` los pixeles por unidad en el pivote y `F` la focal en pixeles, el
        # radio proyectado de la parte de delante es s*radio / (1 - s*radio/F), y
        # tiene que caber en la mitad del lado mas corto:
        #
        #     s <= medio / (radio * (1 + medio / F))
        #
        # `medio` va por el lado mas corto porque ahi manda el encuadre; `F` se
        # calcula con el alto, que es como lo hace `_camara`.
        medio = min(ancho, alto) / 2.0
        focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
        tope = medio / (self.radio * (1.0 + medio / focal))
        return min(tope, min(ancho, alto) * 0.78 / self.rango[1])

    def distancia(self, punto, ancho, alto, yaw, pitch, zoom):
        """Signed camera-space depth of a model point, in model units."""
        cy, sy = np.cos(yaw), np.sin(yaw)
        cp, sp = np.cos(pitch), np.sin(pitch)
        x, y, z = np.asarray(punto, dtype=np.float64) - self._punto_mira()
        fondo = sy * x + cy * y
        escala = self._escala(ancho, alto)
        if FOV <= 0:
            return 0.0
        focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
        distancia = focal / (escala * min(zoom, self.zoom_tope(ancho, alto)))
        return distancia + sp * z + cp * fondo

    def _posiciones_del_encuadre(self):
        """Los vertices de las piezas que si cuentan para el encuadre.

        Se deja fuera lo que va en `sin_framing`, que es el cielo de los mapas:
        son esferas de 528 de lado y si su caja entrara en la del encuadre la
        camara se separaria hasta que el solar entero quepa en un punto de dos
        pixeles. Se quitan por tramo de vertices, no por bandera en el dibujo,
        porque la caja se calcula una vez al montar y no en cada fotograma.

        Cuando no hay nada que dejar fuera se devuelven todos los vertices sin
        montar ninguna mascara: `sin_framing` es vacio en practicamente todos los
        visores y esto esta en el arranque de cada escena.
        """
        if not self.sin_framing:
            return self.posiciones
        mascara = np.ones(len(self.posiciones), dtype=bool)
        for nombre, (inicio, cuenta) in zip(self.nombres, self.tramos_vertices):
            if nombre in self.sin_framing:
                mascara[inicio : inicio + cuenta] = False
        return self.posiciones[mascara]

    def _rango(self):
        """Bounding box in model units; the camera framing itself lives in _camara()."""
        puntos = self._posiciones_del_encuadre()
        if len(puntos) == 0:
            puntos = self.posiciones
        minimo = puntos.min(axis=0)
        maximo = puntos.max(axis=0)
        tamano = float(max(maximo - minimo))
        return (minimo, maximo), max(0.05, tamano)

    def _punto_mira(self, camara=None):
        offset = getattr(camara, "focus_offset", (0.0, 0.0, 0.0))
        return self.pivote + np.asarray(offset, dtype=np.float64)

    def _altura_suelo_grid(self):
        suelo = (
            0.0
            if self.tipo.startswith("personaje") and "criatura" not in self.tipo
            else float(self.rango[0][0][2])
        )
        separacion = max(max(1.0, self.radio) * 0.01, 0.001)
        return suelo - separacion

    def fijar_poses(self, posiciones, pose=None):
        """Replaces the positions (shape keys or skinning) without re-uploading indices."""
        self.posiciones = np.ascontiguousarray(posiciones, dtype=np.float32)
        self.pose = pose

    def fijar_panning_tiempo(self, elapsed_seconds):
        """Animate marked preview UVs vertically; source/export UVs stay intact."""
        if not self._uv_panning_ranges:
            return
        self.UV[:] = self._uv_base
        for start, count, speed in self._uv_panning_ranges:
            offset = (float(elapsed_seconds) * speed) % 1.0
            end = start + count
            self.UV[start:end, 1] += offset

    def fijar_vertex_colors(self, enabled, intensity=100.0):
        """Switch cached shading and its strength without rebuilding geometry."""
        self._vertex_colors_enabled = bool(enabled)
        self._vertex_color_intensity = float(np.clip(intensity, 0.0, 200.0)) / 100.0
        self._aplicar_vertex_colors()
        self.alpha_modes = self._modos_vertex if enabled else self._modos_base

    def _aplicar_vertex_colors(self):
        if not self._vertex_colors_enabled:
            self.colores = self._colores_base.copy()
        else:
            intensidad = getattr(self, "_vertex_color_intensity", 1.0)
            self.colores = levantar_vertex_colors_oscuros(
                self._colores_vertex_sin_luz,
                self._vertices_vertex_color_original,
            )
            self.colores[self._vertices_con_color, :3] *= intensidad
            np.clip(self.colores[:, :3], 0.0, 1.0, out=self.colores[:, :3])

        if self._iluminacion_mapa_enabled:
            # Keep authored material/vertex color as albedo, then apply the
            # light gain afterwards. Gains above 1 must survive to texturing so
            # local lights can brighten white, non-vertex-colored materials.
            normales = ~self._vertices_texture_blend
            self.colores[normales, :3] *= self._factores_iluminacion_mapa[normales]

    def fijar_luz_direccional_mapa(self, perfil):
        """Apply the active source day/night profile to map vertex colors.

        The three directional lights come from the map's referenced FX rig.
        This operates on mesh colors before rasterization and never changes
        framebuffer exposure.
        """
        self._perfil_luz_mapa = perfil
        if self._iluminacion_mapa_enabled:
            factores_dia = factores_luz_direccional_mapa(self.normales, perfil)
            # The day/night profile shades the base surface. Local lights add
            # radiance and must not be attenuated again by that base factor.
            factores = factores_dia + self._factores_luces_locales_mapa - 1.0
        else:
            factores = np.ones((len(self.normales), 3), dtype=np.float32)
        self._factores_iluminacion_mapa = factores.copy()
        self._colores_vertex = self._colores_vertex_sin_luz.copy()
        self._colores_vertex[:, :3] *= factores
        self._aplicar_vertex_colors()

    def fijar_luces_locales_mapa(self, luces):
        """Use authored local light records to illuminate the map's surfaces."""
        self._luces_locales_mapa = tuple(luces or ())
        self._factores_luces_locales_mapa = factores_luces_locales_mapa(
            self.posiciones, self.normales, self._luces_locales_mapa
        )
        self.fijar_luz_direccional_mapa(self._perfil_luz_mapa)

    def fijar_iluminacion_mapa(self, enabled):
        """Enable or disable authored local and day/night map lighting."""
        self._iluminacion_mapa_enabled = bool(enabled)
        self.fijar_luz_direccional_mapa(self._perfil_luz_mapa)

    def pose_actual(self):
        return self.pose

    # -- framebuffer ----------------------------------------------------------
    def _dibujar_grid(
        self, gl, ancho, alto, ojo, limite, yaw, pitch, zoom, pan, punto_mira
    ):
        """Dibuja la rejilla del plano del suelo, detrás del modelo.

        El suelo está en Z = 0 (personajes) o en el mínimo del bbox (props/mapas).
        La rejilla se hace en espacio mundo, se transforma con la misma cámara que el
        modelo (profundidad = 0), y se dibuja CON depth test para que no tape al
        modelo que esté encima. Un ligero polygon offset evita el z-fighting con el
        propio suelo del modelo si lo hubiera.
        """
        if not self.mostrar_grid:
            return

        # Celdas amplias, proporcionadas al modelo, como la rejilla de Blender.
        radio = max(1.0, self.radio)
        celda = radio * GRID_CELDA_RADIO
        max_celdas = GRID_MAX_CELDAS
        semiancho = max_celdas * celda
        suelo_z = self._altura_suelo_grid()

        # si la vista es muy estrecha en el eje Y (pitch > 70°), la rejilla se apaga
        if abs(math.sin(pitch)) > 0.94:
            return

        # (re)construye si la vista cambió
        if self._grid_para_vista != (ancho, alto):
            self._grid_para_vista = (ancho, alto)
            self._grid_vertices = None
            self._grid_indices = None

        if self._grid_vertices is None:
            # Líneas menores, principales cada diez divisiones y ejes de color.
            vertices, colores, indices = [], [], []
            i = 0
            for coord in range(-max_celdas, max_celdas + 1):
                y = coord * celda
                vertices.extend([(-semiancho, y, suelo_z), (semiancho, y, suelo_z)])
                if coord == 0:
                    color = (0.72, 0.24, 0.27, 0.95)  # eje X
                elif coord % 10 == 0:
                    color = (0.34, 0.43, 0.60, 0.82)
                else:
                    color = (0.27, 0.34, 0.46, 0.72)
                colores.extend([color, color])
                indices.extend([i, i + 1])
                i += 2
            for coord in range(-max_celdas, max_celdas + 1):
                x = coord * celda
                vertices.extend([(x, -semiancho, suelo_z), (x, semiancho, suelo_z)])
                if coord == 0:
                    color = (0.22, 0.68, 0.36, 0.95)  # eje Y
                elif coord % 10 == 0:
                    color = (0.34, 0.43, 0.60, 0.82)
                else:
                    color = (0.27, 0.34, 0.46, 0.72)
                colores.extend([color, color])
                indices.extend([i, i + 1])
                i += 2
            self._grid_vertices = np.array(vertices, dtype=np.float32)
            self._grid_colores = np.array(colores, dtype=np.float32)
            self._grid_indices = np.array(indices, dtype=np.uint32)

        # misma transformación de cámara que el modelo
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        escala = self._escala(ancho, alto)
        tope = self.zoom_tope(ancho, alto)
        zoom_eff = min(zoom, tope)
        focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
        distancia_camara = focal / (escala * zoom_eff)
        # centro = pivote; la rejilla es relativa a él
        dx = self._grid_vertices[:, 0] - punto_mira[0]
        dy = self._grid_vertices[:, 1] - punto_mira[1]
        dz = self._grid_vertices[:, 2] - punto_mira[2]
        fondo = sy * dx + cy * dy
        derecha = cy * dx - sy * dy
        profundidad = sp * dz + cp * fondo
        pan_x, pan_y = pan[0] / (escala * zoom_eff), pan[1] / (escala * zoom_eff)

        arriba = cp * dz - sp * fondo
        gx = derecha + pan_x
        gy = arriba - pan_y
        gz = -(distancia_camara + profundidad)

        grid_ojo = np.empty((len(self._grid_vertices), 3), dtype=np.float32)
        grid_ojo[:, 0] = gx
        grid_ojo[:, 1] = gy
        grid_ojo[:, 2] = gz
        grid_ojo = np.ascontiguousarray(grid_ojo)
        # Difumina los extremos para que la rejilla parezca prolongarse en perspectiva.
        colores = self._grid_colores.copy()
        distancia = np.linalg.norm(self._grid_vertices[:, :2], axis=1) / semiancho
        colores[:, 3] *= np.maximum(0.35, 1.0 - 0.65 * distancia**2)
        gl.mascara_profundidad(1)
        gl.funcion_profundidad(GL_LESS)
        gl.activar(GL_DEPTH_TEST)
        gl.mascara_profundidad(0)
        gl.ancho_linea(1.0)
        gl.activar(GL_BLEND)
        gl.mezcla(GL_BLEND_SRC_ALPHA, GL_BLEND_DST_ONE_MINUS_SRC_ALPHA)
        gl.desactivar(GL_TEXTURE_2D)
        gl.activar_array(GL_VERTEX_ARRAY)
        gl.activar_array(GL_COLOR_ARRAY)
        gl.desactivar_array(GL_TEXTURE_COORD_ARRAY)
        gl.punteros(3, GL_FLOAT, 0, grid_ojo.ctypes.data)
        gl.punteros_color(4, GL_FLOAT, 0, colores.ctypes.data)
        gl.dibujar(
            GL_LINES,
            len(self._grid_indices),
            GL_UNSIGNED_INT,
            self._grid_indices.ctypes.data,
        )
        gl.desactivar(GL_BLEND)
        gl.mascara_profundidad(1)

    def _preparar(self, ancho, alto):
        gl = self.contexto
        gl.viewport(0, 0, ancho, alto)
        if self.ancho == ancho and self.alto == alto and self.lectura is not None:
            gl.bind_framebuffer(GL_FRAMEBUFFER, self.filtros.value)
            return
        gl.gen_framebuffer(1, ctypes.byref(self.filtros))
        gl.bind_framebuffer(GL_FRAMEBUFFER, self.filtros.value)
        for punto, formato in (
            (GL_COLOR_ATTACHMENT0, GL_RGBA),
            (GL_DEPTH_ATTACHMENT, GL_DEPTH_COMPONENT24),
        ):
            buffer = ctypes.c_uint()
            gl.gen_renderbuffer(1, ctypes.byref(buffer))
            gl.bind_renderbuffer(GL_RENDERBUFFER, buffer.value)
            gl.almacenar(GL_RENDERBUFFER, formato, ancho, alto)
            gl.adjuntar(GL_FRAMEBUFFER, punto, GL_RENDERBUFFER, buffer.value)
        if gl.comprobar(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            raise SinOpenGL("the framebuffer is incomplete")
        self.ancho, self.alto = ancho, alto
        self.lectura = np.empty((alto, ancho, 4), dtype=np.uint8)
        self._grid_para_vista = (ancho, alto)  # fuerza recalculo de la rejilla

    # -- cámara ---------------------------------------------------------------
    def _camara(self, ancho, alto, yaw, pitch, zoom, pan=(0.0, 0.0), camara=None):
        """Transforma vértices a coordenadas de cámara para proyección perspectiva."""
        cy, sy = np.cos(yaw), np.sin(yaw)
        cp, sp = np.cos(pitch), np.sin(pitch)
        escala = self._escala(ancho, alto)
        centro = self._punto_mira(camara)
        dx = self.posiciones[:, 0] - centro[0]
        dy = self.posiciones[:, 1] - centro[1]
        dz = self.posiciones[:, 2] - centro[2]
        fondo = sy * dx + cy * dy
        derecha = cy * dx - sy * dy
        arriba = cp * dz - sp * fondo
        profundidad = sp * dz + cp * fondo
        zoom = min(zoom, self.zoom_tope(ancho, alto))
        focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
        distancia_camara = focal / (escala * zoom)
        pan_x, pan_y = pan[0] / (escala * zoom), pan[1] / (escala * zoom)
        ojo = np.empty_like(self.posiciones)
        ojo[:, 0] = derecha + pan_x
        ojo[:, 1] = arriba - pan_y
        ojo[:, 2] = -(distancia_camara + profundidad)
        limite = max(1.0, float(np.max(-ojo[:, 2])) + 1.0)
        return np.ascontiguousarray(ojo, dtype=np.float32), limite

    # -- render ---------------------------------------------------------------
    def render(self, ancho, alto, yaw, pitch, zoom, pan=(0.0, 0.0), camara=None):
        if ancho < 2 or alto < 2:
            return None
        if camara is not None:
            camara.set_view(self.pivote, self._escala(ancho, alto))
            camara.zoom_tope = self.zoom_tope(ancho, alto)
        gl = self.contexto
        self._preparar(ancho, alto)
        centro = self._punto_mira(camara)
        ojo, limite = self._camara(ancho, alto, yaw, pitch, zoom, pan, camara)
        gl.limpiar_color(*(c / 255.0 for c in self.fondo), 1.0)
        gl.limpiar(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.activar(GL_DEPTH_TEST)

        # Aplicar una proyección de perspectiva real para mantener la interpolación
        # correcta de texturas incluso cuando la cámara está muy cerca.
        gl.matrices(GL_PROJECTION)
        gl.identidad()
        top = PLANO_CERCANO * math.tan(math.radians(FOV) * 0.5)
        right = top * ancho / alto
        if self.mostrar_grid:
            # El plano lejano debe incluir las esquinas de la rejilla, no solo
            # el modelo; de lo contrario OpenGL la recorta antes de su límite visual.
            radio = max(1.0, self.radio)
            semiancho = GRID_MAX_CELDAS * GRID_CELDA_RADIO * radio
            suelo_z = self._altura_suelo_grid()
            dz = suelo_z - centro[2]
            cy, sy = math.cos(yaw), math.sin(yaw)
            cp, sp = math.cos(pitch), math.sin(pitch)
            profundidad_max = max(
                sp * dz + cp * (sy * (x - centro[0]) + cy * (y - centro[1]))
                for x in (-semiancho, semiancho)
                for y in (-semiancho, semiancho)
            )
            escala = self._escala(ancho, alto)
            focal = (alto / 2.0) / math.tan(math.radians(FOV) * 0.5)
            distancia_camara = focal / (escala * min(zoom, self.zoom_tope(ancho, alto)))
            limite = max(limite, distancia_camara + profundidad_max + 1.0)
        gl.perspectiva(-right, right, -top, top, PLANO_CERCANO, limite)
        gl.matrices(GL_MODELVIEW)
        gl.identidad()

        # ---- rejilla de suelo ----
        if self.mostrar_grid:
            self._dibujar_grid(
                gl, ancho, alto, ojo, limite, yaw, pitch, zoom, pan, centro
            )
        if self.sin_mallas:
            # No hay malla que dibujar pero el fotograma se devuelve igual: es lo
            # que lee el visor de mapas cuando todas las piezas estan apagadas, y
            # sin este paso se quedaria pegada la imagen anterior.
            gl.fin()
            return self._leer(ancho, alto)

        gl.activar(GL_TEXTURE_2D)
        gl.activar(GL_CULL_FACE)
        gl.cara_cull(GL_BACK)
        # Hay modelos que dibujan sus caras al reves y se ven huecos con el
        # recorte de caras traseras; para ellos se apaga en toda la escena.
        sin_culling = self.sin_culling
        if sin_culling:
            gl.desactivar(GL_CULL_FACE)
        for array in (GL_VERTEX_ARRAY, GL_COLOR_ARRAY, GL_TEXTURE_COORD_ARRAY):
            gl.activar_array(array)
        gl.punteros(3, GL_FLOAT, 0, ojo.ctypes.data)
        gl.punteros_uv(2, GL_FLOAT, 0, self.UV.ctypes.data)
        gl.punteros_color(4, GL_FLOAT, 0, self.colores.ctypes.data)
        primero, recortados, mezclados = [], [], []
        sin_recorte = self.sin_recorte
        for indice, (draw, mode) in enumerate(zip(self.dibujos, self.alpha_modes)):
            if indice in self.ocultas_mallas:
                continue
            indices, textura, alfa, es_peinado = draw
            group = (
                mezclados
                if mode == "BLEND"
                else recortados if mode == "MASK" else primero
            )
            group.append((indices, textura, es_peinado, indice))
        culling_activo = not sin_culling

        def dibujar_grupos(grupos):
            nonlocal culling_activo
            for indices, textura, es_peinado, indice in grupos:
                # Tres cosas se dibujan sin recortar: el pelo, que son triangulos
                # sueltos, y las piezas marcadas a mano, que son de dos caras.
                dos_caras = es_peinado or indice in sin_recorte
                if not sin_culling:
                    if dos_caras and culling_activo:
                        gl.desactivar(GL_CULL_FACE)
                        culling_activo = False
                    elif not dos_caras and not culling_activo:
                        gl.activar(GL_CULL_FACE)
                        culling_activo = True
                gl.bind_textura(GL_TEXTURE_2D, textura)
                second = self.texture_layers[indice]
                if second:
                    if not hasattr(gl, "terrain_blend"):
                        from ts2psp.rendering.texture_blend import TextureBlendProgram

                        gl.terrain_blend = TextureBlendProgram()
                    gl.terrain_blend.begin(
                        gl,
                        second,
                        self._blend_coords,
                        self._factores_iluminacion_mapa,
                    )
                gl.dibujar(
                    GL_TRIANGLES, len(indices), GL_UNSIGNED_INT, indices.ctypes.data
                )
                if second:
                    gl.terrain_blend.end(gl)

        # la textura se enlaza justo antes de cada dibujo: enlazarlas todas antes y
        # dibujar después dejaría activa solo la última
        dibujar_grupos(primero)
        if not culling_activo and not sin_culling:
            gl.activar(GL_CULL_FACE)
            culling_activo = True
        if recortados:
            gl.desactivar(GL_BLEND)
            gl.activar(GL_ALPHA_TEST)
            gl.funcion_alfa(GL_GREATER, 0.5)
            dibujar_grupos(recortados)
            if not culling_activo and not sin_culling:
                gl.activar(GL_CULL_FACE)
            gl.desactivar(GL_ALPHA_TEST)
        if mezclados:
            # Draw glass, water and shadows after opaque geometry, back to front.
            mezclados.sort(key=lambda group: float(ojo[group[0], 2].mean()))
            gl.activar(GL_BLEND)
            gl.mezcla(GL_BLEND_SRC_ALPHA, GL_BLEND_DST_ONE_MINUS_SRC_ALPHA)
            gl.activar(GL_ALPHA_TEST)
            gl.funcion_alfa(GL_GREATER, 0.001)
            gl.mascara_profundidad(0)
            dibujar_grupos(mezclados)
            gl.mascara_profundidad(1)
            gl.desactivar(GL_ALPHA_TEST)
            gl.desactivar(GL_BLEND)
        gl.fin()
        return self._leer(ancho, alto)

    def _leer(self, ancho, alto):
        """Trae el fotograma del búfer fuera de pantalla a una imagen."""
        gl = self.contexto
        gl.leer(0, 0, ancho, alto, GL_RGBA, GL_UNSIGNED_BYTE, self.lectura.ctypes.data)
        # glReadPixels devuelve las filas de abajo arriba. frombytes y no frombuffer:
        # frombuffer no copia y devolvería una vista sobre el búfer de lectura, así que
        # dos fotogramas seguidos serían el mismo objeto y cambiarían al volver a pintar
        return Image.frombytes("RGB", (ancho, alto), self.lectura, "raw", "RGBX", 0, -1)

    def proyectar(
        self, punto, ancho, alto, yaw, pitch, zoom, pan=(0.0, 0.0), camara=None
    ):
        """Proyecta un punto del modelo a píxeles, con la misma cámara del render."""
        cy, sy = np.cos(yaw), np.sin(yaw)
        cp, sp = np.cos(pitch), np.sin(pitch)
        escala = self._escala(ancho, alto)
        centro = self._punto_mira(camara)
        x, y, z = np.asarray(punto, dtype=np.float64) - centro
        fondo = sy * x + cy * y
        profundidad = sp * z + cp * fondo
        k = escala_profundidad(profundidad, escala, zoom, alto)
        ex, ey = cy * x - sy * y, cp * z - sp * fondo
        return (ancho / 2 + pan[0] + k * ex, alto / 2 + pan[1] - k * ey)


_escenas = {}
_fallo = None


def disponible():
    """True si se puede crear un contexto OpenGL acelerado.

    El resultado se recuerda: si falla, no se reintenta en cada movimiento del ratón.
    """
    global _fallo
    if _fallo is not None:
        return False
    try:
        return Contexto.compartido() is not None
    except Exception as exc:
        _fallo = exc
        return False


# Modelos que hay que dibujar sin recortar las caras traseras: el suyo llega
# con las caras invertidas y se le ve el interior. La clave lleva el nombre del
# personaje, por eso se decide aqui y no por pieza.
SIN_CULLING = ("doctordominion",)


def escena(
    clave,
    piezas,
    texturas=None,
    tipo="personaje",
    fondo=(27, 31, 42),
    culling=False,
    sin_framing=(),
    ruta_escena="",
):
    """Escena cacheada para `clave`. Avisa con SinOpenGL si la GPU no está disponible.

    `culling` recorta las caras traseras. Los props vienen con materiales de dos
    caras y se dibujan sin recortar; un nivel se dibuja con las normales hacia
    fuera, y sin recortar se ve el interior de cada muro desde el otro lado.

    `sin_framing` son los nombres de las piezas que se dibujan pero no cuentan
    para el encuadre: el cielo de los mapas, que es una esfera de 528 de lado y
    se llevaria la camara tan lejos que el nivel quedaria en un punto.

    El recorte se fija en cada llamada y no solo al crear la escena, para que la
    clave pueda seguir siendo la de siempre: hay sitios que vacian la cache
    nombrando la clave a mano y, si `culling` se metiera dentro, se quedarian
    con la escena del ajuste anterior.
    """
    if _fallo is not None:
        raise SinOpenGL(str(_fallo))
    if clave not in _escenas:
        _escenas[clave] = Escena(
            piezas, texturas, tipo, fondo, sin_framing, ruta_escena
        )
    escena_actual = _escenas[clave]
    nombre = clave[1] if isinstance(clave, (tuple, list)) and len(clave) > 1 else clave
    escena_actual.sin_culling = (tipo == "prop" and not culling) or str(
        nombre
    ) in SIN_CULLING
    return escena_actual


def ahora_ms():
    """Reloj monotónico en milisegundos."""
    return time.perf_counter() * 1000.0


def faltan_para(ultimo_frame, intervalo):
    """Milisegundos que faltan para poder dibujar otro frame (0 si ya toca).

    Los visores lo usan para limitar el refresco SIN posponerlo: si cada movimiento
    del ratón cancelara el temporizador y lo reprogramara, al girar deprisa el frame
    no llegaría a dispararse nunca y la cámara iría a trompicones.
    """
    return max(0, int(intervalo - (ahora_ms() - ultimo_frame)))


def forget(clave):
    _escenas.pop(clave, None)


def forget_all():
    _escenas.clear()
