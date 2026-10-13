# The Sims 2 PSP Viewer

Windows app for characters, facial shape keys, props, maps and animations. The UI is in English. Open `TS2 PSP Viewer.exe`, then choose your own ISO from **File > Select ISO**. Game resources are not included.

## Run from source

Use Python 3.11 or newer (the EXE is built with Python 3.13):

```powershell
python -m pip install -r requirements.txt
python run_viewer.py
```

`run.bat` starts the same app. Imported files live in `%LOCALAPPDATA%\TS2 PSP Viewer\data`. You can also place resource ZIPs in `data/` or set `TS2_PSP_DATA_DIR` to an existing resource folder.

## Build and check

```powershell
python -m pip install -r requirements-build.txt
python tools/build.py
python tools/package_source.py
python tools/check.py
```

The EXE and source ZIP go to `dist/`. For checks using your own game data, set `TS2_PSP_DATA_DIR`. Add `--gui` to `tools/check.py` for the GPU and window checks.

## Where the code lives

| Folder | Purpose |
| --- | --- |
| `src/ts2psp/formats/` | Read mesh, texture, shader, scene and animation formats |
| `src/ts2psp/rendering/` | OpenGL previews, camera projection and animated geometry |
| `src/ts2psp/ui/` | Tabs, controls, image styles and UV windows |
| `src/ts2psp/exporting/` | GLB/glTF writers and Blender FBX bridge |
| `src/ts2psp/io/` | Read the ISO and create resource archives |
| `src/ts2psp/core/` | Coordinate conversions shared by readers and writers |
| `src/ts2psp/assets/` | Interface images, font and icon |
| `tests/` | Regression checks; no game resources |
| `tools/` | Build, package and diagnostic scripts |
| `docs/` | User guide, architecture and binary format notes |

Props now use their shader references, texture palettes, packed coordinates, UV transforms and static scene hierarchy. Vertex colors and repeating UVs are preserved. FBX export requires a separate Blender installation. Maps still expose mesh parts; complete terrain and playable scene placement are not reconstructed.

See [architecture](docs/architecture.md) and [format notes](docs/formats.md) for the implementation path.

Props preserve the NODE hierarchy, named joints, rotation/scale pivots and linked
animation channels. Embedded transform animations play in the Objects tab and
are included in GLB/FBX exports. See [prop rigs](docs/prop-rigs.md) for supported
tracks, the satellite regression and the validation command.

The Objects tab hides authoring and picking geometry by default; **Show helper
meshes / effect templates** restores it for inspection and export. Material
status distinguishes textured parts, stored solid colors and unresolved references.
Use `tools/review_prop_catalog.py` to review your entire extracted prop catalogue.

Character accessories are resolved from their game declarations. Emily Emory's maid hat loads automatically, keeps its lace transparency and follows the head when animated or exported. See `formats/accessories.py` to extend the supported accessory mappings.
