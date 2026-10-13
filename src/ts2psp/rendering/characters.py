"""Character loading and previews."""

import io
import zipfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from ts2psp.formats.characters import (
    TextureLibrary,
    find_character_mesh,
    align_arm_bind_pose,
    arm_bind_offset,
    character_mesh_paths,
    character_mesh_texture,
    character_texture_key,
    load_character_rows,
    parse_bones,
    parse_character_mesh,
    parse_character_parts,
    parse_face_archetype,
    face_base_path,
    face_linked_shapes,
    is_face_archetype,
    find_mesh,
    select_character_row,
    PERSONAJES_OCULTOS,
)
import ts2psp.rendering.opengl as gl


def list_characters(z):
    rows = load_character_rows(z)
    return sorted(
        {
            r["personaje"]
            for r in rows
            if (
                any(
                    r.get(k)
                    for k in ["bodyTorso", "bodyLegs", "bodyHead", "bodyHair"]
                )
                or character_mesh_paths(z, r)
            )
            and r["personaje"] not in PERSONAJES_OCULTOS
        }
    )


def load_character(zip_path, character, archetype=None):
    with zipfile.ZipFile(zip_path) as z:
        rows = load_character_rows(z)
        row = select_character_row(rows, character)
        paths = character_mesh_paths(z, row, archetype)
        parts = []
        bones = None
        for path in paths:
            data = find_character_mesh(z, row, path)
            if bones is None:
                bones = parse_bones(
                    find_mesh(z, face_base_path(path))
                    if path.startswith("face/cas/")
                    else data
                )
            if is_face_archetype(path):
                base = face_base_path(path)
                base_parts = parse_character_mesh(base, find_mesh(z, base))
                mesh_parts = parse_face_archetype(
                    data, base_parts, face_linked_shapes(z, path)
                )
            else:
                mesh_parts = parse_character_parts(row, path, data)
            for part in mesh_parts:
                part["shape_scope"] = (
                    face_base_path(path) if path.startswith("face/cas/") else path
                ).casefold()
                if (
                    row["personaje"].casefold() == "lincolnbroadsheet"
                    and path == "body/ambodyhovercodpiece"
                ):
                    part["double_sided"] = True
                if (
                    row["personaje"].casefold() == "nightbeast"
                    and path == "body/aubodynightbeast"
                ):
                    part_index = (
                        int(part["name"].removeprefix("pieza_"))
                        if part["name"].startswith("pieza_")
                        else 0
                    )
                    part["repeat_texture"] = part_index > 29
                parts.append((path, part))
        torso_parts = [part for path, part in parts if path == row.get("bodyTorso")]
        if bones and torso_parts:
            bones = align_arm_bind_pose(bones, arm_bind_offset(bones, torso_parts))

    textures = {}
    texture_zip = Path(zip_path).with_name("sims2_psp_personajes_texturas_png.zip")
    if texture_zip.exists():
        with zipfile.ZipFile(texture_zip) as z:
            library = TextureLibrary(z)
            textured_parts = []
            for path, part in parts:
                texture_key = character_texture_key(row, path, part["name"])
                found = character_mesh_texture(library, row, path, part["name"])
                if found:
                    textures[texture_key] = np.asarray(
                        Image.open(io.BytesIO(found[1])).convert("RGBA")
                    )
                textured_parts.append((texture_key, part))
            parts = textured_parts
    return paths, parts, bones, textures


def _shape_positions(parts, shape, shape_weight, shape_id=None):
    """Positions with the morph applied, as float32 rows ready for the GPU."""
    rows = []
    for path, part in parts:
        if part.get("oculta"):
            continue
        names = part.get("morph_names", [])
        if shape_id is not None:
            scope, morph_id = shape_id
            morph_ids = part.get("morph_ids", [])
            part_scope = part.get("shape_scope", path).casefold()
            morph = (
                morph_ids.index(morph_id)
                if part_scope == scope and morph_id in morph_ids
                else None
            )
        else:
            morph = names.index(shape) if shape in names else None
        for i, (pos, _, _, _) in enumerate(part["vertices"]):
            if morph is None:
                rows.append((pos[0] / 1000.0, pos[1] / 1000.0, pos[2] / 1000.0))
            else:
                delta = part["morph_positions"][morph][i]
                rows.append(
                    tuple((pos[j] + delta[j] * shape_weight) / 1000.0 for j in range(3))
                )
    return rows


def shape_key_options(parts, preserve_morph_ids=False):
    if preserve_morph_ids:
        options = {}
        for path, part in parts:
            scope = part.get("shape_scope", path).casefold()
            for index, name in enumerate(part.get("morph_names", [])):
                morph_ids = part.get("morph_ids", [])
                if index < len(morph_ids):
                    options.setdefault((scope, morph_ids[index]), name)
        return sorted(
            ((name, identity) for identity, name in options.items()),
            key=lambda option: (option[0].casefold(), option[1]),
        )
    names = sorted({name for _, part in parts for name in part.get("morph_names", [])})
    return [(name, None) for name in names]


def draw_character(
    parts,
    bones,
    width=800,
    height=800,
    yaw=0.0,
    pitch=0.0,
    zoom=1.0,
    show_bones=False,
    shape="",
    shape_weight=0.0,
    textures=None,
    pan=(0, 0),
    key=None,
    camara=None,
    show_grid=True,
    shape_id=None,
):
    """Dibuja un personaje con la GPU. `key` identifica el modelo en la caché.

    No hay render por CPU: si el equipo no puede acelerar avisa con gl.SinOpenGL y el
    llamante lo enseña en su barra de estado.
    """
    creature = any(
        path.startswith(("body/aubodycowthulu", "body/aubodynightbeast"))
        for path, _ in parts
    )
    scene = gl.escena(
        ("personaje", key),
        parts,
        textures,
        "personaje_criatura" if creature else "personaje",
    )
    scene.mostrar_grid = show_grid
    pose_key = (shape, shape_id, shape_weight)
    if scene.pose_actual() != pose_key:
        positions = _shape_positions(parts, shape, shape_weight, shape_id)
        scene.fijar_poses(positions, pose_key)
    image = scene.render(width, height, yaw, pitch, zoom, pan, camara)
    if show_bones and bones:
        overlay = ImageDraw.Draw(image)
        pts = [
            scene.proyectar(
                b["world"][3, :3], width, height, yaw, pitch, zoom, pan, camara
            )
            for b in bones
        ]
        for i, bone in enumerate(bones):
            if bone["parent"] >= 0:
                overlay.line(
                    (pts[bone["parent"]], pts[i]), fill=(88, 235, 220), width=2
                )
        for x, y in pts:
            overlay.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(225, 255, 250))
    return image
