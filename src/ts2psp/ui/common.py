"""Small helpers shared by the application tabs."""

import csv
import io
import re
import zipfile
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from ts2psp.formats.animations import REFERENCE_F, REFERENCE_M
from ts2psp.ui.i18n import localize_widget_tree

# Los valores a los que cycla el boton de FPS. Van aqui y no en el panel de
# animaciones porque los usa tambien la reproduccion de los props, que lleva la
# misma tabla de control.
FPS_VALORES = (20, 30, 60, 15)


def read_character_rows(path):
    with zipfile.ZipFile(path) as z:
        return list(
            csv.DictReader(
                io.TextIOWrapper(z.open("personajes.csv"), encoding="utf-8-sig")
            )
        )


def character_fallback(rows, name):
    matching = [r for r in rows if r["personaje"] == name]
    row = max(
        matching,
        key=lambda r: sum(
            bool(r.get(k)) for k in ("bodyTorso", "bodyLegs", "bodyHead", "bodyHair")
        ),
    )
    body = (row.get("bodyTorso") or row.get("bodyLegs") or "").split("/")[-1].lower()
    return REFERENCE_M if body.startswith("am") else REFERENCE_F


def set_photo(label, image):
    if hasattr(label, "set_image"):
        label.set_image(image)
        return
    photo = ImageTk.PhotoImage(image)
    label.configure(image=photo)
    label.image = photo


def export_model_name(name):
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(name)).strip(" .")
    if not result:
        raise ValueError("The model name is not valid for export")
    return result


def choose_export_format(
    parent, item_name, title="Export as...", animation_available=False
):
    """Show the shared format picker, optionally with an animation checkbox.

    Existing callers receive the selected format string. Prop exports that
    expose the animation option receive ``(format, include_animation)``.
    """
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent.winfo_toplevel())
    dialog.resizable(False, False)
    selected = tk.StringVar(value="glb")
    content = ttk.Frame(dialog, padding=16)
    content.pack(fill="both", expand=True)
    ttk.Label(content, text=f"Format for {item_name}:").pack(
        anchor="w", pady=(0, 8)
    )
    for label, value in (
        ("GLB (.glb) — single file", "glb"),
        ("glTF (.gltf) — model, binary and textures", "gltf"),
        ("FBX (.fbx) — requires Blender", "fbx"),
    ):
        ttk.Radiobutton(content, text=label, value=value, variable=selected).pack(
            anchor="w", pady=2
        )
    include_animation = tk.BooleanVar(value=True)
    if animation_available:
        ttk.Checkbutton(
            content,
            text="Include animation",
            variable=include_animation,
        ).pack(anchor="w", pady=(8, 0))
    buttons = ttk.Frame(content)
    buttons.pack(fill="x", pady=(12, 0))
    choice = {"format": None, "include_animation": True}

    def accept():
        choice["format"] = selected.get()
        choice["include_animation"] = include_animation.get()
        dialog.destroy()

    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(
        buttons, text="Export", style="Primary.TButton", command=accept
    ).pack(side="right", padx=(0, 8))
    localize_widget_tree(dialog, getattr(parent.winfo_toplevel(), "language", "en"))
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.grab_set()
    dialog.wait_window()
    if animation_available:
        return choice["format"], choice["include_animation"]
    return choice["format"]
