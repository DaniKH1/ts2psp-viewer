# Map Vertex Colors

## Graveyard: layer masks are not opacity

Graveyard's grass material has two diffuse SURF layers: grass and dirt. Its
vertex alpha blends these layers. Loading only grass and treating vertex alpha
as opacity exposed the viewport background through the terrain. Both texture layers are required to reproduce that material.

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

## Controls and performance

**Vertex Color** shows the stored vertex RGB without an artificial light fill.
Turning it off omits vertex shading and keeps material colors and terrain masks.
It does not decode assets, reupload textures, replace geometry, change layer
visibility or reset the camera. Exports use the same source color setting.

The **Layers** browser shows texture basenames without `txt_`, bitmap suffixes
or mesh IDs. Its textured search field filters only the list. Hidden layers
stay hidden, and selecting a filtered row still targets the original mesh.

## Espiritu Estate counter textures

SURF sizes exclude the resource header. The texture decoder incorrectly used
128 bytes for map containers with a 160-byte header, rejecting bitmaps that
ended in the last 32 bytes of the section. The decoder now uses the declared
header and checks pixel and palette bounds against that section size.
All 51 counter pieces/instances in Espiritu Estate load their referenced
`textures/design_mode/counters/counter01.tif` with the original UVs.

## Checks

132 unit/resource tests passed. All 21 maps were parsed and rendered with zero
texture decode warnings or invalid mesh/color/secondary-UV data. The Espiritu
GUI test exercised filtered layer selection plus 21 color toggles and 20 prop
color toggles, with no texture reuploads. Its largest switch plus frame was
0.047 seconds and event gap 0.145 seconds on the validation PC.

Run the full map audit with your own imported resources:

```powershell
$env:PYTHONPATH = "src"
python tools/audit_map_colors.py --data "path/to/resources" --output "path/to/audit" --render
python tests/test_texture_blend_gpu.py
```

The audit checks all 21 maps, both UV layouts, color/position ranges and layer
references. It saves per-map GPU views with colors off and exact source colors,
three contact sheets and `audit.json`. It cannot establish visual
parity with all original game lighting states.
