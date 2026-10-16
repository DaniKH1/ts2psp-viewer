"""Inspect decoded textures and UV triangles for PSP meshes."""

import io
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk
from ts2psp.core.names import part_label


def character_uv_parts(parts, textures):
    result = []
    for path, part in parts:
        tex = textures.get(path)
        vertices = []
        for _, _, uv, _ in part["vertices"]:
            vertices.append(
                (
                    None,
                    None,
                    ((uv[0] + 32768) / 4096.0, 1.0 - (uv[1] + 32768) / 4096.0),
                    None,
                )
            )
        result.append(
            {
                "name": f"{path}/{part_label(part)}",
                "vertices": vertices,
                "indices": part["indices"],
                "texture": Image.fromarray(tex) if tex is not None else None,
            }
        )
    return result


def uv_image(part, size=768, show_lines=True):
    source = part.get("texture")
    if isinstance(source, tuple):
        source = Image.open(io.BytesIO(source[1]))
    elif isinstance(source, np.ndarray):
        source = Image.fromarray(source)
    if source is None:
        image = Image.new("RGBA", (size, size), (52, 55, 68, 255))
    else:
        image = source.convert("RGBA").resize((size, size), Image.Resampling.NEAREST)
    if show_lines:
        draw = ImageDraw.Draw(image)
        uv = [v[2] for v in part["vertices"]]
        indices = part["indices"]
        for i in range(0, len(indices), 3):
            tri = [uv[j] for j in indices[i : i + 3]]
            if len(tri) != 3 or any(x is None for x in tri):
                continue
            points = [(round(u[0] * size), round(u[1] * size)) for u in tri]
            draw.line(points + points[:1], fill=(0, 20, 30, 245), width=3)
            draw.line(points + points[:1], fill=(90, 255, 238, 255), width=1)
    return image


class UVWindow(tk.Toplevel):
    def __init__(self, parent, parts, title):
        super().__init__(parent)
        expanded = []
        for part in parts:
            if part.get("texture_blend") and part.get("secondary_uvs"):
                for i, layer in enumerate(part["texture_layers"]):
                    uvs = (
                        part["secondary_uvs"] if i else [v[2] for v in part["vertices"]]
                    )
                    expanded.append(
                        {
                            **part,
                            "name": f"{part['name']} · layer {i + 1}",
                            "texture": layer["texture"],
                            "texture_blend": False,
                            "vertices": [
                                (v[0], v[1], uv, v[3])
                                for v, uv in zip(part["vertices"], uvs)
                            ],
                        }
                    )
            else:
                expanded.append(part)
        parts = expanded
        self.parts = [p for p in parts if any(v[2] is not None for v in p["vertices"])]
        self.title(f"Texture maps and UVs · {title}")
        self.geometry("850x910")
        self.name = tk.StringVar()
        self.lines = tk.BooleanVar(value=True)
        controls = ttk.Frame(self, padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="Part").pack(side="left")
        self.select = ttk.Combobox(
            controls, textvariable=self.name, state="readonly", width=55
        )
        self.select.pack(side="left", fill="x", expand=True, padx=8)
        self.select["values"] = [part_label(p) for p in self.parts]
        self.select.bind("<<ComboboxSelected>>", self.render)
        ttk.Checkbutton(
            controls, text="Mostrar UV", variable=self.lines, command=self.render
        ).pack(side="left")
        ttk.Button(controls, text="Save PNG", command=self.save).pack(
            side="left", padx=8
        )
        self.label = ttk.Label(self, anchor="center")
        self.label.pack(fill="both", expand=True)
        self.status = ttk.Label(self, padding=8)
        self.status.pack(fill="x")
        self.image = None
        if self.parts:
            self.select.current(0)
            self.render()
        else:
            self.status.configure(text="The model has no readable UV coordinates.")

    def render(self, _event=None):
        if not self.parts:
            return
        part = self.parts[self.select.current()]
        self.image = uv_image(part, show_lines=self.lines.get())
        photo = ImageTk.PhotoImage(self.image)
        self.label.configure(image=photo)
        self.label.image = photo
        texture = "texture" if part.get("texture") is not None else "no texture"
        self.status.configure(
            text=f"{part_label(part)} · {texture} · "
            f'{len(part["indices"]) // 3} UV triangles'
        )

    def save(self):
        if self.image is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png", filetypes=[("Imagen PNG", "*.png")]
        )
        if path:
            self.image.save(path)
