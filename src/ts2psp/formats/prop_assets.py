"""Optional ISO support bank: object bitmaps and separate skeletal clips.

Old extracted archives remain readable. Source assets stay in the local ZIP;
repairs record whether UVs came from the game or a generated projection.
"""

import json
import zipfile

import numpy as np

from ts2psp.formats.animations import parse_banm
from ts2psp.formats.materials import assign_material
from ts2psp.formats.surfaces import decode_surface


class PropAssets:
    def __init__(self, archive):
        self.path = (
            archive.filename if isinstance(archive, zipfile.ZipFile) else archive
        )
        with zipfile.ZipFile(self.path) as source:
            self.manifest = (
                json.loads(source.read("prop_support.json"))
                if "prop_support.json" in source.namelist()
                else {"textures": {}, "animations": []}
            )
        self._textures = {}
        self._clips = {}

    def texture(self, path):
        path = path.casefold()
        if path not in self._textures:
            item = self.manifest["textures"].get(path)
            png = None
            if item:
                with zipfile.ZipFile(self.path) as source:
                    data = source.read(item["file"])
                png = decode_surface(data, item["off"], item["size"])
            self._textures[path] = (path, png) if png else None
        return self._textures[path]

    def animation_rows(self, model, target_data=None):
        directory = model.rsplit("/", 1)[0] + "/anims/"
        rows = [
            r for r in self.manifest["animations"] if r["name"].startswith(directory)
        ]
        if target_data is None:
            return rows
        from ts2psp.formats.prop_anim import retarget_curve_bindings

        folder = model.casefold().rpartition("/")[0] + "/"
        for row in self.manifest.get("prop_animations", ()):
            if row.get("kind") != "node" or not row["name"].casefold().startswith(
                folder
            ):
                continue
            clip = self.clip(row)
            bindings = retarget_curve_bindings(clip["node_data"], target_data, clip)
            if bindings:
                rows.append({**row, "bindings": bindings})
        return rows

    def clip(self, row):
        if row["file"] not in self._clips:
            with zipfile.ZipFile(self.path) as source:
                data = source.read(row["file"])
                if row.get("kind") == "node":
                    from ts2psp.formats.prop_anim import prop_clip

                    clip = prop_clip(data)
                    if clip is None:
                        raise ValueError(f"No animation curves in {row['name']}")
                    self._clips[row["file"]] = {
                        **clip,
                        "name": row["name"],
                        "kind": "node",
                        "node_data": data,
                    }
                else:
                    self._clips[row["file"]] = {
                        **parse_banm(data),
                        "name": row["name"],
                        "kind": "banm",
                    }
        return self._clips[row["file"]]

    def clips(self, model, target_data=None):
        return [
            {**self.clip(row), "bindings": row.get("bindings")}
            if row.get("kind") == "node"
            else self.clip(row)
            for row in self.animation_rows(model, target_data)
        ]

    def repair(self, parts, model):
        """Resolve verified placeholder/water materials without inventing assets."""
        for part in parts:
            if model == "objects/sinks/fx_sinkcounterskinscorcher" and part.get(
                "texture"
            ):
                # This counter mesh references a pedestal atlas with another UV
                # layout. These atlas projections are repairs, not recovered UVs.
                bowl = part.get("node_name") == "sinkShape1"
                rect = (0.52, 0.99, 0.46, 0.99) if bowl else (0.36, 0.46, 0.30, 0.65)
                self.project_uvs(part, rect)
                part["uv_source"] = "generated_atlas"
                part["texture_warning"] = (
                    "Reprojected UVs for the source pedestal atlas"
                )
                assign_material(
                    part,
                    {"texture": part["texture"], "base_color": part["base_color"]},
                    [v[2] for v in part["vertices"]],
                    modo="OPAQUE",
                )
            source = part.get("source_texture_path", "")
            if model == "objects/career_polygraph/fx_polygraph" and source.endswith(
                "missingtexture.tif"
            ):
                texture = self.texture(
                    "objects/career_polygraph/txt_careerpolygraph-surfaces.tif"
                )
                if texture:
                    assign_material(
                        part,
                        {"texture": texture, "base_color": (1, 1, 1, 1)},
                        [v[2] for v in part["vertices"]],
                    )
                    part["texture_warning"] = (
                        "Source placeholder replaced with the object's ISO bitmap"
                    )
            if part.get("texture") or not part["vertices"]:
                continue
            name = part.get("node_name", "").casefold()
            water = "water" in name or "foam" in name or "liquid" in name
            if not water:
                if model.rsplit("/", 1)[-1].startswith(("fx_o-", "fx_o2a-")):
                    part["texture_warning"] = "Source template has no bitmap" + (
                        " or UV coordinates"
                        if any(v[2] is None for v in part["vertices"])
                        else ""
                    )
                continue
            folder = model.split("/")[1] if "/" in model else ""
            texture_path = {
                "bathtubs": "objects/bathtubs/bathtubwater-water.tif",
                "hottubs": "objects/hottubs/txt_hottub_waterstill.tif",
                "sinks": "objects/counters/counterquaint-soapbubbles.tif",
                "toilets": "objects/toilets/toiletwater-water.tif",
            }.get(folder)
            if folder == "hottubs" and "foam" in name:
                texture_path = "objects/hottubs/txt_hottub_waterfoamyedge.tif"
            texture = self.texture(texture_path) if texture_path else None
            if not texture:
                part["texture_warning"] = (
                    "Source template has no bitmap or UV coordinates"
                )
                continue
            if all(v[2] is not None for v in part["vertices"]):
                assign_material(
                    part,
                    {"texture": texture, "base_color": (1, 1, 1, 1)},
                    [v[2] for v in part["vertices"]],
                    modo="BLEND",
                )
                part["uv_source"] = "source"
                part["texture_warning"] = (
                    "ISO water bitmap restored; original UVs preserved"
                )
                continue
            self.project_uvs(part)
            uvs = [v[2] for v in part["vertices"]]
            assign_material(
                part,
                {"texture": texture, "base_color": (1, 1, 1, 1)},
                uvs,
                modo="BLEND",
            )
            part["uv_source"] = "generated_planar"
            part["texture_warning"] = (
                "ISO water bitmap; generated UVs (the template has none)"
            )
        return parts

    @staticmethod
    def project_uvs(part, rect=(0, 1, 0, 1)):
        local = part.get("local_vertices", part["vertices"])
        positions = np.asarray([v[0] for v in local])
        axes = np.sort(np.argsort(np.ptp(positions, axis=0))[-2:])
        coords = positions[:, axes]
        coords = (coords - coords.min(axis=0)) / np.maximum(
            np.ptp(coords, axis=0), 1e-8
        )
        left, right, bottom, top = rect
        uvs = [
            (float(left + u * (right - left)), float(top - v * (top - bottom)))
            for u, v in coords
        ]
        for key in ("vertices", "local_vertices"):
            if key in part:
                part[key] = [(v[0], v[1], uv, v[3]) for v, uv in zip(part[key], uvs)]
