"""Small English/Spanish UI catalog for text shown by the viewer."""

import re
import tkinter


EN_ES = {
    "Language": "Lenguaje",
    "Select ISO...": "Seleccionar ISO...",
    "Select ISO…": "Seleccionar ISO…",
    "Cancel import": "Cancelar importación",
    "Characters": "Personajes",
    "Character": "Personaje",
    "Object": "Objeto",
    "Character View": "Vista de personaje",
    "Animation View": "Vista de animación",
    "Replace character": "Reemplazar personaje",
    "Textures": "Texturas",
    "Shape Keys": "Shape Keys",
    "Strength": "Intensidad",
    "Props": "Objetos",
    "Animations": "Animaciones",
    "Group": "Grupo",
    "All": "Todos",
    "Individual": "Individual",
    "Social": "Social",
    "Scenes": "Escenas",
    "Creatures": "Criaturas",
    "▶ Play": "▶ Reproducir",
    "❚❚ Pause": "❚❚ Pausar",
    "Loop": "Repetir",
    "Auto Play": "Reproducción automática",
    "Vertex colors": "Colores de vértice",
    "Maps": "Mapas",
    "Spawn points": "Puntos de aparición",
    "Hidden": "Oculto",
    "Pointers": "Indicadores",
    "Light illumination": "Iluminación de luces",
    "Cameras": "Cámaras",
    "Lights": "Luces",
    "Hide All": "Ocultar todo",
    "Show All": "Mostrar todo",
    "Click a piece to show or hide it": "Pulsa una pieza para mostrarla u ocultarla",
    "Layers": "Capas",
    "Vertex Color": "Color de vértice",
    "Time of day: 12:00": "Hora del día: 12:00",
    "Hide sky": "Ocultar cielo",
    "Show sky": "Mostrar cielo",
    "Export as...": "Exportar como...",
    "Export clip as...": "Exportar clip como...",
    "Export...": "Exportar...",
    "Format for": "Formato para",
    "GLB (.glb) — single file": "GLB (.glb) — un solo archivo",
    "glTF (.gltf) — model, binary and textures": "glTF (.gltf) — modelo, binario y texturas",
    "FBX (.fbx) — requires Blender": "FBX (.fbx) — requiere Blender",
    "Cancel": "Cancelar",
    "Export": "Exportar",
    "Include animation": "Incluir animación",
    "Select multiple clips with Ctrl or Shift": "Selecciona varios clips con Ctrl o Mayús",
    "Select multiple clips": "Selecciona varios clips",
    "These clips mix skeletons. Export human, Cowthulu and Night Beast clips separately.": "Estos clips mezclan esqueletos. Exporta por separado los clips humanos, de Cowthulu y de Night Beast.",
    "Export the selected animation": "Exportar la animación seleccionada",
    "Unmarked, only the character model is exported.": "Si no se marca, solo se exporta el modelo del personaje.",
    "Save PNG": "Guardar PNG",
    "Part": "Pieza",
    "Mostrar UV": "Mostrar UV",
    "The model has no readable UV coordinates.": "El modelo no tiene coordenadas UV legibles.",
    "Import": "Importar",
    "Wait for the current export to finish.": "Espera a que termine la exportación actual.",
    "Starting ISO extraction…": "Iniciando extracción de la ISO…",
    "Cancelling import…": "Cancelando la importación…",
    "Opening imported resources…": "Abriendo los recursos importados…",
    "Import cancelled. Existing resources were kept.": "Importación cancelada. Se conservaron los recursos existentes.",
    "Could not import the ISO. You can select it again.": "No se pudo importar la ISO. Puedes volver a seleccionarla.",
    "GPU renderer": "Renderizador de GPU",
    "Extracting to": "Extrayendo en",
    "Resources loaded from": "Recursos cargados desde",
    "Hardware acceleration unavailable": "Aceleración por hardware no disponible",
    "Export animations": "Exportar animaciones",
    "Select one or more clips in the list first.": "Selecciona primero uno o más clips de la lista.",
    "Export prop placements": "Exportar ubicaciones de objetos",
    "Export part": "Exportar pieza",
    "Extract textures": "Extraer texturas",
    "Export map parts": "Exportar piezas del mapa",
    "Export object": "Exportar objeto",
    "Export objects": "Exportar objetos",
    "Export character": "Exportar personaje",
    "Character": "Personaje",
    "Export object": "Exportar objeto",
    "Export objects": "Exportar objetos",
    "Export prop placements": "Exportar ubicaciones de objetos",
    "Map": "Mapa",
    "Export part": "Exportar pieza",
    "Extract textures": "Extraer texturas",
    "Export map parts": "Exportar piezas del mapa",
    "Incompatible skeletons": "Esqueletos incompatibles",
    "Extract BANM": "Extraer BANM",
    "Preview": "Vista previa",
    "Loading...": "Cargando...",
    "Waiting for an ISO": "Esperando una ISO",
    "ISO image": "Imagen ISO",
    "All files": "Todos los archivos",
    "Imagen ISO": "Imagen ISO",
    "Todos los archivos": "Todos los archivos",
    "Create a Sim": "Crear un Sim",
    "Search": "Buscar",
    "Name": "Nombre",
    "Animation": "Animación",
    "Group": "Grupo",
    "Objects": "Objetos",
    "All": "Todos",
    "Chair": "Silla",
    "Chairs": "Sillas",
    "Doors": "Puertas",
    "Windows": "Ventanas",
    "Vehicles": "Vehículos",
    "Furniture": "Muebles",
    "Time of day": "Hora del día",
    "Reset view": "Restablecer vista",
    "Grid": "Cuadrícula",
    "Bones": "Huesos",
    "Layers": "Capas",
    "Export as...": "Exportar como...",
    "Set pose": "Establecer pose",
    "Textures": "Texturas",
    "CAS": "Crear un Sim",
    "Search": "Buscar",
    "Hide All": "Ocultar todo",
    "Show All": "Mostrar todo",
    "Time of day: {time}": "Hora del día: {time}",
    "Intensity: {value}%": "Intensidad: {value}%",
    "Saved:": "Guardado:",
    "Part saved:": "Pieza guardada:",
    "Original map saved:": "Mapa original guardado:",
    "Preview:": "Vista previa:",
    "Could not load:": "No se pudo cargar:",
    "Export the selected animation": "Exportar la animación seleccionada",
    "Textures": "Texturas",
    "Animations": "Animaciones",
    "Group": "Grupo",
    "FPS": "FPS",
    "FPS values": "FPS",
    "Apply": "Aplicar",
    "Close": "Cerrar",
    "Open": "Abrir",
    "Details": "Detalles",
    "Show UV": "Mostrar UV",
    "Save PNG": "Guardar PNG",
    "Texture maps and UVs": "Mapas de textura y UV",
    "Select a character": "Selecciona un personaje",
}

EN_ES_WORDS = {
    "bones": "huesos",
    "vertices": "vértices",
    "parts": "piezas",
    "piece": "pieza",
    "pieces": "piezas",
    "saved": "guardado",
    "exported": "exportado",
    "selected": "seleccionado",
    "loading": "cargando",
    "unavailable": "no disponible",
    "failed": "falló",
    "preview": "vista previa",
    "textures": "texturas",
    "original map": "mapa original",
    "map parts": "piezas del mapa",
    "parts saved": "piezas guardadas",
    "map parts saved": "piezas del mapa guardadas",
    "png textures saved": "texturas PNG guardadas",
    "textures saved": "texturas guardadas",
    "models exported": "modelos exportados",
    "models": "modelos",
    "with vertex colors": "con colores de vértice",
    "intensity": "intensidad",
    "time of day": "hora del día",
    "reset view": "restablecer vista",
    "grid": "cuadrícula",
    "bones": "huesos",
    "resources": "recursos",
    "gpu renderer": "renderizador de GPU",
    "loaded": "cargados",
    "from": "desde",
    "for": "para",
    "format": "formato",
    "hardware acceleration": "aceleración por hardware",
    "cpu rendering is slower": "la renderización por CPU es más lenta",
    "saved to": "guardado en",
    "exporting": "exportando",
    "clips": "clips",
    "model": "modelo",
    "files": "archivos",
    "original": "originales",
    "colors": "colores",
    "black shading is stored in vertex colors": "el sombreado oscuro está guardado en los colores de vértice",
    "toggle vertex colors to inspect the texture": "activa o desactiva los colores de vértice para revisar la textura",
    "wait for the current export to finish": "espera a que termine la exportación actual",
    "select one or more clips in the list first": "selecciona primero uno o más clips de la lista",
    "could not load": "no se pudo cargar",
    "preview unavailable": "vista previa no disponible",
    "invalid animation": "animación no válida",
    "it was not loaded": "no se ha cargado",
    "map": "mapa",
    "character": "personaje",
    "object": "objeto",
    "objects": "objetos",
    "clips selected": "clips seleccionados",
    "selected clips": "clips seleccionados",
    "original banm files": "archivos BANM originales",
    "unmarked, only the character model is exported": "si no se marca, solo se exporta el modelo del personaje",
    "these clips mix skeletons": "estos clips mezclan esqueletos",
    "export human": "exporta los clips humanos",
    "and night beast clips separately": "y los de Night Beast por separado",
    "of": "de",
}

ES_EN = {translated: source for source, translated in EN_ES.items()}
ES_EN.update({translated: source for source, translated in EN_ES_WORDS.items()})


def translate(value, language):
    """Translate known UI labels while leaving resource names and paths intact."""
    if not isinstance(value, str):
        return value
    if language != "es":
        exact = ES_EN.get(value)
        if exact is not None:
            return exact
        result, protected = _protect_paths(value)
        reverse_words = {translated: source for source, translated in EN_ES_WORDS.items()}
        for source in sorted(reverse_words, key=len, reverse=True):
            result = re.sub(
                rf"(?i)\b{re.escape(source)}\b",
                lambda match: _keep_case(match.group(0), reverse_words[source]),
                result,
            )
        return _restore_paths(result, protected)
    exact = EN_ES.get(value)
    if exact is not None:
        return exact
    result, protected = _protect_paths(value)
    for source in sorted(EN_ES_WORDS, key=len, reverse=True):
        result = re.sub(
            rf"(?i)\b{re.escape(source)}\b",
            lambda match: _keep_case(match.group(0), EN_ES_WORDS[source]),
            result,
        )
    result = re.sub(
        r"^(\d+) piezas guardado\b",
        r"\1 piezas guardadas",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"^(\d+) modelos exportado\b",
        r"\1 modelos exportados",
        result,
        flags=re.IGNORECASE,
    )
    return _restore_paths(result, protected)


def _keep_case(found, replacement):
    return replacement.capitalize() if found[:1].isupper() else replacement


def _protect_paths(value):
    paths = []

    def hide(match):
        paths.append(match.group(0))
        return f"\u0000PATH{len(paths) - 1}\u0000"

    value = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\r\n]+", hide, value)
    return value, paths


def _restore_paths(value, paths):
    for index, path in enumerate(paths):
        value = value.replace(f"\u0000PATH{index}\u0000", path)
    return value


def localize_widget_tree(root, language):
    """Update existing static widget labels when the selected UI language changes."""
    pending = list(root.winfo_children())
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        try:
            value = widget.cget("text")
            translated = translate(value, language)
            if translated != value:
                widget.configure(text=translated)
        except (tkinter.TclError, TypeError, ValueError):
            pass
        try:
            values = widget.cget("values")
            if isinstance(values, str):
                values = widget.tk.splitlist(values)
            translated_values = tuple(translate(item, language) for item in values)
            if tuple(values) != translated_values:
                widget.configure(values=translated_values)
        except (tkinter.TclError, TypeError, ValueError):
            pass
        try:
            variable = widget.cget("textvariable")
            if variable:
                value = root.getvar(variable)
                translated = translate(value, language)
                if translated != value:
                    root.setvar(variable, translated)
        except (tkinter.TclError, TypeError, ValueError):
            pass
        try:
            title = widget.title()
            translated_title = translate(title, language)
            if translated_title != title:
                widget.title(translated_title)
        except (AttributeError, tkinter.TclError):
            pass
