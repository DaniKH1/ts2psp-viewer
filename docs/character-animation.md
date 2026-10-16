# Character rotation channels

## Cowthulu / Beelzebeef

The reported `a-cowthulu-idle-brandoitch-mon` preview folded the torso and
stretched the left side. The reader built the static skeleton correctly from
inverse bind matrices, but animation channels replaced the complete local
rotation and discarded the fixed BODY joint orientation.

Seven Cowthulu joints have a nonidentity orientation: the first two spine joints,
left ear, left clavicle, two left nipple roots and left leg. These include
180-degree orientations and a rotated coordinate frame for the spine. Most
BANM rotation values stay close to identity because those fixed frames are
applied separately by the game.

The BODY reader now retains the quaternion at bone record `+0x20`. GLB nodes
keep it as `extras.pspJointOrient` in glTF coordinates. Animation sampling
composes `jointOrient * animatedRotation`, including reference-pose corrections.
The rest rotation at `+0x30` is already represented by the animation channels;
it must not be applied again.

Static mesh positions and inverse bind matrices are preserved. The preview's
intermediate GLB cache version is updated, so an older cached model cannot omit
the new orientation metadata. No ISO reimport is needed. New GLB/glTF/FBX
animation exports bake the corrected rotations into ordinary glTF channels.

Skinned prop previews use the same composition for their reference-adjusted
BANM channels, preserving parity with the shared GLB exporter. The existing
four-clip Cowthulu stage check covers this path.

## Checks

- The original BODY rest rotation/translation channels reconstruct Cowthulu and
  Night Beast meshes within `2e-5` source units, including mirrored joints.
- All 12 clips in `cowthulu_anims`, including the `-mon` variants, retain the
  spine orientation and yield finite poses at the checked frames.
- Quaternion order is tested with rotations about different axes.
- The real GUI played the reported clip for 163 frame updates and two loops,
  with the bone overlay enabled and no callback errors. GPU frames were also
  inspected from the front and rear to verify the reported distortion.
