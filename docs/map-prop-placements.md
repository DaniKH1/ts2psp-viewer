# Map prop placements

## Finding the references

Map bundles contain reusable object graphs alongside the level graph. Rendering
every object graph independently puts furniture around its local origin and
loses repeated copies. The RDMS position origin is a compression parameter;
it does not indicate that a prop lacks a map placement.

The placement chain is:

1. Read the RES header size at `0x0c` (160 bytes in the checked map bundles).
2. Follow LEVL payload `+0x04` to the level NODE graph.
3. A type 31 effect node has a relative resource-path pointer at record `+0x28`.
4. The preceding NDBG section identifies each embedded NODE graph with a source
   path such as `objects\beds\fx_bedsinglebarracks.ma`.
5. Compose the reference's world matrix with the object's own mesh transforms.
   Traverse nested object references, guarding against cycles.

The importer needs no additional files for this: these records are already in
the map resource archive. Standalone props retain their own local scene.

Each instance keeps the correct owning graph when classifying mesh helpers and
reading node names. Node indices are local to a graph and cannot be looked up
in the level's first node table.

## Use in the app

The Maps viewer and GLB exports apply these authored transforms automatically.
The selected piece's status includes the source resource and placement name.
GLB mesh nodes keep those two identifiers in `extras`.

**Export as... > Prop placement references (.json)** saves each level reference,
its source path, position, full matrix and whether an embedded graph was found.
It also includes named character/NPC start and spawn transforms from the level
NODE graph. The **Pointers** panel overlays these transforms and their local
XYZ axes. Coordinates use the PSP basis: X/Y ground, Z up. JSON matrix rows
use column-vector transforms; translations are in the last column. GLB geometry
uses the viewer's normal glTF conversion `(-x, z, y)`.

The **Pointers** panel has independent toggles for spawn points, lights, and
cameras. Camera markers are only present where a map stores camera locators;
the gameplay camera can also be created or moved at runtime by scripts. Light
markers use the authored RGB color, intensity, and light type; their marker
color and size visualize those properties, while the XYZ axes show orientation.
Point lights also display a wireframe guide for their preview reach. The
day/night rig markers use the preview's default 12:00 sample.

Light RGB, intensity, type, and transform are read from the original level and
lighting-rig resources. The three day/night lights also use the original
sequencer curves, including their cubic tangents. These source values are kept
at their stored precision; the on-screen marker color is necessarily displayed
as 8-bit RGB.

Authored ambient, directional, point, and spot lights also illuminate map
surfaces in the preview. Vertex/material color remains the base, so its painted
shading is preserved and local light adds a bounded diffuse contribution. The
viewer uses the original RGB and intensity in the surface calculation, with a
per-channel color tint, a preview intensity scale, and distance falloff for
local lights. Point-light gain and radius are preview settings, not source-file
fields. The tint is applied before display clipping so colored sources
do not collapse to white on bright surfaces. The intensity scale is a preview
calibration because the resources do not define the game's conversion from
these stored intensity units to framebuffer radiance. Spot lights
currently use point-light falloff because their cone parameters are not yet
decoded. Generic Maya `light` shapes are treated as point lights when the
resource does not identify a more specific type. The game's full
GPU/material equation, light cutoffs, and occlusion are not available in these
resources, so the final pixel values cannot yet be claimed as exact.

To inspect all maps from the command line:

```powershell
python tools/audit_map_placements.py --data path/to/imported/data --output placements.json --check-meshes
```

## Validation and remaining limits

The checked catalogue contains 21 maps, 1,402 direct level references and 1,395
matches to embedded graphs. Some references describe sounds, functions or
virtual triggers and have no visible geometry. Seven references are unmatched:
five sound banks and two instances of
`objects/lamps_ceiling/fx_lightingceilingchandeliermoderndirty` in Rossum House.
Unmatched references remain in the JSON report and are not assigned a guessed
replacement.

All 21 maps decode without rejected RDMS parts, nonfinite vertices or duplicate
piece IDs. They yield 1,218 prop instance IDs with RDMS geometry, including references
nested inside other objects. This is a structural check, not visual confirmation
of every prop in the original game.

Regression checks cover Barracks' four beds (including a 90-degree rotation),
three showers with identical source names but distinct node IDs, 160-byte NODE
headers, JSON export and GLB coordinate parity.

The full suite passes 113 tests. A real GPU/window check switched map colors 21
times across 72,507 vertices and prop colors 20 times with no texture reuploads;
the largest measured switch plus frame was 0.052 seconds on the test PC. The
compiled Windows EXE also passed its open/close smoke check.

The viewer shows authored references, not a running game simulation. Scripts
can choose between clean/dirty or open/closed variants, hide objects, move them
or spawn new ones. Alternate authored states can therefore overlap. Animated
props in the Props tab still use their existing animation pipeline; map objects
are shown statically.
