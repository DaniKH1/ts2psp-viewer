# Map Vertex Colors

## Graveyard: layer masks are not opacity

Graveyard's grass material has two diffuse SURF layers: grass and dirt. Its
vertex alpha blends these layers. Loading only grass and treating vertex alpha
as opacity exposed the viewport background through the terrain. Ambient fill
could not repair that missing material layer.

The reader now keeps both GSHD layer descriptors and both RDMS UV streams. The
second stream offset is relative to the first vertex buffer, and has its own
UV compression origin and scale. A zero offset means both textures share UVs.
The GPU blends both textures, including their coverage, before applying the
vertex RGB and material tint. Turning off Vertex Colors removes the RGB shading
while retaining the terrain mask and texture mixture.

The same two-diffuse-layer technique occurs in 68 pieces/instances across nine
of the 21 imported maps, including hidden authoring meshes. Reflection layers
are recorded separately; they are not interpreted as diffuse terrain blends.
Materials without a vertex-alpha mask retain a constant first-layer weight;
the game's generated masks, reflection mapping and complete dynamic lighting
have not been reconstructed.

An independent header bug affected untextured GSHD records: in 160-byte map
containers, they were read 32 bytes early when no SURF pointer resolved their
layout. This produced arbitrary RGB values and zero opacity. Untextured
materials now use the declared resource header, including 96/128-byte layouts.

### Export

For portability, GLB/glTF/FBX exports bake the two diffuse textures and their
mask into a per-mesh PNG atlas. Vertices are split at atlas seams; triangle
positions and normals stay the same. Atlases use up to 4096 pixels per side,
so finite texture resolution can affect close-up sampling. Preview rendering
samples both original textures directly and does not use this bake.

The GLB also retains both original texture images, both original UV sets
(`_PSP_UV0`, `_PSP_UV1`) and the original alpha mask (`_PSP_BLEND`). Mesh extras
identify these attributes. `COLOR_0` retains vertex RGB and uses opacity alpha
instead of the mix weight; ordinary single-texture meshes keep their original
RGBA. Exporting with Vertex Colors off still keeps the terrain blend.

## Why Deadtree turned black

The packed vertex colors in the checked Deadtree resource include actual
`RGB = (0, 0, 0), alpha = 1` values, along with very dark nonzero colors. For
example, RGBA5551 word `0x8000` expands to opaque black. Directly multiplying
those values by the texture and material color produces the reported black
areas. The raw-channel decoder is retained; black values are not replaced in
the model data.

The viewer does not yet reproduce the game's complete scene lighting,
light-linking or time-of-day logic. A preview fill control provides a readable
view of the geometry while keeping the source shading available for inspection.
This is a viewing aid, not recovered original game lighting.

## Controls

Turn on **Vertex Colors**, then open **Lighting…**:

- **Ambient fill** defaults to 50%.
- **Source (0%)** displays the stored vertex colors exactly as before.
- **Reset (50%)** restores the preview default.

Fill blends each eligible vertex's modulated RGB toward its material-only RGB:
`preview = sourceModulated + fill * (materialOnly - sourceModulated)`.
It preserves color variation while making dark regions inspectable. Alpha is
unchanged; sky textures, shadow textures and geometry without normals are left
out. Props and characters retain their existing preview behavior.

Fill does not affect GLB/FBX export. With Vertex Colors on, export retains
source RGB, including black values. With it off, vertex shading is omitted.
Terrain blend masks are preserved separately as described above. No reimport
is required.

## Performance and checks

The renderer retains the original color array and one reusable fill result.
Changing fill only recomputes RGB when its value changes. Toggling source/fill
or Vertex Colors does not decode assets, reupload textures, replace geometry,
change layers or reset the camera.

Unit checks cover black input, alpha preservation, exact source mode, GLB source
colors, exclusions for sky/shadows/effects and reuse of the filled array. The
real Deadtree window was checked across 80,691 vertices with 21 color toggles,
six fill changes and 20 prop color toggles. There were zero texture reuploads;
the largest measured toggle plus frame was 0.0441 seconds and UI event gap was
0.148 seconds on the validation PC. GPU renders of Deadtree were inspected in
both source and filled modes.

Run the full map audit with your own imported resources:

```powershell
$env:PYTHONPATH = "src"
python tools/audit_map_colors.py --data "path/to/resources" --output "path/to/audit" --render
python tests/test_texture_blend_gpu.py
```

The audit checks all 21 maps, both UV layouts, color/position ranges and layer
references. It saves per-map GPU views with colors off, exact source colors,
preview fill, three contact sheets and `audit.json`. It cannot establish visual
parity with all original game lighting states.
