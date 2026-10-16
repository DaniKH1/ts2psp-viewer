"""Readable asset labels without changing mesh IDs used by the renderer."""

import re
import hashlib
from pathlib import PurePosixPath


def layer_label(part):
    """Compact texture names for the layer browser; mesh IDs stay unchanged."""
    texture = part.get("texture")
    sources = [texture[0] if texture else part.get("source_texture_path", "")]
    if part.get("texture_blend"):
        sources = [layer["source"] for layer in part["texture_layers"]]
    names = []
    for source in sources:
        name = PurePosixPath(source.replace("\\", "/")).name
        name = re.sub(r"\.(tiff?|png|bmp|dds|tga)$", "", name, flags=re.I)
        name = re.sub(r"^txt_", "", name, flags=re.I)
        if name and name not in names:
            names.append(name)
    return " + ".join(names) or "Untextured"


def part_label(part, texture=None):
    texture = texture if texture is not None else part.get("texture")
    source = texture[0] if texture else part.get("source_texture_path", "")
    if not source:
        return part["name"]
    source = source.replace("\\", "/")
    if part.get("texture_blend"):
        source = " + ".join(
            PurePosixPath(layer["source"]).name for layer in part["texture_layers"]
        )
    return f'{PurePosixPath(source).name} / {part["name"]}'


def texture_path(name, extension=".png"):
    """Keep internal folders while excluding absolute paths and traversal."""
    components = []
    for item in str(name).replace("\\", "/").split("/"):
        item = re.sub(r"[^A-Za-z0-9._-]+", "_", item).strip(". ")
        if item:
            components.append(item)
    path = (
        PurePosixPath(*components) if components else PurePosixPath("unnamed_texture")
    )
    return str(path.with_suffix(extension))


def part_filename(part):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", part_label(part)).strip("._")


def texture_paths(names, extension=".png"):
    """Resolve collisions after sanitizing paths and replacing bitmap suffixes."""
    result, used = {}, set()
    for name in names:
        if name in result:
            continue
        path = texture_path(name, extension)
        if path.casefold() in used:
            source = PurePosixPath(path)
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
            path = str(source.with_name(f"{source.stem}__{digest}{extension}"))
            while path.casefold() in used:
                path = str(PurePosixPath(path).with_suffix("")) + "_2" + extension
        result[name] = path
        used.add(path.casefold())
    return result
