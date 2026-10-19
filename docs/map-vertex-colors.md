# Map Vertex Colors

## Source lighting and time of day

Map resources store vertex RGBA values. RGB is authored shading; alpha can
control material opacity or blend diffuse texture layers, depending on the
material. It is not safe to treat every alpha channel as transparency. For
example, Graveyard's grass uses alpha as the grass/dirt mix mask, so both
diffuse layers and their separate UV streams must be retained together.

Map archives can reference an `objects/light_rigs/fx_dnl_*` resource and its
`lighting/<rig>.res` data. The viewer reads the rig's three directional light
transforms and the `day_night_sequencer` curves for RGB and intensity. Those
values come from the game resource, not a guessed sun direction. The curves
have authored keys at frames 0, 30 and 60, with cubic tangents.

The preview currently samples the source rig at 12:00. The original curves
support a day/night schedule: 22:00–07:00 uses the night key, 08:00–21:00 uses
the day key, 07:00–08:00 interpolates from night to day, and 21:00–22:00
interpolates from day to night. The two transition hours use the original cubic
curve tangents. The interface currently has no time-of-day control.

The original resources provide light transforms, RGB and intensity curves. The
viewer keeps the authored RGB magnitude and intensity values; it does not
normalize each light's brightest color channel. It also reads the level's
ambient, directional, and point-light records. Point-light gain and radius are
preview settings: source intensity feeds a bounded strength curve, while the
radius uses a separate scale. The viewer currently uses a point-light gain of
2 and a radius scale of 0.01; these values are not stored as gain settings in
the source files. Directional light shapes store RGB and intensity but no
radius or gain; their source intensity is applied directly, with no distance
falloff. Other local light types use separate preview factors. The
diffuse/ambient material equation remains approximate because the resources do
not contain the game's GPU combiner or light-unit conversion. The preview does
not reproduce reflections, scripted lighting, post-processing, spot-light
cones, or every material-specific GPU behavior. If the archive does not
contain the referenced lighting rig, the default 12:00 profile is unavailable.

## Vertex-color intensity

Vertex-color shading is enabled by default for all maps. The preview uses the
default 100% intensity. It first applies the selected source day/night light
factors, then raises luminance for dark vertex colors with a neutral RGB
addition and an 8% brightness gain. The lift starts below luminance 0.80, uses
a 0.15 gamma exponent, and has a 0.16 luminance floor. Colors are clamped to
the displayable range. Terrain texture blends are excluded because their RGB
is not the color channel; their alpha blend mask remains unchanged.

Map exports currently omit vertex colors altogether. They contain neither the
preview's luminance lift nor its source day/night lighting, so export output is
not changed by the panel settings.

## Graveyard: layer masks are not opacity

Graveyard's grass material has two diffuse SURF layers: grass and dirt. Its
vertex alpha blends these layers. Loading only grass and treating vertex alpha
as opacity exposed the viewport background through the terrain. Both texture layers are required to reproduce that material.

The reader now keeps both GSHD layer descriptors and both RDMS UV streams. The
second stream offset is relative to the first vertex buffer, and has its own
UV compression origin and scale. A zero offset means both textures share UVs.
The GPU blends both textures, including their coverage, before applying the
vertex RGB and material tint. With vertex-color shading off, the terrain mask
and texture mixture remain active; only the authored RGB shading is omitted.

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
identify these attributes. Map exports currently omit vertex colors, but keep
the texture-layer blend mask and its UV data so the terrain textures can still
be mixed by a compatible viewer.

## Why Deadtree turned black

The packed vertex colors in the checked Deadtree resource include actual
`RGB = (0, 0, 0), alpha = 1` values, along with very dark nonzero colors. For
example, RGBA5551 word `0x8000` expands to opaque black. Directly multiplying
those values by the texture and material color produces the reported black
areas. The raw-channel decoder is retained; black values are not replaced in
the model data.

## Controls and performance

Map vertex-color shading is enabled by default for all maps. Its preview
intensity is 100%. The **Lights** panel independently turns map illumination
on or off and selects **Day** or **Night**, sampling the source rig at 12:00 or
00:00 and showing the matching sky dome. The **Pointers** panel controls the
spawn, light and camera overlays.

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

The green ground is also vertex-colored. `pieza_382` has 1,682 colored
vertices, while `pieza_377` and `pieza_378` have 171 and 294. Their constant
RGBA is `(0, 8/255, 0, 1)`: alpha selects the second, grass diffuse layer, while
the near-black RGB is not a useful brightness value and would nearly black out
the grass if used as a tint. The terrain blend shader therefore carries map
lighting separately from its vertex-color mask. Local-light gain is added to
the day/night surface factor before the texture is shaded, so a dim DNL factor
does not suppress the local lights a second time.

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
three contact sheets and `audit.json`. It cannot establish visual parity with
the game's full material and lighting pipeline; its GPU combiner equation is
not known.
