# Prop rigs and animation

The reader uses the resource's NODE graph for rigid props. It keeps transform
ancestors of the rendered meshes, even when two nodes share a position. Such
nodes can have distinct rotations, scales or animation channels. Names come
from the node's own string pointer; a `__...__` text search misses names such
as `arm_set`, `arm` and `dish`.

## Transform records

A transform has its value payload at the relative pointer in `node + 8`:
visibility, translation XYZ, Euler rotation XYZ and scale XYZ. The node record
also contains scale pivot XYZ at `+32`, scale pivot offset at `+44`,
rotation pivot at `+56` and rotation pivot offset at `+68`. Rotations are in radians.
The matrix uses column vectors:

`T(t) T(rpt) T(rp) Rz Ry Rx T(-rp) T(spt) T(sp) S T(-sp)`

The type string is checked as well as numeric type 10: light records can use
the same number and their payload is different.

`joint` records are spatial nodes too. Their additional payload stores segment
scale compensation at `+84`, rotate-axis quaternion XYZW at `+88` and
joint-orient quaternion XYZW at `+104`. For column vectors the joint matrix is
`T IS JO R RO S`, where `IS` cancels the parent's local scale when enabled.
These orientations are required by bathtubs, cake and the mind-control device.

See Autodesk's [joint matrix documentation](https://help.autodesk.com/cloudhelp/2022/ENU/Maya-SDK/cpp_ref/class_m_fn_ik_joint.html)
for the authoring convention. The binary offsets above were checked against
the local game resources.

## Helper meshes and materials

Meshes beneath `__maya_only__`, untextured footprint polygons and pickable polygons
are authoring or interaction geometry. Textured footprints are preserved because
some carry ground shadows. Unreferenced RDMS meshes are effect templates.
They stay available in the parser and through **Show helper meshes / effect
templates**, but are excluded from the default preview and export. The checkbox
also controls export. Seven catalogue entries contain only such helpers.

Each GSHD resolves its own SURF reference. Diffuse RGB and opacity multiply the
bitmap and vertex colors. No SURF reference means a stored solid-color material;
it does not mean that a texture failed to load. Some animation resources contain
only untextured deformation templates or water planes, without the base object.
The viewer reports bitmap coverage and unresolved references separately.

The GPU caches textures by bitmap **and** repeat/clamp state. GLB exports retain
separate samplers while sharing the image bytes. Props render both face directions,
matching their exported two-sided materials. Glass, water and shadows use alpha
blending; low opacity is not discarded by the character hair cutout threshold.

## Curve links

The NODE header contains scalar curves in 12-byte descriptors from `head + 32`.
Each descriptor gives flags, key count and a relative key pointer. Keys contain
time, value, incoming tangent and outgoing tangent. Interpolation uses Hermite
slopes; infinite tangents represent held values. Repeat flags allow short
cyclic motions to continue over the clip duration.

`node + 24` points to the last property link; each link's `+20` pointer follows
the preceding link, ending at sentinel 1. A link's `+4` pointer identifies its
curve descriptor, `+8` holds the unit factor, the property byte at `+13` chooses
translation (1–3), rotation (4–6) or scale (7–9), and `+16` identifies source and
destination nodes. Curves replace the corresponding stored property.

For `fx_rightarm_satellite`, curve 0 targets node 5 (`arm_set`) rotation Z;
curve 1 targets node 8 (`dish`) rotation X. The clip lasts 10 seconds. The dish
pivot is about 0.752 m above the prop's origin after its parent transform, and
the arm pivot about 0.481 m. Four mesh parts use six transform joints.

## Preview and export

`PropAnimator` compiles links once, evaluates the hierarchy, and applies world
matrices to the original local mesh vertices. The skeleton overlay uses the
transformed rotation pivots. Playback does not mutate the original bind data.

The exporter gives rigid vertices weight 1 on their owning transform's joint.
Joint indices are positions inside `skin.joints`, not document node IDs. The
bind uses unit scales to preserve vertex precision for effects that start at
tiny or zero scale. Default scene transforms retain the original source scales.

GLB animations bake translation, quaternion rotation and scale at 30 FPS plus
source key times. FBX uses this GLB through the existing Blender bridge. BMSH
props retain their stored skin weights and named skeletons.

Visibility, light, particle and script-driven effects are not evaluated by this
transform player. Clips without supported transform links are not offered as
playable prop animations. The graph is preserved for meshes; no speculative
channels are assigned to unrelated bones.

## Validation

No game data is checked into the repository. With locally extracted archives:

```powershell
$env:TS2_PSP_DATA_DIR = 'path\to\your\data'
python tools/check.py
python tools/audit_prop_rigs.py "$env:TS2_PSP_DATA_DIR\sims2_psp_props_recursos.zip"
```

The audit checks mesh ownership and invertible binds across the catalogue,
exports rigid props, and compares sampled exported motion against the preview.
Results from the development archive are in `prop-rig-validation.json`.

To decode all source textures and generate two views of every prop:

```powershell
python tools/review_prop_catalog.py "$env:TS2_PSP_DATA_DIR\sims2_psp_props_recursos.zip" review-output
```

The command writes contact sheets and a per-resource JSON report. It does not
reproduce game lighting, reflections, particles or script-controlled scene setup.
The numerical audit compares the reader with its exported animation; it is not
a comparison against footage from the game. See `prop-catalog-validation.json`
for the complete material/geometry coverage from the local development archive.

Playback always queues the next frame through Tk, including when rendering takes
longer than 16 ms. Pending redraws are coalesced, and render errors stop playback.
The GUI regression runs Play/Pause for two complete satellite loops while
simulating slow frames and checking that other window events remain responsive:

```powershell
$env:PYTHONPATH = 'src'
python tests/test_prop_playback_gui.py --slow-frame
```

This check is also included in `python tools/check.py --gui`.
