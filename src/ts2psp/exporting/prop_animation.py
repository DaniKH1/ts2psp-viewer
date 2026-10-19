"""Bake linked NODE motion into the same rig used by the prop preview."""

import numpy as np

from ts2psp.core.coordinates import convert_row_matrix
from ts2psp.formats.animations import matrix_trs
from ts2psp.formats.prop_anim import PropAnimator


def _trs(matrix, fallback=None):
    matrix = np.asarray(matrix).copy()
    scales = np.linalg.norm(matrix[:3, :3], axis=0)
    zero = scales < 1e-12
    if zero.any():
        # Rotation is immaterial at zero scale; use the bind axes for decomposition.
        for axis in np.flatnonzero(zero):
            column = fallback[:3, axis] if fallback is not None else np.eye(3)[:, axis]
            matrix[:3, axis] = column / max(np.linalg.norm(column), 1e-12)
    flat = convert_row_matrix(matrix.T).reshape(16)
    translation, rotation, scale = matrix_trs(flat)
    scale = np.asarray(scale)
    # The basis swaps Y/Z scale components too.
    for axis, absent in enumerate((zero[0], zero[2], zero[1])):
        if absent:
            scale[axis] = 0.0
    return translation, rotation, tuple(scale)


def add_prop_animation(glb, data, bones, name, fps=30, clip=None, bindings=None):
    animator = PropAnimator(data, clip, bindings)
    clip = animator.clip
    if not bones:
        return 0
    animated = (set(animator.by_node) | animator.rest_overrides) & {bone["index"] for bone in bones}
    joints = glb.doc["skins"][0]["joints"]
    by_index = {bone["index"]: bone for bone in bones}
    bind_local = {}
    for bone, joint in zip(bones, joints):
        parent = by_index.get(bone["parent"])
        matrix = (
            np.linalg.inv(parent["world"]) @ bone["world"]
            if parent is not None
            else bone["world"]
        )
        bind_local[bone["index"]] = matrix
        # The default scene uses the source scales, independently of the unit bind.
        t, r, s = _trs(animator.joint_local_matrix(bone, by_index, None), matrix)
        node = glb.doc["nodes"][joint]
        node.pop("matrix", None)
        node.update(translation=t, rotation=r, scale=s)
    if not clip or clip["duration"] <= 0 or not animated:
        return 0
    # Preserve source key times as well as the regular bake samples.
    times = sorted(
        set(
            np.linspace(
                0, clip["duration"], max(2, int(np.ceil(clip["duration"] * fps)) + 1)
            )
        )
        | {time for channel in clip["channels"] for time in channel["times"]}
    )
    times = np.unique(np.asarray(times, dtype=np.float32))
    time_accessor = glb.accessor(
        times.tobytes(),
        5126,
        "SCALAR",
        len(times),
        minimum=[float(times[0])],
        maximum=[float(times[-1])],
    )
    animation = {
        "name": name.rsplit("/", 1)[-1],
        "samplers": [],
        "channels": [],
        "extras": {"sourceDuration": clip["duration"], "bakeFPS": fps},
    }
    for bone, joint in zip(bones, joints):
        if bone["index"] not in animated:
            continue
        frames = [
            _trs(
                animator.joint_local_matrix(bone, by_index, float(time)),
                bind_local[bone["index"]],
            )
            for time in times
        ]
        for column, path, size in (
            (0, "translation", 3),
            (1, "rotation", 4),
            (2, "scale", 3),
        ):
            values = np.asarray([frame[column] for frame in frames], dtype=np.float32)
            if path == "rotation":
                for i in range(1, len(values)):
                    if np.dot(values[i - 1], values[i]) < 0:
                        values[i] *= -1
            output = glb.accessor(values.tobytes(), 5126, f"VEC{size}", len(times))
            sampler = len(animation["samplers"])
            animation["samplers"].append(
                {"input": time_accessor, "output": output, "interpolation": "LINEAR"}
            )
            animation["channels"].append(
                {"sampler": sampler, "target": {"node": joint, "path": path}}
            )
    glb.doc.setdefault("animations", []).append(animation)
    return 1
