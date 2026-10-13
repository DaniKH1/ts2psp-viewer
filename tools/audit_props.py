"""Audit all props in a resource ZIP and optionally save a JSON report."""

import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ts2psp.formats.props import parse_rdms, parse_prop_bmsh, sections
from ts2psp.formats.surfaces import decode_surface


def audit(path):
    counts, errors = Counter(), []
    with zipfile.ZipFile(path) as archive:
        rows = list(
            csv.DictReader(
                io.StringIO(archive.read("inventario_props.csv").decode("utf-8-sig"))
            )
        )
        for row in rows:
            try:
                data = archive.read(row["archivo_zip"])
                counts["props"] += 1
                for tag, off, size, _, _ in sections(data):
                    if tag == b"surf":
                        counts["surfaces"] += 1
                        if decode_surface(data, off, size) is None:
                            raise ValueError("SURF could not be decoded")
                parts, rejected = (
                    parse_rdms(data)
                    if row["formato"] == "rdms"
                    else (parse_prop_bmsh(data), [])
                )
                if rejected or not parts:
                    raise ValueError(f"Rejected geometry: {rejected}")
                counts["parts"] += len(parts)
                counts["textured_parts"] += sum(bool(p["texture"]) for p in parts)
                counts["plain_color_parts"] += sum(not p["texture"] for p in parts)
                for part in parts:
                    if part.get("texture_warning"):
                        errors.append(
                            {
                                "prop": row["ruta_interna"],
                                "part": part["name"],
                                "error": part["texture_warning"],
                            }
                        )
            except Exception as error:
                errors.append({"prop": row["ruta_interna"], "error": str(error)})
    return {"counts": dict(counts), "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(args.archive)
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(text)
    raise SystemExit(bool(report["errors"]))
