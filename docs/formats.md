# Prop format notes

These observations come from the resource archive used for validation. They describe the supported PSP files, not a PS2 format.

- RES directory offsets are relative to the container header size stored at `0x0c`: commonly 128 bytes for props, 160 for map bundles and 96 for small leaf resources. Prefer that field when locating NODE payloads; guessing 128 first can accept unrelated bytes as a plausible node table.
- Each RDMS mesh references its GSHD shader with a signed relative pointer at the mesh header. A BMSH part has its shader pointer after the vertex layout fields. Section order does not determine the material.
- GSHD references SURF through the signed relative pointer at payload offset `0x64`. Short constant shaders have no SURF and retain their material color.
- SURF uses a variable mip pointer table. Its CLUT descriptor locates the palette; the palette is not always at `0x40`. T4 unpacks the low nibble first. T8 and RGBA4444 use PSP pixel swizzling.
- PSP DXT blocks are linear, with color indices before the RGB565 endpoints and alpha after color. The decoder also accepts base images smaller than one 4x4 block. Reference: [PPSSPP texture decoder](https://github.com/hrydgard/ppsspp/blob/master/GPU/Common/TextureDecoder.h).
- RDMS stores a position origin and scale, plus a UV offset and scale. Integer fields are normalized according to the PSP vertex type. UV shorts are unsigned. Reference: [PPSSPP vertex decoder](https://github.com/hrydgard/ppsspp/blob/master/GPU/Common/VertexDecoderCommon.cpp).
- NODE transform payloads contain visibility, translation, Euler rotation in radians and scale. Platform nodes reference arrays of 44-byte render groups and LOD descriptors. The preview/export uses the first LOD and preserves repeated rigid mesh instances.
- LEVL payload `+0x04` points to the level NODE graph. Effect nodes (type 31) reference an object path through the relative pointer at record `+0x28`; ancestor transforms place that object. NDBG payload `+0x00` points to the source `.ma` path of the following NODE graph, allowing embedded prototypes to be matched to references. See [map prop placements](map-prop-placements.md).

The catalogue audit validates 767 props and 1,619 embedded surfaces. Plain color materials are counted separately from textured parts. It does not simulate the game's animated object logic, lights or material effects.
