# Architecture

`run_viewer.py` starts `ts2psp.app`. The application selects the ISO/cache and creates three tabs. Each tab owns its selection and camera state; format readers do not depend on GUI widgets.

For a prop, `rendering/props.py` loads `formats/props.py`. The reader decodes each RDMS/BMSH mesh. `formats/materials.py` follows its GSHD pointer to the SURF texture, and `formats/surfaces.py` decodes the pixels. `formats/prop_scene.py` applies the static NODE hierarchy to rigid mesh instances. The same decoded parts feed the OpenGL preview and GLB writer.

Character meshes and facial modifiers are in `formats/characters.py`. Animation decoding is in `formats/animations.py`; evaluated frames are in `rendering/animation.py`. The GLB builder is in `exporting/glb.py`, so the readers share one buffer/material implementation.

`formats/accessories.py` resolves `accessories_N` from the character CSV or the matching `newSpecies` block in older caches. It maps supported effect names to mesh parts and textures. Emily's `fx_hatmaid` uses only the 13-vertex hat part of `hair/afhairhatmaid`, preserving her existing hairstyle. The lace texture includes its alpha mask, and the original head weights work in the preview, animation and GLB export. Unsupported effects are skipped. Add a verified mapping and regression check here when supporting another accessory.

Asset paths are resolved by `paths.py`. PyInstaller extracts bundled assets to its temporary folder; writable game data stays in the user's cache. Development data and exported game models are excluded from the source ZIP.

Small comments explain format offsets and non-obvious rendering decisions. Longer observations belong in these documents.

Maps share the rigid mesh reader. `formats/prop_scene.py` links LEVL's level
graph to embedded object graphs through type 31 resource references and NDBG
source paths. Placement records retain their own graph, so local node indices
and helper ancestry remain valid. Both the map preview and GLB writer receive
the expanded world-space instances. See [map prop placements](map-prop-placements.md).

For props, `formats/prop_scene.py` composes pivot-aware NODE transforms;
`formats/prop_anim.py` follows explicit curve links and evaluates motion.
`exporting/prop_animation.py` bakes that same hierarchy into GLB animation.
The Objects tab reuses its compiled animator and keeps bind data immutable.
See [prop rigs](prop-rigs.md) for the binary fields and scope.

`formats/prop_assets.py` reads the optional `prop_support.json` inside the local
props archive. It resolves standalone ISO bitmaps and separate BANM clips, and
keeps documented material repairs apart from raw format decoding. Raw source
resources are never modified. Generated UVs are labelled in the preview and in
GLB mesh extras.

`formats/prop_skin.py` evaluates BANM rotation/translation tracks by bone name.
It compiles vertex positions, weights and palettes once, then skins with NumPy.
The GLB exporter uses the same reference pose; regression tests compare the
exported motion against preview vertices.

The renderer stores both material-only and vertex-modulated color arrays, plus
their alpha modes, when it creates a scene. `fijar_vertex_colors()` switches
references to those arrays; map and prop controls share this path. Map readers
keep source colors even when the preview setting starts off. Colorless map
exports use copies and leave the decoded data intact.
