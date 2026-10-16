"""Audit every imported map; optionally save GPU comparisons of color modes."""

import argparse
import csv
import io
import json
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ts2psp.formats.props import parse_rdms


def audit(data, path):
    parts, rejected = parse_rdms(data, ruta=path)
    # MapTab includes authored static world meshes even without a NODE instance.
    visible = parts
    colors = np.asarray(
        [v[3] for p in visible for v in p["vertices"] if v[3] is not None]
    )
    layered = [p for p in parts if p.get("texture_blend")]
    errors = list(rejected)
    if len(colors) and (
        not np.isfinite(colors).all() or np.min(colors) < 0 or np.max(colors) > 1
    ):
        errors.append("Invalid source vertex color")
    for part in visible:
        if not np.isfinite([v[0] for v in part["vertices"]]).all():
            errors.append(f"{part['name']}: invalid geometry")
    for part in layered:
        if len(part["secondary_uvs"]) != len(part["vertices"]):
            errors.append(f"{part['name']}: missing second UV stream")
        elif not np.isfinite(part["secondary_uvs"]).all():
            errors.append(f"{part['name']}: invalid second UV stream")
    report = {
        "map": path,
        "parts": len(parts),
        "visible_parts": len(visible),
        "source_colored_vertices": len(colors),
        "source_black_vertices": (
            int(np.sum(np.max(colors[:, :3], axis=1) == 0)) if len(colors) else 0
        ),
        "diffuse_blend_parts": len(layered),
        "blend_details": [
            {
                "part": p["name"],
                "textures": [l["source"] for l in p["texture_layers"]],
                "uv_vertices": len(p["secondary_uvs"]),
                "uv_source": p["secondary_uv_source"],
                "authoring_helper": p.get("helper", False),
                "vertex_alpha_mask": any(v[3] is not None for v in p["vertices"]),
                "alpha_mode": p["alpha_mode"],
            }
            for p in layered
        ],
        "additional_materials": [
            {"part": p["name"], "modes": [l["mode"] for l in p["texture_layers"]]}
            for p in visible
            if len(p.get("texture_layers", [])) > 1 and not p.get("texture_blend")
        ],
        "errors": errors,
    }
    return report, visible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root, reports, previews = None, [], []
    if args.render:
        import tkinter as tk
        from ts2psp.rendering.props import prop_scene
        from ts2psp.ui.maps import es_cupula
        import ts2psp.rendering.opengl as gl

        root = tk.Tk()
        root.withdraw()
    try:
        with zipfile.ZipFile(args.data / "sims2_psp_mapas_recursos.zip") as archive:
            rows = csv.DictReader(
                io.StringIO(archive.read("inventario_mapas.csv").decode("utf-8-sig"))
            )
            for row in rows:
                started = time.perf_counter()
                report, parts = audit(
                    archive.read(row["archivo_zip"]), row["ruta_interna"]
                )
                if args.render:
                    scene = prop_scene(
                        [p for p in parts if not es_cupula(p)],
                        ("map-audit", row["entry"]),
                        culling=True,
                    )
                    scene.mostrar_grid = False
                    frames, switches = [], []
                    geometry, uv, blend = (
                        scene.posiciones,
                        scene.UV,
                        scene._blend_coords,
                    )
                    for label, enabled, fill in (
                        ("textures", False, 0),
                        ("source", True, 0),
                        ("fill", True, 0.5),
                    ):
                        tick = time.perf_counter()
                        scene.fijar_vertex_colors(enabled, fill)
                        frame = scene.render(700, 460, 0.4, -0.85, 1.25)
                        frame.save(args.output / f"{row['entry']}_{label}.png")
                        switches.append(time.perf_counter() - tick)
                        frames.append(frame)
                        assert (
                            scene.posiciones is geometry
                            and scene.UV is uv
                            and scene._blend_coords is blend
                        )
                    report["gpu_mode_seconds"] = switches
                    report["gpu_color_difference"] = float(
                        np.abs(
                            np.asarray(frames[0], dtype=float)
                            - np.asarray(frames[2], dtype=float)
                        ).mean()
                    )
                    previews.append((row["ruta_interna"], frames))
                    gl.forget_all()
                    root.update()
                report["seconds"] = time.perf_counter() - started
                reports.append(report)
                print(
                    json.dumps(
                        {
                            k: report[k]
                            for k in ("map", "diffuse_blend_parts", "errors", "seconds")
                        }
                    ),
                    flush=True,
                )
        if previews:
            for page in range(0, len(previews), 7):
                sheet = Image.new("RGB", (1050, 7 * 256), "#20232f")
                draw = ImageDraw.Draw(sheet)
                for row, (name, frames) in enumerate(previews[page : page + 7]):
                    draw.text(
                        (6, row * 256 + 3),
                        name + " | Textures / Source vertex colors / Preview fill",
                        fill="white",
                    )
                    for column, frame in enumerate(frames):
                        sheet.paste(
                            frame.resize((350, 230)), (column * 350, row * 256 + 22)
                        )
                sheet.save(args.output / f"overview_{page // 7 + 1}.png")
        (args.output / "audit.json").write_text(
            json.dumps(reports, indent=2), encoding="utf-8"
        )
        assert not any(r["errors"] for r in reports), "Map errors; see audit.json"
    finally:
        if root:
            gl.forget_all()
            root.destroy()


if __name__ == "__main__":
    main()
