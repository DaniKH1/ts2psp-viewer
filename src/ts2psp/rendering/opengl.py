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
        posiciones, uvs, colores, indices, tramos = [], [], [], [], []
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
        for ruta, parte in self._normalizar(piezas):
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
            tiene_uv = all(v[2] is not None for v in parte["vertices"])
            vertex_alpha = False
            for vertice in parte["vertices"]:
                posiciones.append(tuple(v * escala_pos for v in vertice[0]))
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
                if not normaliza_uv and vertice[3] is not None:
                    rgba = tuple(vertice[3])
                    rgba = rgba + (1.0,) if len(rgba) == 3 else rgba
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
            transparent = tramos[-1][3]
            modo = parte.get("alpha_mode")
            if self.tipo != "prop":
                # en los personajes solo se mezcla si el material lo pide; el
                # resto de las piezas con alfa van a recorte
                modo = "BLEND" if modo == "BLEND" else ("MASK" if transparent else "OPAQUE")
            elif not modo and transparent:
                # los props respetan el grupo que les asigno el material. Sin
                # material resuelto se cae en mezcla, que es lo que se hacia
                # antes con cualquier textura que tuviera un pixel de alfa bajo
                modo = "BLEND"
            self.alpha_modes.append(modo or "OPAQUE")
        if not posiciones or not indices:
            raise SinOpenGL("the scene has no geometry")
        self.posiciones = np.ascontiguousarray(posiciones, dtype=np.float32)
        self.UV = np.ascontiguousarray(uvs, dtype=np.float32)
        self.colores = np.ascontiguousarray(colores, dtype=np.float32) / 255.0
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
        for nombre, (inicio, cuenta) in zip(
            self.nombres, self.tramos_vertices
        ):
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
                gl.dibujar(
                    GL_TRIANGLES, len(indices), GL_UNSIGNED_INT, indices.ctypes.data
                )

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
        _escenas[clave] = Escena(piezas, texturas, tipo, fondo, sin_framing)
    escena_actual = _escenas[clave]
    nombre = (
        clave[1] if isinstance(clave, (tuple, list)) and len(clave) > 1 else clave
    )
    escena_actual.sin_culling = (
        tipo == "prop" and not culling
    ) or str(nombre) in SIN_CULLING
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
