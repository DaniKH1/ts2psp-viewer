# First ISO import and Vertex Colors

## Fixes

- **No existing data folder:** ISO selection now checks for active tabs before
  consulting the character export state. The welcome screen can import an ISO
  without a `character_tab` attribute. Existing exports still block replacement.
- **Color decoding:** RGB565 and RGBA5551 expand channels to 8-bit values using
  bit replication, matching PSP behavior. RGBA4444 and RGBA8888 preserve channel
  order and alpha. Color and normal attributes respect their own alignment.
- **Vertex alpha:** Partial vertex alpha selects blending; binary alpha uses
  cutout rendering. Explicit material overrides take precedence. Turning vertex
  colors off restores the material-only alpha mode as well as its colors.
- **Switch pause:** Maps previously reparsed the complete resource and rebuilt
  the scene. Props created a separate scene for each color setting. Both now
  switch cached color arrays and retain geometry, textures and camera state.
- **Exports:** Prop exports preserve source colors. Map exports currently omit
  vertex colors regardless of the preview toggle or intensity setting.

The color packing/alignment was checked against the primary implementation in
[PPSSPP's vertex decoder](https://github.com/hrydgard/ppsspp/blob/master/GPU/Common/VertexDecoderCommon.cpp).
This verifies packed-channel decoding, not game lighting. The viewer keeps the
source color data intact and applies a selective dark-color luminance lift only
in the map preview; see [map Vertex Colors and Lighting Findings](map-vertex-colors.md).

## Local verification

The **Vertex Color** map panel applies source day/night rig lighting and a
selective luminance lift to dark authored vertex colors in the preview. It does
not modify map exports, which currently omit vertex colors. See [map Vertex
Colors and Lighting Findings](map-vertex-colors.md) for the source data, hour
schedule, correction behavior and limits of matching the game renderer.
The figures below record the earlier decoder/switch validation.

- 110 unit/integration checks passed with the locally imported resources; the
  existing GPU/camera checks also passed.
- Fresh ISO import through an actual empty welcome screen: 767 props loaded,
  support bank present, no Tkinter callback errors; 33.01 seconds on the test PC.
- Graveyard map: 52,440 vertices; 21 color switches using the same scene and
  geometry arrays. Lamp prop: 20 switches using the same scene.
- No texture reuploads during those switches. Maximum measured switch plus
  frame time: 0.0481 seconds. Maximum UI heartbeat gap: 0.0994 seconds, including
  switching tabs and loading the prop. These timings depend on the PC.

```powershell
$env:PYTHONPATH = 'src'
python tests/test_first_import_gui.py --iso 'path\to\your\game.iso'
$env:TS2_PSP_DATA_DIR = 'path\to\your\imported\resources'
python tests/test_vertex_colors_gui.py
python tools/check.py
```

The automated unit tests also cover canceling the first ISO dialog, starting
the import without character tabs, the export guard, known packed color values,
an aligned RGBA8888/float-normal stream and color switching during a posed mesh.
