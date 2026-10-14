"""Locations for bundled assets and the user's game resource cache."""

import os
import sys
from pathlib import Path


def project_root():
    return Path(__file__).resolve().parents[2]


def beside_app():
    """La carpeta que tiene el .bat o el .exe, que es donde seexports.

    Con PyInstaller el ejecutable va dentro de la carpeta; al Developing desde
    el codigo, lo que hay al lado es el .bat y los .py. Si esa carpeta no
    admitiera escritura (Program Files, por ejemplo) se cae a Documentos, que
    siempre la tiene, en vez de reventar al exportar.
    """
    candidate = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else project_root()
    )
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".ts2psp-escribe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TS2 PSP Viewer"
    return candidate


def resource_path(relative):
    base = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    return base / relative


def default_data_base():
    configured = os.environ.get("TS2_PSP_DATA_DIR")
    if configured:
        return Path(configured)
    beside_app = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else project_root()
    )
    if (beside_app / "data" / "sims2_psp_props_recursos.zip").is_file() or (
        beside_app / "sims2_psp_props_recursos.zip"
    ).is_file():
        return beside_app
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TS2 PSP Viewer"
