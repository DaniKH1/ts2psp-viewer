"""Application startup, ISO import and tab coordination."""

import csv
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from ts2psp.formats.characters import convert_character
from ts2psp.rendering.characters import load_character, list_characters
from ts2psp.rendering.props import load_parts
from ts2psp.formats.animations import (
    REFERENCE_F,
    attach_animations,
    open_glb,
    parse_banm,
)
from ts2psp.io.iso import ARCHIVES, import_iso
from ts2psp.rendering.animation import draw as draw_animation
from ts2psp.rendering.animation import posed_meshes
import ts2psp.rendering.opengl as gl
from ts2psp.ui.maps import MapTab
from ts2psp.paths import default_data_base
from ts2psp.exporting.blender import export_path
from ts2psp.ui.characters import CharacterTab
from ts2psp.ui.objects import PropTab
from ts2psp.ui.animations import AnimationTab
from ts2psp.ui.theme import (
    APP_ICON_ICO_PATH,
    NOTEBOOK_TAB_LABELS,
    _install_sims_theme,
    _unregister_sims_font,
)
from ts2psp.ui.common import read_character_rows


class Sims2PSPApp(tk.Tk):
    def __init__(self, base=None):
        super().__init__()
        # El icono va solo desde el .ico: Tk no lee .ico en un PhotoImage, asÃƒÂ­
        # que en Windows la vÃƒÂ­a correcta es iconbitmap. Es decorativo, asÃƒÂ­ que
        # si el fichero no estÃƒÂ¡ la app arranca igual.
        if sys.platform == "win32" and APP_ICON_ICO_PATH.is_file():
            self.iconbitmap(default=str(APP_ICON_ICO_PATH))
            self.iconbitmap(str(APP_ICON_ICO_PATH))
        self.sims_colors = _install_sims_theme(self)
        self.title("The Sims 2 PSP Ã‚Â· Models, maps and animations (GPU)")
        self.geometry("1260x850")
        self.minsize(1080, 740)
        self.base = Path(base) if base is not None else default_data_base()
        self.base.mkdir(parents=True, exist_ok=True)
        self.export_root = Path.home() / "Documents" / "TS2 PSP Viewer" / "exports"
        self.data_dir = self.base / "data"
        self.paths = {}
        self.tabs = None
        self.welcome = None
        self.animation_tab = None
        self.importing = False
        self.import_messages = queue.Queue()
        self.info = tk.StringVar(value="")
        self.temp = tempfile.TemporaryDirectory(prefix="sims2_psp_app_")
        self.cache = Path(self.temp.name)
        self.blender = None
        menu = tk.Menu(
            self,
            background=self.sims_colors["panel"],
            foreground=self.sims_colors["foreground"],
            activebackground=self.sims_colors["selection"],
            activeforeground=self.sims_colors["foreground"],
            borderwidth=0,
        )
        file_menu = tk.Menu(
            menu,
            tearoff=False,
            background=self.sims_colors["panel"],
            foreground=self.sims_colors["foreground"],
            activebackground=self.sims_colors["selection"],
            activeforeground=self.sims_colors["foreground"],
            borderwidth=0,
        )
        file_menu.add_command(label="Select ISOÃ¢â‚¬Â¦", command=self.select_iso)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)
        self.configure(menu=menu)
        self.status_bar = ttk.Label(self, textvariable=self.info, padding=(10, 5))
        self.status_bar.pack(side="bottom", fill="x")
        available = next(
            (
                folder
                for folder in (self.base, self.data_dir)
                if all((folder / name).is_file() for name in ARCHIVES.values())
            ),
            None,
        )
        if available:
            self.load_archives(available)
        else:
            self.show_welcome()
        self.reportar_render()
        self.after(100, self.poll_import)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def show_welcome(self):
        if self.tabs:
            self.tabs.destroy()
            self.tabs = None
        if self.welcome:
            self.welcome.destroy()
        self.welcome = ttk.Frame(self, padding=35)
        self.welcome.pack(fill="both", expand=True)
        ttk.Label(
            self.welcome,
            text="The Sims 2 PSP Ã‚Â· Viewer and extractor",
            font=(self._sims_font_family, 21, "bold"),
        ).pack(anchor="w", pady=(30, 20))
        ttk.Label(
            self.welcome,
            text="Select your The Sims 2 PSP ISO. The app will extract "
            "characters, textures, objects, maps and animations to a local folder.",
            wraplength=720,
            font=(self._sims_font_family, 11),
        ).pack(anchor="w", pady=(0, 18))
        self.import_button = ttk.Button(
            self.welcome,
            text="Select ISOÃ¢â‚¬Â¦",
            command=self.select_iso,
            style="Primary.TButton",
        )
        self.import_button.pack(anchor="w", pady=8)
        ttk.Label(
            self.welcome,
            text="The ISO and game resources are not included with this application. "
            "Import once; the extracted files will load automatically next time.",
            wraplength=720,
        ).pack(anchor="w", pady=12)
        self.info.set("Waiting for an ISO")

    def load_archives(self, folder):
        if self.tabs:
            self.character_tab.dispose()
            self.prop_tab.dispose()
            self.map_tab.dispose()
            self.animation_tab.dispose()
            self.tabs.destroy()
        if self.welcome:
            self.welcome.destroy()
            self.welcome = None
        self.paths = {key: folder / name for key, name in ARCHIVES.items()}
        gl.forget_all()  # al cambiar de carpeta de recursos las escenas GPU son obsoletas
        self.character_rows = read_character_rows(self.paths["characters"])
        with zipfile.ZipFile(self.paths["characters"]) as z:
            self.characters = list_characters(z)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.character_tab = CharacterTab(self.tabs, self)
        self.prop_tab = PropTab(self.tabs, self)
        self.map_tab = MapTab(self.tabs, self, export_path)
        self.animation_tab = AnimationTab(self.tabs, self)
        self.tabs.add(
            self.character_tab,
            text="Characters",
            image=self._sims_tab_images["Characters"],
            compound="center",
        )
        self.tabs.add(
            self.animation_tab,
            text="Animations",
            image=self._sims_tab_images["Animations"],
            compound="center",
        )
        self.tabs.add(
            self.prop_tab,
            text="Props",
            image=self._sims_tab_images["Props"],
            compound="center",
        )
        self.tabs.add(
            self.map_tab,
            text="Maps",
            image=self._sims_tab_images["Maps"],
            compound="center",
        )
        hovered_tab = None

        def set_tab_highlight(index):
            nonlocal hovered_tab
            if index == hovered_tab:
                return
            if hovered_tab is not None:
                label = NOTEBOOK_TAB_LABELS[hovered_tab]
                self.tabs.tab(hovered_tab, image=self._sims_tab_images[label])
            hovered_tab = index
            if hovered_tab is not None:
                label = NOTEBOOK_TAB_LABELS[hovered_tab]
                self.tabs.tab(hovered_tab, image=self._sims_tab_highlight_images[label])

        def update_tab_highlight(event):
            if self.tabs.identify(event.x, event.y) == "label":
                set_tab_highlight(self.tabs.index(f"@{event.x},{event.y}"))
            else:
                set_tab_highlight(None)

        self.tabs.bind("<Motion>", update_tab_highlight)
        self.tabs.bind("<Leave>", lambda _event: set_tab_highlight(None))
        self.info.set(f"Resources loaded from {folder}")

    def reportar_render(self):
        """Dice en la barra de estado con quÃƒÂ© render se estÃƒÂ¡ trabajando.

        La GPU es siempre la opciÃƒÂ³n preferida; el rasterizador de CPU solo entra si el
        equipo no puede crear un contexto acelerado, sin que haya que tocar nada.
        """
        try:
            gpu = gl.Contexto.compartido().renderer
        except Exception as exc:
            self.info.set(
                f"Hardware acceleration unavailable ({exc}). CPU rendering is slower."
            )
        else:
            self.info.set(f"GPU renderer: {gpu}")

    def select_iso(self):
        if self.importing:
            return
        if self.animation_tab and self.animation_tab.exporting:
            messagebox.showinfo("Import", "Wait for the current export to finish.")
            return
        path = filedialog.askopenfilename(
            title="Select The Sims 2 PSP ISO",
            filetypes=[("ISO image", "*.iso"), ("All files", "*.*")],
        )
        if not path:
            return
        self.importing = True
        if self.welcome:
            self.import_button.configure(state="disabled")
        self.info.set("Reading ISOÃ¢â‚¬Â¦")

        def work():
            try:
                result = import_iso(
                    path,
                    self.data_dir,
                    lambda message: self.import_messages.put(("progress", message)),
                )
                self.import_messages.put(("done", result))
            except Exception as exc:
                self.import_messages.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def poll_import(self):
        try:
            while True:
                kind, payload = self.import_messages.get_nowait()
                if kind == "progress":
                    self.info.set(payload)
                elif kind == "error":
                    self.importing = False
                    if self.welcome:
                        self.import_button.configure(state="normal")
                    self.info.set("Could not import the ISO")
                    messagebox.showerror("Import ISO", payload)
                elif kind == "done":
                    self.importing = False
                    self.temp.cleanup()
                    self.temp = tempfile.TemporaryDirectory(prefix="sims2_psp_app_")
                    self.cache = Path(self.temp.name)
                    try:
                        self.load_archives(self.data_dir)
                    except Exception as exc:
                        messagebox.showerror("Open resources", str(exc))
                    else:
                        self.info.set(
                            f'ISO imported: {payload["characters"]} characters, '
                            f'{payload["props"]} objects, '
                            f'{payload["animations"]} animations, '
                            f'{payload["maps"]} maps'
                        )
        except queue.Empty:
            pass
        self.after(100, self.poll_import)

    def resolve_blender(self):
        if self.blender and Path(self.blender).is_file():
            return self.blender
        found = shutil.which("blender")
        if not found:
            root = Path(r"C:\Program Files\Blender Foundation")
            found = next(
                (
                    str(path)
                    for path in sorted(root.glob("Blender */blender.exe"), reverse=True)
                ),
                None,
            )
        if not found:
            messagebox.showinfo(
                "Export FBX", "Select blender.exe. Blender converts GLB files to FBX."
            )
            found = filedialog.askopenfilename(
                title="Select blender.exe",
                filetypes=[("Blender", "blender.exe"), ("Executables", "*.exe")],
            )
        if found and Path(found).is_file():
            self.blender = found
            return found
        return None

    def close(self):
        if self.tabs:
            self.character_tab.dispose()
            self.prop_tab.dispose()
            self.map_tab.dispose()
            self.animation_tab.dispose()
        # cancela el temporizador de la importacion: si no, Tk avisa de un comando
        # pendiente en el momento de destruir la ventana
        try:
            self.after_cancel(self.poll_timer)
        except Exception:
            pass
        self.poll_timer = None
        self.temp.cleanup()
        self.destroy()
        _unregister_sims_font(self)


def self_test(base):
    base = Path(base)
    if not all((base / name).is_file() for name in ARCHIVES.values()):
        base = base / "data"
    paths = {key: base / name for key, name in ARCHIVES.items()}
    for path in paths.values():
        assert path.exists(), path
    rows = read_character_rows(paths["characters"])
    with zipfile.ZipFile(paths["characters"]) as z:
        names = list_characters(z)
    assert len(names) == 87
    _, parts, bones, textures = load_character(paths["characters"], "bellagoth")
    assert parts and len(bones) == 47 and textures
    with zipfile.ZipFile(paths["props"]) as z:
        props = list(
            csv.DictReader(
                io.StringIO(z.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        part_data = load_parts(z.read(props[0]["archivo_zip"]))
    assert len(props) == 767 and part_data
    with zipfile.ZipFile(paths["animations"]) as z:
        catalog = json.loads(z.read("catalog.json"))
        row = next(r for r in catalog if r["name"].endswith("a-female-walk-alt0-norm"))
        clip = parse_banm(z.read(row["file"]))
        fallback_row = next(r for r in catalog if r["name"] == REFERENCE_F)
        fallback = parse_banm(z.read(fallback_row["file"]))
    assert len(catalog) == 1336 and clip["tracks"]
    with zipfile.ZipFile(paths["maps"]) as z:
        maps = list(
            csv.DictReader(
                io.StringIO(z.read("inventario_mapas.csv").decode("utf-8-sig"))
            )
        )
        assert len(maps) == 21
        graveyard = next(
            r for r in maps if r["ruta_interna"] == "levels/deadtree/graveyard"
        )
        from ts2psp.formats.props import parse_rdms

        map_parts, rejected = parse_rdms(
            z.read(graveyard["archivo_zip"]), apply_scene=False
        )
        assert len(map_parts) > 300 and not rejected
        assert any(p["texture"] and p["vertices"][0][2] is not None for p in map_parts)
    with tempfile.TemporaryDirectory() as d:
        model = Path(d) / "bella.glb"
        animated = Path(d) / "bella_animada.glb"
        convert_character(paths["characters"], "bellagoth", model)
        attach_animations(model, [clip], None, animated, fallback=fallback)
        doc, binary = open_glb(animated)
        assert len(doc["animations"]) == 1
        image = draw_animation(
            posed_meshes(doc, binary, doc["animations"][0], 0.3), width=320, height=440
        )
        assert image.size == (320, 440)
    print(
        json.dumps(
            {
                "characters": len(names),
                "props": len(props),
                "animations": len(catalog),
                "maps": len(maps),
                "preview": "ok",
            },
            ensure_ascii=False,
        )
    )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test(sys.argv[2] if len(sys.argv) > 2 else default_data_base())
    else:
        Sims2PSPApp().mainloop()


if __name__ == "__main__":
    main()
