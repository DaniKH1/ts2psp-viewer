"""Locations for bundled assets and the user's game resource cache."""

import os
import sys
from pathlib import Path


def project_root():
    return Path(__file__).resolve().parents[2]


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
