"""Export authored map references without distributing any game meshes/textures."""

import argparse
import csv
import io
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ts2psp.formats.prop_scene import level_prop_references
from ts2psp.formats.props import parse_rdms, sections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, required=True, help="Imported game data directory"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Destination JSON report"
    )
    parser.add_argument(
        "--check-meshes", action="store_true", help="Also decode placed geometry"
    )
    args = parser.parse_args()
    maps = []
    with zipfile.ZipFile(args.data / "sims2_psp_mapas_recursos.zip") as archive:
        rows = csv.DictReader(
            io.StringIO(archive.read("inventario_mapas.csv").decode("utf-8-sig"))
        )
        for row in rows:
            data = archive.read(row["archivo_zip"])
            refs = level_prop_references(data, sections(data))
            report = {
                "map": row["ruta_interna"],
                "references": refs,
                "resolved": sum(ref["resolved"] for ref in refs),
            }
            if args.check_meshes:
                parts, rejected = parse_rdms(data)
                report.update(
                    parts=len(parts),
                    rejected=rejected,
                    prop_instances=len(
                        {p["placement_node"] for p in parts if p["placement_node"]}
                    ),
                )
            maps.append(report)
    report = {
        "coordinates": "PSP: X/Y ground, Z up; column-vector matrices, row-major JSON",
        "note": "Authored references; runtime scripts may change states or transforms.",
        "maps": maps,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"{len(maps)} maps, {sum(len(m['references']) for m in maps)} references -> {args.output}"
    )


if __name__ == "__main__":
    main()
