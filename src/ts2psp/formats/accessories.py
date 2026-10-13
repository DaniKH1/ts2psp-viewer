"""Character accessory declarations and their supported mesh parts."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Accessory:
    mesh: str
    parts: tuple[str, ...]
    texture: str


# The maid resource includes another hairstyle. Its first part is the hat only.
ACCESSORIES = {
    "fx_hatmaid": Accessory(
        "hair/afhairhatmaid",
        ("principal",),
        "hair/afhairhatmaid/afhairhatmaid-lace",
    ),
}

_ASSIGNMENT = re.compile(r'^\s*(\w+)\s*=\s*["\']([^"\']+)["\']', re.M)
_SPECIES = re.compile(r"\bnewSpecies\s*\{(.*?)^\s*\}", re.M | re.S)
_SLOT = re.compile(r"accessories_\d+$")


def species_accessories(content, row):
    """Read only the matching species: some resources contain several NPCs."""
    content = re.sub(r"--[^\r\n]*", "", content)
    for block in reversed(_SPECIES.findall(content)):
        fields = dict(_ASSIGNMENT.findall(block))
        if all(
            fields.get(key, "") == row.get(key, "")
            for key in ("bodyHead", "bodyHair", "bodyTorso", "bodyLegs")
        ):
            return {key: value for key, value in fields.items() if _SLOT.fullmatch(key)}
    return {}


def accessory_names(archive, row):
    slots = {key: value for key, value in row.items() if _SLOT.fullmatch(key) and value}
    if not slots and row.get("archivo_zip"):
        try:
            content = archive.read(row["archivo_zip"]).decode("latin1")
        except KeyError:
            content = ""
        slots = species_accessories(content, row)
    return tuple(dict.fromkeys(slots[key] for key in sorted(slots)))


def accessory_mesh_paths(archive, row):
    paths = []
    for name in accessory_names(archive, row):
        accessory = ACCESSORIES.get(name)
        if accessory is not None:
            resource = f"mallas/characters/bodyanims/{accessory.mesh}.res"
            if resource in archive.namelist():
                paths.append(accessory.mesh)
    return paths


def accessory_for_mesh(path):
    return next((item for item in ACCESSORIES.values() if item.mesh == path), None)


def select_accessory_parts(row, path, parts):
    accessory = accessory_for_mesh(path)
    if accessory is None or row.get("bodyHair") == path:
        return parts
    return [part for part in parts if part["name"] in accessory.parts]


def accessory_texture(library, path, part_name):
    accessory = accessory_for_mesh(path)
    if accessory is None or part_name not in accessory.parts:
        return None
    png = library.read(accessory.texture)
    return (accessory.texture, png) if png is not None else None
