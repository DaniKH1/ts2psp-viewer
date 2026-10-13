# Architecture

`run_viewer.py` starts `ts2psp.app`. The application selects the ISO/cache and creates four tabs. Each tab owns its selection and camera state; format readers do not depend on GUI widgets.

For a prop, `rendering/props.py` loads `formats/props.py`. The reader decodes each RDMS/BMSH mesh. `formats/materials.py` follows its GSHD pointer to the SURF texture, and `formats/surfaces.py` decodes the pixels. `formats/prop_scene.py` applies the static NODE hierarchy to rigid mesh instances. The same decoded parts feed the OpenGL preview and GLB writer.

Character meshes and facial modifiers are in `formats/characters.py`. Animation decoding is in `formats/animations.py`; evaluated frames are in `rendering/animation.py`. The GLB builder is in `exporting/glb.py`, so the readers share one buffer/material implementation.

`formats/accessories.py` resolves `accessories_N` from the character CSV or the matching `newSpecies` block in older caches. It maps supported effect names to mesh parts and textures. Emily's `fx_hatmaid` uses only the 13-vertex hat part of `hair/afhairhatmaid`, preserving her existing hairstyle. The lace texture includes its alpha mask, and the original head weights work in the preview, animation and GLB export. Unsupported effects are skipped. Add a verified mapping and regression check here when supporting another accessory.

Asset paths are resolved by `paths.py`. PyInstaller extracts bundled assets to its temporary folder; writable game data stays in the user's cache. Development data and exported game models are excluded from the source ZIP.

Small comments explain format offsets and non-obvious rendering decisions. Longer observations belong in these documents.
