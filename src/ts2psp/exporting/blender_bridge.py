"""Run inside Blender: blender -b --python glb_to_fbx_blender.py -- input.glb output.fbx"""

import sys

import bpy


def main():
    args = sys.argv[sys.argv.index("--") + 1 :]
    source, target = args
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=source)
    bpy.ops.export_scene.fbx(
        filepath=target,
        use_selection=False,
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_actions=True,
        path_mode="COPY",
        embed_textures=True,
    )


if __name__ == "__main__":
    main()
