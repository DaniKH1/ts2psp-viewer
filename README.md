# The Sims 2 PSP Viewer

Windows app for characters, facial shape keys, props and maps. Game resources are not included.

<img width="1919" height="985" alt="image" src="https://github.com/user-attachments/assets/b650eefa-ab89-480c-9bf7-4fdba72d03ad" />


Three tabs: **Characters**, **Props** and **Maps**. The top bar has **Language**
(English or Spanish) and **Select ISO...**.

Textures use their internal bitmap names. Hover over a thumbnail or open its
preview to see the name. **Layers**, UV inspection and exported meshes include
the texture name alongside the original piece ID, so pieces sharing a texture
stay distinguishable. GLB stores the full internal texture path; glTF and map
texture ZIPs preserve internal folders with PNG filenames. Differing images
that share a source name receive a short suffix to prevent overwrites.

Map terrain materials can mix two textures using vertex alpha and separate UV
sets. The viewer keeps that mixture when toggling **Vertex Colors**; exports
bake it into a portable texture and preserve source UVs/masks in GLB attributes.
See [map colors and the full-map audit](docs/map-vertex-colors.md).

## Run from source

Use Python 3.11 or newer (the EXE is built with Python 3.13):

```powershell
python -m pip install -r requirements.txt
python run_viewer.py
```

`run.bat` starts the same app. Imported files live in `%LOCALAPPDATA%\TS2 PSP Viewer\data`. You can also place resource ZIPs in `data/` or set `TS2_PSP_DATA_DIR` to an existing resource folder.

ISO extraction runs in a separate process, with visible extraction/validation
progress and **Cancel import**. Failures are shown and recorded in
`%LOCALAPPDATA%\TS2 PSP Viewer\last-import.log` (or your configured cache folder).
See [ISO import checks](docs/iso-import.md) for troubleshooting and the actual
EXE extraction test.

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
anything, and you can also start it by hand just to rebuild.

The schedule is `17 22 * * *`, which is 00:17 in Spain during summer time and
23:17 in winter: GitHub cron is always UTC and does not follow daylight saving.
The minute is 17 rather than 0 on purpose, because scheduled runs queue behind
each other at the top of the hour.

### Publishing a release

Publishing lives in its own workflow, `release.yml`, which only ever runs when
you start it from the Actions tab. It compiles the EXE and puts it in a release,
but the release job is pinned to an environment called `release`, so GitHub
leaves it in *waiting for approval* until somebody on that environment's
reviewer list clicks approve. Nobody but you can be on that list.

Leave the `tag` input empty and it publishes as `v<today's date>`, or type a
version like `v1.0.0` to choose it yourself.

One-time setup, in **Settings > Environments > New environment**:

1. Name it exactly `release`.
2. Under *Required reviewers*, add your account.
3. Save.

Without that step nobody is asked to approve anything and the job starts
straight away, which is the opposite of what this is for.

Neither workflow has a conditional job, so neither can show one as *skipped*:
`build-exe.yml` either runs or it does not, and `release.yml` runs both jobs or
none. Pushing a `v*` tag does nothing at all.

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

Character animations retain the fixed BODY joint orientations. This prevents
Cowthulu/Beelzebeef's spine and left arm from folding through the body. The same
composition is used in preview and exported animations; see
[character rotation channels](docs/character-animation.md).

## Props

After updating from an older build, select your ISO again with the top-right
**Select ISO...** button. The new cache includes standalone object bitmaps and
both embedded and separate prop animation clips. Older ZIPs still open with a
reminder.

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
animation channels. Embedded clips and separate `fx_o-` / `fx_o2a-` NODE clips
play here. Separate clips are associated with a prop when their animated node
names match transforms in that prop's rig and both resources are in the same
game folder; this also picks up shared rigs such as chair, computer and stove
variants. Clips that only animate sim-held accessories have no matching prop
rig and are left out. When a prop has a supported clip, its export dialog offers
an **Include animation** checkbox; uncheck it to export the static prop. See
[prop rigs](docs/prop-rigs.md) for supported tracks, the satellite regression
and the validation command.

The **FPS** button cycles 20/30/60/15 and genuinely caps how often the clock
advances, without slowing the animation down. **Auto Play** starts a prop as soon
as it loads if it has a clip.

Selecting more than one animation in the list (Ctrl or Shift) and exporting with
**Include animation** enabled writes one animated model per clip. Exports use
`exports/<model>_export/` in the project directory, like Character exports.

The Objects tab hides authoring and picking geometry by default; the mesh helpers
stay out of the preview until asked for. Material status distinguishes textured
parts, stored solid colors and unresolved references. Use
`tools/review_prop_catalog.py` to review your entire extracted prop catalogue.

Model statistics are painted over the viewport in the bottom left corner rather
than under the playback controls: the name and what was loaded, plus the
duration and track count when the prop has a clip.

The **Vertex colors** checkbox lets you inspect the texture without stored
vertex shading; it does not remove colors from the exported model. Cowthulu's
stage exposes four separate hose clips and all 13 joints. See
[reported prop fixes and source limitations](docs/reported-props.md) for water
materials, UV repairs and the remaining untextured authoring templates.

Props and maps switch Vertex Colors using cached color arrays. Changing this
setting does not reread the archive, reupload textures, reset the camera or
restart an animation. Prop exports retain their source colors. Map exports
currently omit vertex colors regardless of the preview setting.
See [first import and Vertex Colors validation](docs/first-import-and-vertex-colors.md)
for the decoder changes and measured switch timings.

## Maps

Map props use their authored position, rotation and scale, including repeated
instances and nested object references. The same placements are preserved in
GLB exports. Click a piece to see its source prop and instance name.

The floating **Vertex Color** panel controls the preview's authored vertex
shading and source day/night time. Its intensity defaults to 100%; dark authored
colors receive a luminance lift while their relative shadow variation is kept.
The time slider samples the map's original day/night rig when available. The
preview still approximates the game's lighting, as its GPU combiner is unknown.
Map exports currently omit vertex colors. See [map Vertex Colors and lighting
findings](docs/map-vertex-colors.md).

**Maps > Export as... > Prop placement references (.json)** saves the resource
paths and full transforms in PSP coordinates (X/Y ground, Z up). This reads the
existing map archive; importing the ISO again is unnecessary for this change.

See [map prop placements](docs/map-prop-placements.md) for binary references,
the catalogue audit and limits involving scripted object states.

## Export

Character, prop and map exports write to `exports/<model>_export/` inside the
project without asking for a path. GLB, glTF (model, binary and textures) and
FBX are offered; FBX export requires a separate Blender installation. Map
exports omit vertex colors for now. Prop exports include supported animation by
default, with an option to export a static model.

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

Props use their shader references, texture palettes, packed coordinates, UV transforms and static scene hierarchy. Vertex colors and repeating UVs are preserved. Maps expose mesh parts and authored prop placements; complete terrain and runtime game state are not reconstructed.

See [architecture](docs/architecture.md) and [format notes](docs/formats.md) for the implementation path.

Character accessories are resolved from their game declarations. Emily Emory's maid hat loads automatically, keeps its lace transparency and follows the head when animated or exported. See `formats/accessories.py` to extend the supported accessory mappings.
