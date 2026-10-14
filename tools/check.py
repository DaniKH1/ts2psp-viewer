"""Run unit tests; add --gui for the real GPU and window checks."""

from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true")
    args = parser.parse_args()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        env=environment,
        check=True,
        timeout=180,
    )
    if args.gui:
        for name in (
            "test_shape_key_preset_textures.py",
            "test_floating_button_wiring.py",
            "test_gpu.py",
            "test_prop_material_gpu.py",
            "test_prop_helpers_gui.py",
        ):
            subprocess.run(
                [sys.executable, str(ROOT / "tests" / name)],
                cwd=ROOT,
                env=environment,
                check=True,
            )
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tests/test_prop_playback_gui.py"),
                "--slow-frame",
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            timeout=45,
        )


if __name__ == "__main__":
    main()
