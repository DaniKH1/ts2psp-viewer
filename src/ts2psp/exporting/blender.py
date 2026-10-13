"""Run Blender when the user requests FBX export."""

import subprocess
from pathlib import Path
from ts2psp.paths import resource_path


def export_path(source, target, blender=None):
    """Convert a completed GLB to FBX only when the selected target requires it."""
    if Path(target).suffix.lower() != ".fbx":
        return
    if not blender:
        raise RuntimeError("Select the Blender executable to export FBX.")
    script = resource_path("exporting/blender_bridge.py")
    result = subprocess.run(
        [str(blender), "-b", "--python", str(script), "--", str(source), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode or not Path(target).exists():
        raise RuntimeError(
            "Blender could not export FBX:\n" + (result.stderr or result.stdout)[-2000:]
        )
