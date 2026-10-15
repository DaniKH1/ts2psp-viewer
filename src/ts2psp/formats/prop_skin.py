"""BANM poses for skinned object meshes, compiled once per selection."""

import numpy as np

from ts2psp.formats.animations import matrix_trs, normalize, qinv, qmul, sample
from ts2psp.formats.characters import parse_bones
from ts2psp.formats.prop_scene import _quaternion_matrix


class SkinnedPropAnimator:
    def __init__(self, data, clip, reference):
        self.clip = {**clip, "channels": clip["tracks"]}
        self.rig = parse_bones(data)
        self.bind = [bone["world"].T for bone in self.rig]
        self.local = [
            (
                np.linalg.inv(self.bind[bone["parent"]]) @ self.bind[i]
                if bone["parent"] >= 0
                else self.bind[i]
            )
            for i, bone in enumerate(self.rig)
        ]
        names = {bone["name"]: i for i, bone in enumerate(self.rig)}
        refs = {(t["name"], t["kind"]): t for t in reference["tracks"]}
        self.bindings = []
        for track in clip["tracks"]:
            index = names.get(track["name"])
            if index is None or track["kind"] == "weights":
                continue
            ref = refs.get((track["name"], track["kind"]), track)
            t, q, scale = matrix_trs(self.local[index].T.reshape(16))
            self.bindings.append((index, track, ref, t, q, scale))
        self._mesh_cache = {}

    def pose(self, parts, t, bones=()):
        local = [m.copy() for m in self.local]
        for index, track, ref, bt, bq, scale in self.bindings:
            if track["kind"] == "rotation":
                q = qmul(
                    normalize(sample(track, t)),
                    qmul(qinv(normalize(sample(ref, 0))), bq),
                )
                local[index][:3, :3] = _quaternion_matrix(normalize(q)) * scale
            elif track["kind"] == "translation":
                local[index][:3, 3] = np.asarray(sample(track, t)) - sample(ref, 0) + bt
        world = []
        for i, bone in enumerate(self.rig):
            world.append(
                world[bone["parent"]] @ local[i] if bone["parent"] >= 0 else local[i]
            )
        delta = np.asarray(
            [matrix @ bone["ibm"].T for matrix, bone in zip(world, self.rig)]
        )
        output = []
        for part in parts:
            key = id(part)
            if key not in self._mesh_cache:
                vertices = part["skin_vertices"]
                positions = np.asarray(
                    [(*np.asarray(v[0]) / 1000, 1) for v in vertices]
                )
                weights = np.asarray([list(v[3]) for v in vertices], dtype=float)
                weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
                self._mesh_cache[key] = (
                    positions,
                    weights,
                    np.asarray(part["palette"], dtype=int),
                )
            positions, weights, palette = self._mesh_cache[key]
            transformed = np.einsum("kij,nj->nki", delta[palette], positions)
            posed = np.einsum("nk,nki->ni", weights, transformed)[:, :3]
            output.append(
                {
                    **part,
                    "vertices": [
                        (tuple(p), v[1], v[2], v[3])
                        for p, v in zip(posed, part["vertices"])
                    ],
                }
            )
        return output, {i: m[:3, 3] for i, m in enumerate(world)}
