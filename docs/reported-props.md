# Reported props: fixes and source limitations

Reimport your own ISO with **File > Select ISO** after updating. The props ZIP
now includes `prop_support.json`, 96-byte-header standalone SURF resources and
separate object BANM clips. No game data is distributed with the source or EXE.

## Results

| Resource or family | Result |
| --- | --- |
| `fx_trackingdevice` | The closed rest assembly removes the opposite housing/lid offsets. Playback retains the original source curves and transforms. Export includes the constant transforms needed to preserve that playback. |
| `moneytree` | BMSH UV shorts are interpreted as unsigned. Coordinates just below the bias no longer jump by 16 texture tiles. Preview and export share this correction. |
| `fx_polygraph` | Its embedded atlas is literally the game's Missing Texture placeholder. The support bank provides the genuine `txt_careerpolygraph-surfaces.tif` for those parts. Other materials remain separate. |
| `cowthulu_stage` | All 13 source joints and four hose BANM clips are available. The importer previously excluded clips stored under `objects/`. Preview skins the mesh, and GLB includes all four clips or the selected clip. |
| Bathtub water templates | The ISO water bitmap is applied. These templates have no original UVs: planar UVs are generated and labelled as such. This is an approximation, not recovery of original UVs. |
| Hottub water/foam templates | Separate ISO water and foamy-edge bitmaps are applied. Generated planar UVs are explicitly labelled. |
| Sink washhands templates | The existing water UVs are retained and the ISO soap/water bitmap is restored. Duplicate resources remain separate catalogue entries. |
| Toilet lowerseat/raiseseat templates | The existing water UVs are retained and the ISO toilet-water bitmap is restored. |
| `fx_sinkcounterskinscorcher` | The counter mesh references an atlas made for the pedestal sink. An approximate atlas projection places the basin and fittings on the appropriate regions. Labelled `generated_atlas`, also in GLB extras. |
| `fx_lamp_kine`, `fx_lamp_swinging` | Both source textures decode. The black areas are stored vertex shading. Toggle **Vertex colors** to inspect the bitmap without that shading; source colors remain in exports. |
| `fx_fallenbookcase`, `02`, `03` | The index buffer is U16 despite a U8 VTYPE. Width correction now checks the allocated buffer span, preserving genuine U8 data and meshes that intentionally leave vertices unused. |
| `fx_toiletvalue`, `_01`, `_02` | The current base includes the indexed-texture swizzle fix. Verified again in the complete catalogue render. |
| `fx_d47mechdoor`, `fx_elevatordoors` | The current base's opaque-door material treatment is retained and exported consistently. The PSP BC3 layout is correct; low source alpha is not by itself proof of a decoder fault. |

## Still untextured in the source

The reported stove/food FX templates, the bar tumbler FX and the two crib
get/put templates contain no SURF bitmap reference. Most also contain no UVs.
Searching the ISO's named resources did not recover matching food atlases.
These remain inspectable as untextured templates, with a visible source-status
message. They are **not counted as fixed texture cases**. In particular, assigning
an unrelated object atlas would not recover their original appearance.

Examples include `fx_o-generic-resettozero-pose`,
`fx_o-sandwich-inpan-neutral-pose`, the `fx_o2a-food*` and
`fx_o2a-stove-fry-*` families, `fx_o2a-servingplatter-flambe-start-*`,
`fx_o2a-bar-makedrink-bottled-bartumbler`, and
`fx_o2a-crib-baby-get-getter` / `fx_o2a-crib-baby-put-putter`.

## Reproduce the checks

```powershell
$env:TS2_PSP_DATA_DIR = 'path\to\your\imported\resources'
python tools/check.py
python tests/test_prop_playback_gui.py --slow-frame
python tests/test_prop_playback_gui.py --slow-frame --prop cowthulu_stage --clip idle-breathe
python tools/review_prop_catalog.py "$env:TS2_PSP_DATA_DIR\sims2_psp_props_recursos.zip" review
```

The catalogue report separates successful parsing/rendering from missing source
materials and generated UVs. A zero-error render does not mean that every source
template has a recoverable original texture. Existing caches without the support
bank are supported; ISO-dependent regression tests skip with a reimport message.
