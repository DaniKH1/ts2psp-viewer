"""Textures, fonts and ttk styling."""

import ctypes
import math
import numpy as np
from tkinter import font as tkfont, ttk
from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
    ImageTk,
)
from ts2psp.paths import resource_path

APP_FONT_FAMILY = "ExpressDecoGothicSSK"
APP_FONT_PATH = resource_path("assets/fonts/exprdgs.ttf")
SHAPE_TEXT_GRADIENT = ((0.0, (0x00, 0x00, 0x81)), (1.0, (0x00, 0x5D, 0x9E)))
PREVIEW_BORDER_COLOR = "#3c4a86"
PREVIEW_BORDER_WIDTH = 3
PREVIEW_CORNER_RADIUS = 14
APP_ICON_ICO_PATH = resource_path("assets/Icon.ico")
UI_ASSET_PATH = resource_path("assets/ui")
BUTTON_NORMAL_LEFT_CAP_PATH = UI_ASSET_PATH / "button_normal_left_cap.png"
BUTTON_NORMAL_RIGHT_CAP_PATH = UI_ASSET_PATH / "button_normal_right_cap.png"
BUTTON_NORMAL_CENTER_PATH = UI_ASSET_PATH / "button_normal_center.png"
BUTTON_ACTIVE_LEFT_CAP_PATH = UI_ASSET_PATH / "button_active_left_cap.png"
BUTTON_ACTIVE_RIGHT_CAP_PATH = UI_ASSET_PATH / "button_active_right_cap.png"
BUTTON_ACTIVE_CENTER_PATH = UI_ASSET_PATH / "button_active_center.png"
BUTTON_HIGHLIGHT_LEFT_CAP_PATH = UI_ASSET_PATH / "button_highlight_left_cap.png"
BUTTON_HIGHLIGHT_RIGHT_CAP_PATH = UI_ASSET_PATH / "button_highlight_right_cap.png"
BUTTON_HIGHLIGHT_CENTER_PATH = UI_ASSET_PATH / "button_highlight_center.png"
TAB_BUTTON_LEFT_CAP_PATH = UI_ASSET_PATH / "tab_button_left_cap.png"
TAB_BUTTON_RIGHT_CAP_PATH = UI_ASSET_PATH / "tab_button_right_cap.png"
TAB_BUTTON_CENTER_PATH = UI_ASSET_PATH / "tab_button_center_fill.png"
SHAPE_SLIDER_THUMB_PATH = UI_ASSET_PATH / "shape_slider_thumb.png"
SHAPE_SLIDER_TROUGH_PATH = UI_ASSET_PATH / "shape_slider_trough.png"
SHAPE_KEY_BUTTON_PATH = UI_ASSET_PATH / "shape_key_button.png"
SHAPE_KEY_UNSELECTED_PATH = UI_ASSET_PATH / "shape_key_button_unselected.png"
SEPARATOR_PATH = UI_ASSET_PATH / "separator.png"
CHARACTER_SEARCH_PATH = UI_ASSET_PATH / "character_search.png"
SCROLL_THUMB_PATH = UI_ASSET_PATH / "scroll_thumb.png"
SCROLL_TROUGH_CAP_TOP_PATH = UI_ASSET_PATH / "scroll_trough_cap_top.png"
SCROLL_TROUGH_CAP_BOTTOM_PATH = UI_ASSET_PATH / "scroll_trough_cap_bottom.png"
SCROLL_TROUGH_CENTER_PATH = UI_ASSET_PATH / "scroll_trough_center.png"
LIST_PANEL_PATH = UI_ASSET_PATH / "list_panel.png"
# Las etiquetas de las pestañas, para construir su textura. La anchura de cada
# textura sale de lo que mide el texto, así que van por nombre y no por posición:
# si la lista se queda desfasada con las pestañas que hay, al resaltar sale la
# textura de otra y la pestaña cambia de ancho al pasar el ratón.
NOTEBOOK_TAB_LABELS = ("Characters", "Props", "Maps")

# Ancho en pixeles del trough del deslizador, uno por cada sitio donde se usa.
# El trough es una imagen de ancho fijo: si el widget mide otra cosa, ttk la
# estira o la recorta y el remate redondeado de un lado se pierde. No se puede
# arreglar con `border`, porque ese margen es el relleno de los hijos del
# elemento y el pulgar es hijo del trough: con margen a los lados el pulgar
# dejaria de llegar al extremo. Asi que la imagen se hace del ancho justo.
ANCHO_TROUGH_SHAPES = 182
ANCHO_TROUGH_CLIPS = 272
ANCHO_TROUGH_PROPS = 250


def _register_sims_font(root):
    if not APP_FONT_PATH.is_file():
        raise FileNotFoundError(f"Application font not found: {APP_FONT_PATH}")
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    add_font = gdi32.AddFontResourceExW
    add_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
    add_font.restype = ctypes.c_int
    if not add_font(str(APP_FONT_PATH), 0x10, None):
        raise OSError(ctypes.get_last_error(), f"Could not load font {APP_FONT_PATH}")
    if APP_FONT_FAMILY not in tkfont.families(root):
        gdi32.RemoveFontResourceExW(str(APP_FONT_PATH), 0x10, None)
        raise RuntimeError(f"Tk did not detect font {APP_FONT_FAMILY}")
    root._sims_font_registered = True
    root._sims_font_family = APP_FONT_FAMILY
    return APP_FONT_FAMILY


def _unregister_sims_font(root):
    if not getattr(root, "_sims_font_registered", False):
        return
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    remove_font = gdi32.RemoveFontResourceExW
    remove_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
    remove_font.restype = ctypes.c_int
    if not remove_font(str(APP_FONT_PATH), 0x10, None):
        raise OSError(ctypes.get_last_error(), f"Could not unload font {APP_FONT_PATH}")
    root._sims_font_registered = False


def _round_window_corners(window, radius):
    """Recorta las esquinas de una ventana sin marco con SetWindowRgn.

    Sin marco Tk no dibuja nada redondeado, así que se recorta la región de la
    ventana con un rectángulo redondeado. Devuelve False si no se pudo, para
    que la ventana siga siendo usable aunque el recorte falle.
    """
    width, height = window.winfo_width(), window.winfo_height()
    if width < 2 or height < 2:
        return False
    try:
        user32 = ctypes.windll.user32
        # CreateRoundRectRgn vive en gdi32, no en user32: buscarlo en user32
        # lanza AttributeError y el recorte se queda sin aplicar.
        gdi32 = ctypes.windll.gdi32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = (ctypes.c_void_p,)
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi32.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.SetWindowRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
        child = ctypes.c_void_p(window.winfo_id())
        parent = user32.GetParent(child)
        hwnd = parent if parent else child
        # +1 de alto y ancho: CreateRoundRectRgn excluye el borde inferior y
        # derecho, y sin eso se recorta una fila y una columna de píxeles.
        region = gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        if not region:
            return False
        # Con wParam=True el sistema pasa a ser dueño de la región y la libera.
        return bool(user32.SetWindowRgn(hwnd, region, True))
    except Exception:
        return False


def _install_sims_theme(root):
    font_family = _register_sims_font(root)
    texture_paths = (
        BUTTON_NORMAL_LEFT_CAP_PATH,
        BUTTON_NORMAL_RIGHT_CAP_PATH,
        BUTTON_NORMAL_CENTER_PATH,
        BUTTON_ACTIVE_LEFT_CAP_PATH,
        BUTTON_ACTIVE_RIGHT_CAP_PATH,
        BUTTON_ACTIVE_CENTER_PATH,
        BUTTON_HIGHLIGHT_LEFT_CAP_PATH,
        BUTTON_HIGHLIGHT_RIGHT_CAP_PATH,
        BUTTON_HIGHLIGHT_CENTER_PATH,
        TAB_BUTTON_LEFT_CAP_PATH,
        TAB_BUTTON_RIGHT_CAP_PATH,
        TAB_BUTTON_CENTER_PATH,
        SHAPE_SLIDER_THUMB_PATH,
        SHAPE_SLIDER_TROUGH_PATH,
        SHAPE_KEY_BUTTON_PATH,
        SHAPE_KEY_UNSELECTED_PATH,
        SEPARATOR_PATH,
        CHARACTER_SEARCH_PATH,
        SCROLL_THUMB_PATH,
        SCROLL_TROUGH_CAP_TOP_PATH,
        SCROLL_TROUGH_CAP_BOTTOM_PATH,
        SCROLL_TROUGH_CENTER_PATH,
        LIST_PANEL_PATH,
    )
    for texture_path in texture_paths:
        if not texture_path.is_file():
            raise FileNotFoundError(f"Interface texture not found: {texture_path}")
    colors = {
        "background": "#aeb8e5",
        "panel": "#c5ccef",
        "panel_dark": "#8999cf",
        "foreground": "#182658",
        "muted": "#3e4c7d",
        "selection": "#77baf0",
    }
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        font=(font_family, 9),
        background=colors["background"],
        foreground=colors["foreground"],
    )
    style.configure("TFrame", background=colors["background"])
    # La cabecera de idioma/ISO se superpone a la fila del Notebook; comparte
    # el color del lienzo y no dibuja una banda propia tras los botones.
    style.configure("Toolbar.TFrame", background=colors["background"], borderwidth=0)
    style.configure(
        "TLabel", background=colors["background"], foreground=colors["foreground"]
    )
    style.configure(
        "TLabelframe",
        background=colors["background"],
        foreground=colors["foreground"],
        bordercolor=colors["panel_dark"],
    )
    style.configure(
        "TLabelframe.Label",
        background=colors["background"],
        foreground=colors["foreground"],
        font=(font_family, 9, "bold"),
    )
    style.configure("TNotebook", background=colors["background"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        padding=0,
        background=colors["panel_dark"],
        foreground=colors["foreground"],
        font=(font_family, 9, "bold"),
        borderwidth=0,
        focuscolor="",
        embossed=False,
    )
    style.map(
        "TNotebook.Tab",
        foreground=[("selected", "#ffffff"), ("active", colors["foreground"])],
    )
    style.configure(
        "TEntry",
        fieldbackground="#b5bee9",
        foreground=colors["foreground"],
        insertcolor=colors["foreground"],
        bordercolor=colors["panel_dark"],
        lightcolor=colors["panel"],
        darkcolor=colors["panel_dark"],
    )
    style.configure(
        "CharacterSearch.TEntry",
        padding=(7, 4),
        foreground=colors["foreground"],
        insertcolor=colors["foreground"],
    )
    style.configure(
        "TCombobox",
        fieldbackground="#f0f2ff",
        foreground=colors["foreground"],
        arrowcolor=colors["foreground"],
        bordercolor=colors["panel_dark"],
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "#b5bee9")],
        foreground=[("readonly", colors["foreground"])],
    )
    style.configure(
        "TCheckbutton",
        background=colors["background"],
        foreground=colors["foreground"],
        padding=3,
    )
    style.map(
        "TCheckbutton",
        background=[("active", colors["background"])],
        foreground=[("disabled", colors["muted"])],
    )
    # clam pinta el indicador del radiobutton sobre un indicatorbackground blanco
    # puro, y eso se ve como un recuadro blanco alrededor del circulo. Se pone
    # del color del panel para que solo se vea el punto.
    style.configure(
        "TRadiobutton",
        background=colors["background"],
        foreground=colors["foreground"],
        indicatorbackground=colors["background"],
        indicatorforeground=colors["foreground"],
        padding=3,
    )
    style.map(
        "TRadiobutton",
        background=[("active", colors["background"])],
        indicatorforeground=[("disabled", colors["muted"])],
        foreground=[("disabled", colors["muted"])],
    )
    style.configure(
        "TScale", background=colors["background"], troughcolor=colors["panel_dark"]
    )
    style.configure(
        "TScrollbar",
        background=colors["background"],
        troughcolor=colors["background"],
        bordercolor=colors["background"],
        arrowcolor=colors["foreground"],
    )
    style.configure("Vertical.TScrollbar", width=18)
    style.configure("Horizontal.TScrollbar", height=18)
    style.configure(
        "Treeview",
        background="#eef0ff",
        fieldbackground="#eef0ff",
        foreground=colors["foreground"],
        rowheight=24,
        bordercolor=colors["panel_dark"],
    )
    style.configure(
        "Treeview.Heading",
        background=colors["panel_dark"],
        foreground=colors["foreground"],
        font=(font_family, 9, "bold"),
    )
    style.map(
        "Treeview",
        background=[("selected", colors["selection"])],
        foreground=[("selected", colors["foreground"])],
    )
    root.option_add("*Listbox.font", (font_family, 9))
    root.option_add("*Menu.font", (font_family, 9))
    root.option_add("*Listbox.background", "#eef0ff")
    root.option_add("*Listbox.foreground", colors["foreground"])
    root.option_add("*Listbox.selectBackground", colors["selection"])
    root.option_add("*Listbox.selectForeground", colors["foreground"])
    root.option_add("*Listbox.highlightBackground", colors["panel_dark"])
    root.option_add("*Listbox.highlightColor", colors["panel_dark"])

    def vertical_gradient(height, stops):
        """Degradado vertical RGB de 1x`height`, interpolando `stops`.

        `stops` son pares (posición 0..1, (r, g, b)) ordenados de arriba abajo.
        """
        gradient = Image.new("RGB", (1, max(1, height)))
        pixels = gradient.load()
        stops = sorted(stops)
        for y in range(gradient.height):
            ratio = y / (gradient.height - 1) if gradient.height > 1 else 0.0
            lower, upper = stops[0], stops[-1]
            for index in range(len(stops) - 1):
                if stops[index][0] <= ratio <= stops[index + 1][0]:
                    lower, upper = stops[index], stops[index + 1]
                    break
            span = (upper[0] - lower[0]) or 1
            blend = (ratio - lower[0]) / span
            pixels[0, y] = tuple(
                round(a + (b - a) * blend) for a, b in zip(lower[1], upper[1])
            )
        return gradient

    def shape_preset_raster(text, state):
        """Dibuja el preset como imagen PIL (sin envolverla en Tk)."""
        size = 40
        scale = 3
        # Los estados sin seleccionar (normal, hover, disabled) usan su propia
        # textura; los seleccionados conservan la original con el anillo verde.
        base = (
            shape_key_button if state.startswith("selected") else shape_key_unselected
        )
        image = base.resize((size * scale, size * scale), Image.Resampling.LANCZOS)
        if state == "disabled":
            gray = ImageOps.grayscale(image)
            image = Image.merge("RGBA", (gray, gray, gray, image.getchannel("A")))
            image.putalpha(
                image.getchannel("A").point(lambda value: round(value * 0.55))
            )
        elif state != "normal":
            brightness = {"hover": 1.13, "selected": 1.16, "selected_hover": 1.28}[
                state
            ]
            image = ImageEnhance.Brightness(image).enhance(brightness)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(APP_FONT_PATH), 20 * scale)
        # La única fuente es Regular y no tiene variantes, así que la negrita
        # es sintética: se engorda el glifo con un contorno del mismo color.
        # El ancho del trazo entra también en textbbox para que el centrado
        # accounted del engorde.
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=scale)
        position = (
            (size * scale - (bounds[2] - bounds[0])) / 2 - bounds[0],
            (size * scale - (bounds[3] - bounds[1])) / 2 - bounds[1],
        )
        # Sombra difusa: misma silueta engruesida, desenfocada.
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text(
            position,
            text,
            font=font,
            fill=(20, 43, 98, 170),
            stroke_width=scale,
            stroke_fill=(20, 43, 98, 170),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(scale * 1.6))
        image.alpha_composite(shadow)
        draw = ImageDraw.Draw(image)
        canvas = size * scale
        if state.startswith("selected"):
            # Seleccionado: blanco liso, tal cual, sin degradado.
            draw.text(
                position,
                text,
                font=font,
                fill="#e6f8e4",
                stroke_width=scale,
                stroke_fill="#e6f8e4",
            )
        elif state == "disabled":
            # Desactivado ya comunica su estado con el gris de la textura, así
            # que el número se queda en un tono plano apagado.
            draw.text(
                position,
                text,
                font=font,
                fill="#5c6789",
                stroke_width=scale,
                stroke_fill="#5c6789",
            )
        else:
            # Relleno con degradado vertical: #000081 arriba y #005d9e abajo.
            # El glifo engruesido se pinta en una máscara y el degradado se le
            # pasa a través, en vez de usar un color liso. El degradado se mide
            # sobre la altura del glifo, no sobre el lienzo entero, o el glifo
            # solo vería una parte de la rampa.
            mask = Image.new("L", (canvas, canvas), 0)
            ImageDraw.Draw(mask).text(
                position, text, font=font, fill=255, stroke_width=scale, stroke_fill=255
            )
            top = round(position[1] + bounds[1])
            height = max(1, bounds[3] - bounds[1])
            gradient = (
                vertical_gradient(height, SHAPE_TEXT_GRADIENT)
                .resize((canvas, height))
                .convert("RGBA")
            )
            layer = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            # El parche del degradado es opaco, así que basta con pegarlo y
            # ponerle la máscara del glifo como canal alfa.
            layer.paste(gradient, (0, top))
            layer.putalpha(mask)
            image.alpha_composite(layer)
        return image.resize((size, size), Image.Resampling.LANCZOS)

    def shape_preset_image(text, state):
        return ImageTk.PhotoImage(shape_preset_raster(text, state), master=root)

    def load_texture(path, expected_size):
        image = Image.open(path).convert("RGBA")
        if image.size != expected_size:
            raise ValueError(f"Unexpected size for {path.name}: {image.size}")
        return image

    def scroll_trough_texture():
        """Monta el canal del scrollbar con capirotes y tramo central.

        Tk mosaiquea (no escala) el interior de los elementos de imagen, así
        que el centro se compone solo con filas uniformes: los capirotes de
        12 px quedan fijos y el canal estira sin costuras ni reflejos.
        """
        cap_top = load_texture(SCROLL_TROUGH_CAP_TOP_PATH, (18, 15))
        cap_bottom = load_texture(SCROLL_TROUGH_CAP_BOTTOM_PATH, (18, 15))
        center = load_texture(SCROLL_TROUGH_CENTER_PATH, (18, 123))
        image = Image.new("RGBA", (18, 153), (0, 0, 0, 0))
        image.paste(cap_top, (0, 0))
        image.paste(center, (0, 15))
        image.paste(cap_bottom, (0, 138))
        return image

    button_normal_left_cap = load_texture(BUTTON_NORMAL_LEFT_CAP_PATH, (35, 79))
    button_normal_right_cap = load_texture(BUTTON_NORMAL_RIGHT_CAP_PATH, (35, 79))
    button_normal_center = load_texture(BUTTON_NORMAL_CENTER_PATH, (439, 79))
    button_active_left_cap = load_texture(BUTTON_ACTIVE_LEFT_CAP_PATH, (35, 79))
    button_active_right_cap = load_texture(BUTTON_ACTIVE_RIGHT_CAP_PATH, (36, 79))
    button_active_center = load_texture(BUTTON_ACTIVE_CENTER_PATH, (439, 77))
    button_highlight_left_cap = load_texture(BUTTON_HIGHLIGHT_LEFT_CAP_PATH, (35, 79))
    button_highlight_right_cap = load_texture(BUTTON_HIGHLIGHT_RIGHT_CAP_PATH, (35, 79))
    button_highlight_center = load_texture(BUTTON_HIGHLIGHT_CENTER_PATH, (439, 79))
    tab_button_left_cap = load_texture(TAB_BUTTON_LEFT_CAP_PATH, (48, 90))
    tab_button_right_cap = load_texture(TAB_BUTTON_RIGHT_CAP_PATH, (48, 90))
    tab_button_center = load_texture(TAB_BUTTON_CENTER_PATH, (415, 90))
    shape_slider_thumb = load_texture(SHAPE_SLIDER_THUMB_PATH, (27, 15))
    shape_slider_trough = load_texture(SHAPE_SLIDER_TROUGH_PATH, (346, 18))
    shape_key_button = load_texture(SHAPE_KEY_BUTTON_PATH, (28, 28))
    shape_key_unselected = load_texture(SHAPE_KEY_UNSELECTED_PATH, (28, 28))
    separator = load_texture(SEPARATOR_PATH, (127, 3))
    character_search = load_texture(CHARACTER_SEARCH_PATH, (147, 28))
    scroll_thumb = load_texture(SCROLL_THUMB_PATH, (15, 129))
    scroll_trough = scroll_trough_texture()

    def button_texture(left_cap, right_cap, center):
        height = 32
        cap_width = round(left_cap.width * height / left_cap.height)
        center_width = 64 - 2 * cap_width
        center_slice = center.crop(
            (
                (center.width - center_width) // 2,
                0,
                (center.width + center_width) // 2,
                center.height,
            )
        )
        image = Image.new("RGBA", (64, 32))
        image.paste(
            left_cap.resize((cap_width, height), Image.Resampling.LANCZOS), (0, 0)
        )
        image.paste(
            center_slice.resize((center_width, height), Image.Resampling.LANCZOS),
            (cap_width, 0),
        )
        image.paste(
            right_cap.resize((cap_width, height), Image.Resampling.LANCZOS),
            (64 - cap_width, 0),
        )
        return image

    def sized_button_texture(left_cap, right_cap, center, width, height=32):
        """Capirotes + tramo central estirado al ancho pedido.

        A diferencia de button_texture, que fija 64 px para el mosaico de ttk,
        aquí el ancho es el que necesita la etiqueta del botón flotante.
        """
        cap_width = round(left_cap.width * height / left_cap.height)
        center_width = max(1, width - 2 * cap_width)
        center_slice = center.crop(
            (
                (center.width - center_width) // 2,
                0,
                (center.width + center_width) // 2,
                center.height,
            )
        )
        image = Image.new("RGBA", (width, height))
        image.paste(
            left_cap.resize((cap_width, height), Image.Resampling.LANCZOS), (0, 0)
        )
        image.paste(
            center_slice.resize((center_width, height), Image.Resampling.LANCZOS),
            (cap_width, 0),
        )
        image.paste(
            right_cap.resize((cap_width, height), Image.Resampling.LANCZOS),
            (width - cap_width, 0),
        )
        return image

    tab_font = tkfont.Font(root=root, font=(font_family, 9, "bold"))

    def tab_texture(label):
        left_cap_width = round(
            tab_button_left_cap.width * 32 / tab_button_left_cap.height
        )
        right_cap_width = round(
            tab_button_right_cap.width * 32 / tab_button_right_cap.height
        )
        image_width = tab_font.measure(label) + 28
        center_width = image_width - left_cap_width - right_cap_width
        if center_width <= 0:
            raise ValueError(f"Tab label is too short: {label}")
        image = Image.new("RGBA", (image_width, 32))
        image.paste(
            tab_button_left_cap.resize((left_cap_width, 32), Image.Resampling.LANCZOS),
            (0, 0),
        )
        image.paste(
            tab_button_center.resize((center_width, 32), Image.Resampling.LANCZOS),
            (left_cap_width, 0),
        )
        image.paste(
            tab_button_right_cap.resize(
                (right_cap_width, 32), Image.Resampling.LANCZOS
            ),
            (image_width - right_cap_width, 0),
        )
        return image

    def highlighted_tab_texture(image):
        pixels = np.asarray(image).copy()
        rgb = pixels[:, :, :3].astype(np.float32)
        fill = (pixels[:, :, 3] > 0) & (rgb.mean(axis=2) > 80)
        tint = np.array((189, 224, 255), dtype=np.float32)
        rgb[fill] = rgb[fill] * 0.68 + tint * 0.32
        pixels[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        return Image.fromarray(pixels, mode="RGBA")

    normal_texture = ImageTk.PhotoImage(
        button_texture(
            button_normal_left_cap, button_normal_right_cap, button_normal_center
        ),
        master=root,
    )
    active_texture_image = button_texture(
        button_active_left_cap, button_active_right_cap, button_active_center
    )
    active_texture = ImageTk.PhotoImage(active_texture_image, master=root)
    highlight_texture = ImageTk.PhotoImage(
        button_texture(
            button_highlight_left_cap,
            button_highlight_right_cap,
            button_highlight_center,
        ),
        master=root,
    )
    active_hover_texture = active_texture_image.copy()
    active_pixels = np.asarray(active_hover_texture).copy()
    visible = active_pixels[:, :, 3] > 0
    active_pixels[visible, :3] = np.clip(
        active_pixels[visible, :3].astype(np.float32) * 1.08 + 8, 0, 255
    ).astype(np.uint8)
    active_hover_texture = Image.fromarray(active_pixels, mode="RGBA")
    normal_states = {
        "normal": normal_texture,
        "active": highlight_texture,
        # Al pulsar se usa la textura de botón activo, no la de resaltado: si
        # no, 'pressed' y 'active' serían la misma imagen y el clic no se notaría.
        "pressed": active_texture,
        "disabled": normal_texture,
    }
    # Los toggles conservan el aspecto de pulsado que ya tenían.
    toggle_states = dict(normal_states, pressed=highlight_texture)
    active_states = {
        "normal": active_texture,
        "active": ImageTk.PhotoImage(active_hover_texture, master=root),
        "pressed": active_texture,
        "disabled": normal_texture,
    }
    button_normal_caps = (
        button_normal_left_cap,
        button_normal_right_cap,
        button_normal_center,
    )
    button_active_caps = (
        button_active_left_cap,
        button_active_right_cap,
        button_active_center,
    )
    button_highlight_caps = (
        button_highlight_left_cap,
        button_highlight_right_cap,
        button_highlight_center,
    )
    floating_pil_font = ImageFont.truetype(str(APP_FONT_PATH), 13)

    def floating_text_width(text):
        """Ancho en píxeles de la etiqueta, con la misma fuente del botón."""
        return math.ceil(floating_pil_font.getlength(text))

    def floating_button_factory(text, width, height, selected):
        """Texturas de los botones flotantes del visor, con el texto dentro.

        Devuelve los estados normal, active (hover) y pressed. La base es la
        textura normal o, si el botón está seleccionado, la activa, igual que
        hacían los estilos Selected.*.TButton.

        El resalte depende de si se ve el botón verde: cuando está seleccionado
        se aclara un poco su propia base, porque al pasar por encima con la
        textura genérica el verde saltaba de color. Cuando se ve el botón
        normal se usa el resalte de borde azul oscuro, y al pulsarlo aparece un
        momento la textura de botón activo.
        """
        base_caps = button_active_caps if selected else button_normal_caps
        if selected:
            states = {
                "normal": (base_caps, 1.0),
                "active": (base_caps, 1.10),
                "pressed": (base_caps, 1.22),
            }
        else:
            states = {
                "normal": (base_caps, 1.0),
                "active": (button_highlight_caps, 1.0),
                "pressed": (button_active_caps, 1.0),
            }
        images = {}
        for state, (caps, factor) in states.items():
            image = sized_button_texture(*caps, width, height)
            if factor != 1.0:
                image = ImageEnhance.Brightness(image).enhance(factor)
            draw = ImageDraw.Draw(image)
            bounds = draw.textbbox((0, 0), text, font=floating_pil_font)
            position = (
                (width - (bounds[2] - bounds[0])) / 2 - bounds[0],
                (height - (bounds[3] - bounds[1])) / 2 - bounds[1],
            )
            draw.text(position, text, font=floating_pil_font, fill=colors["foreground"])
            images[state] = image
        return images

    root._sims_floating_button_factory = floating_button_factory
    root._sims_floating_font = floating_pil_font
    root._sims_floating_text_width = floating_text_width

    tab_rasters = {label: tab_texture(label) for label in NOTEBOOK_TAB_LABELS}
    tab_images = {
        label: ImageTk.PhotoImage(image, master=root)
        for label, image in tab_rasters.items()
    }
    tab_highlight_images = {
        label: ImageTk.PhotoImage(highlighted_tab_texture(image), master=root)
        for label, image in tab_rasters.items()
    }

    def scroll_track(thumb, horizontal=False):
        """Monta el pulgar en un lienzo que encaja en el canal del trough.

        El canal del trough tiene sus líneas oscuras a 1 px de un lado y 2 px
        del otro; así los bordes del pulgar las cubren exactamente.
        """
        if horizontal:
            canvas = Image.new("RGBA", (thumb.height + 3, thumb.width), (0, 0, 0, 0))
            canvas.paste(thumb.transpose(Image.ROTATE_90), (0, 2))
        else:
            canvas = Image.new("RGBA", (thumb.width + 3, thumb.height), (0, 0, 0, 0))
            canvas.paste(thumb, (1, 0))
        return canvas

    def highlight_scroll_texture(image, factor, offset):
        pixels = np.asarray(image).copy()
        visible = pixels[:, :, 3] > 0
        rgb = pixels[:, :, :3].astype(np.float32)
        rgb[visible] = np.clip(rgb[visible] * factor + offset, 0, 255)
        pixels[:, :, :3] = rgb.astype(np.uint8)
        return Image.fromarray(pixels, mode="RGBA")

    scroll_trough_image = ImageTk.PhotoImage(scroll_trough, master=root)
    scroll_thumb_raster = scroll_track(scroll_thumb)
    scroll_thumb_hover_raster = highlight_scroll_texture(scroll_thumb_raster, 1.06, 6)
    scroll_thumb_pressed_raster = highlight_scroll_texture(
        scroll_thumb_raster, 0.94, -6
    )
    scroll_thumb_h_raster = scroll_track(scroll_thumb, horizontal=True)
    scroll_thumb_hover_h_raster = highlight_scroll_texture(
        scroll_thumb_h_raster, 1.06, 6
    )
    scroll_thumb_pressed_h_raster = highlight_scroll_texture(
        scroll_thumb_h_raster, 0.94, -6
    )
    scroll_thumb_image = ImageTk.PhotoImage(scroll_thumb_raster, master=root)
    scroll_thumb_hover_image = ImageTk.PhotoImage(
        scroll_thumb_hover_raster, master=root
    )
    scroll_thumb_pressed_image = ImageTk.PhotoImage(
        scroll_thumb_pressed_raster, master=root
    )
    scroll_trough_h_image = ImageTk.PhotoImage(
        scroll_trough.rotate(90, expand=True), master=root
    )
    scroll_thumb_h_image = ImageTk.PhotoImage(scroll_thumb_h_raster, master=root)
    scroll_thumb_hover_h_image = ImageTk.PhotoImage(
        scroll_thumb_hover_h_raster, master=root
    )
    scroll_thumb_pressed_h_image = ImageTk.PhotoImage(
        scroll_thumb_pressed_h_raster, master=root
    )
    shape_preset_images = {}
    shape_preset_rasters = {}
    for value in (-1, 0, 1):
        text = str(value)
        shape_preset_rasters[value] = {
            state: shape_preset_raster(text, state)
            for state in ("normal", "hover", "selected", "selected_hover", "disabled")
        }
        shape_preset_images[value] = {
            state: ImageTk.PhotoImage(raster, master=root)
            for state, raster in shape_preset_rasters[value].items()
        }
    active_button_shadows = {}
    shape_slider_thumb_image = ImageTk.PhotoImage(
        # Se recorta el margen transparente de la textura original para que lo
        # opaco llegue al borde de la imagen: sin eso el pulgar se queda a 1 px
        # de cada extremo del hueco, que es justo lo que feo.
        #
        # La altura se deja en 10 px, que es la que salia antes de recortar
        # (12 filas opacas de 15 encogidas a 13). Estirarlas a 13 hacia el alto
        # ponia un pulgar 3 px mas grueso de mas.
        shape_slider_thumb.crop((2, 1, 25, 13)).resize(
            (25, 10), Image.Resampling.LANCZOS
        ),
        master=root,
    )
    def trough_para(ancho):
        # Al ancho que de verdad mide el deslizador. Con el borde lateral a 0
        # ttk estira la imagen entera, y si el widget mide otra cosa se
        # deforman las tapas redondeadas y se pierde el remate de un lado.
        return ImageTk.PhotoImage(
            shape_slider_trough.resize((ancho, 14), Image.Resampling.LANCZOS),
            master=root,
        )

    shape_slider_trough_image = trough_para(ANCHO_TROUGH_SHAPES)
    shape_slider_trough_clips_image = trough_para(ANCHO_TROUGH_CLIPS)
    shape_slider_trough_props_image = trough_para(ANCHO_TROUGH_PROPS)
    # Imagen vacia para los elementos intermedios del layout del deslizador, que
    # solo anadian relleno donde el pulgar no llegaba.
    vacia = ImageTk.PhotoImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)), master=root)
    separator_horizontal_image = ImageTk.PhotoImage(separator, master=root)
    separator_vertical_image = ImageTk.PhotoImage(
        separator.rotate(90, expand=True), master=root
    )
    character_search_image = ImageTk.PhotoImage(character_search, master=root)
    root._sims_theme_images = (
        normal_states,
        active_states,
        tab_images,
        tab_highlight_images,
        scroll_trough_image,
        scroll_thumb_image,
        scroll_thumb_hover_image,
        scroll_thumb_pressed_image,
        scroll_trough_h_image,
        scroll_thumb_h_image,
        scroll_thumb_hover_h_image,
        scroll_thumb_pressed_h_image,
        normal_texture,
        active_texture,
        highlight_texture,
        shape_preset_images,
        shape_slider_thumb_image,
        shape_slider_trough_image,
        shape_slider_trough_clips_image,
        shape_slider_trough_props_image,
        separator_horizontal_image,
        separator_vertical_image,
        character_search_image,
    )
    root._sims_shape_preset_images = shape_preset_images
    root._sims_shape_preset_rasters = shape_preset_rasters
    root._sims_active_button_shadows = active_button_shadows
    root._sims_tab_images = tab_images
    root._sims_tab_highlight_images = tab_highlight_images
    style.element_create(
        "Sims.RoundedButton",
        "image",
        normal_states["normal"],
        ("disabled", normal_states["disabled"]),
        ("pressed", normal_states["pressed"]),
        ("active", normal_states["active"]),
        border=(16, 8, 16, 8),
        width=1,
        sticky="nsew",
    )
    style.element_create(
        "Sims.ToggleButton",
        "image",
        toggle_states["normal"],
        ("disabled", toggle_states["disabled"]),
        ("pressed", toggle_states["pressed"]),
        ("active", toggle_states["active"]),
        border=(16, 8, 16, 8),
        width=1,
        sticky="nsew",
    )
    style.element_create(
        "Sims.ViewToggleButton",
        "image",
        normal_states["normal"],
        ("disabled", normal_states["disabled"]),
        # Igual que el boton normal: al pulsar sale la textura verde y se
        # vuelve sola al soltar o al sacar el raton. Los toggles de verdad
        # conservan su aspecto de pulsado, que es solo el resaltado.
        ("pressed", normal_states["pressed"]),
        ("active", normal_states["active"]),
        border=(16, 8, 16, 8),
        width=1,
        sticky="nsew",
    )
    style.element_create(
        "Sims.SelectedToggleButton",
        "image",
        active_states["normal"],
        ("disabled", active_states["disabled"]),
        ("pressed", active_states["pressed"]),
        ("active", active_states["active"]),
        border=(16, 8, 16, 8),
        width=1,
        sticky="nsew",
    )
    style.element_create(
        "Sims.SelectedHoldButton",
        "image",
        active_texture,
        ("disabled", active_states["disabled"]),
        # El raton por encima no resalta nada: con el boton ya verde, ponerse mas
        # claro al entrar el raton solo hace que al alejarlo dé un salto. Al
        # mantener si se aclara, que es lo unico que tiene que verse distinto.
        ("pressed", active_states["active"]),
        ("active", active_texture),
        border=(16, 8, 16, 8),
        width=1,
        sticky="nsew",
    )
    style.element_create(
        "Sims.Vertical.Scrollbar.trough",
        "image",
        scroll_trough_image,
        border=(0, 12, 0, 12),
        padding=(0, 0, 0, 0),
        sticky="nsew",
    )
    style.element_create(
        "Sims.Vertical.Scrollbar.thumb",
        "image",
        scroll_thumb_image,
        ("active", scroll_thumb_hover_image),
        ("pressed", scroll_thumb_pressed_image),
        border=(0, 11, 0, 11),
        sticky="nsew",
    )
    style.element_create(
        "Sims.Horizontal.Scrollbar.trough",
        "image",
        scroll_trough_h_image,
        border=(12, 0, 12, 0),
        padding=(0, 0, 0, 0),
        sticky="nsew",
    )
    style.element_create(
        "Sims.Horizontal.Scrollbar.thumb",
        "image",
        scroll_thumb_h_image,
        ("active", scroll_thumb_hover_h_image),
        ("pressed", scroll_thumb_pressed_h_image),
        border=(11, 0, 11, 0),
        sticky="nsew",
    )
    style.element_create(
        "Sims.Horizontal.Separator",
        "image",
        separator_horizontal_image,
        border=(2, 0, 2, 0),
        sticky="nsew",
    )
    style.element_create(
        "Sims.Vertical.Separator",
        "image",
        separator_vertical_image,
        border=(0, 2, 0, 2),
        sticky="nsew",
    )
    # border=5, no menor: la textura tiene 5 px de degradado en cada lado y Tk
    # mosaica (no escala) la franja central. Con border=3 el mosaico arrastraba
    # los píxeles 3 y 4 del degradado y los repetía cada 141 px, dejando una
    # rayita vertical en mitad del campo.
    style.element_create(
        "Sims.CharacterSearch.field",
        "image",
        character_search_image,
        border=(5, 5, 5, 5),
        sticky="nsew",
    )
    style.layout(
        "Horizontal.TSeparator", [("Sims.Horizontal.Separator", {"sticky": "nsew"})]
    )
    style.layout(
        "Vertical.TSeparator", [("Sims.Vertical.Separator", {"sticky": "nsew"})]
    )
    style.layout(
        "CharacterSearch.TEntry",
        [
            (
                "Sims.CharacterSearch.field",
                {
                    "sticky": "nsew",
                    "children": [
                        (
                            "Entry.padding",
                            {
                                "sticky": "nsew",
                                "children": [("Entry.textarea", {"sticky": "nsew"})],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.element_create(
        "Horizontal.Scale.focus",
        "image",
        vacia,
        border=0,
        sticky="ew",
    )
    style.element_create(
        "Horizontal.Scale.padding",
        "image",
        vacia,
        border=0,
        sticky="ew",
    )
    def trough_element(nombre, imagen):
        # Sin borde lateral: en ttk el `border` de un elemento es el relleno de
        # sus hijos, y el pulgar es hijo del trough, asi que con margen aqui no
        # llegaba al extremo. Ojo al orden, que en `border` es
        # (izquierda, arriba, derecha, abajo).
        #
        # No se puede llegar a 0 del todo: al componer un elemento de imagen,
        # ttk redondea el alto y el ancho a pixel entero y siempre queda 1 px.
        # Se recupera con el margen del pulgar, que es lo que hace la imagen de
        # mas abajo.
        #
        # Con el margen a cero no hay donde anclar las tapas, y la imagen se
        # estira o se recorta entera segun el ancho del widget: el remate
        # redondeado se pierde por un lado. Por eso la imagen va ya hecha del
        # ancho que mide el deslizador.
        style.element_create(
            nombre,
            "image",
            imagen,
            ("disabled", imagen),
            border=(0, 4, 0, 4),
            sticky="ew",
        )

    trough_element("ShapeKeys.Scale.trough", shape_slider_trough_image)
    trough_element("Clips.Scale.trough", shape_slider_trough_clips_image)
    trough_element("Props.Scale.trough", shape_slider_trough_props_image)
    style.element_create(
        "Horizontal.Scale.trough",
        "image",
        shape_slider_trough_image,
        ("disabled", shape_slider_trough_image),
        # Sin borde lateral: en ttk el `border` de un elemento es el relleno de
        # sus hijos, y el pulgar es hijo del trough, asi que con margen aqui no
        # llegaba al extremo. Ojo al orden, que en `border` es
        # (izquierda, arriba, derecha, abajo).
        #
        # No se puede llegar a 0: al componer un elemento de imagen, ttk
        # redondea el alto y el ancho a pixel entero y siempre queda 1 px. Se
        # recupera con el margen del pulgar, que es lo que hace la imagen de mas
        # abajo.
        border=(0, 4, 0, 4),
        sticky="ew",
    )
    style.element_create(
        "Horizontal.Scale.slider",
        "image",
        shape_slider_thumb_image,
        ("disabled", shape_slider_thumb_image),
        sticky="",
    )
    button_layout = [
        (
            "Sims.RoundedButton",
            {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
        )
    ]
    primary_layout = [
        (
            "Sims.RoundedButton",
            {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
        )
    ]
    style.layout("TButton", button_layout)
    style.layout("Primary.TButton", primary_layout)
    style.layout("TNotebook.Tab", [("Notebook.label", {"sticky": "nsew"})])
    style.layout(
        "Toggle.TButton",
        [
            (
                "Sims.ToggleButton",
                {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
            )
        ],
    )
    style.layout(
        "ViewToggle.TButton",
        [
            (
                "Sims.ViewToggleButton",
                {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
            )
        ],
    )
    style.layout(
        "Selected.Toggle.TButton",
        [
            (
                "Sims.SelectedToggleButton",
                {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
            )
        ],
    )
    style.layout(
        "SelectedHold.TButton",
        [
            (
                "Sims.SelectedHoldButton",
                {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]},
            )
        ],
    )
    style.layout("ShapePreset.TButton", [("Button.label", {"sticky": "nsew"})])

    def scale_layout(trough):
        return [
            (
                "Horizontal.Scale.focus",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Horizontal.Scale.padding",
                            {
                                "sticky": "nswe",
                                "children": [
                                    (
                                        trough,
                                        {
                                            "sticky": "nswe",
                                            "children": [
                                                (
                                                    "Horizontal.Scale.slider",
                                                    {"side": "left", "sticky": ""},
                                                )
                                            ],
                                        },
                                    )
                                ],
                            },
                        )
                    ],
                },
            )
        ]

    style.layout("Shape.Horizontal.TScale", scale_layout("ShapeKeys.Scale.trough"))
    style.layout("Clips.Horizontal.TScale", scale_layout("Clips.Scale.trough"))
    style.layout("Props.Horizontal.TScale", scale_layout("Props.Scale.trough"))
    vertical_scrollbar_layout = [
        (
            "Sims.Vertical.Scrollbar.trough",
            {
                "sticky": "nsew",
                "children": [("Sims.Vertical.Scrollbar.thumb", {"sticky": "nsew"})],
            },
        )
    ]
    horizontal_scrollbar_layout = [
        (
            "Sims.Horizontal.Scrollbar.trough",
            {
                "sticky": "nsew",
                "children": [("Sims.Horizontal.Scrollbar.thumb", {"sticky": "nsew"})],
            },
        )
    ]
    style.layout("Vertical.TScrollbar", vertical_scrollbar_layout)
    style.layout("Horizontal.TScrollbar", horizontal_scrollbar_layout)
    style.configure(
        "TButton",
        padding=(12, 6),
        foreground=colors["foreground"],
        font=(font_family, 9, "bold"),
        borderwidth=0,
        focuscolor="",
    )
    style.configure("TButton", background=colors["background"])
    style.configure(
        "Primary.TButton",
        padding=(14, 7),
        foreground=colors["foreground"],
        font=(font_family, 10, "bold"),
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
    )
    style.configure(
        "Toggle.TButton",
        padding=(10, 5),
        foreground=colors["foreground"],
        font=(font_family, 11, "bold"),
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
    )
    style.configure(
        "ViewToggle.TButton",
        padding=(10, 5),
        foreground=colors["foreground"],
        font=(font_family, 11, "bold"),
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
    )
    style.configure(
        "Selected.Toggle.TButton",
        padding=(10, 5),
        foreground=colors["foreground"],
        font=(font_family, 11, "bold"),
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
        embossed=False,
    )
    style.configure(
        "SelectedHold.TButton",
        padding=(10, 5),
        foreground=colors["foreground"],
        font=(font_family, 11, "bold"),
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
        embossed=False,
    )
    style.configure(
        "ShapePreset.TButton",
        padding=0,
        borderwidth=0,
        focuscolor="",
        background=colors["background"],
    )
    style.map(
        "TButton",
        background=[
            ("disabled", colors["background"]),
            ("pressed", colors["background"]),
            ("active", colors["background"]),
        ],
        foreground=[("disabled", colors["muted"])],
        focuscolor=[("focus", "")],
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", colors["background"]),
            ("pressed", colors["background"]),
            ("active", colors["background"]),
        ],
        foreground=[("disabled", colors["muted"])],
        focuscolor=[("focus", "")],
    )
    style.map(
        "Toggle.TButton",
        background=[
            ("disabled", colors["background"]),
            ("pressed", colors["background"]),
            ("active", colors["background"]),
        ],
        foreground=[("disabled", colors["muted"])],
        focuscolor=[("focus", "")],
    )
    style.map(
        "ViewToggle.TButton",
        background=[
            ("disabled", colors["background"]),
            ("pressed", colors["background"]),
            ("active", colors["background"]),
        ],
        foreground=[("disabled", colors["muted"])],
        focuscolor=[("focus", "")],
    )
    style.map(
        "Selected.Toggle.TButton",
        background=[
            ("disabled", colors["background"]),
            ("pressed", colors["background"]),
            ("active", colors["background"]),
        ],
        foreground=[("disabled", colors["muted"])],
        focuscolor=[("focus", "")],
    )
    style.configure(
        "Horizontal.TScale",
        background=colors["background"],
        sliderlength=18,
        borderwidth=0,
        focuscolor="",
        # Relleno negativo para que el pulgar se salga 3 px por cada lado: el
        # trough va a lo ancho del hueco y el pulgar llega mas alla de el.
        padding=(-3, 0, -3, 0),
    )
    style.configure(
        "Shape.Horizontal.TScale",
        background=colors["background"],
        sliderlength=27,
        borderwidth=0,
        focuscolor="",
        padding=(-3, 0, -3, 0),
    )
    style.configure(
        "Clips.Horizontal.TScale",
        background=colors["background"],
        sliderlength=27,
        borderwidth=0,
        focuscolor="",
        padding=(-3, 0, -3, 0),
    )
    style.configure(
        "Props.Horizontal.TScale",
        background=colors["background"],
        sliderlength=27,
        borderwidth=0,
        focuscolor="",
        padding=(-3, 0, -3, 0),
    )
    for _escala in (
        "Shape.Horizontal.TScale",
        "Clips.Horizontal.TScale",
        "Props.Horizontal.TScale",
    ):
        style.map(
            _escala,
            background=[
                ("disabled", colors["background"]),
                ("active", colors["background"]),
            ],
        )
    style.configure("Vertical.TScrollbar", background=colors["background"])
    style.configure("Horizontal.TScrollbar", background=colors["background"])
    for style_name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            style_name,
            background=colors["background"],
            troughcolor=colors["background"],
            bordercolor=colors["background"],
            lightcolor="#d7e5ff",
            darkcolor="#243774",
        )
    style.map(
        "TScrollbar",
        background=[
            ("active", colors["background"]),
            ("pressed", colors["background"]),
        ],
    )
    root.configure(background=colors["background"])
    root._sims_theme_colors = colors
    return colors


