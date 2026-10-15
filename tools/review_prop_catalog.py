"""Render every local prop from two angles and record geometry/material coverage."""

import argparse
import csv
import io
import json
import sys
import textwrap
import tkinter as tk
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ts2psp.formats.props import sections, parse_rdms, parse_prop_bmsh, prop_joints
from ts2psp.formats.prop_scene import node_hierarchy, is_joint
from ts2psp.formats.surfaces import decode_surface
from ts2psp.formats.prop_assets import PropAssets
from ts2psp.formats.props import ALFA_ILEGIBLE
from ts2psp.rendering.props import load_parts, render_prop
import ts2psp.rendering.opengl as gl


def review(archive_path, output):
    output.mkdir(parents=True, exist_ok=True)
    assets = PropAssets(archive_path)
    root = tk.Tk()
    root.withdraw()
    font = ImageFont.truetype("arial.ttf", 13)
    report = {
        "props": 0,
        "surfaces": 0,
        "joint_nodes": 0,
        "helper_parts": 0,
        "textured_parts": 0,
        "solid_color_parts": 0,
        "mixed_wrap_props": 0,
        "errors": [],
        "resources": [],
    }
    cell_width, cell_height = 350, 240
    sheet = None
    with zipfile.ZipFile(archive_path) as archive:
        rows = list(
            csv.DictReader(
                io.StringIO(archive.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        for index, row in enumerate(rows):
            if index % 24 == 0:
                sheet = Image.new(
                    "RGB", (cell_width * 4, cell_height * 6), (27, 31, 42)
                )
            x, y = (index % 4) * cell_width, (index % 24 // 4) * cell_height
            draw = ImageDraw.Draw(sheet)
            draw.text(
                (x + 3, y + 178),
                "\n".join(textwrap.wrap(row["ruta_interna"], 44)),
                font=font,
                fill="white",
            )
            item = {
                "entry": row["entry"],
                "name": row["ruta_interna"],
                "format": row["formato"],
            }
            try:
                data = archive.read(row["archivo_zip"])
                records = sections(data)
                for tag, off, size, _, _ in records:
                    if tag == b"surf":
                        if decode_surface(data, off, size) is None:
                            raise ValueError("Undecoded source texture")
                        report["surfaces"] += 1
                raw = (
                    parse_rdms(data)[0]
                    if row["formato"] == "rdms"
                    else parse_prop_bmsh(data)
                )
                helpers = sum(bool(part.get("helper")) for part in raw)
                report["helper_parts"] += helpers
                nodes = node_hierarchy(data, records)[0]
                report["joint_nodes"] += sum(is_joint(data, node) for node in nodes)
                modes = {}
                for part in raw:
                    if part.get("texture_warning"):
                        raise ValueError(part["texture_warning"])
                    if part.get("texture"):
                        report["textured_parts"] += 1
                        modes.setdefault(part["texture"][0], set()).add(
                            part["repeat_texture"]
                        )
                    else:
                        report["solid_color_parts"] += 1
                report["mixed_wrap_props"] += any(
                    len(states) > 1 for states in modes.values()
                )
                parts = load_parts(
                    data,
                    ruta=row["ruta_interna"],
                    opaco=row["ruta_interna"] in ALFA_ILEGIBLE,
                    assets=assets,
                )
                item.update(
                    parts=len(parts),
                    helper_parts=helpers,
                    textured_parts=sum(bool(part.get("texture")) for part in parts),
                    solid_color_parts=sum(not part.get("texture") for part in parts),
                    generated_uv_parts=sum(
                        p.get("uv_source", "").startswith("generated") for p in parts
                    ),
                    notes=sorted(
                        {
                            p["texture_warning"]
                            for p in parts
                            if p.get("texture_warning")
                        }
                    ),
                )
                if parts:
                    points = np.array(
                        [vertex[0] for part in parts for vertex in part["vertices"]]
                    )
                    if not np.isfinite(points).all():
                        raise ValueError("Nonfinite mesh positions")
                    item["bounds"] = [points.min(0).tolist(), points.max(0).tolist()]
                    prop_joints(data)
                    for angle, yaw in enumerate((0.5, 3.65)):
                        image = render_prop(
                            parts,
                            174,
                            174,
                            yaw=yaw,
                            pitch=0.15,
                            key=f"catalog-{index}",
                            show_grid=False,
                        )
                        sheet.paste(image.convert("RGB"), (x + angle * 175, y))
                else:
                    draw.text(
                        (x + 10, y + 70),
                        "AUTHORING / EFFECT TEMPLATES",
                        font=font,
                        fill="orange",
                    )
                draw.text(
                    (x + 3, y + 222),
                    f'{item["parts"]} parts | {item["textured_parts"]} textured | {helpers} helpers',
                    font=font,
                    fill=(160, 190, 220),
                )
            except Exception as error:
                item["error"] = str(error)
                report["errors"].append(
                    {"name": row["ruta_interna"], "error": str(error)}
                )
                draw.text((x + 5, y + 70), str(error)[:43], font=font, fill="red")
            report["props"] += 1
            report["resources"].append(item)
            if index % 24 == 23 or index == len(rows) - 1:
                sheet.save(output / f"catalog_{index//24 + 1:02}.png")
                print(f"Reviewed {index + 1}/{len(rows)} props", flush=True)
                gl.forget_all()
    root.destroy()
    (output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "resources"},
            indent=2,
        )
    )
    return bool(report["errors"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    sys.exit(review(args.archive, args.output))
