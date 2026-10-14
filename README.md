# The Sims 2 PSP Viewer

Windows app for characters, facial shape keys, props and maps. The UI is in
English. Open `TS2 PSP Viewer.exe`, then choose your own ISO from **File > Select
ISO**. Game resources are not included.

Three tabs: **Characters**, **Props** and **Maps**.

## Run from source

Use Python 3.11 or newer (the EXE is built with Python 3.13):

```powershell
python -m pip install -r requirements.txt
python run_viewer.py
```

`run.bat` starts the same app. Imported files live in `%LOCALAPPDATA%\TS2 PSP Viewer\data`. You can also place resource ZIPs in `data/` or set `TS2_PSP_DATA_DIR` to an existing resource folder.

Rendering requires an OpenGL GPU. Without one the app still opens and lists
what it can read, and the previews come back as `SinOpenGL`.

## Build and check

```powershell
python -m pip install -r requirements-build.txt
python tools/build.py
python tools/package_source.py
python tools/check.py
```

The EXE and source ZIP go to `dist/`. For checks using your own game data, set `TS2_PSP_DATA_DIR`. Add `--gui` to `tools/check.py` for the GPU and window checks; that is the only mode that opens real windows and needs your resources.

## Nightly build

`.github/workflows/build-exe.yml` compiles the EXE on `windows-latest` every
night and leaves it as an artifact, alongside the source ZIP. It never publishes
anything on its own.

The schedule is `17 22 * * *`, which is 00:17 in Spain during summer time and
23:17 in winter: GitHub cron is always UTC and does not follow daylight saving.
The minute is 17 rather than 0 on purpose, because scheduled runs queue behind
each other at the top of the hour.

### Publishing a release

Pushing a `v*` tag does nothing. To publish, run the workflow by hand from the
Actions tab, type the tag you want in the `tag` input, and approve the *Publicar
release* job when GitHub asks.

That approval is the whole point, and it comes from an environment: the release
job is pinned to `release`, and whoever is listed as a required reviewer for that
environment has to click approve before the job starts. Until somebody sets that
up, the job runs straight through and anyone with push access can publish.

One-time setup, in **Settings > Environments > New environment**:

1. Name it exactly `release`.
2. Under *Required reviewers*, add your account.
3. Save.

Once the environment exists, `Publicar release` sits in *waiting for approval*
instead of *skipped* whenever you leave the tag empty.

Game resources are never part of the build, and neither is `data/`: the user
picks their own ISO at runtime. The job checks that the EXE exists and is a
plausible size, but it does not launch it — the app needs your ISO to get past
the import screen, which is what `tools/smoke_exe.py --data` is for locally.

Two things worth knowing: a scheduled workflow only runs from the default
branch, and GitHub turns scheduled workflows off after 60 days without activity
in the repository.

## Characters

One viewport, two views. The **Animation View** button in the top left swaps the
column between the character list and the clip list; the character on screen
changes with it.

- **Character View** shows the static sim. Shape Keys and their Strength slider
  live in the right column, and the textures of whoever is loaded sit under the
  character list on the left. That block belongs to the character, so it is only
  there in this view.
- **Animation View** shows a clip playing on the same canvas, with the clip list,
  its Group dropdown and the playback table on the right. The export button
  becomes *Export clip as...*.
- **Replace character**, just below the view button and only in Animation View,
  opens the character list so a specific sim can be forced onto a clip. While it
  is marked, the clip's own rules stop applying: the choice in the list wins, and
  the viewport character is left alone so Character View still remembers its own.

Hand bone placement is corrected when the model is read, so wrists stay inside
the skin; the hand bind offset is measured per branch, excluding the wrist, which
otherwise drags one hand away from the other. See `formats/characters.py`.

## Props

The **Props** tab is laid out in three columns.

- **Left**: the model list, its search box, a **Group** dropdown that splits the
  list by the folder each prop lives in, and below a separator the texture
  gallery. Click a thumbnail for a full-screen preview.
- **Right**: the animation list with its own search box, its **Group** dropdown,
  and the playback table — ▶ Play and an FPS button on one row, Loop, Auto Play
  and the clock on the next, and the scrubber below. Both lists are the same
  height and show the prop name only.
- **Centre**: the model, with floating buttons for Export as..., Bones, Grid and
  Reset view. Bones start off.

Props keep the NODE hierarchy, named joints, rotation/scale pivots and linked
animation channels. Embedded transform animations play here and are included in
GLB/FBX exports. See [prop rigs](docs/prop-rigs.md) for supported tracks, the
satellite regression and the validation command.

The **FPS** button cycles 20/30/60/15 and genuinely caps how often the clock
advances, without slowing the animation down. **Auto Play** starts a prop as soon
as it loads if it has a clip.

Selecting more than one animation in the list (Ctrl or Shift) and exporting writes
one model per animation into the folder you choose. With a single selection it
exports as a single file, as before.

The Objects tab hides authoring and picking geometry by default; the mesh helpers
stay out of the preview until asked for. Material status distinguishes textured
parts, stored solid colors and unresolved references. Use
`tools/review_prop_catalog.py` to review your entire extracted prop catalogue.

Model statistics are painted over the viewport in the bottom left corner rather
than under the playback controls: the name and what was loaded, plus the
duration and track count when the prop has a clip.

## Export

Character and clip exports write to `exports/<model>_export/` inside the project
without asking for a path. GLB, glTF (model, binary and textures) and FBX are
offered; FBX export requires a separate Blender installation.

Props ask where to save instead: a single export opens a file dialog, and a
multiple export asks for a folder and writes one file per animation into it.

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

Props use their shader references, texture palettes, packed coordinates, UV transforms and static scene hierarchy. Vertex colors and repeating UVs are preserved. Maps still expose mesh parts; complete terrain and playable scene placement are not reconstructed.

See [architecture](docs/architecture.md) and [format notes](docs/formats.md) for the implementation path.

Character accessories are resolved from their game declarations. Emily Emory's maid hat loads automatically, keeps its lace transparency and follows the head when animated or exported. See `formats/accessories.py` to extend the supported accessory mappings.
