"""Small helpers shared by the application tabs."""

import csv
import io
import re
import zipfile
from PIL import ImageTk
from ts2psp.formats.animations import REFERENCE_F, REFERENCE_M

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
