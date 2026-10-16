"""Extract the supported Sims 2 PSP resources from a user-selected ISO.

The generated archives are local cache files; no game assets are distributed
with the application source.
"""

import csv
import io
import json
import mmap
import os
import re
import struct
import tempfile
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from PIL import Image

from ts2psp.formats.animations import clip_name, parse_banm
from ts2psp.formats.accessories import species_accessories

MAGIC = b"\x8e\xac\xac\x1b"
ARCHIVES = {
    "characters": "sims2_psp_personajes_recursos.zip",
    "textures": "sims2_psp_personajes_texturas_png.zip",
    "props": "sims2_psp_props_recursos.zip",
    "animations": "sims2_psp_animaciones_banm.zip",
    "maps": "sims2_psp_mapas_recursos.zip",
}
MESH_PATH = re.compile(
    rb"characters/bodyanims/(?:body|bottom|face|facialhair|hair|top)/[A-Za-z0-9_./-]+"
)
CHAR_PATH = re.compile(rb"characters/([A-Za-z0-9_-]+)/character\x00")
ASSIGNMENT = re.compile(
    r'^\s*(bodyHead|bodyHair|bodyTorso|bodyLegs|faceArchetype|texture\w+)\s*=\s*["\']([^"\']+)["\']',
    re.M,
)
TEXTURE_PATH = re.compile(rb"characters/bodyanims/[A-Za-z0-9_./-]+\.tif")
PROP_PATH = re.compile(rb"objects/[A-Za-z0-9_./-]+")
LEVEL_PATH = re.compile(rb"levels/[A-Za-z0-9_./-]+")


def csv_bytes(rows, fields):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode("utf-8-sig")


def find_pathcache(mm):
    base = mm.find(MAGIC, 0, min(len(mm), 128 * 1024 * 1024))
    if base < 0:
        raise ValueError(
            "PATHCACHE not found. Check that this is a The Sims 2 PSP ISO."
        )
    return base


def index_iso(mm, base):
    rows = []
    visited = set()

    def walk(pos, depth=0):
        if pos in visited:
            return
        if depth > 30 or pos < 0 or base + pos + 12 > len(mm):
            raise ValueError("Invalid PATHCACHE index")
        visited.add(pos)
        _, info = struct.unpack_from("<II", mm, base + pos)
        dirs, files = struct.unpack_from("<HH", mm, base + pos + 8)
        if dirs + files > 20000 or base + info + (dirs + files) * 12 > len(mm):
            raise ValueError("Tabla PATHCACHE fuera de rango")
        for i in range(dirs):
            walk(info + i * 12, depth + 1)
        for i in range(files):
            entry = info + (dirs + i) * 12
            _, sector, size = struct.unpack_from("<III", mm, base + entry)
            offset = sector * 2048
            if size and base + offset + size <= len(mm):
                rows.append({"entry": entry, "offset": offset, "size": size})

    walk(16)
    if len(rows) < 1000:
        raise ValueError("The ISO lacks the expected resource index")
    return rows


def res_sections(data):
    if not data.startswith(b"res\n") or len(data) < 32:
        return []
    footer = struct.unpack_from("<I", data, 0x14)[0]
    if footer + 8 > len(data):
        return []
    count = struct.unpack_from("<I", data, footer)[0]
    if count > 10000 or footer + 8 + 20 * count > len(data):
        return []
    return [
        struct.unpack_from("<4s4I", data, footer + 8 + 20 * i) for i in range(count)
    ]


def object_resource_name(data, records):
    """Only read the resource name table, never incidental script references."""
    header = struct.unpack_from("<I", data, 12)[0]
    for tag, off, size, _, _ in records:
        if tag == b"strg":
            paths = PROP_PATH.findall(data[off + header : off + header + size])
            if paths:
                return paths[-1].decode("ascii")
    return ""


def decode_texture(data):
    if data[:4] != b"res\n" or data[0x38:0x3C] != b"surf":
        return None
    hits = list(TEXTURE_PATH.finditer(data))
    if not hits or hits[-1].start() < len(data) - 2048:
        return None
    internal = hits[-1].group().decode("ascii")
    surf = struct.unpack_from("<I", data, 12)[0]
    if surf + 0x80 > len(data):
        return None
    flags = struct.unpack_from("<I", data, surf + 8)[0]
    width, height = struct.unpack_from("<HH", data, surf + 12)
    if flags & 0xFFFFFF != 0x010118:
        return None
    if not (
        8 <= width <= 512
        and 8 <= height <= 512
        and width & (width - 1) == 0
        and height & (height - 1) == 0
    ):
        return None
    length = width * height * 2
    start = surf + 0x80
    if start + length > len(data) or len(data) > int(length * 4 / 3) + 2048:
        return None
    raw = np.frombuffer(data[start : start + length], dtype=np.uint8)
    linear = raw.reshape(height // 8, width * 2 // 16, 8, 16)
    vals = linear.transpose(0, 2, 1, 3).reshape(height, width * 2)
    vals = vals.view("<u2").reshape(height, width)
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[:, :, 0] = (vals & 15) * 17
    rgba[:, :, 1] = ((vals >> 4) & 15) * 17
    rgba[:, :, 2] = ((vals >> 8) & 15) * 17
    rgba[:, :, 3] = ((vals >> 12) & 15) * 17
    image = Image.fromarray(rgba, "RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return internal, width, height, output.getvalue()


class ImportCancelled(Exception):
    """The caller requested cancellation before publishing the new cache."""


def _check_cancel(cancelled):
    if cancelled and cancelled():
        raise ImportCancelled()


def _validate_archive(path, progress, cancelled):
    """Read CRC-checked chunks so validation reports progress and can stop."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        total = sum(entry.file_size for entry in entries)
        read = 0
        last = 0.0
        for entry in entries:
            with archive.open(entry) as source:
                while True:
                    _check_cancel(cancelled)
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    read += len(block)
                    now = time.monotonic()
                    if now - last >= 0.25:
                        progress(
                            f"Validating {path.name}: {read * 100 // max(1, total)}%"
                        )
                        last = now


def import_iso(iso_path, output_dir, progress=None, cancelled=None):
    """Create five local resource archives, reporting text progress on demand."""
    iso_path = Path(iso_path)
    output_dir = Path(output_dir)
    if not iso_path.is_file():
        raise FileNotFoundError(iso_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    progress = progress or (lambda message: None)
    _check_cancel(cancelled)
    progress("Reading the ISO index…")
    with tempfile.TemporaryDirectory(
        prefix="sims2_psp_import_", dir=output_dir
    ) as temp:
        staging = Path(temp)
        with (
            iso_path.open("rb") as source,
            mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as mm,
        ):
            base = find_pathcache(mm)
            index = index_iso(mm, base)
            progress(f"Index read: {len(index)} entries. Extracting resources…")
            meshes = []
            characters = []
            textures = []
            props = []
            animations = []
            maps = []
            prop_support = {"version": 1, "textures": {}, "animations": []}
            used_meshes = set()
            used_textures = set()
            used_props = set()
            with ExitStack() as stack:
                zips = {
                    kind: stack.enter_context(
                        zipfile.ZipFile(
                            staging / name,
                            "w",
                            compression=zipfile.ZIP_DEFLATED,
                            compresslevel=6,
                        )
                    )
                    for kind, name in ARCHIVES.items()
                }
                for i, row in enumerate(index, 1):
                    _check_cancel(cancelled)
                    if i == 1 or i % 100 == 0:
                        progress(
                            f"Extracting resources: {i:,}/{len(index):,} ({i * 100 // len(index)}%)"
                        )
                    start = base + row["offset"]
                    data = mm[start : start + row["size"]]
                    if not data.startswith(b"res\n"):
                        continue
                    records = res_sections(data)
                    object_name = object_resource_name(data, records)
                    if object_name and b".." not in object_name.encode().split(b"/"):
                        if any(s[0] == b"surf" for s in records) and not any(
                            s[0] in (b"bmsh", b"rdms", b"node", b"levl")
                            for s in records
                        ):
                            surf = next(s for s in records if s[0] == b"surf")
                            file = f'support/textures/{row["entry"]}.res'
                            zips["props"].writestr(file, data)
                            prop_support["textures"][object_name.casefold()] = {
                                "file": file,
                                "off": surf[1],
                                "size": surf[2],
                            }
                        if any(s[0] == b"banm" for s in records):
                            file = f'support/animations/{row["entry"]}.res'
                            zips["props"].writestr(file, data)
                            try:
                                clip = parse_banm(data)
                            except (ValueError, struct.error) as exc:
                                prop_support.setdefault(
                                    "unsupported_animations", []
                                ).append(
                                    {
                                        "name": object_name,
                                        "file": file,
                                        "error": str(exc),
                                    }
                                )
                            else:
                                prop_support["animations"].append(
                                    {
                                        "name": object_name,
                                        "file": file,
                                        "duration": clip["duration"],
                                    }
                                )

                    if b"bmsh" in data:
                        paths = MESH_PATH.findall(data)
                        if paths:
                            internal = paths[-1].decode("ascii")
                            name = "mallas/" + internal + ".res"
                            if name in used_meshes:
                                name = (
                                    "mallas/"
                                    + internal
                                    + "__entry"
                                    + str(row["entry"])
                                    + ".res"
                                )
                            used_meshes.add(name)
                            zips["characters"].writestr(name, data)
                            meshes.append(
                                {
                                    "entry": row["entry"],
                                    "ruta_interna": internal,
                                    "tamano_bytes": row["size"],
                                    "archivo_zip": name,
                                }
                            )

                    if b"lua!" in data:
                        matches = CHAR_PATH.findall(data)
                        if matches:
                            slug = matches[-1].decode("ascii")
                            name = f'fichas/{slug}__entry{row["entry"]}.res'
                            zips["characters"].writestr(name, data)
                            content = re.sub(
                                r"--[^\r\n]*", "", data.decode("latin1", "replace")
                            )
                            assignments = dict(ASSIGNMENT.findall(content))
                            assignments.update(
                                species_accessories(content, assignments)
                            )
                            characters.append(
                                {
                                    "personaje": slug,
                                    "entry": row["entry"],
                                    "archivo_zip": name,
                                    **assignments,
                                }
                            )

                    texture = decode_texture(data)
                    if texture:
                        internal, width, height, png = texture
                        name = "texturas/" + internal[:-4] + ".png"
                        if name in used_textures:
                            name = (
                                "texturas/"
                                + internal[:-4]
                                + "__entry"
                                + str(row["entry"])
                                + ".png"
                            )
                        used_textures.add(name)
                        zips["textures"].writestr(name, png)
                        textures.append(
                            {
                                "entry": row["entry"],
                                "ruta_interna": internal,
                                "ancho": width,
                                "alto": height,
                                "archivo_zip": name,
                            }
                        )

                    if b"rdms" in data[:128] or b"bmsh" in data[:128]:
                        sections = res_sections(data)
                        if any(
                            section[0] in (b"rdms", b"bmsh") for section in sections
                        ):
                            paths = PROP_PATH.findall(data[-4096:])
                            if paths:
                                internal = paths[-1].decode("ascii")
                                if ".." not in internal.split("/"):
                                    kind = (
                                        "bmsh"
                                        if any(s[0] == b"bmsh" for s in sections)
                                        else "rdms"
                                    )
                                    name = "recursos/" + internal + ".res"
                                    if name in used_props:
                                        name = (
                                            "recursos/"
                                            + internal
                                            + "__entry"
                                            + str(row["entry"])
                                            + ".res"
                                        )
                                    used_props.add(name)
                                    zips["props"].writestr(name, data)
                                    props.append(
                                        {
                                            "entry": row["entry"],
                                            "ruta_interna": internal,
                                            "formato": kind,
                                            "tamano_bytes": row["size"],
                                            "archivo_zip": name,
                                        }
                                    )

                    if b"banm" in data[:96]:
                        name = clip_name(data)
                        if name.startswith("characters/bodyanims/"):
                            clip = parse_banm(data)
                            category = name.split("/")[2]
                            file = f'res/{row["entry"]}.res'
                            zips["animations"].writestr(file, data)
                            animations.append(
                                {
                                    **row,
                                    "name": name,
                                    "duration": round(clip["duration"], 6),
                                    "tracks": len(clip["tracks"]),
                                    "category": category,
                                    "file": file,
                                }
                            )

                    if b"levl" in data:
                        level_sections = res_sections(data)
                        if any(section[0] == b"levl" for section in level_sections):
                            found = LEVEL_PATH.findall(data)
                            if found:
                                internal = found[-1].decode("ascii")
                                file = f'res/{row["entry"]}.res'
                                zips["maps"].writestr(file, data)
                                maps.append(
                                    {
                                        "entry": row["entry"],
                                        "ruta_interna": internal,
                                        "tamano_bytes": row["size"],
                                        "piezas_rdms": sum(
                                            s[0] == b"rdms" for s in level_sections
                                        ),
                                        "superficies": sum(
                                            s[0] == b"surf" for s in level_sections
                                        ),
                                        "terrenos": sum(
                                            s[0] == b"tern" for s in level_sections
                                        ),
                                        "archivo_zip": file,
                                    }
                                )

                progress("Writing resource catalogues…")
                char_fields = ["personaje", "entry", "archivo_zip"] + sorted(
                    {
                        key
                        for row in characters
                        for key in row
                        if key not in ("personaje", "entry", "archivo_zip")
                    }
                )
                zips["characters"].writestr(
                    "inventario_mallas.csv",
                    csv_bytes(
                        meshes, ["entry", "ruta_interna", "tamano_bytes", "archivo_zip"]
                    ),
                )
                zips["characters"].writestr(
                    "personajes.csv", csv_bytes(characters, char_fields)
                )
                zips["textures"].writestr(
                    "inventario_texturas.csv",
                    csv_bytes(
                        textures,
                        ["entry", "ruta_interna", "ancho", "alto", "archivo_zip"],
                    ),
                )
                zips["props"].writestr(
                    "inventario_props.csv",
                    csv_bytes(
                        props,
                        [
                            "entry",
                            "ruta_interna",
                            "formato",
                            "tamano_bytes",
                            "archivo_zip",
                        ],
                    ),
                )
                zips["props"].writestr(
                    "prop_support.json", json.dumps(prop_support, indent=2)
                )

                zips["animations"].writestr(
                    "catalog.json", json.dumps(animations, ensure_ascii=False, indent=2)
                )
                zips["animations"].writestr(
                    "animaciones.csv",
                    csv_bytes(
                        animations,
                        [
                            "entry",
                            "name",
                            "category",
                            "duration",
                            "tracks",
                            "size",
                            "file",
                        ],
                    ),
                )
                zips["maps"].writestr(
                    "inventario_mapas.csv",
                    csv_bytes(
                        maps,
                        [
                            "entry",
                            "ruta_interna",
                            "tamano_bytes",
                            "piezas_rdms",
                            "superficies",
                            "terrenos",
                            "archivo_zip",
                        ],
                    ),
                )
            usable = {
                r["personaje"]
                for r in characters
                if any(
                    r.get(key)
                    for key in ("bodyTorso", "bodyLegs", "bodyHead", "bodyHair")
                )
            }
            counts = {
                "characters": len(usable),
                "meshes": len(meshes),
                "textures": len(textures),
                "props": len(props),
                "animations": len(animations),
                "maps": len(maps),
            }
            if any(value == 0 for value in counts.values()):
                raise ValueError(
                    f"La ISO no produjo todos los tipos de recursos: {counts}"
                )
        for name in ARCHIVES.values():
            progress(f"Validating {name}…")
            _validate_archive(staging / name, progress, cancelled)
        _check_cancel(cancelled)
        progress("Installing validated resource archives…")
        for name in ARCHIVES.values():
            os.replace(staging / name, output_dir / name)
    progress(
        f'Import complete: {counts["characters"]} characters, '
        f'{counts["props"]} objects, {counts["animations"]} animations, '
        f'{counts["maps"]} maps.'
    )
    return counts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(import_iso(args.iso, args.output, print))
