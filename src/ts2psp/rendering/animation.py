"""Software-render a few frames of an animated GLB for visual QA."""

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ts2psp.formats.animations import open_glb


def accessor(doc, binary, idx):
    a = doc["accessors"][idx]
    v = doc["bufferViews"][a["bufferView"]]
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    dtype = {5121: "u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[a["componentType"]]
    return np.frombuffer(
        binary,
        dtype=dtype,
        count=a["count"] * width,
        offset=v["byteOffset"] + a.get("byteOffset", 0),
    ).reshape(a["count"], width)


def quat_matrix(q):
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    k = 2 / n
    return np.array(
        [
            [1 - k * (y * y + z * z), k * (x * y - z * w), k * (x * z + y * w)],
            [k * (x * y + z * w), 1 - k * (x * x + z * z), k * (y * z - x * w)],
            [k * (x * z - y * w), k * (y * z + x * w), 1 - k * (x * x + y * y)],
        ]
    )


def posiciones_visorde_z_up(positions):
    """Convert GLB Y-up positions to the Z-up coordinate system of the viewer."""
    positions = np.asarray(positions)
    return positions[:, (0, 2, 1)] * np.array((-1.0, 1.0, 1.0))


def posed_bones(doc, binary, anim, t):
    """Articulaciones del esqueleto en el instante `t`.

    Devuelve una lista de {'parent': indice, 'world': matriz 4x4}, el mismo
    esquema que consume el visor de personajes para pintar el esqueleto, de modo
    que el overlay se dibuja con el mismo codigo. Las matrices salen ya en el
    sistema Z-up del visor.

    A diferencia de las mallas, aqui no se aplican las inverse bind matrices:
    se quiere la posicion de la articulacion en el mundo, no la del vertice
    deformado.
    """
    nodes = doc["nodes"]
    transforms = [
        {
            "translation": x.get("translation", [0, 0, 0]),
            "rotation": x.get("rotation", [0, 0, 0, 1]),
            "scale": x.get("scale", [1, 1, 1]),
        }
        for x in nodes
    ]
    for ch in anim["channels"]:
        target = ch["target"]
        path = target["path"]
        if path not in ("rotation", "translation", "scale"):
            continue
        s = anim["samplers"][ch["sampler"]]
        tt = accessor(doc, binary, s["input"]).ravel()
        vv = accessor(doc, binary, s["output"])
        idx = min(len(tt) - 1, int(np.searchsorted(tt, t)))
        transforms[target["node"]][path] = vv[idx]
    parents = {child: i for i, x in enumerate(nodes) for child in x.get("children", [])}
    world = {}

    def wmat(i):
        if i in world:
            return world[i]
        x = transforms[i]
        M = np.eye(4)
        M[:3, :3] = quat_matrix(x["rotation"]) * x["scale"]
        M[:3, 3] = x["translation"]
        world[i] = wmat(parents[i]) @ M if i in parents else M
        return world[i]

    # La posicion de la articulacion es la de su nodo (wmat). La matriz de
    # skinning wmat @ inverseBind no vale aqui: es la que deforma los
    # vertices, y medida contra la malla daba un esqueleto tres veces mayor
    # que el cuerpo.
    skins = doc.get("skins") or []
    if not skins:
        return []
    joints = list(skins[0]["joints"])
    posicion = {node: i for i, node in enumerate(joints)}
    huesos = []
    for node in joints:
        # Se guarda transpuesta: asi la traduccion queda en world[3,:3], que es
        # justo donde el overlay del visor de personajes la busca.
        M = wmat(node).T.copy()
        xyz = posiciones_visorde_z_up(M[3, :3][None, :])[0]
        M[3, :3] = xyz
        # El padre solo cuenta si tambien es una articulacion.
        padre = parents.get(node)
        huesos.append(
            {"parent": posicion.get(padre, -1) if padre is not None else -1, "world": M}
        )
    return huesos


def posed_meshes(doc, binary, anim, t):
    nodes = doc["nodes"]
    transforms = [
        {
            "translation": x.get("translation", [0, 0, 0]),
            "rotation": x.get("rotation", [0, 0, 0, 1]),
            "scale": x.get("scale", [1, 1, 1]),
        }
        for x in nodes
    ]
    for ch in anim["channels"]:
        target = ch["target"]
        path = target["path"]
        if path not in ("rotation", "translation", "scale", "weights"):
            continue
        s = anim["samplers"][ch["sampler"]]
        tt = accessor(doc, binary, s["input"]).ravel()
        vv = accessor(doc, binary, s["output"])
        idx = min(len(tt) - 1, int(np.searchsorted(tt, t)))
        if path == "weights":
            count = len(doc["meshes"][nodes[target["node"]]["mesh"]].get("weights", []))
            transforms[target["node"]][path] = vv.reshape(len(tt), count)[idx]
        else:
            transforms[target["node"]][path] = vv[idx]
    parents = {child: i for i, x in enumerate(nodes) for child in x.get("children", [])}
    world = {}

    def wmat(i):
        if i in world:
            return world[i]
        x = transforms[i]
        M = np.eye(4)
        M[:3, :3] = quat_matrix(x["rotation"]) * x["scale"]
        M[:3, 3] = x["translation"]
        world[i] = wmat(parents[i]) @ M if i in parents else M
        return world[i]

    palettes = {}
    for si, skin in enumerate(doc["skins"]):
        ibm = (
            accessor(doc, binary, skin["inverseBindMatrices"])
            .reshape(-1, 4, 4)
            .transpose(0, 2, 1)
        )
        palettes[si] = np.stack(
            [wmat(j) @ ibm[k] for k, j in enumerate(skin["joints"])]
        )
    out = []
    for ni, node in enumerate(nodes):
        if "mesh" not in node:
            continue
        mesh = doc["meshes"][node["mesh"]]
        for prim in mesh["primitives"]:
            attr = prim["attributes"]
            pos = accessor(doc, binary, attr["POSITION"]).astype(float)
            weights_morph = transforms[ni].get("weights", mesh.get("weights", []))
            for target, morph_weight in zip(prim.get("targets", []), weights_morph):
                if morph_weight and "POSITION" in target:
                    pos += float(morph_weight) * accessor(
                        doc, binary, target["POSITION"]
                    )
            pos4 = np.column_stack((pos, np.ones(len(pos))))
            joints = accessor(doc, binary, attr["JOINTS_0"]).astype(int)
            weights = accessor(doc, binary, attr["WEIGHTS_0"]).astype(float)
            if "JOINTS_1" in attr:
                joints = np.column_stack(
                    (joints, accessor(doc, binary, attr["JOINTS_1"]).astype(int))
                )
                weights = np.column_stack(
                    (weights, accessor(doc, binary, attr["WEIGHTS_1"]).astype(float))
                )
            palette = palettes[node["skin"]]
            posed = np.zeros((len(pos), 3))
            for k in range(joints.shape[1]):
                posed += (
                    weights[:, k, None]
                    * np.einsum("nij,nj->ni", palette[joints[:, k]], pos4)[:, :3]
                )
            posed = posiciones_visorde_z_up(posed)
            ix = accessor(doc, binary, prim["indices"]).ravel().reshape(-1, 3)
            out.append((mesh["name"], posed, ix))
    return out


def validate_preview_frame(doc, binary, anim, t=0.0):
    """Evaluate and validate a frame before installing it in the interactive viewer."""
    duration = anim.get("extras", {}).get("sourceDuration")
    if (
        not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError("Invalid animation duration")
    if not anim.get("channels"):
        raise ValueError("The animation has no channels")
    meshes = posed_meshes(doc, binary, anim, t)
    if not meshes:
        raise ValueError("The animation produces no geometry")
    for name, vertices, indices in meshes:
        indices = np.asarray(indices)
        if (
            vertices.ndim != 2
            or vertices.shape[1] != 3
            or not np.isfinite(vertices).all()
        ):
            raise ValueError(f"The animation produces invalid vertices: {name}")
        if indices.size == 0 or indices.min() < 0 or indices.max() >= len(vertices):
            raise ValueError(f"The animation produces invalid indices: {name}")
    return meshes


TONOS_PLANOS = (
    ("hair/", (102, 75, 67, 255)),
    ("face/", (208, 160, 128, 255)),
    ("body/", (105, 120, 184, 255)),
    ("", (138, 140, 150, 255)),
)


def tono_plano(nombre):
    """Color plano que se usa cuando una malla no trae textura propia."""
    for prefijo, color in TONOS_PLANOS:
        if not prefijo or nombre.startswith(prefijo):
            return color
    return TONOS_PLANOS[-1][1]


def glb_texturas(doc, binary):
    """{material: (nombre de textura, bytes PNG) | None} leídos del propio GLB.

    materials -> pbrMetallicRoughness -> baseColorTexture -> textures -> source ->
    images -> bufferViews. El PNG va crudo dentro del chunk BIN.
    """
    salida, vistas = {}, {}
    for indice, material in enumerate(doc.get("materials", [])):
        ref = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
        if not ref or "index" not in ref:
            salida[indice] = None
            continue
        if ref["index"] in vistas:  # varias mallas comparten textura
            salida[indice] = vistas[ref["index"]]
            continue
        image = doc["images"][doc["textures"][ref["index"]]["source"]]
        vista = doc["bufferViews"][image["bufferView"]]
        at = vista.get("byteOffset", 0) + image.get("byteOffset", 0)
        salida[indice] = vistas[ref["index"]] = (
            image.get("name") or f'tex{ref["index"]}',
            bytes(binary[at : at + vista["byteLength"]]),
        )
    return salida


def glb_piezas(doc, binary, mallas):
    """Piezas para la Escena de GPU, en el MISMO orden que devuelve posed_meshes.

    Recorre nodos y primitivas igual que posed_meshes, así que el índice i de la lista
    coincide con mallas[i]. Las posiciones solo sirven para el encuadre inicial: cada
    frame las sustituye fijar_poses.
    """
    por_material = glb_texturas(doc, binary)
    piezas, texturas = [], {}
    i = 0
    for node in doc["nodes"]:
        if "mesh" not in node:
            continue
        mesh = doc["meshes"][node["mesh"]]
        for prim in mesh.get("primitives", ()):
            nombre = mesh["name"]
            if "TEXCOORD_0" in prim["attributes"]:
                uv = accessor(doc, binary, prim["attributes"]["TEXCOORD_0"]).astype(
                    np.float32
                )
            else:
                uv = np.zeros(
                    (len(accessor(doc, binary, prim["attributes"]["POSITION"])), 2),
                    np.float32,
                )
            indices = accessor(doc, binary, prim["indices"]).ravel().tolist()
            textura = por_material.get(prim.get("material"))
            clave = textura[0] if textura else f"sin-textura:{nombre}"
            base = mallas[i][1]
            i += 1
            piezas.append(
                (
                    clave,
                    {
                        "vertices": [
                            (
                                (float(p[0]), float(p[1]), float(p[2])),
                                None,
                                (float(u), float(v)),
                                None,
                            )
                            for p, (u, v) in zip(base, uv)
                        ],
                        "indices": indices,
                        "nombre": nombre,
                        "repeat_texture": (
                            nombre.startswith("body/aubodynightbeast /")
                            and int(nombre.rsplit("pieza_", 1)[-1]) > 29
                            if "pieza_" in nombre
                            else False
                        ),
                        "color": None if textura else tono_plano(nombre),
                    },
                )
            )
            if textura:
                texturas[clave] = textura  # clave por textura: bellagoth sube 3 y no 23
    return piezas, texturas


def aplanar(mallas, buffer=None):
    """Concatena los vértices de posed_meshes en un float32 reutilizable."""
    total = sum(len(m[1]) for m in mallas)
    destino = (
        np.empty((total, 3), np.float32)
        if buffer is None or len(buffer) != total
        else buffer
    )
    k = 0
    for _, vertices, _ in mallas:
        n = len(vertices)
        destino[k : k + n] = vertices
        k += n
    return destino


def draw(
    meshes, path=None, width=480, height=640, yaw=0.0, pitch=0.0, zoom=1.0, pan=(0, 0)
):
    image = Image.new("RGB", (width, height), (30, 32, 44))
    draw = ImageDraw.Draw(image)
    points = np.concatenate([p for _, p, _ in meshes])
    center = (points.min(axis=0) + points.max(axis=0)) / 2
    scale = min(width / 1.8, height / 2.3) * zoom
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    polys = []
    for name, verts, ix in meshes:
        if name.startswith("hair/"):
            color = (102, 75, 67)
        elif name.startswith("face/"):
            color = (208, 160, 128)
        elif name.startswith("body/"):
            color = (105, 120, 184)
        else:
            color = (138, 140, 150)
        relative = verts - center
        right = cy * relative[:, 0] - sy * relative[:, 1]
        depth = sy * relative[:, 0] + cy * relative[:, 1]
        up = cp * relative[:, 2] - sp * depth
        depth = sp * relative[:, 2] + cp * depth
        pxy = np.column_stack(
            (width / 2 + pan[0] - right * scale, height / 2 + pan[1] - up * scale)
        )
        for tri in ix:
            xy = pxy[tri]
            if np.any(~np.isfinite(xy)):
                continue
            area = np.linalg.det(np.column_stack((xy, np.ones(3))))
            if abs(area) < 0.3:
                continue
            polys.append((float(np.mean(depth[tri])), xy.tolist(), color))
    for _, xy, color in sorted(polys, reverse=True):
        draw.polygon(
            [tuple(p) for p in xy],
            fill=color,
            outline=tuple(int(c * 0.75) for c in color),
        )
    if path is not None:
        image.save(path)
    return image


def main():
    glb = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    doc, binary = open_glb(glb)
    for ai in range(len(doc["animations"])):
        anim = doc["animations"][ai]
        if ai not in (0, 1, 2, 3, 4):
            continue
        sampler = anim["samplers"][0]
        times = accessor(doc, binary, sampler["input"]).ravel()
        for frac in (0, 0.33, 0.66):
            t = float(times[-1]) * frac
            draw(
                posed_meshes(doc, binary, anim, t), out / f"{ai}_{int(frac*100):02}.png"
            )
    print("rendered", len(list(out.glob("*.png"))))


if __name__ == "__main__":
    main()
