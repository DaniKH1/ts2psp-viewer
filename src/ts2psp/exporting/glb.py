"""Build binary glTF meshes, materials and skins."""

import io
import json
import math
import struct
import numpy as np
from PIL import Image
from ts2psp.core.coordinates import convert_row_matrix, convert_vector3


class GLB:
    def __init__(self):
        self.doc = {
            "asset": {"version": "2.0", "generator": "Sims 2 PSP BMSH converter"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "skins": [],
            "materials": [],
            "buffers": [{"byteLength": 0}],
            "bufferViews": [],
            "accessors": [],
        }
        self.bin = bytearray()
        self.texture_cache = {}

    def add_texture(self, name, png_bytes):
        if name in self.texture_cache:
            return self.texture_cache[name]
        while len(self.bin) % 4:
            self.bin.append(0)
        at = len(self.bin)
        self.bin.extend(png_bytes)
        view_idx = len(self.doc["bufferViews"])
        self.doc["bufferViews"].append(
            {"buffer": 0, "byteOffset": at, "byteLength": len(png_bytes)}
        )
        images = self.doc.setdefault("images", [])
        image_idx = len(images)
        images.append({"name": name, "bufferView": view_idx, "mimeType": "image/png"})
        textures = self.doc.setdefault("textures", [])
        texture_idx = len(textures)
        textures.append({"name": name, "source": image_idx})
        self.texture_cache[name] = texture_idx
        return texture_idx

    def accessor(
        self,
        payload,
        component_type,
        shape,
        count,
        target=None,
        minimum=None,
        maximum=None,
    ):
        while len(self.bin) % 4:
            self.bin.append(0)
        at = len(self.bin)
        self.bin.extend(payload)
        view = {"buffer": 0, "byteOffset": at, "byteLength": len(payload)}
        if target:
            view["target"] = target
        view_idx = len(self.doc["bufferViews"])
        self.doc["bufferViews"].append(view)
        acc = {
            "bufferView": view_idx,
            "componentType": component_type,
            "count": count,
            "type": shape,
        }
        if minimum is not None:
            acc["min"] = minimum
            acc["max"] = maximum
        acc_idx = len(self.doc["accessors"])
        self.doc["accessors"].append(acc)
        return acc_idx

    def add_skin(self, bones):
        node_start = len(self.doc["nodes"])
        for bone in bones:
            parent = bone["parent"]
            local = (
                bone["world"]
                if parent < 0
                else bone["world"] @ np.linalg.inv(bones[parent]["world"])
            )
            local = convert_row_matrix(local)
            self.doc["nodes"].append(
                {"name": bone["name"], "matrix": [float(v) for v in local.reshape(16)]}
            )
        for i, bone in enumerate(bones):
            parent = bone["parent"]
            if parent >= 0:
                self.doc["nodes"][node_start + parent].setdefault(
                    "children", []
                ).append(node_start + i)
            else:
                self.doc["scenes"][0]["nodes"].append(node_start + i)
        ibms = b"".join(
            struct.pack(
                "<16f", *[float(v) for v in convert_row_matrix(bone["ibm"]).reshape(16)]
            )
            for bone in bones
        )
        ibm_acc = self.accessor(ibms, 5126, "MAT4", len(bones))
        skin = {
            "joints": list(range(node_start, node_start + len(bones))),
            "inverseBindMatrices": ibm_acc,
            "skeleton": node_start,
        }
        skin_idx = len(self.doc["skins"])
        self.doc["skins"].append(skin)
        return skin_idx

    def add_joint_skeleton(self, joints):
        """Esqueleto de solo traslaciones, escrito ya en base glTF.

        `add_skin` esta pensado para huesos con rotacion, donde la matriz de
        mundo y su inversa comparten convencion. Aqui no hace falta: las
        articulaciones son puntos, asi que se compone directamente la traslacion
        local y la inversa de ligadura en el mismo espacio en el que glTF espera
        las dos, que es lo que hace que la malla no se mueva al abrir.
        """
        start = len(self.doc["nodes"])
        positions = np.array([position for _, _, position in joints], float)
        for index, (name, parent, position) in enumerate(joints):
            if 0 <= parent < index:
                local = positions[index] - positions[parent]
            else:
                local = positions[index]
            matrix = np.eye(4)
            matrix[:3, 3] = local
            self.doc["nodes"].append({
                "name": name,
                "translation": [float(v) for v in local],
            })
        for index, (name, parent, _) in enumerate(joints):
            if 0 <= parent < index:
                self.doc["nodes"][start + parent].setdefault("children", []).append(
                    start + index
                )
            else:
                self.doc["scenes"][0]["nodes"].append(start + index)
        ibms = b"".join(
            struct.pack(
                "<16f",
                *[float(v) for v in np.array([
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [-p[0], -p[1], -p[2], 1.0],
                ]).reshape(16)],
            )
            for _, _, p in joints
        )
        ibm_acc = self.accessor(ibms, 5126, "MAT4", len(joints))
        self.doc["skins"].append({
            "joints": list(range(start, start + len(joints))),
            "inverseBindMatrices": ibm_acc,
            "skeleton": start,
        })
        return len(self.doc["skins"]) - 1

    def add_mesh(self, name, parts, skin_index, bone_count, texture=None):
        colors = [
            (0.55, 0.62, 0.78, 1),
            (0.75, 0.55, 0.55, 1),
            (0.58, 0.72, 0.59, 1),
            (0.76, 0.68, 0.48, 1),
        ]
        for part_i, part in enumerate(parts):
            part_texture = (
                texture.get(part["name"]) if isinstance(texture, dict) else texture
            )
            palette = part["palette"]
            if max(palette) >= bone_count:
                raise ValueError("Palette references a missing bone")
            positions = []
            normals = []
            texcoords = []
            joints0, joints1 = [], []
            weights0, weights1 = [], []
            for pos, normal, uv, raw_weights in part["vertices"]:
                positions.extend(v / 1000.0 for v in convert_vector3(pos))
                length = max(1.0, math.sqrt(sum(float(v) ** 2 for v in normal)))
                normals.extend(v / length for v in convert_vector3(normal))
                texcoords.extend(
                    ((uv[0] + 32768) / 4096.0, 1.0 - (uv[1] + 32768) / 4096.0)
                )
                ids = list(range(len(raw_weights)))
                w = [v / 128.0 for v in raw_weights]
                joints0.extend(palette[j] for j in ids[:4])
                weights0.extend(w[:4])
                joints0.extend([0] * (4 - len(ids[:4])))
                weights0.extend([0.0] * (4 - len(ids[:4])))
                if len(ids) > 4:
                    extra_ids = ids[4:8]
                    joints1.extend(palette[j] for j in extra_ids)
                    weights1.extend(w[4:8])
                    joints1.extend([0] * (4 - len(extra_ids)))
                    weights1.extend([0.0] * (4 - len(extra_ids)))
            count = len(part["vertices"])
            attrs = {}
            packed = struct.pack("<" + "f" * len(positions), *positions)
            mins = [min(positions[j::3]) for j in range(3)]
            maxs = [max(positions[j::3]) for j in range(3)]
            attrs["POSITION"] = self.accessor(
                packed, 5126, "VEC3", count, 34962, mins, maxs
            )
            attrs["NORMAL"] = self.accessor(
                struct.pack("<" + "f" * len(normals), *normals),
                5126,
                "VEC3",
                count,
                34962,
            )
            attrs["TEXCOORD_0"] = self.accessor(
                struct.pack("<" + "f" * len(texcoords), *texcoords),
                5126,
                "VEC2",
                count,
                34962,
            )
            attrs["JOINTS_0"] = self.accessor(
                bytes(joints0), 5121, "VEC4", count, 34962
            )
            attrs["WEIGHTS_0"] = self.accessor(
                struct.pack("<" + "f" * len(weights0), *weights0),
                5126,
                "VEC4",
                count,
                34962,
            )
            if joints1:
                attrs["JOINTS_1"] = self.accessor(
                    bytes(joints1), 5121, "VEC4", count, 34962
                )
                attrs["WEIGHTS_1"] = self.accessor(
                    struct.pack("<" + "f" * len(weights1), *weights1),
                    5126,
                    "VEC4",
                    count,
                    34962,
                )
            inds = part["indices"]
            component = 5123 if count < 65536 else 5125
            ixdata = struct.pack(
                "<" + ("H" if component == 5123 else "I") * len(inds), *inds
            )
            ix_acc = self.accessor(ixdata, component, "SCALAR", len(inds), 34963)
            color = colors[part_i % len(colors)]
            material_idx = len(self.doc["materials"])
            pbr = {
                "baseColorFactor": (
                    part.get("base_color", color)
                    if part_texture is None
                    else [1, 1, 1, 1]
                ),
                "metallicFactor": 0,
                "roughnessFactor": 1,
            }
            if part_texture is not None:
                tex_name, png_bytes = part_texture
                pbr["baseColorTexture"] = {
                    "index": self.add_texture(tex_name, png_bytes),
                    "texCoord": 0,
                }
            material = {
                "name": f'{name} / {part["name"]}',
                "pbrMetallicRoughness": pbr,
                "doubleSided": True,
            }
            if part_texture is not None:
                alpha = (
                    Image.open(io.BytesIO(png_bytes)).convert("RGBA").getchannel("A")
                )
                if alpha.getextrema()[0] < 255:
                    material.update(alphaMode="MASK", alphaCutoff=0.5)
            self.doc["materials"].append(material)
            primitive = {
                "attributes": attrs,
                "indices": ix_acc,
                "material": material_idx,
                "mode": 4,
            }
            morphs = part.get("morph_positions", [])
            if morphs:
                primitive["targets"] = []
                for deltas in morphs:
                    values = [
                        v / 1000.0 for xyz in deltas for v in convert_vector3(xyz)
                    ]
                    payload = struct.pack("<" + "f" * len(values), *values)
                    acc = self.accessor(payload, 5126, "VEC3", count, 34962)
                    primitive["targets"].append({"POSITION": acc})
            mesh_idx = len(self.doc["meshes"])
            mesh_name = f'{name} / {part["name"]}'
            mesh = {"name": mesh_name, "primitives": [primitive]}
            if morphs:
                mesh["weights"] = [0.0] * len(morphs)
                mesh["extras"] = {"targetNames": part["morph_names"]}
            self.doc["meshes"].append(mesh)
            node_idx = len(self.doc["nodes"])
            self.doc["nodes"].append(
                {"name": mesh_name, "mesh": mesh_idx, "skin": skin_index}
            )
            self.doc["scenes"][0]["nodes"].append(node_idx)

    def save(self, path):
        self.doc["buffers"][0]["byteLength"] = len(self.bin)
        json_bytes = json.dumps(
            self.doc, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        json_bytes += b" " * (-len(json_bytes) % 4)
        binary = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        total = 12 + 8 + len(json_bytes) + 8 + len(binary)
        with open(path, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
            f.write(json_bytes)
            f.write(struct.pack("<I4s", len(binary), b"BIN\0"))
            f.write(binary)
