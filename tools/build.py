"""Build a Windows one-file EXE; game resources are never bundled."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "TS2 PSP Viewer",
        "--paths",
        str(ROOT / "src"),
        "--icon",
        str(ROOT / "src/ts2psp/assets/Icon.ico"),
        "--add-data",
        f'{ROOT / "src/ts2psp/assets"};assets',
        "--add-data",
        f'{ROOT / "src/ts2psp/exporting/blender_bridge.py"};exporting',
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
        str(ROOT / "run_viewer.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)


if __name__ == "__main__":
    main()
