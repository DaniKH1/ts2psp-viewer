# Map Vertex Colors

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

Fill does not affect GLB/FBX export. With Vertex Colors on, export retains the
original source colors, including black values. With it off, map export keeps
the existing behavior of omitting vertex colors. No reimport is required.

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
