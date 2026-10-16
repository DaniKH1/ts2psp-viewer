"""Helpers for exporting glTF JSON files from the app's GLB output."""

import json
from pathlib import Path

from ts2psp.formats.animations import open_glb
from ts2psp.core.names import texture_paths


def export_gltf(source, target):
    """Write a GLB as .gltf with external binary and texture files."""
    source = Path(source)
    target = Path(target)
    if target.suffix.lower() != ".gltf":
        raise ValueError("The output file must have a .gltf extension")
    target.parent.mkdir(parents=True, exist_ok=True)
    document, binary = open_glb(source)
    buffers = document.get("buffers", [])
    if len(buffers) != 1:
        raise ValueError("Only models with one glTF buffer can be exported")

    buffer_path = target.with_suffix(".bin")
    buffers[0]["uri"] = buffer_path.name
    image_paths = set()
    names = [
        image.get("name") or f"texture_{i}"
        for i, image in enumerate(document.get("images", []))
    ]
    paths = texture_paths(names, extension=".png")
    for index, image in enumerate(document.get("images", [])):
        if "bufferView" not in image:
            continue
        view_index = image["bufferView"]
        if not isinstance(view_index, int) or not 0 <= view_index < len(
            document.get("bufferViews", [])
        ):
            raise ValueError(f"Invalid buffer view for texture {index}")
        view = document["bufferViews"][view_index]
        if view.get("buffer", 0) != 0:
            raise ValueError(f"Unsupported buffer for texture {index}")
        start = view.get("byteOffset", 0)
        end = start + view.get("byteLength", 0)
        if start < 0 or end > len(binary) or end < start:
            raise ValueError(f"Texture outside binary buffer: {index}")
        mime_type = image.get("mimeType")
        extension = {"image/png": "png", "image/jpeg": "jpg"}.get(mime_type)
        if extension is None:
            raise ValueError(f"Unsupported texture type: {mime_type}")
        relative = Path(paths[names[index]]).with_suffix("." + extension).as_posix()
        filename = f"{target.stem}_textures/{relative}"
        if filename in image_paths:
            # GLBs from other tools may repeat image names for different pixels.
            path = Path(filename)
            filename = str(
                path.with_name(f"{path.stem}__{index}{path.suffix}")
            ).replace("\\", "/")
            while filename in image_paths:
                path = Path(filename)
                filename = str(path.with_name(f"{path.stem}_2{path.suffix}")).replace(
                    "\\", "/"
                )
        image_paths.add(filename)
        destination = target.parent / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(binary[start:end])
        image["uri"] = filename
        image.pop("bufferView")
        image.pop("mimeType", None)

    buffer_path.write_bytes(binary[: buffers[0]["byteLength"]])
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target
