"""Decode Sims 2 PSP BANM curves and attach them to an existing character GLB.

The BANM tracks store 60 Hz timestamps and cubic Bezier control points as
signed 16-bit values. Bone translations are millimetres, rotations are
normalised quaternions, and facial morph weights use 4096 for a full unit.
"""

import argparse
import csv
import json
import math
import mmap
import re
import struct
import zipfile
from pathlib import Path

from ts2psp.core.coordinates import convert_quaternion, convert_vector3

ISO_BASE = 47_972_352
DEFAULT_ISO = None
REFERENCE_F = "characters/bodyanims/solo_anims/casidles/a-cas-idle-breathe-f"
REFERENCE_M = "characters/bodyanims/solo_anims/casidles/a-cas-idle-breathe-m"


def u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def clip_name(data):
    names = re.findall(rb"(?:characters/bodyanims|objects)/[^\x00]+", data)
    return names[0].decode("ascii", "replace") if names else ""


def parse_banm(data):
    if data[:4] != b"res\n":
        raise ValueError("No es un recurso .res")
    footer = u32(data, 0x14)
    section_count = u32(data, footer)
    banm = None
    for i in range(section_count):
        tag, off, size, count, _ = struct.unpack_from(
            "<4s4I", data, footer + 8 + 20 * i
        )
        if tag == b"banm":
            banm = (off, size, count)
            break
    if banm is None:
        raise ValueError("BANM section not found")
    off, size, count = banm
    head = off + 0x60
    duration = struct.unpack_from("<f", data, head + 0x28)[0]
    if not 0 < duration < 300 or not 0 < count < 200:
        raise ValueError("Invalid BANM header")
    table = head + 0x2C
    ptrs = [p + u32(data, p) for p in range(table, table + 4 * count, 4)]
    sorted_ptrs = sorted(set(ptrs))
    tracks = []
    for i, at in enumerate(ptrs):
        next_idx = sorted_ptrs.index(at) + 1
        end = (
            sorted_ptrs[next_idx] if next_idx < len(sorted_ptrs) else off + 0x60 + size
        )
        if at + 8 > len(data) or end < at + 8:
            raise ValueError(f"Pista {i} fuera de rango")
        name_at = at + u32(data, at)
        if not 0 <= name_at < len(data):
            raise ValueError(f"Nombre de pista {i} fuera de rango")
        name = data[name_at : data.index(0, name_at)].decode("ascii", "replace")
        nkeys, mode = struct.unpack_from("<HH", data, at + 4)
        key_at = at + 8
        if key_at + 2 * nkeys > end:
            raise ValueError(f"Tiempos fuera de rango: {name}")
        times = struct.unpack_from("<" + "H" * nkeys, data, key_at) if nkeys else ()
        if any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError(f"Tiempos no crecientes: {name}")
        val_at = key_at + 2 * nkeys
        if (
            name.startswith("__FaceBlends__")
            or name.startswith("__TopTeethBlends__")
            or name.startswith("__BotTeethBlends__")
        ):
            kind, width, varying, scale = "weights", 1, 1, 4096.0
            animated_components = (0,)
        elif mode & 0x8000:
            kind, width, varying, scale = "translation", 3, 0x83 - (mode >> 8), 1000.0
            animated_components = tuple(
                i for i in range(width) if (mode & 15) & (1 << i)
            )
        else:
            kind, width, varying, scale = "rotation", 4, 4 - (mode >> 8), 1.0
            animated_components = tuple(
                i for i in range(width) if (mode & 15) & (1 << i)
            )
        if not 0 <= varying <= width or (
            kind != "weights" and len(animated_components) != varying
        ):
            raise ValueError(f"Modo desconocido {mode:#x}: {name}")
        need = val_at + 2 * width + 6 * nkeys * varying
        if need > end or end - need > 3:
            raise ValueError(f"Incompatible size: {name} {mode:#x}, {end-need} bytes")
        packed = tuple(
            v / scale for v in struct.unpack_from("<" + "h" * width, data, val_at)
        )
        if kind == "weights":
            base = packed
        else:
            static_components = tuple(
                i for i in range(width) if i not in animated_components
            )
            order = static_components + animated_components
            base_list = [0.0] * width
            for comp, value in zip(order, packed):
                base_list[comp] = value
            base = tuple(base_list)
        raw = (
            struct.unpack_from(
                "<" + "h" * (3 * nkeys * varying), data, val_at + 2 * width
            )
            if nkeys
            else ()
        )
        curves = []
        for j in range(nkeys):
            span = raw[3 * j * varying : 3 * (j + 1) * varying]
            curves.append(
                tuple(
                    tuple(span[k * varying + c] / scale for c in range(varying))
                    for k in range(3)
                )
            )
        tracks.append(
            {
                "name": name,
                "kind": kind,
                "mode": mode,
                "times": tuple(t / 60.0 for t in times),
                "base": base,
                "varying": varying,
                "animated_components": animated_components,
                "curves": tuple(curves),
            }
        )
    return {"name": clip_name(data), "duration": duration, "tracks": tracks}


def sample(track, t):
    base = track["base"]
    times = track["times"]
    if not times or t <= 0:
        return base
    varying = track["varying"]
    comps = track["animated_components"]
    start = base
    prev_time = 0.0
    for end_time, controls in zip(times, track["curves"]):
        end = list(start)
        for c, value in zip(comps, controls[2]):
            end[c] = value
        end = tuple(end)
        if t <= end_time:
            u = max(0.0, min(1.0, (t - prev_time) / (end_time - prev_time)))
            a = (1 - u) ** 3
            b = 3 * (1 - u) ** 2 * u
            c = 3 * (1 - u) * u * u
            d = u**3
            result = list(start)
            for i, component in enumerate(comps):
                result[component] = (
                    a * start[component]
                    + b * controls[0][i]
                    + c * controls[1][i]
                    + d * controls[2][i]
                )
            return tuple(result)
        start = end
        prev_time = end_time
    return start


def normalize(q):
    length = math.sqrt(sum(v * v for v in q))
    return tuple(v / length for v in q) if length > 1e-8 else (0.0, 0.0, 0.0, 1.0)


def qmul(a, b):
    x, y, z, w = a
    X, Y, Z, W = b
    return (
        w * X + x * W + y * Z - z * Y,
        w * Y - x * Z + y * W + z * X,
        w * Z + x * Y - y * X + z * W,
        w * W - x * X - y * Y - z * Z,
    )


def qinv(q):
    x, y, z, w = q
    return (-x, -y, -z, w)


def bone_rotation(track, t, node):
    """Compose the fixed BODY joint orientation with its animated rotation."""
    rotation = convert_quaternion(normalize(sample(track, t)))
    orient = node.get("extras", {}).get("pspJointOrient", (0, 0, 0, 1))
    return normalize(qmul(orient, rotation))


def matrix_trs(m):
    t = tuple(m[12:15])
    s = tuple(math.sqrt(sum(m[4 * c + r] ** 2 for r in range(3))) for c in range(3))
    a, b, c = m[0] / s[0], m[4] / s[1], m[8] / s[2]
    d, e, f = m[1] / s[0], m[5] / s[1], m[9] / s[2]
    g, h, i = m[2] / s[0], m[6] / s[1], m[10] / s[2]
    trace = a + e + i
    if trace > 0:
        v = math.sqrt(trace + 1) * 2
        q = ((h - f) / v, (c - g) / v, (d - b) / v, v / 4)
    elif a > e and a > i:
        v = math.sqrt(1 + a - e - i) * 2
        q = (v / 4, (b + d) / v, (c + g) / v, (h - f) / v)
    elif e > i:
        v = math.sqrt(1 + e - a - i) * 2
        q = ((b + d) / v, v / 4, (f + h) / v, (c - g) / v)
    else:
        v = math.sqrt(1 + i - a - e) * 2
        q = ((c + g) / v, (f + h) / v, v / 4, (d - b) / v)
    return t, normalize(q), s


def open_glb(path):
    d = Path(path).read_bytes()
    if d[:4] != b"glTF" or u32(d, 4) != 2:
        raise ValueError("Se requiere GLB 2.0")
    length = u32(d, 12)
    doc = json.loads(d[20 : 20 + length])
    bin_at = 20 + length
    if d[bin_at + 4 : bin_at + 8] != b"BIN\0":
        raise ValueError("Chunk BIN ausente")
    binary = bytearray(d[bin_at + 8 : bin_at + 8 + u32(d, bin_at)])
    return doc, binary


def save_glb(path, doc, binary):
    while len(binary) % 4:
        binary.append(0)
    doc["buffers"][0]["byteLength"] = len(binary)
    encoded = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    total = 12 + 8 + len(encoded) + 8 + len(binary)
    with Path(path).open("wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(encoded), b"JSON"))
        f.write(encoded)
        f.write(struct.pack("<I4s", len(binary), b"BIN\0"))
        f.write(binary)


def add_accessor(doc, binary, values, shape, minimum=None, maximum=None):
    while len(binary) % 4:
        binary.append(0)
    at = len(binary)
    binary.extend(struct.pack("<" + "f" * len(values), *values))
    view = len(doc["bufferViews"])
    doc["bufferViews"].append(
        {"buffer": 0, "byteOffset": at, "byteLength": 4 * len(values)}
    )
    idx = len(doc["accessors"])
    dim = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[shape]
    acc = {
        "bufferView": view,
        "componentType": 5126,
        "count": len(values) // dim,
        "type": shape,
    }
    if minimum is not None:
        acc["min"], acc["max"] = [minimum], [maximum]
    doc["accessors"].append(acc)
    return idx


def attach_animations(
    source_glb,
    clips,
    reference,
    output_glb,
    fps=30,
    fallback=None,
    reference_pose=None,
    reference_pose_time=0.0,
):
    doc, binary = open_glb(source_glb)
    nodes = doc["nodes"]
    if not math.isfinite(reference_pose_time) or reference_pose_time < 0:
        raise ValueError("Invalid reference pose time")
    if reference_pose and not reference:
        raise ValueError("La pose objetivo requiere un clip de referencia")
    if reference_pose and reference_pose_time > reference_pose["duration"]:
        raise ValueError("Reference pose time exceeds clip duration")
    bone_nodes = {}
    bind = {}
    for i, node in enumerate(nodes):
        if "matrix" in node:
            t, q, s = matrix_trs(node.pop("matrix"))
            node["translation"] = list(t)
            node["rotation"] = list(q)
            node["scale"] = list(s)
            bone_nodes.setdefault(node.get("name", ""), []).append(i)
            bind[i] = (t, q)
    if not bone_nodes:
        raise ValueError("GLB has no bones")
    ref_tracks = (
        {(x["name"], x["kind"]): x for x in reference["tracks"]} if reference else {}
    )
    pose_tracks = (
        {(x["name"], x["kind"]): x for x in reference_pose["tracks"]}
        if reference_pose
        else {}
    )
    fallback_tracks = (
        {
            (x["name"], x["kind"]): x
            for x in fallback["tracks"]
            if x["kind"] != "weights"
        }
        if fallback
        else {}
    )
    animated = []
    for clip in clips:
        duration = clip["duration"]
        times = [
            k / fps
            for k in range(int(math.ceil(duration * fps)))
            if k / fps < duration - 1e-5
        ]
        times.append(duration)
        if len(times) < 2:
            times = [0.0, duration]
        frames = len(times)
        time_acc = add_accessor(doc, binary, times, "SCALAR", 0.0, duration)
        animation = {
            "name": clip["name"].split("/")[-1],
            "samplers": [],
            "channels": [],
            "extras": {"source": clip["name"], "sourceDuration": duration},
        }

        def channel(node, path, values, shape):
            out = add_accessor(doc, binary, values, shape)
            sampler = len(animation["samplers"])
            animation["samplers"].append(
                {"input": time_acc, "output": out, "interpolation": "LINEAR"}
            )
            animation["channels"].append(
                {"sampler": sampler, "target": {"node": node, "path": path}}
            )

        morphs = {}
        source_tracks = {(x["name"], x["kind"]): x for x in clip["tracks"]}
        active_tracks = list(clip["tracks"]) + [
            tr for key, tr in fallback_tracks.items() if key not in source_tracks
        ]
        for tr in active_tracks:
            name, kind = tr["name"], tr["kind"]
            if kind == "weights":
                morphs[name.split("/")[-1]] = tr
                continue
            for node_idx in bone_nodes.get(name, []):
                bt, bq = bind[node_idx]
                rt = ref_tracks.get((name, kind), tr)
                pt = pose_tracks.get((name, kind))
                if kind == "translation":
                    ref = (
                        convert_vector3(sample(rt, 0)) if reference else (0.0, 0.0, 0.0)
                    )
                    target = (
                        convert_vector3(sample(pt, reference_pose_time)) if pt else bt
                    )
                    adjust = (
                        tuple(target[i] - ref[i] for i in range(3))
                        if reference
                        else (0.0, 0.0, 0.0)
                    )
                    values = [
                        v + adjust[i]
                        for t in times
                        for i, v in enumerate(convert_vector3(sample(tr, t)))
                    ]
                    channel(node_idx, "translation", values, "VEC3")
                elif kind == "rotation":
                    ref = bone_rotation(rt, 0, nodes[node_idx]) if reference else None
                    target = (
                        bone_rotation(pt, reference_pose_time, nodes[node_idx])
                        if pt
                        else bq
                    )
                    correction = qmul(qinv(ref), target) if reference else (0, 0, 0, 1)
                    values = []
                    prev = None
                    for t in times:
                        q = normalize(
                            qmul(bone_rotation(tr, t, nodes[node_idx]), correction)
                        )
                        if prev and sum(a * b for a, b in zip(prev, q)) < 0:
                            q = tuple(-v for v in q)
                        values.extend(q)
                        prev = q
                    channel(node_idx, "rotation", values, "VEC4")
        if morphs:
            for i, node in enumerate(nodes):
                mesh_idx = node.get("mesh")
                if mesh_idx is None:
                    continue
                mesh = doc["meshes"][mesh_idx]
                target_names = mesh.get("extras", {}).get("targetNames", [])
                if not target_names or not any(n in morphs for n in target_names):
                    continue
                values = []
                for t in times:
                    values.extend(
                        sample(morphs[n], t)[0] if n in morphs else 0.0
                        for n in target_names
                    )
                channel(i, "weights", values, "SCALAR")
        if animation["channels"]:
            doc.setdefault("animations", []).append(animation)
            animated.append((animation["name"], len(animation["channels"]), frames))
    save_glb(output_glb, doc, binary)
    return animated


def iso_rows(index_path):
    return json.loads(Path(index_path).read_text(encoding="utf-8"))


def catalog_from_iso(iso_path, index_path):
    result = []
    rows = iso_rows(index_path)
    with (
        Path(iso_path).open("rb") as f,
        mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm,
    ):
        for row in rows:
            if row["size"] < 128 or row["head"][:8] != "7265730a":
                continue
            start = ISO_BASE + row["offset"]
            data = mm[start : start + row["size"]]
            if b"banm" not in data[:96]:
                continue
            name = clip_name(data)
            if not name:
                continue
            result.append(
                {
                    "entry": row["entry"],
                    "name": name,
                    "offset": row["offset"],
                    "size": row["size"],
                }
            )
    return result


def read_iso_entry(mm, row):
    start = ISO_BASE + row["offset"]
    return mm[start : start + row["size"]]


def main():
    ap = argparse.ArgumentParser(
        description="Export The Sims 2 PSP BANM animations to GLB"
    )
    ap.add_argument("--iso", default=DEFAULT_ISO)
    ap.add_argument("--index", help="index.json generado al extraer PATHCACHE")
    ap.add_argument("--archive", help="ZIP of extracted BANM animations")
    ap.add_argument("--model", required=True, help="Previously extracted character GLB")
    ap.add_argument(
        "--clip",
        action="append",
        required=True,
        help="Unique name fragment; repeat for multiple clips",
    )
    ap.add_argument(
        "--reference",
        default="none",
        help="Ajuste de pose; por defecto conserva las rotaciones originales",
    )
    ap.add_argument(
        "--fallback",
        default=REFERENCE_F,
        help="Pose for bones omitted by partial clips; use none to disable",
    )
    ap.add_argument("--output", required=True)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()
    if args.archive:
        with zipfile.ZipFile(args.archive) as z:
            catalog = json.loads(z.read("catalog.json"))
    else:
        if not args.index:
            raise SystemExit("Indique --index o --archive")
        if not args.iso:
            raise SystemExit("Indique --iso para leer la ISO")
        catalog = catalog_from_iso(args.iso, args.index)
    names = {r["name"]: r for r in catalog}
    if args.reference != "none" and args.reference not in names:
        raise SystemExit("Referencia no encontrada")
    if args.fallback != "none" and args.fallback not in names:
        raise SystemExit("Pose auxiliar no encontrada")
    selected = []
    for frag in args.clip:
        matches = [
            r for r in catalog if r["name"].split("/")[-1].casefold() == frag.casefold()
        ]
        if not matches:
            matches = [r for r in catalog if frag.casefold() in r["name"].casefold()]
        if len(matches) != 1:
            raise SystemExit(
                f"--clip {frag!r}: {len(matches)} matches; use a longer search term"
            )
        selected.append(matches[0])
    if args.archive:
        with zipfile.ZipFile(args.archive) as z:
            get = lambda row: z.read(row["file"])
            reference = (
                parse_banm(get(names[args.reference]))
                if args.reference != "none"
                else None
            )
            fallback = (
                parse_banm(get(names[args.fallback]))
                if args.fallback != "none"
                else None
            )
            clips = [parse_banm(get(r)) for r in selected]
    else:
        with (
            Path(args.iso).open("rb") as f,
            mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm,
        ):
            reference = (
                parse_banm(read_iso_entry(mm, names[args.reference]))
                if args.reference != "none"
                else None
            )
            fallback = (
                parse_banm(read_iso_entry(mm, names[args.fallback]))
                if args.fallback != "none"
                else None
            )
            clips = [parse_banm(read_iso_entry(mm, r)) for r in selected]
    result = attach_animations(
        args.model, clips, reference, args.output, args.fps, fallback
    )
    for name, channels, frames in result:
        print(f"{name}: {channels} pistas, {frames} fotogramas")
    print(args.output)


if __name__ == "__main__":
    main()
