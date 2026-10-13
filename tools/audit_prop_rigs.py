"""Check all local prop rigs and compare exported animation with the preview."""

import argparse
import csv
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from ts2psp.formats.props import prop_joints, convert_prop
from ts2psp.formats.prop_anim import PropAnimator
from ts2psp.formats.animations import open_glb
from ts2psp.rendering.props import load_parts
from ts2psp.rendering.animation import posed_meshes, accessor


def audit(archive_path):
    result = {
        "props": 0,
        "animated_exports": 0,
        "transform_bindings": 0,
        "worst_sample_error": 0.0,
        "errors": [],
    }
    with (
        zipfile.ZipFile(archive_path) as archive,
        tempfile.TemporaryDirectory() as directory,
    ):
        rows = list(
            csv.DictReader(
                io.StringIO(archive.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        for row in rows:
            result["props"] += 1
            try:
                data = archive.read(row["archivo_zip"])
                bones, anchors = prop_joints(data)
                parts = load_parts(data)
                for bone in bones:
                    if not np.isfinite(np.linalg.inv(bone["world"])).all():
                        raise ValueError("Invalid inverse bind matrix")
                if row["formato"] != "rdms":
                    continue
                if any(
                    part["node"] >= 0 and part["node"] not in anchors for part in parts
                ):
                    raise ValueError("Mesh lost its articulation")
                animator = PropAnimator(data)
                result["transform_bindings"] += len(animator.bindings)
                output = Path(directory) / "prop.glb"
                convert_prop(data, row["ruta_interna"], output)
                doc, binary = open_glb(output)
                if not doc.get("animations"):
                    continue
                result["animated_exports"] += 1
                duration = animator.clip["duration"]
                time_accessor = doc["animations"][0]["samplers"][0]["input"]
                times = accessor(doc, binary, time_accessor).ravel()
                for time in (
                    float(times[0]),
                    float(times[len(times) // 2]),
                    float(times[-1]),
                ):
                    preview, _ = animator.pose(parts, time, bones)
                    exported = posed_meshes(doc, binary, doc["animations"][0], time)
                    if len(exported) != len(preview):
                        raise ValueError("Animated export lost mesh parts")
                    for part, (_, points, _) in zip(preview, exported):
                        difference = float(
                            np.max(
                                np.abs(
                                    np.array([v[0] for v in part["vertices"]]) - points
                                )
                            )
                        )
                        result["worst_sample_error"] = max(
                            result["worst_sample_error"], difference
                        )
                        if not np.isfinite(difference) or difference > 0.0005:
                            raise ValueError(
                                f"Preview/export mismatch: {difference:g} at {time:g}s"
                            )
            except Exception as error:
                result["errors"].append(
                    {"prop": row["ruta_interna"], "error": str(error)}
                )
            if result["props"] % 100 == 0:
                print(f'Checked {result["props"]}/{len(rows)} props', flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.archive)
    report = json.dumps(result, indent=2)
    print(report)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    sys.exit(bool(result["errors"]))
